from src.config.models import (
    AntivirusSettings,
    AppSettings,
    DatabaseSettings,
    JWTSettings,
    LoggingSettings,
    RabbitMQSettings,
    RedisSettings,
    RootSettings,
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
    jwt: JWTSettings = JWTSettings()
    root: RootSettings = RootSettings()


settings = Settings()
