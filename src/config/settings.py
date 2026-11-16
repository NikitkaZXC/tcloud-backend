from src.config.models import (
    AntivirusSettings,
    AppSettings,
    DatabaseSettings,
    LoggingSettings,
    RabbitMQSettings,
    RedisSettings,
    TelegramSettings,
    UploadSettings,
)


class Settings:
    app: AppSettings = AppSettings()
    db: DatabaseSettings = DatabaseSettings()
    telegram: TelegramSettings = TelegramSettings()
    antivirus: AntivirusSettings = AntivirusSettings()
    upload: UploadSettings = UploadSettings()
    rabbitmq: RabbitMQSettings = RabbitMQSettings()
    redis: RedisSettings = RedisSettings()
    logging: LoggingSettings = LoggingSettings()


settings = Settings()
