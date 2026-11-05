from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    APP_NAME: str = "AI Travel Planner"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True

    # PostgreSQL (required: the app refuses to start if these are missing)
    POSTGRES_USER: str
    POSTGRES_PASSWORD: SecretStr
    POSTGRES_DB: str
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    # Geocoding (OpenStreetMap Nominatim public API)
    # Usage policy: https://operations.osmfoundation.org/policies/nominatim/
    # - max 1 request/second for the whole app, results must be cached
    # - must identify the app (User-Agent); library defaults are not accepted
    # - must be switchable off without a code change -> GEOCODING_ENABLED
    # - no autocomplete: resolve only when the user submits a search
    GEOCODING_ENABLED: bool = True
    NOMINATIM_BASE_URL: str = "https://nominatim.openstreetmap.org"
    NOMINATIM_USER_AGENT: str = (
        "ai-travel-planner/0.1 (learning project; https://github.com/eshwarprudhvi/ai-travel-planner)"
    )
    GEOCODING_TIMEOUT_SECONDS: float = 5.0
    GEOCODING_MIN_INTERVAL_SECONDS: float = 1.0
    GEOCODING_CACHE_TTL_SECONDS: int = 86_400  # 1 day
    GEOCODING_MAX_RESULTS: int = 5

    @property
    def DATABASE_URL(self) -> URL:
        """SQLAlchemy URL built from the parts above. The password stays hidden when printed."""
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD.get_secret_value(),
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
        )

    @property
    def TEST_DATABASE_URL(self) -> URL:
        """SQLAlchemy URL for the test database (travel_planner_test)."""
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD.get_secret_value(),
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database="travel_planner_test",
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
