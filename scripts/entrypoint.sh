#!/bin/sh
set -eu

SECRETS_DIR="/app/secrets"
SESSION_SECRET_FILE="$SECRETS_DIR/session_secret.txt"
ENCRYPTION_KEY_FILE="$SECRETS_DIR/encryption_key.txt"

mkdir -p "$SECRETS_DIR"

if [ ! -f "$SESSION_SECRET_FILE" ]; then
    python3 -c "import secrets; print(secrets.token_urlsafe(48))" > "$SESSION_SECRET_FILE"
fi

if [ ! -f "$ENCRYPTION_KEY_FILE" ]; then
    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > "$ENCRYPTION_KEY_FILE"
fi

export SESSION_SECRET=$(cat "$SESSION_SECRET_FILE")
export SETTINGS_ENCRYPTION_KEY=$(cat "$ENCRYPTION_KEY_FILE")

exec "$@"
