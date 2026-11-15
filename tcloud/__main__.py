import argparse
import asyncio
import logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="tcloud")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="запустить API")
    sub.add_parser("genkey", help="сгенерировать API-ключ и пароль БД")
    sub.add_parser("login", help="войти в аккаунт Telegram (телефон, код, 2FA); сессия сохранится в volume")
    args = parser.parse_args()

    if args.cmd == "genkey":
        import secrets

        print(f"TCLOUD_API_KEY={secrets.token_urlsafe(32)}")
        print(f"TCLOUD_DB_PASSWORD={secrets.token_urlsafe(24)}")
        return

    from .config import Settings

    s = Settings()
    if args.cmd == "login":
        from telethon import TelegramClient

        async def run() -> None:
            s.tg_session.parent.mkdir(parents=True, exist_ok=True)
            async with TelegramClient(str(s.tg_session), s.tg_api_id, s.tg_api_hash) as client:
                me = await client.get_me()
                print(f"logged in as {me.username or me.id}, premium={bool(me.premium)}")

        asyncio.run(run())
    elif args.cmd == "serve":
        import uvicorn

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        uvicorn.run("tcloud.api:create_app", factory=True, host=s.host, port=s.port)


if __name__ == "__main__":
    main()
