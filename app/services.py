from __future__ import annotations
import hashlib
import hmac
import base64
import asyncio
import logging

import json
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Media, MediaRequest, Setting
from .security import decrypt, encrypt

logger = logging.getLogger(__name__)


ENCRYPTED_SETTINGS = {"emby_api_key", "tmdb_api_key", "feishu_webhook_url", "feishu_sign_secret"}
EMBY_SYNC_INTERVAL = timedelta(minutes=30)


def parse_emby_datetime(value: object) -> datetime | None:
    """Convert Emby's ISO timestamp into an aware UTC datetime."""
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    setting = db.get(Setting, key)
    if not setting:
        return default
    return decrypt(setting.value) if key in ENCRYPTED_SETTINGS else setting.value


def set_setting(db: Session, key: str, value: str) -> None:
    stored = encrypt(value) if key in ENCRYPTED_SETTINGS else value
    setting = db.get(Setting, key)
    if setting:
        setting.value = stored
    else:
        db.add(Setting(key=key, value=stored))


def setup_complete(db: Session) -> bool:
    return bool(get_setting(db, "setup_complete", ""))


def configured(db: Session) -> dict[str, str]:
    return {
        "emby_url": (get_setting(db, "emby_url", "") or "").rstrip("/"),
        "emby_api_key": get_setting(db, "emby_api_key", "") or "",
        "tmdb_api_key": get_setting(db, "tmdb_api_key", "") or "",
        "feishu_webhook_url": get_setting(db, "feishu_webhook_url", "") or "",
        "feishu_sign_secret": get_setting(db, "feishu_sign_secret", "") or "",
    }


def get_dashboard_stats(db: Session) -> dict:
    raw_movie_count = get_setting(db, "emby_movie_count")
    raw_tv_count = get_setting(db, "emby_tv_count")
    try:
        movie_count = int(raw_movie_count) if raw_movie_count is not None else None
        tv_count = int(raw_tv_count) if raw_tv_count is not None else None
    except ValueError:
        movie_count = tv_count = None
    pending_count = db.scalar(select(func.count()).where(MediaRequest.status.in_(["submitted", "processing"]))) or 0
    completed_count = db.scalar(select(func.count()).where(MediaRequest.status == "completed")) or 0
    return {
        "movie_count": movie_count,
        "tv_count": tv_count,
        "pending_count": pending_count,
        "completed_count": completed_count,
    }


def get_emby_sync_status(db: Session) -> dict[str, datetime | int | None]:
    """返回 Emby 片库同步器持久化保存的计划状态。

    管理员首次手动同步成功前，自动同步会保持关闭。将时间戳保存到配置中，
    可确保服务重启后不会重置 30 分钟的同步节奏。
    """
    raw_last_sync = get_setting(db, "emby_last_sync_at")
    raw_movie_count = get_setting(db, "emby_movie_count")
    raw_tv_count = get_setting(db, "emby_tv_count")
    try:
        movie_count = int(raw_movie_count) if raw_movie_count is not None else None
        tv_count = int(raw_tv_count) if raw_tv_count is not None else None
    except ValueError:
        movie_count = tv_count = None
    if not raw_last_sync:
        return {"last_sync_at": None, "next_sync_at": None, "remaining_seconds": None, "movie_count": movie_count, "tv_count": tv_count}
    try:
        last_sync_at = datetime.fromisoformat(raw_last_sync)
        if last_sync_at.tzinfo is None:
            last_sync_at = last_sync_at.replace(tzinfo=timezone.utc)
        else:
            last_sync_at = last_sync_at.astimezone(timezone.utc)
    except ValueError:
        return {"last_sync_at": None, "next_sync_at": None, "remaining_seconds": None, "movie_count": movie_count, "tv_count": tv_count}
    next_sync_at = last_sync_at + EMBY_SYNC_INTERVAL
    remaining_seconds = max(0, int((next_sync_at - datetime.now(timezone.utc)).total_seconds()))
    return {
        "last_sync_at": last_sync_at,
        "next_sync_at": next_sync_at,
        "remaining_seconds": remaining_seconds,
        "movie_count": movie_count,
        "tv_count": tv_count,
    }


def mark_emby_sync_complete(db: Session) -> None:
    """同步成功后启动或重置每 30 分钟一次的自动同步计划。"""
    set_setting(db, "emby_last_sync_at", datetime.now(timezone.utc).isoformat())
    db.commit()


async def search_tmdb(db: Session, query: str) -> list[dict]:
    config = configured(db)
    if not config["tmdb_api_key"]:
        raise ValueError("尚未配置 TMDB API Key")
    params = {
        "api_key": config["tmdb_api_key"],
        "query": query,
        "language": "zh-CN",
        "include_adult": "false",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get("https://api.themoviedb.org/3/search/multi", params=params)
        response.raise_for_status()
    results = []
    tv_ids_for_seasons = []
    for item in response.json().get("results", []):
        media_type = item.get("media_type")
        if media_type not in {"movie", "tv"}:
            continue
        title = item.get("title") or item.get("name")
        if not title:
            continue
        date = item.get("release_date") or item.get("first_air_date") or ""
        media = db.scalar(
            select(Media).where(Media.tmdb_id == item["id"], Media.media_type == media_type)
        )
        result = {
            "tmdb_id": item["id"],
            "media_type": media_type,
            "title": title,
            "original_title": item.get("original_title") or item.get("original_name"),
            "year": date[:4] or None,
            "overview": item.get("overview") or "暂无简介",
            "poster_path": item.get("poster_path"),
            "is_in_emby": bool(media and media.is_in_emby),
            "media_id": media.id if media else None,
            "tmdb_url": f"https://www.themoviedb.org/{media_type}/{item['id']}",
            "emby_seasons": [],
            "tmdb_seasons": [],
        }
        if media_type == "tv":
            # 收集剧集 ID，后续并发获取季信息。
            tv_ids_for_seasons.append((result, item["id"], media))
        results.append(result)

    # 并发获取所有剧集的 TMDB 季信息。
    if tv_ids_for_seasons:
        tmdb_api_key = config["tmdb_api_key"]
        async with httpx.AsyncClient(timeout=15) as client:
            tasks = []
            for result, tmdb_id, media in tv_ids_for_seasons:
                tasks.append(
                    client.get(
                        f"https://api.themoviedb.org/3/tv/{tmdb_id}",
                        params={"api_key": tmdb_api_key, "language": "zh-CN"},
                    )
                )
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            for (result, _, media), resp in zip(tv_ids_for_seasons, responses):
                if isinstance(resp, Exception) or resp.status_code != 200:
                    continue
                tv_data = resp.json()
                tmdb_seasons = [
                    s["season_number"]
                    for s in tv_data.get("seasons", [])
                    if s.get("season_number", 0) > 0  # 跳过“特别篇”（第 0 季）
                ]
                result["tmdb_seasons"] = tmdb_seasons
                if media and media.emby_seasons:
                    try:
                        result["emby_seasons"] = json.loads(media.emby_seasons)
                    except (json.JSONDecodeError, TypeError):
                        result["emby_seasons"] = []

    return results


def upsert_tmdb_media(db: Session, payload: dict) -> Media:
    media = db.scalar(
        select(Media).where(
            Media.tmdb_id == int(payload["tmdb_id"]), Media.media_type == payload["media_type"]
        )
    )
    if not media:
        media = Media(tmdb_id=int(payload["tmdb_id"]), media_type=payload["media_type"], title=payload["title"])
        db.add(media)
    media.title = payload["title"]
    media.original_title = payload.get("original_title")
    media.release_year = payload.get("year")
    media.overview = payload.get("overview")
    media.poster_path = payload.get("poster_path")
    db.flush()
    return media


def _feishu_sign(secret: str, timestamp: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_sha256 = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_sha256).decode("utf-8")


async def send_feishu_notification(
    config: dict,
    username: str,
    title: str,
    season_label: str,
    media_type: str,
    request_type: str,
    poster_path: str | None = None,
    tmdb_url: str | None = None,
) -> None:
    webhook_url = config.get("feishu_webhook_url", "")
    sign_secret = config.get("feishu_sign_secret", "")
    if not webhook_url:
        return

    type_label = "求片" if request_type == "request" else "追新"
    media_label = "电影" if media_type == "movie" else "剧集"
    lines = [
        [{"tag": "text", "text": "用户："}, {"tag": "text", "text": username}],
        [{"tag": "text", "text": "内容："}, {"tag": "text", "text": f"{title}{season_label}"}],
        [{"tag": "text", "text": "类型："}, {"tag": "text", "text": f"{type_label} / {media_label}"}],
    ]
    if tmdb_url:
        lines.append([{"tag": "a", "text": "TMDB 资源页面", "href": tmdb_url}])

    payload: dict = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": f"📺 新{type_label}申请",
                    "content": lines,
                }
            }
        },
    }
    if sign_secret:
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        payload["timestamp"] = timestamp
        payload["sign"] = _feishu_sign(sign_secret, timestamp)

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.post(webhook_url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Feishu notification failed")


async def sync_emby_library(db: Session) -> int:
    config = configured(db)
    if not config["emby_url"] or not config["emby_api_key"]:
        raise ValueError("请先配置 Emby 地址和 API Key")
    endpoint = f"{config['emby_url']}/Items"

    async def fetch_items(params: dict) -> list[dict]:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(endpoint, params=params)
            resp.raise_for_status()
            return resp.json().get("Items", [])

    async def fetch_total(item_type: str) -> int:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                endpoint,
                params={
                    "api_key": config["emby_api_key"],
                    "IncludeItemTypes": item_type,
                    "Recursive": "true",
                    "Limit": 0,
                },
            )
            resp.raise_for_status()
            return int(resp.json().get("TotalRecordCount", 0))

    # 第一步：同步电影和剧集。
    series_params = {
        "api_key": config["emby_api_key"],
        "IncludeItemTypes": "Movie,Series",
        "Recursive": "true",
        "Fields": "ProviderIds,ProductionYear,DateCreated",
        "Limit": 100000,
    }
    items = await fetch_items(series_params)

    # 建立剧集 Emby 条目 ID 到 TMDB ID 的映射。
    emby_id_to_tmdb: dict[str, int] = {}

    for media in db.scalars(select(Media).where(Media.is_in_emby.is_(True))):
        media.is_in_emby = False
        media.emby_item_id = None
    db.flush()

    seen: set[tuple[int, str]] = set()
    count = 0
    for item in items:
        media_type = "movie" if item.get("Type") == "Movie" else "tv"
        tmdb_value = (item.get("ProviderIds") or {}).get("Tmdb")
        if not tmdb_value:
            continue
        try:
            tmdb_id = int(tmdb_value)
        except (TypeError, ValueError):
            continue
        key = (tmdb_id, media_type)
        if key in seen:
            continue
        seen.add(key)
        media = db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.media_type == media_type))
        if not media:
            media = Media(
                tmdb_id=tmdb_id,
                media_type=media_type,
                title=item.get("Name") or "未命名",
                emby_added_at=parse_emby_datetime(item.get("DateCreated")) or datetime.now(timezone.utc),
            )
            db.add(media)
        elif media.emby_added_at is None:
            # Existing records from older versions did not store Emby's creation time.
            # Backfill it once; later syncs keep the original value stable.
            media.emby_added_at = parse_emby_datetime(item.get("DateCreated")) or datetime.now(timezone.utc)
        media.title = item.get("Name") or media.title
        media.release_year = str(item.get("ProductionYear")) if item.get("ProductionYear") else media.release_year
        media.is_in_emby = True
        media.emby_item_id = item.get("Id")
        count += 1
        if media_type == "tv":
            emby_id_to_tmdb[item["Id"]] = tmdb_id

    # 第二步：同步剧集的季信息。
    if emby_id_to_tmdb:
        # 方案一：递归查询全部季，速度更快。
        try:
            season_params = {
                "api_key": config["emby_api_key"],
                "IncludeItemTypes": "Season",
                "Recursive": "true",
                "Fields": "ProviderIds,IndexNumber,ParentIndexNumber,SeriesId",
                "Limit": 100000,
            }
            season_items = await fetch_items(season_params)
        except httpx.HTTPError:
            season_items = []

        def extract_season_num(item: dict) -> int | None:
            for key in ("IndexNumber", "ParentIndexNumber"):
                val = item.get(key)
                if val is not None and isinstance(val, int) and val > 0:
                    return val
            return None

        series_seasons: dict[str, list[int]] = {}
        for s in season_items:
            series_id = s.get("SeriesId")
            season_num = extract_season_num(s)
            if series_id and season_num is not None:
                series_seasons.setdefault(series_id, []).append(season_num)

        # 方案二：递归查询未返回结果时，逐剧集查询；速度较慢但兼容性更好。
        if not series_seasons and emby_id_to_tmdb:
            async with httpx.AsyncClient(timeout=60) as client:
                for series_emby_id in emby_id_to_tmdb:
                    try:
                        resp = await client.get(
                            f"{config['emby_url']}/Items",
                            params={
                                "api_key": config["emby_api_key"],
                                "ParentId": series_emby_id,
                                "IncludeItemTypes": "Season",
                                "Fields": "IndexNumber,ParentIndexNumber",
                            },
                        )
                        resp.raise_for_status()
                        for s in resp.json().get("Items", []):
                            season_num = extract_season_num(s)
                            series_id = s.get("SeriesId") or series_emby_id
                            if season_num is not None:
                                series_seasons.setdefault(series_id, []).append(season_num)
                    except httpx.HTTPError:
                        continue

        # 更新媒体记录中的已入库季数。
        for series_emby_id, season_numbers in series_seasons.items():
            tid = emby_id_to_tmdb.get(series_emby_id)
            if not tid:
                continue
            media = db.scalar(
                select(Media).where(Media.tmdb_id == tid, Media.media_type == "tv")
            )
            if media:
                media.emby_seasons = json.dumps(sorted(set(season_numbers)))

    movie_count, tv_count = await asyncio.gather(fetch_total("Movie"), fetch_total("Series"))
    set_setting(db, "emby_movie_count", str(movie_count))
    set_setting(db, "emby_tv_count", str(tv_count))
    db.commit()
    return count
