"""환경 설정 — pydantic-settings.

URL·secret 은 default 박지 않음 — env 누락 시 startup 실패 (ValidationError).
동작 파라미터 (pool size, ttl 등) 는 default 유지.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """앱 환경변수. `.env` 파일 또는 OS env 에서 로드."""

    # 앱
    debug: bool = False
    log_level: str = "INFO"

    # CORS — front 도메인 (env 필수, comma-separated)
    cors_origins: str = Field(...)

    # PostgreSQL — URL 필수 (postgresql+asyncpg://...)
    database_url: str = Field(...)
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_pre_ping: bool = True

    # JWT (secret 필수) — mediness 와 공유. HS256 로컬 검증 (업무 요청)
    jwt_secret: str = Field(...)
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_seconds: int = 60 * 60 * 24 * 7  # 7d

    # mediness 인증 위임 — 로그인/리프레시/로그아웃 라이프사이클 프록시 대상 (URL 필수)
    mediness_api_url: str = Field(...)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
