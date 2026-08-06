from functools import lru_cache

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL
from typing import Literal


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    """

    APP_NAME: str = Field(default="Student API")

    APP_VERSION: str = Field(default="1.0.0")

    APP_ENV: str = Field(default="development")

    DEBUG: bool = Field(default=False)

    LOG_LEVEL: Literal[
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    ] = "INFO"

    # --- Discrete DB credentials, instead of one pre-built URL ---
    # This mirrors how Vault / ESO / K8s Secrets hand you credentials:
    # separate fields, not a single opaque connection string.
    DB_DRIVER: str = Field(default="postgresql+psycopg")
    DB_HOST: str = Field(default="localhost")
    DB_PORT: int = Field(default=5432)
    DB_NAME: str
    DB_USER: str
    # SecretStr keeps the password out of repr()/logs/tracebacks -
    # printing `settings` or a Pydantic ValidationError will show
    # "**********" instead of the real password.
    DB_PASSWORD: SecretStr

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=True,
    )

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        """
        Builds the SQLAlchemy connection URL from the discrete DB_* fields.
        Using sqlalchemy.engine.URL.create() (rather than an f-string)
        correctly percent-encodes the username/password, so special
        characters like @, :, / in a password can't break the URL.
        """
        return URL.create(
            drivername=self.DB_DRIVER,
            username=self.DB_USER,
            password=self.DB_PASSWORD.get_secret_value(),
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    """
    Returns a cached Settings instance.
    """
    return Settings()


settings = get_settings()