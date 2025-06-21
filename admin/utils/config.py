from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    postgres_user: str
    postgres_password: str
    postgres_host: str = "green_db"
    postgres_port: int = 5432
    postgres_db: str
    pool_size: int = 80          # depends
    max_overflow: int = 5

    ADMIN_RPC_TOKEN: str

    BOT_URL: str

    WEBHOOK_HOST: str

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    ALLOW_ORIGINS: List[str] = ["*"]

    @property
    def database_url(self) -> str:  # → postgresql+asyncpg://user:pw@host/db
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
