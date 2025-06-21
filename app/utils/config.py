from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    postgres_user: str
    postgres_password: str
    postgres_host: str = "green_db"
    postgres_port: int = 5432
    postgres_db: str
    pool_size: int = 50
    max_overflow: int = 5

    bot_token: str

    ADMIN_RPC_TOKEN: str

    WEBHOOK_HOST: str

    BOT_URL: str

    GREEN_WEBHOOK_PUBLIC: str

    AUTO_REPLY_INTERVAL: int = 24       # hrs

    HISTORY_WAIT_AUTHORIZED: int = 60   # minutes

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def database_url(self) -> str:  # → postgresql+asyncpg://user:pw@host/db
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
