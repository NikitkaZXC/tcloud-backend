#!/bin/sh
set -e

[ -f .env ] || cp .env.exemple .env

secret() {
    python3 -c "import secrets; print(secrets.token_urlsafe($1))"
}

fill() {
    if grep -q "^$1=$" .env; then
        sed -i.bak "s|^$1=$|$1=$2|" .env && rm -f .env.bak
        echo "generated $1"
    fi
}

fill APP_API_KEY "$(secret 32)"
fill POSTGRES_PASSWORD "$(secret 24)"

missing=$(grep -E "^(TELEGRAM_API_ID|TELEGRAM_API_HASH)=$" .env | cut -d= -f1 || true)
[ -z "$missing" ] || echo "fill by hand in .env: $missing (https://my.telegram.org)"
