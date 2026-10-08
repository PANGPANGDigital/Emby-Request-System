<p align="center">
  <img src="app/static/logo.svg" alt="Emby 求片中心 Logo" width="112">
</p>

<h1 align="center">Emby 求片中心</h1>

<p align="center">
  面向个人媒体库与小型 Emby 社群的媒体请求管理系统
</p>

<p align="center">
  <img src="https://img.shields.io/badge/UI-v11-9A3412?style=flat-square" alt="已测试界面 v11">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker Compose">
  <img src="https://img.shields.io/badge/Python-3.13-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.13">
</p>

<p align="center">
  <a href="#项目亮点">项目亮点</a> ·
  <a href="#界面预览">界面预览</a> ·
  <a href="#快速部署">快速部署</a> ·
  <a href="#数据与备份">数据与备份</a>
</p>

---

用户可以搜索电影和剧集、提交求片或追新申请、跟踪处理进度；管理员可以同步 Emby 片库、管理用户和处理申请，并通过飞书接收新申请通知。系统支持 Docker 部署，适用于群晖等 NAS。

## 项目亮点

- **资源发现**：通过 TMDB 搜索电影和剧集，并结合 Emby 同步结果显示片库状态。
- **完整申请流程**：电影可直接求片；剧集求片和追新均可在弹窗中选择季数。
- **面向不同角色的界面**：普通用户使用“片库掠影 / 资源搜索 / 求片广场”；管理员另有工作台、申请处理和站点配置。
- **最新入库片库**：普通用户片库掠影展示最新入库的最多 10 个资源，并按 TMDB ID 与媒体类型去重。
- **片库与申请管理**：管理员可同步 Emby、处理申请、添加备注、管理普通用户；“测试数据”状态以灰色标签显示。
- **飞书通知**：新申请可通过飞书机器人通知，支持签名校验和测试消息；不包含 Telegram 通知。
- **持久化与安全**：数据库、密钥和站点 Logo 由 Docker Compose 持久化；服务凭据加密后保存。
- **响应式页面**：适配桌面和移动设备。

## 界面预览

以下为已测试通过的 v11 页面截图。画面中的账号、片目、状态和配置均为演示内容，不含真实用户数据、API Key、Webhook 或私有服务地址。

<table>
  <tr>
    <td align="center" width="50%"><strong>管理员工作台</strong><br><img src="docs/screenshots/dashboard.png" alt="管理员工作台 v11" width="100%"></td>
    <td align="center" width="50%"><strong>普通用户片库掠影</strong><br><img src="docs/screenshots/library.png" alt="普通用户片库掠影 v11" width="100%"></td>
  </tr>
  <tr>
    <td align="center" width="50%"><strong>资源搜索</strong><br><img src="docs/screenshots/search.png" alt="资源搜索 v11" width="100%"></td>
    <td align="center" width="50%"><strong>初始化配置</strong><br><img src="docs/screenshots/setup.png" alt="初始化配置页面 v11" width="100%"></td>
  </tr>
  <tr>
    <td align="center" width="50%"><strong>管理员配置</strong><br><img src="docs/screenshots/settings.png" alt="管理员配置页面 v11" width="100%"></td>
    <td align="center" width="50%"><strong>移动端</strong><br><img src="docs/screenshots/mobile.png" alt="普通用户移动端页面 v11" width="42%"></td>
  </tr>
</table>

> 截图使用演示账号与示例数据。海报由 TMDB 图片服务提供；没有海报的演示条目会显示应用占位样式。

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 后端 | Python 3.13、FastAPI、Uvicorn |
| 页面 | Jinja2、原生 CSS、响应式布局 |
| 数据库 | SQLite、SQLAlchemy 2 |
| 安全 | Starlette Session、CSRF Token、Argon2、Fernet 配置加密 |
| 外部服务 | TMDB v3 API、Emby Server API、飞书机器人 Webhook |
| 部署 | Docker、Docker Compose |

## 快速部署

### 环境准备

- Docker Engine 或 Docker Desktop
- Docker Compose v2
- Emby 服务地址和 API Key
- TMDB v3 API Key
- 可选：飞书自定义机器人 Webhook 和签名密钥

### 启动服务

在项目根目录执行：

```sh
 docker compose up -d --build
```

也可以使用启动脚本：

```sh
 ./start.sh
```

应用默认监听 `9521` 端口。群晖可在 Container Manager 中通过项目目录部署，然后访问：

```text
http://<NAS 地址>:9521
```

Compose 使用相对挂载路径，项目目录可放在 NAS 上任意位置，无需修改本机绝对路径。

### 首次初始化

1. 首次访问时，创建管理员账号并填写 Emby 地址、Emby API Key 和 TMDB API Key。
2. 管理员登录后，在“站点配置”中手动同步一次 Emby 片库。
3. 在“用户管理”中创建普通用户。
4. 如需通知，在“站点配置 → 飞书通知”填写 Webhook；只有开启加签时才需要填写签名密钥。

凭据不会写入 Compose 文件。容器首次启动会生成会话密钥和配置加密密钥；Emby、TMDB 与飞书配置会加密后保存到数据库。首次手动同步成功后，系统每 30 分钟自动同步一次 Emby 片库。

## 数据与备份

Compose 会在项目目录创建 `config/` 并保存运行数据：

```text
config/
├── emby_requests.db
├── secrets/
└── uploads/
```

升级、迁移或重装前，请备份完整的 `config/` 目录：

- `emby_requests.db`：账号、申请、媒体索引和加密后的配置。
- `secrets/`：会话密钥和配置解密密钥。
- `uploads/`：站点 Logo。

如果丢失 `config/secrets/`，数据库中加密的 Emby、TMDB 和飞书凭据将无法解密，需要重新配置。**不要将 `config/` 上传到 GitHub。**

## 常用命令

```sh
# 查看容器状态
docker compose ps

# 查看实时日志
docker compose logs -f app

# 停止应用（保留 config 数据）
docker compose down

# 用当前源码重新构建
docker compose up -d --build
```

## HTTPS 与反向代理

使用 Nginx、Caddy 或 Traefik 提供 HTTPS 时，确认代理配置完成后，将 `compose.yaml` 中的 `COOKIE_SECURE` 设为 `"true"` 并重建容器。纯 HTTP 访问时保持 `"false"`，否则浏览器不会发送登录 Cookie。

## 发布安全说明

公开发布包不包含数据库、备份、`.env`、密钥目录、用户上传 Logo、Python 缓存或本地临时文件。截图只使用演示账号、示例片目和占位服务地址。发布前可参阅 [GitHub 上传检查清单](GITHUB_UPLOAD_CHECKLIST.md)，版本记录见 [CHANGELOG.md](CHANGELOG.md)。

## 许可与数据来源

当前仓库未指定开源许可证。使用者应自行确认部署、媒体数据和服务配置符合对应服务条款及内容授权要求。媒体元数据与海报由 TMDB 提供；Emby 服务器由部署者自行配置。
