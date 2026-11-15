from src.config.models import (
    AntivirusSettings,
    AppSettings,
    DatabaseSettings,
    LoggingSettings,
    TelegramSettings,
    UploadSettings,
)


class Settings:
    app: AppSettings = AppSettings()
    db: DatabaseSettings = DatabaseSettings()
    telegram: TelegramSettings = TelegramSettings()
    antivirus: AntivirusSettings = AntivirusSettings()
    upload: UploadSettings = UploadSettings()
    logging: LoggingSettings = LoggingSettings()


settings = Settings()
