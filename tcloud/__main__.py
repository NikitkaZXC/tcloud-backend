import argparse
import asyncio
import logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="tcloud")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="запустить API")
    sub.add_parser("genkey", help="сгенерировать мастер-ключ и API-ключ")
    login = sub.add_parser("login", help="войти в аккаунт Telegram и получить session string")
    login.add_argument("--api-id", type=int, required=True)
    login.add_argument("--api-hash", required=True)
    args = parser.parse_args()

    if args.cmd == "genkey":
        import secrets
        from .crypto import generate_master_key

        print(f"TCLOUD_MASTER_KEY={generate_master_key()}")
        print(f"TCLOUD_API_KEY={secrets.token_urlsafe(32)}")
    elif args.cmd == "login":
        from pyrogram import Client

        async def run() -> None:
            async with Client("tcloud-login", api_id=args.api_id, api_hash=args.api_hash, in_memory=True) as client:
                print("\nTCLOUD_TG_SESSION_STRING=" + await client.export_session_string())

        asyncio.run(run())
    elif args.cmd == "serve":
        import uvicorn
        from .config import Settings

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        s = Settings()
        uvicorn.run("tcloud.api:create_app", factory=True, host=s.host, port=s.port)


if __name__ == "__main__":
    main()
