"""Лимиты Telegram (ТЗ §3) — все в одном месте."""

MiB = 1024 * 1024

FILE_MAX = 2000 * MiB  # 4000 частей по 512 КБ
FILE_MAX_PREMIUM = 4000 * MiB
PHOTO_MAX = 10 * MiB  # фото, отправленное как фото
PHOTO_SIDES_SUM = 10000
PHOTO_RATIO = 20
CAPTION_MAX = 1024
THUMB_MAX = 200 * 1024
THUMB_SIDE = 320
TITLE_MAX = 128
ABOUT_MAX = 255
BATCH = 100  # сообщений за один запрос удаления/получения
FLOOD_WAIT_MAX = 60  # FloodWait дольше — задача падает с retry_after


def file_limit(premium: bool) -> int:
    return FILE_MAX_PREMIUM if premium else FILE_MAX
