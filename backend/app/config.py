"""Application settings loaded from environment / .env."""
from datetime import date
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. See backend/.env.example."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./mohalla.db"
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-5-5"
    sarvam_api_key: str = ""
    use_llm: bool = True
    use_sarvam: bool = True
    sim_start_date: date = date(2026, 11, 7)
    mohalla_center_lat: float = 28.5677
    mohalla_center_lng: float = 77.2433
    k_anon_min: int = 10
    dp_epsilon: float = 1.0
    cors_origins: str = "http://localhost:3000"
    llm_timeout_s: float = 20.0

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_enabled(self) -> bool:
        return self.use_llm and bool(self.anthropic_api_key)

    @property
    def sarvam_enabled(self) -> bool:
        return self.use_sarvam and bool(self.sarvam_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
