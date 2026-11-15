import asyncio

from telethon import TelegramClient

from src.config import settings


async def login() -> None:
    settings.telegram.SESSION.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(settings.telegram.SESSION),
        settings.telegram.API_ID,
        settings.telegram.API_HASH.get_secret_value(),
    )
    async with client:
        me = await client.get_me()
        print(f"logged in as {me.username or me.id}, premium={bool(me.premium)}")


if __name__ == "__main__":
    asyncio.run(login())
