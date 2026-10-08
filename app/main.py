from __future__ import annotations
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload
from starlette.middleware.sessions import SessionMiddleware
from zoneinfo import ZoneInfo

from .database import SessionLocal, ensure_schema, get_db
from .models import Media, MediaRequest, User
from .security import csrf_token, hash_password, session_secret, validate_csrf, verify_password
from .services import (
    configured,
    get_dashboard_stats,
    get_emby_sync_status,
    mark_emby_sync_complete,
    search_tmdb,
    send_feishu_notification,
    set_setting,
    setup_complete,
    sync_emby_library,
    upsert_tmdb_media,
)

logger = logging.getLogger(__name__)

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _to_beijing(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING_TZ)


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_schema()

    sync_lock = asyncio.Lock()

    async def _auto_sync():
        """首次手动同步成功后，按已保存的时间表每 30 分钟同步一次。"""
        await asyncio.sleep(20)  # 启动后等服务和数据库就绪
        while True:
            try:
                db = SessionLocal()
                try:
                    cfg = configured(db)
                    sync_status = get_emby_sync_status(db)
                    if (
                        sync_status["remaining_seconds"] == 0
                        and cfg["tmdb_api_key"]
                        and cfg["emby_url"]
                        and cfg["emby_api_key"]
                    ):
                        async with sync_lock:
                            # 获得锁后再次检查，避免其他任务刚完成同步时重复执行。
                            if get_emby_sync_status(db)["remaining_seconds"] == 0:
                                await sync_emby_library(db)
                                mark_emby_sync_complete(db)
                finally:
                    db.close()
            except Exception:
                pass  # 不因个别错误中断循环
            await asyncio.sleep(30)

    _sync_task = asyncio.create_task(_auto_sync())
    yield
    _sync_task.cancel()
    try:
        await _sync_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Emby 求片系统", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret(),
    same_site="lax",
    https_only=os.getenv("COOKIE_SECURE", "false").lower() == "true",
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["beijing"] = _to_beijing


def beijing_strftime(dt: datetime | None, fmt: str) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING_TZ).strftime(fmt)


templates.env.filters["beijing_strftime"] = beijing_strftime

STATUS_LABELS = {
    "submitted": "已提交",
    "processing": "处理中",
    "completed": "已入库",
    "paused": "已暂缓",
    "test": "测试数据",
}
TYPE_LABELS = {"request": "求片", "follow": "追新"}
ADMIN_REQUESTS_PER_PAGE = 10


def flash(request: Request, message: str, level: str = "success") -> None:
    request.session["flash"] = {"message": message, "level": level}


def page(request: Request, name: str, **context):
    logo_path = os.path.join(os.path.dirname(__file__), "static", "uploads", "logo.png")
    logo_exists = os.path.exists(logo_path)
    context.setdefault("current_user", get_current_user_optional(request))
    context.setdefault("csrf_token", csrf_token(request.session))
    context.setdefault("flash", request.session.pop("flash", None))
    context.setdefault("status_labels", STATUS_LABELS)
    context.setdefault("type_labels", TYPE_LABELS)
    context.setdefault("logo_exists", logo_exists)
    context.setdefault("logo_src", "/static/uploads/logo.png" if logo_exists else "/static/logo.svg")
    context.setdefault("logo_version", int(os.path.getmtime(logo_path)) if logo_exists else 2)
    return templates.TemplateResponse(request, name, context)


def get_current_user_optional(request: Request) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    db = SessionLocal()
    try:
        user = db.get(User, int(user_id))
        if not user or not user.is_active:
            request.session.clear()
            return None
        db.expunge(user)
        return user
    finally:
        db.close()


def require_setup(db: Session):
    if not setup_complete(db):
        return RedirectResponse("/setup", status_code=303)
    return None


def require_user(request: Request, db: Session) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, int(user_id)) if user_id else None
    if not user or not user.is_active:
        request.session.clear()
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_admin(request: Request, db: Session) -> User:
    user = require_user(request, db)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def latest_library_media(db: Session, limit: int = 10) -> list[Media]:
    """Return the newest unique resources for the public library view.

    The database already prevents new duplicate TMDB/type rows, but filtering
    here also keeps the display correct for legacy databases that may contain
    duplicates. Because the rows are sorted first, the newest copy wins.
    """
    rows = db.scalars(
        select(Media)
        .where(Media.is_in_emby.is_(True))
        .order_by(
            Media.emby_added_at.is_(None),
            Media.emby_added_at.desc(),
            Media.updated_at.desc(),
            Media.id.desc(),
        )
    ).all()
    unique_rows: list[Media] = []
    seen: set[tuple[int, str]] = set()
    for media in rows:
        identity = (media.tmdb_id, media.media_type)
        if identity in seen:
            continue
        seen.add(identity)
        unique_rows.append(media)
        if len(unique_rows) >= limit:
            break
    return unique_rows


def checked_csrf(request: Request, token: str):
    if not validate_csrf(request.session, token):
        raise HTTPException(status_code=403, detail="表单已过期，请刷新页面后重试")


@app.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    if redirect := require_setup(db):
        return redirect
    user = get_current_user_optional(request)
    return RedirectResponse("/dashboard" if user else "/login", status_code=303)


@app.get("/setup")
def setup_form(request: Request, db: Session = Depends(get_db)):
    if setup_complete(db):
        return RedirectResponse("/login", status_code=303)
    return page(request, "setup.html")


@app.post("/setup")
def setup_submit(
    request: Request,
    csrf: Annotated[str, Form(alias="_csrf")],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    emby_url: Annotated[str, Form()],
    emby_api_key: Annotated[str, Form()],
    tmdb_api_key: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    checked_csrf(request, csrf)
    if setup_complete(db):
        return RedirectResponse("/login", status_code=303)
    username, emby_url = username.strip(), emby_url.strip().rstrip("/")
    if len(username) < 3 or len(password) < 10:
        flash(request, "账号至少 3 位，密码至少 10 位。", "error")
        return RedirectResponse("/setup", status_code=303)
    if not emby_url.startswith(("http://", "https://")) or not emby_api_key.strip() or not tmdb_api_key.strip():
        flash(request, "请填写有效的 Emby 地址、Emby API Key 和 TMDB API Key。", "error")
        return RedirectResponse("/setup", status_code=303)
    db.add(User(username=username, password_hash=hash_password(password), is_admin=True))
    set_setting(db, "emby_url", emby_url)
    set_setting(db, "emby_api_key", emby_api_key.strip())
    set_setting(db, "tmdb_api_key", tmdb_api_key.strip())
    set_setting(db, "setup_complete", "1")
    db.commit()
    flash(request, "基础配置已保存，请使用管理员账号登录。")
    return RedirectResponse("/login", status_code=303)


@app.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    if redirect := require_setup(db):
        return redirect
    if get_current_user_optional(request):
        return RedirectResponse("/dashboard", status_code=303)
    return page(request, "login.html")


@app.post("/login")
def login_submit(
    request: Request,
    csrf: Annotated[str, Form(alias="_csrf")],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    checked_csrf(request, csrf)
    user = db.scalar(select(User).where(User.username == username.strip()))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        flash(request, "账号或密码错误。", "error")
        return RedirectResponse("/login", status_code=303)
    request.session["user_id"] = user.id
    csrf_token(request.session)
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf: Annotated[str, Form(alias="_csrf")]):
    checked_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db)):
    if redirect := require_setup(db):
        return redirect
    user = require_user(request, db)
    if not user.is_admin:
        return RedirectResponse("/library", status_code=303)
    statement = select(MediaRequest).options(joinedload(MediaRequest.media), joinedload(MediaRequest.applicant))
    if user.is_admin:
        statement = statement.order_by(MediaRequest.created_at.desc()).limit(10)
    else:
        statement = statement.where(MediaRequest.applicant_id == user.id).order_by(MediaRequest.updated_at.desc()).limit(6)
    requests = db.scalars(statement).unique().all()
    library_media = db.scalars(
        select(Media)
        .where(Media.is_in_emby.is_(True))
        .order_by(Media.emby_added_at.is_(None), Media.emby_added_at.desc(), Media.updated_at.desc())
        .limit(6)
    ).all()
    stats = None
    sync_status = None
    cfg = None
    if user.is_admin:
        try:
            stats = get_dashboard_stats(db)
        except Exception:
            logger.exception("dashboard stats failed")
        try:
            sync_status = get_emby_sync_status(db)
        except Exception:
            logger.exception("dashboard sync status failed")
        try:
            cfg = configured(db)
        except Exception:
            logger.exception("dashboard configured failed")
    service_online_count = 0
    if cfg:
        service_online_count = sum(
            (
                bool(cfg.get("tmdb_api_key")),
                bool(cfg.get("emby_url") and cfg.get("emby_api_key")),
                bool(cfg.get("feishu_webhook_url")),
            )
        )
    return page(
        request,
        "dashboard.html",
        requests=requests,
        stats=stats,
        sync_status=sync_status,
        configured=cfg,
        library_media=library_media,
        service_online_count=service_online_count,
    )


@app.get("/library")
def library(request: Request, db: Session = Depends(get_db)):
    if redirect := require_setup(db):
        return redirect
    require_user(request, db)
    library_media = latest_library_media(db, limit=10)
    return page(request, "library.html", library_media=library_media)


@app.get("/media/{media_id}/poster")
async def media_poster(media_id: int, request: Request, db: Session = Depends(get_db)):
    """Proxy a poster so Emby/TMDB API keys never appear in the browser URL."""
    if redirect := require_setup(db):
        return redirect
    require_user(request, db)
    media = db.get(Media, media_id)
    if not media:
        raise HTTPException(status_code=404, detail="封面不存在")
    try:
        config = configured(db)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail="封面来源配置不可用") from exc
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = None
            if media.emby_item_id and config["emby_url"] and config["emby_api_key"]:
                emby_response = await client.get(
                    f"{config['emby_url']}/Items/{media.emby_item_id}/Images/Primary",
                    params={"api_key": config["emby_api_key"], "maxWidth": 600, "quality": 90},
                )
                if emby_response.is_success and emby_response.headers.get("content-type", "").startswith("image/"):
                    response = emby_response

            if response is None:
                # Older records may only have a TMDB ID. Resolve and cache the
                # poster path once so older records and Emby items without a
                # primary image also get real cover art.
                if not config["tmdb_api_key"]:
                    raise HTTPException(status_code=404, detail="没有可用的封面来源")
                details = await client.get(
                    f"https://api.themoviedb.org/3/{media.media_type}/{media.tmdb_id}",
                    params={"api_key": config["tmdb_api_key"], "language": "zh-CN"},
                )
                details.raise_for_status()
                poster_path = details.json().get("poster_path")
                if not poster_path:
                    raise HTTPException(status_code=404, detail="TMDB 未提供封面")
                if media.poster_path != poster_path:
                    media.poster_path = poster_path
                    db.commit()
                response = await client.get(f"https://image.tmdb.org/t/p/w500{poster_path}")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=404, detail="封面读取失败") from exc
    media_type = response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=404, detail="封面来源未返回图片")
    return Response(
        content=response.content,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/search")
async def search(request: Request, q: str = "", db: Session = Depends(get_db)):
    if redirect := require_setup(db):
        return redirect
    require_user(request, db)
    results, error = [], None
    if q.strip():
        try:
            results = await search_tmdb(db, q.strip())
        except (httpx.HTTPError, ValueError) as exc:
            logger.exception("TMDB search failed")
            error = "搜索失败，请稍后重试。"
    return page(request, "search.html", query=q, results=results, error=error)


@app.post("/requests/create")
def create_request(
    request: Request,
    background_tasks: BackgroundTasks,
    csrf: Annotated[str, Form(alias="_csrf")],
    tmdb_id: Annotated[int, Form()],
    media_type: Annotated[str, Form()],
    title: Annotated[str, Form()],
    original_title: Annotated[str | None, Form()] = None,
    year: Annotated[str | None, Form()] = None,
    overview: Annotated[str | None, Form()] = None,
    poster_path: Annotated[str | None, Form()] = None,
    request_type: Annotated[str, Form()] = "request",
    season_number: Annotated[str | None, Form()] = None,
    user_note: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
):
    checked_csrf(request, csrf)
    user = require_user(request, db)
    if media_type not in {"movie", "tv"} or request_type not in TYPE_LABELS:
        raise HTTPException(status_code=400, detail="无效的请求类型")
    media = upsert_tmdb_media(
        db,
        {"tmdb_id": tmdb_id, "media_type": media_type, "title": title, "original_title": original_title,
         "year": year, "overview": overview, "poster_path": poster_path},
    )

    # 将表单中的季数从字符串转换为整数；空值或非法值统一视为 None。
    season_num = None
    if season_number and season_number.strip():
        try:
            season_num = int(season_number.strip())
        except (ValueError, TypeError):
            season_num = None
    # 剧集的“求片”和“追新”都需要指定季数；电影不使用季数。
    if media_type != "tv":
        season_num = None
    elif not season_num or season_num < 1:
        flash(request, "请填写想观看的季数。", "error")
        return RedirectResponse("/search", status_code=303)

    if media.is_in_emby and request_type == "request":
        flash(request, "该内容已在 Emby 片库中，无需重复求片。", "error")
        return RedirectResponse("/search", status_code=303)
    duplicate_conditions = [
        MediaRequest.media_id == media.id,
        MediaRequest.applicant_id == user.id,
        MediaRequest.request_type == request_type,
    ]
    # PostgreSQL 的 SQL ``IS`` 只适用于 NULL 或布尔值判断；追新申请的季数为数值，
    # 因此需要使用普通相等比较。
    duplicate_conditions.append(
        MediaRequest.season_number.is_(None)
        if season_num is None
        else MediaRequest.season_number == season_num
    )
    duplicate = db.scalar(select(MediaRequest).where(*duplicate_conditions))
    if duplicate:
        flash(request, "你已提交过同一内容的同类申请。", "error")
        return RedirectResponse("/requests/recent", status_code=303)
    db.add(
        MediaRequest(
            media_id=media.id,
            applicant_id=user.id,
            request_type=request_type,
            season_number=season_num,
            user_note=(user_note or "").strip() or None,
        )
    )
    db.commit()

    # 在后台发送通知，避免阻塞当前提交请求。
    cfg = configured(db)
    type_label = TYPE_LABELS[request_type]
    season_label = f" 第{season_num}季" if season_num else ""
    tmdb_url = f"https://www.themoviedb.org/{media_type}/{tmdb_id}"
    background_tasks.add_task(
        send_feishu_notification,
        cfg,
        user.username,
        title,
        season_label,
        media_type,
        request_type,
        media.poster_path,
        tmdb_url,
    )

    flash(request, f"{type_label}申请已提交。")
    return RedirectResponse("/requests/recent", status_code=303)


@app.get("/requests/recent")
def recent_requests(request: Request, db: Session = Depends(get_db)):
    if redirect := require_setup(db):
        return redirect
    require_user(request, db)
    requests = db.scalars(
        select(MediaRequest).options(joinedload(MediaRequest.media), joinedload(MediaRequest.applicant))
        .order_by(MediaRequest.created_at.desc())
    ).unique().all()
    return page(request, "recent_requests.html", requests=requests)


@app.get("/admin/requests")
def admin_requests(
    request: Request,
    status: str = "",
    kind: str = "",
    page_number: int = Query(1, alias="page", ge=1),
    db: Session = Depends(get_db),
):
    if redirect := require_setup(db):
        return redirect
    require_admin(request, db)
    active_status = status if status in STATUS_LABELS else ""
    active_kind = kind if kind in TYPE_LABELS else ""
    filters = []
    if active_status:
        filters.append(MediaRequest.status == active_status)
    if active_kind:
        filters.append(MediaRequest.request_type == active_kind)

    total_requests = db.scalar(select(func.count()).select_from(MediaRequest).where(*filters)) or 0
    total_pages = max(1, (total_requests + ADMIN_REQUESTS_PER_PAGE - 1) // ADMIN_REQUESTS_PER_PAGE)
    page_number = min(page_number, total_pages)
    statement = (
        select(MediaRequest)
        .options(joinedload(MediaRequest.media), joinedload(MediaRequest.applicant))
        .where(*filters)
        .order_by(MediaRequest.updated_at.desc())
        .offset((page_number - 1) * ADMIN_REQUESTS_PER_PAGE)
        .limit(ADMIN_REQUESTS_PER_PAGE)
    )
    requests = db.scalars(statement).unique().all()
    query_parts = []
    if active_status:
        query_parts.append(f"status={active_status}")
    if active_kind:
        query_parts.append(f"kind={active_kind}")
    query_prefix = "&".join(query_parts)
    return_to = "/admin/requests" + (f"?{query_prefix}&page={page_number}" if query_prefix else f"?page={page_number}")
    return page(
        request,
        "admin_requests.html",
        requests=requests,
        active_status=active_status,
        active_kind=active_kind,
        page_number=page_number,
        total_pages=total_pages,
        total_requests=total_requests,
        query_prefix=query_prefix,
        return_to=return_to,
    )


@app.post("/admin/requests/{request_id}")
def update_request(
    request_id: int,
    request: Request,
    csrf: Annotated[str, Form(alias="_csrf")],
    status: Annotated[str, Form()],
    admin_note: Annotated[str | None, Form()] = None,
    return_to: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
):
    checked_csrf(request, csrf)
    require_admin(request, db)
    item = db.get(MediaRequest, request_id)
    if not item or status not in STATUS_LABELS:
        raise HTTPException(status_code=404, detail="申请不存在")
    item.status = status
    item.admin_note = (admin_note or "").strip() or None
    db.commit()
    flash(request, "申请处理进度已更新。")
    destination = "/dashboard" if return_to == "/dashboard" else "/admin/requests"
    if return_to and return_to.startswith("/admin/requests?"):
        destination = return_to
    return RedirectResponse(destination, status_code=303)


@app.get("/admin/users")
def admin_users(request: Request, db: Session = Depends(get_db)):
    if redirect := require_setup(db):
        return redirect
    require_admin(request, db)
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return page(request, "admin_users.html", users=users)


@app.post("/admin/users")
def create_user(
    request: Request,
    csrf: Annotated[str, Form(alias="_csrf")],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    checked_csrf(request, csrf)
    require_admin(request, db)
    username = username.strip()
    if len(username) < 3 or len(password) < 5:
        flash(request, "账号至少 3 位，密码至少 5 位。", "error")
    elif db.scalar(select(User).where(User.username == username)):
        flash(request, "该用户名已存在。", "error")
    else:
        db.add(User(username=username, password_hash=hash_password(password)))
        db.commit()
        flash(request, f"普通用户 {username} 已创建。")
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/toggle")
def toggle_user(user_id: int, request: Request, csrf: Annotated[str, Form(alias="_csrf")], db: Session = Depends(get_db)):
    checked_csrf(request, csrf)
    admin = require_admin(request, db)
    user = db.get(User, user_id)
    if not user or user.id == admin.id or user.is_admin:
        flash(request, "不能停用当前管理员或其他管理员。", "error")
    else:
        user.is_active = not user.is_active
        db.commit()
        flash(request, f"用户 {user.username} 已{'启用' if user.is_active else '停用'}。")
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/delete")
def delete_user(user_id: int, request: Request, csrf: Annotated[str, Form(alias="_csrf")], db: Session = Depends(get_db)):
    checked_csrf(request, csrf)
    admin = require_admin(request, db)
    user = db.get(User, user_id)
    if not user or user.id == admin.id or user.is_admin:
        flash(request, "不能删除管理员账号。", "error")
    else:
        for r in db.scalars(select(MediaRequest).where(MediaRequest.applicant_id == user_id)):
            db.delete(r)
        db.delete(user)
        db.commit()
        flash(request, f"用户 {user.username} 已删除。")
    return RedirectResponse("/admin/users", status_code=303)


@app.get("/admin/settings")
def admin_settings(request: Request, db: Session = Depends(get_db)):
    if redirect := require_setup(db):
        return redirect
    require_admin(request, db)
    config = configured(db)
    sync_status = get_emby_sync_status(db)
    logo_path = os.path.join(os.path.dirname(__file__), "static", "uploads", "logo.png")
    return page(
        request,
        "admin_settings.html",
        emby_url=config["emby_url"],
        tmdb_configured=bool(config["tmdb_api_key"]),
        emby_configured=bool(config["emby_api_key"]),
        feishu_configured=bool(config["feishu_webhook_url"]),
        feishu_webhook_url=config.get("feishu_webhook_url", ""),
        sync_status=sync_status,
        logo_exists=os.path.exists(logo_path),
    )


@app.post("/admin/settings")
def update_settings(
    request: Request,
    csrf: Annotated[str, Form(alias="_csrf")],
    emby_url: Annotated[str, Form()],
    emby_api_key: Annotated[str | None, Form()] = None,
    tmdb_api_key: Annotated[str | None, Form()] = None,
    feishu_webhook_url: Annotated[str | None, Form()] = None,
    feishu_sign_secret: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
):
    checked_csrf(request, csrf)
    require_admin(request, db)
    emby_url = emby_url.strip().rstrip("/")
    if not emby_url.startswith(("http://", "https://")):
        flash(request, "Emby 地址必须以 http:// 或 https:// 开头。", "error")
        return RedirectResponse("/admin/settings", status_code=303)
    set_setting(db, "emby_url", emby_url)
    if (emby_api_key or "").strip():
        set_setting(db, "emby_api_key", emby_api_key.strip())
    if (tmdb_api_key or "").strip():
        set_setting(db, "tmdb_api_key", tmdb_api_key.strip())
    if (feishu_webhook_url or "").strip():
        set_setting(db, "feishu_webhook_url", feishu_webhook_url.strip())
    if (feishu_sign_secret or "").strip():
        set_setting(db, "feishu_sign_secret", feishu_sign_secret.strip())
    db.commit()
    flash(request, "配置已保存。")
    return RedirectResponse("/admin/settings", status_code=303)


@app.post("/admin/settings/logo")
async def upload_logo(
    request: Request,
    csrf: Annotated[str, Form(alias="_csrf")],
    file: Annotated[UploadFile, File(description="Logo 图片")],
    db: Session = Depends(get_db),
):
    checked_csrf(request, csrf)
    require_admin(request, db)

    if not file.content_type or not file.content_type.startswith("image/"):
        flash(request, "请上传图片文件。", "error")
        return RedirectResponse("/admin/settings", status_code=303)

    upload_dir = os.path.join(os.path.dirname(__file__), "static", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    target = os.path.join(upload_dir, "logo.png")

    try:
        content = await file.read()
        with open(target, "wb") as f:
            f.write(content)
        flash(request, "Logo 已上传。")
    except Exception:
        logger.exception("Upload logo failed")
        flash(request, "Logo 上传失败，请稍后重试。", "error")

    return RedirectResponse("/admin/settings", status_code=303)


@app.post("/admin/settings/sync")
async def sync_emby(request: Request, csrf: Annotated[str, Form(alias="_csrf")], db: Session = Depends(get_db)):
    checked_csrf(request, csrf)
    require_admin(request, db)
    try:
        count = await sync_emby_library(db)
        mark_emby_sync_complete(db)
        flash(request, f"Emby 同步完成，识别到 {count} 个带 TMDB ID 的电影或剧集。")
    except (ValueError, httpx.HTTPError) as exc:
        logger.exception("Emby sync failed")
        flash(request, "Emby 同步失败，请检查配置后重试。", "error")
    return RedirectResponse("/admin/settings", status_code=303)


@app.post("/admin/settings/reset-feishu")
async def reset_feishu(request: Request, csrf: Annotated[str, Form(alias="_csrf")], db: Session = Depends(get_db)):
    checked_csrf(request, csrf)
    require_admin(request, db)
    set_setting(db, "feishu_webhook_url", "")
    set_setting(db, "feishu_sign_secret", "")
    db.commit()
    flash(request, "飞书通知配置已重置。")
    return RedirectResponse("/admin/settings", status_code=303)


@app.post("/admin/settings/test-feishu")
async def test_feishu(request: Request, csrf: Annotated[str, Form(alias="_csrf")], db: Session = Depends(get_db)):
    checked_csrf(request, csrf)
    require_admin(request, db)
    cfg = configured(db)
    if not cfg["feishu_webhook_url"]:
        flash(request, "请先配置飞书 Webhook URL。", "error")
        return RedirectResponse("/admin/settings", status_code=303)
    try:
        await send_feishu_notification(
            cfg,
            "管理员",
            "求片系统",
            "",
            "movie",
            "request",
            tmdb_url="https://www.themoviedb.org",
        )
        flash(request, "飞书测试消息已发送，请检查群聊。")
    except Exception:
        logger.exception("Send feishu test failed")
        flash(request, "发送失败，请稍后重试。", "error")
    return RedirectResponse("/admin/settings", status_code=303)
