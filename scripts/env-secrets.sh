#!/bin/sh
set -e

[ -f .env ] || cp .env.exemple .env
chmod 600 .env

secret() {
    python3 -c "import secrets; print(secrets.token_urlsafe($1))"
}

fill() {
    grep -q "^$1=" .env || printf '\n%s=\n' "$1" >> .env
    if grep -q "^$1=$" .env; then
        sed -i.bak "s|^$1=$|$1=$2|" .env && rm -f .env.bak
        echo "generated $1"
    fi
}

fill APP_SECRET_KEY "$(secret 32)"
fill APP_ENCRYPTION_KEY "$(python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())")"
fill ROOT_PASSWORD "$(secret 18)"
fill POSTGRES_PASSWORD "$(secret 24)"
fill RABBITMQ_PASSWORD "$(secret 24)"
fill REDIS_PASSWORD "$(secret 24)"
fill FLOWER_PASSWORD "$(secret 24)"

mkdir -p certs
if [ ! -f certs/jwt-private.pem ]; then
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out certs/jwt-private.pem 2>/dev/null
    openssl rsa -in certs/jwt-private.pem -pubout -out certs/jwt-public.pem 2>/dev/null
    chmod 600 certs/jwt-private.pem
    echo "generated certs/jwt-private.pem and certs/jwt-public.pem"
    if [ "$(uname)" = "Linux" ] && [ "$(id -u)" != "1000" ]; then
        echo "the container runs as uid 1000: sudo chown 1000 certs/jwt-private.pem"
    fi
fi

missing=$(grep -E "^(TELEGRAM_API_ID|TELEGRAM_API_HASH)=$" .env | cut -d= -f1 || true)
[ -z "$missing" ] || echo "fill by hand in .env: $missing (https://my.telegram.org)"
