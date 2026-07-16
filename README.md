# Emby 求片系统

面向个人或小型 Emby 社群的媒体请求中心。用户可搜索 TMDB 的电影和剧集、查看内容是否已在 Emby 片库中，并提交求片或按季追新；管理员可集中处理申请、同步片库和接收 Telegram 通知。

> 本项目仅用于管理你有权提供的媒体内容之可用性请求与片库运营。

## 功能一览

- 首次运行向导：创建管理员并配置 Emby、TMDB。
- TMDB 中文搜索电影和剧集，展示海报、简介、年份和原始标题。
- 同步 Emby 电影、剧集及已入库季数；搜索结果会标明片库状态。
- 电影可求片，剧集可求片或指定季数追新；同一用户的相同申请会被拦截。
- 管理员可按状态、申请类型筛选和分页处理申请，并填写处理备注。
- 申请状态包括：已提交、处理中、已入库、已暂缓。
- 用户管理：创建、启用、停用和删除普通用户（删除用户会同时删除其申请）。
- 新申请可发送 Telegram Bot 通知，带海报和 TMDB 链接；支持在后台发送测试消息。
- 可在后台上传站点 Logo；界面适配手机、平板与桌面端。
- 首次手动同步成功后，每 30 分钟自动同步一次 Emby 片库。

## 技术栈

| 类别 | 采用技术 |
| --- | --- |
| 后端 | Python 3.13、FastAPI、Uvicorn |
| 页面 | Jinja2 服务端模板、原生 CSS、响应式布局 |
| 数据库与 ORM | PostgreSQL 17、SQLAlchemy 2、psycopg 3 |
| 身份与安全 | Starlette Session、CSRF Token、Argon2 密码哈希、Fernet 配置加密 |
| 外部服务 | TMDB v3 API、Emby Server API、可选 Telegram Bot API |
| 容器化 | Docker、Docker Compose |

## 架构与数据流

```text
浏览器
  │
  ▼
FastAPI + Jinja2 ───────────────► PostgreSQL（用户、申请、配置、媒体索引）
  │                                      ▲
  ├──── 搜索内容 ────────────────────────┤
  ▼                                      │
TMDB API ── 电影 / 剧集元数据            │
                                         │
Emby Server API ── 片库与季信息 ── 手动首次同步，随后每 30 分钟自动同步
  │
  └──── 新申请通知（可选）──► Telegram Bot API
```

## 最终版原型

发布包包含 [最终版交互原型](prototype-ui-v6.html)。这是用于展示界面设计的静态 HTML 文件，不连接真实服务，也不包含任何账号、密钥或私有地址；下载后可直接用浏览器打开查看。

以下为已上传至 GitHub 的页面原型预览图。图片仅使用演示内容，不含真实服务配置或凭据。

| 登录 | 初始化设置 |
| --- | --- |
| ![登录页原型](https://raw.githubusercontent.com/PANGPANGDigital/Emby-Request-System/main/docs/prototypes/images/login.png) | ![初始化设置页原型](https://raw.githubusercontent.com/PANGPANGDigital/Emby-Request-System/main/docs/prototypes/images/setup-redesign.png) |
| 工作台 | 用户管理 |
| ![工作台原型](https://raw.githubusercontent.com/PANGPANGDigital/Emby-Request-System/main/docs/prototypes/images/dashboard.png) | ![用户管理页原型](https://raw.githubusercontent.com/PANGPANGDigital/Emby-Request-System/main/docs/prototypes/images/admin_users.png) |

## 部署前准备

- 一台可运行 Docker Compose 的主机（Docker Desktop 或 Docker Engine + Compose 插件）。
- 可被部署主机访问的 Emby 服务地址与 Emby API Key。
- TMDB v3 API Key，用于搜索电影和剧集。可在 [TMDB API 设置](https://www.themoviedb.org/settings/api) 创建。
- 可选：Telegram Bot Token 和接收通知的 Chat ID。

默认对外端口为 `8088`，部署完成后通过 `http://<服务器地址>:8088` 访问。

## 使用 Docker Compose 部署

在项目根目录运行：

```sh
./start.sh
```

`start.sh` 会先检查 `.env` 是否存在；首次运行时会通过本机的 Python 3 自动执行 `scripts/init_env.py`，根据 `.env.example` 创建 `.env` 并生成随机密钥，然后构建并在后台启动应用与 PostgreSQL。无需手动填写数据库密码或应用密钥。

### 使用 Python 3 自动生成 `.env`（推荐）

首次部署主机需要安装 Python 3。启动脚本会优先使用 `python3`，若系统中只有 `python`，则要求它也指向 Python 3。自动生成过程等同于：

```sh
python3 scripts/init_env.py
docker compose up -d --build
```

生成脚本会从 `.env.example` 复制配置，并为以下变量填入随机值：

| 变量 | 自动生成内容 |
| --- | --- |
| `POSTGRES_PASSWORD` | PostgreSQL 随机密码 |
| `SESSION_SECRET` | 登录会话签名密钥 |
| `SETTINGS_ENCRYPTION_KEY` | 用于加密数据库中 API Key 的 Fernet 密钥 |

脚本只会在 `.env` 不存在时创建文件，**绝不会覆盖已有 `.env`**。因此日后执行 `./start.sh` 或 `python3 scripts/init_env.py` 都是安全的；已有配置会被保留，并显示跳过初始化的提示。

若提示找不到 Python 3，请安装 Python 3 后重新执行，或改用下方的手动配置方式。`.env` 创建后可在不再需要 Python 3 的环境中运行 `docker compose up -d`。

查看服务状态与日志：

```sh
docker compose ps
docker compose logs -f app
```

停止服务（保留数据库数据）：

```sh
docker compose down
```

重新构建并升级当前目录中的代码：

```sh
docker compose up -d --build
```

### 手动配置 `.env`（可选）

若不使用启动脚本，可先复制模板并填写值：

```sh
cp .env.example .env
docker compose up -d --build
```

`.env` 中的变量如下：

| 变量 | 用途 |
| --- | --- |
| `POSTGRES_DB` | PostgreSQL 数据库名 |
| `POSTGRES_USER` | PostgreSQL 用户名 |
| `POSTGRES_PASSWORD` | PostgreSQL 密码 |
| `SESSION_SECRET` | 登录会话签名密钥 |
| `SETTINGS_ENCRYPTION_KEY` | 加密数据库中 Emby、TMDB、Telegram 凭据的 Fernet 密钥 |

请妥善备份 `.env`，特别是 `SETTINGS_ENCRYPTION_KEY`。**不得在已有数据的部署中随意更换该密钥**，否则之前保存的 API Key 将无法解密。不要将 `.env` 提交到版本库或公开分享。

## 首次配置与日常使用

1. 打开 `http://<服务器地址>:8088`，完成初始化向导。
2. 创建管理员账号（用户名至少 3 位、密码至少 10 位），填写 Emby 服务地址、Emby API Key 与 TMDB v3 API Key。
3. 使用管理员账号登录，进入“站点配置”，点击“立即同步 Emby”。首次同步成功后，系统才会启用 30 分钟一次的自动同步。
4. 在“用户管理”中创建普通用户。
5. 普通用户从“搜索内容”检索影片或剧集并提交申请；剧集追新必须选择要观看的季数。
6. 管理员在“处理中心”筛选申请、填写处理备注并更新状态。

### Emby 同步说明

- 同步通过 Emby 项目的 TMDB Provider ID 匹配内容，因此 Emby 库中的影片或剧集需具备正确的 TMDB ID，才能可靠显示“已入库”。
- 同步会读取电影、剧集与剧集季信息；页面会提示剧集哪些季已入库或仍可追新。
- “立即同步”成功后会更新下次自动同步时间；服务重启不会重置这 30 分钟的节奏。
- Emby 地址应以 `http://` 或 `https://` 开头，例如 `https://emby.example.com`，系统会自动去除结尾的 `/`。

### Telegram 通知（可选）

管理员可在“站点配置 → Telegram 通知”中填写：

- `Bot Token`：通过 Telegram 的 `@BotFather` 创建机器人后获得。
- `Chat ID`：接收申请消息的私聊或群组 ID。

配置后可发送测试消息。每当用户成功提交求片或追新申请，系统会异步发送通知；若海报发送失败，会自动尝试发送纯文本消息。

### Logo 说明

在“站点配置”上传图片即可替换默认 Logo。当前 Logo 保存在应用容器内；若执行会重建容器的升级操作，建议重新上传 Logo，或在部署层为 `app/static/uploads` 配置持久化挂载。

## HTTPS 与反向代理

生产环境建议由 Nginx、Caddy、Traefik 或其他反向代理终止 TLS，并把应用放在 HTTPS 之后。此时请将 `compose.yaml` 中应用服务的：

```yaml
COOKIE_SECURE: "false"
```

改为：

```yaml
COOKIE_SECURE: "true"
```

然后重建应用：

```sh
docker compose up -d --build
```

启用后，浏览器只会通过 HTTPS 发送会话 Cookie；在纯 HTTP 环境中不要设置为 `true`，否则无法正常保持登录。

## 备份与恢复

业务数据存放在 Docker 命名卷 `postgres_data` 中。升级前至少备份数据库和 `.env`：

```sh
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > emby_requests_backup.sql
```

恢复到同一套配置时：

```sh
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < emby_requests_backup.sql
```

上述命令会直接读取数据库容器中的环境变量。请勿执行 `docker compose down -v`，除非确认要删除包括 PostgreSQL 在内的全部持久化数据。

## 本地开发（可选）

项目默认使用 PostgreSQL；开发时也可通过 `DATABASE_URL` 指向 SQLite 或其他兼容 SQLAlchemy 的数据库。安装依赖后启动：

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SESSION_SECRET='development-session-secret'
export SETTINGS_ENCRYPTION_KEY='替换为有效的 Fernet 密钥'
uvicorn app.main:app --reload
```

可用以下命令生成有效的 Fernet 密钥：

```sh
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

默认开发数据库为项目目录下的 `emby_requests.db`，开发服务地址为 `http://127.0.0.1:8000`。

## 常见问题

**搜索无法使用或结果为空**

确认 TMDB v3 API Key 正确，并确认部署主机可以访问 `api.themoviedb.org`。

**内容明明在 Emby 中，却没有标记已入库**

先在后台手动同步，再检查该 Emby 条目的元数据是否包含正确的 TMDB Provider ID。

**同步失败**

检查 Emby 地址是否可从容器访问、API Key 是否有效，以及 Emby 是否允许该 API Key 读取媒体库。

**升级后无法读取已保存的配置**

通常是 `.env` 中的 `SETTINGS_ENCRYPTION_KEY` 被替换。恢复部署前使用的 `.env` 即可。

**Telegram 没有收到通知**

在后台重新保存 Token 与 Chat ID，并发送测试消息；确认机器人已被允许向目标私聊或群组发言。
