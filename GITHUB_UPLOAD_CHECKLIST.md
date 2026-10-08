# GitHub 上传检查清单

发布包基于测试通过的 v11 应用源码。上传到 GitHub 前，可按此清单检查文件范围。

## 应上传的内容

```text
.
├── .dockerignore
├── .gitignore
├── CHANGELOG.md
├── Dockerfile
├── GITHUB_UPLOAD_CHECKLIST.md
├── README.md
├── compose.yaml
├── requirements.txt
├── start.sh
├── app/
│   ├── __init__.py
│   ├── database.py
│   ├── main.py
│   ├── models.py
│   ├── security.py
│   ├── services.py
│   ├── static/
│   │   ├── app.css
│   │   └── logo.svg
│   └── templates/
└── docs/
    └── screenshots/       # 来自 v11 实际页面的演示截图
```

## 不应上传的内容

| 路径或文件 | 原因 |
| --- | --- |
| `config/` | 数据库、加密密钥和站点上传内容 |
| `*.db`、`*.sqlite*`、`*.dump`、`*.bak` | 用户、申请、配置或备份数据 |
| `.env`、`*.pem`、`*.key`、`*.p12`、`*.pfx` | 密钥、证书或服务凭据 |
| `app/static/uploads/` | 管理员运行时上传的 Logo |
| `.venv/`、`__pycache__/`、`*.py[cod]` | 本机依赖环境和缓存 |
| `dist/`、`output/`、`tmp/`、`tmp-prototype/` | 构建包和临时文件 |
| `docs/prototypes/` | 历史 UI 原型，不是当前应用页面 |
| `.DS_Store` | macOS Finder 元数据 |

## 上传前检查

```sh
git status --short
git add .
git status --short
git diff --cached --check
git diff --cached --name-only
```

核对暂存文件中没有数据库、密钥、上传文件、`.env` 或本机绝对路径。发布截图使用演示账号和示例服务信息；不要将生产数据库截图放入仓库。

如发现服务密钥曾经提交到 Git 历史，应在对应服务中吊销并重新生成；只从当前工作目录删除不足以清除历史记录。
