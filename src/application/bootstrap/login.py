import asyncio

from telethon import TelegramClient

from src.config import settings


async def login() -> None:
    for name, session in (
        ("api", settings.telegram.SESSION),
        ("worker", settings.telegram.WORKER_SESSION),
    ):
        session.parent.mkdir(parents=True, exist_ok=True)
        print(f"{name} session: {session}")
        client = TelegramClient(
            str(session),
            settings.telegram.API_ID,
            settings.telegram.API_HASH.get_secret_value(),
            device_model=f"tcloud-{name}",
        )
        async with client:
            me = await client.get_me()
            print(f"logged in as {me.username or me.id}, premium={bool(me.premium)}")


if __name__ == "__main__":
    asyncio.run(login())
