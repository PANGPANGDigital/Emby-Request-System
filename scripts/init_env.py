from __future__ import annotations

import base64
import os
import random
import string
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"
ENV_FILE = ROOT / ".env"


def rand_hex(length: int = 16) -> str:
    return os.urandom(length).hex()


def rand_urlsafe(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(random.choice(alphabet) for _ in range(length))


def main() -> None:
    if ENV_FILE.exists():
        print(".env 已存在，跳过初始化。")
        return

    if not ENV_EXAMPLE.exists():
        raise FileNotFoundError("未找到 .env.example")

    text = ENV_EXAMPLE.read_text(encoding="utf-8")

    # 仅替换占位符行，保留用户可能手动修改过的值
    text = text.replace("replace-with-a-long-random-password", rand_hex(20))
    text = text.replace("replace-with-a-long-random-session-secret", rand_urlsafe(48))
    text = text.replace("replace-with-a-fernet-key", _gen_fernet())

    ENV_FILE.write_text(text, encoding="utf-8")
    print("已生成包含随机密钥的 .env 文件。")


def _gen_fernet() -> str:
    try:
        from cryptography.fernet import Fernet

        return Fernet.generate_key().decode()
    except ImportError:
        # 生成一个符合 Fernet 规范的 URL-safe Base64 编码 32 字节密钥，
        # 这样首次部署只需标准库中的 Python 3，不依赖预先安装 cryptography。
        return base64.urlsafe_b64encode(os.urandom(32)).decode()


if __name__ == "__main__":
    main()
