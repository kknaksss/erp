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

    # ERP↔mediness internal-auth — 직원 계정 provisioning/비활성화 호출용 서버 전용 secret (WP-007 P3).
    # mediness `verify_internal_auth`(헤더 X-Internal-Auth) 와 동일 값 공유. secret 필수.
    internal_auth_secret: str = Field(...)

    # Slack 워크플로우 webhook 공유 시크릿 (intake 출처 검증, SPEC-004 §검증) — secret 필수
    erp_slack_webhook_secret: str = Field(...)

    # 문서관리 fs 저장소 volume root (WP-006 §저장소 — PG↔fs 책임 분리, 바이너리는 fs).
    # 동작 파라미터라 default 유지(env/.env 미설정에도 동작). 배포 시 운영 경로로 override.
    document_volume_root: str = "/tmp/erp-documents"

    # ONLYOFFICE Document Server 연동 (WP-006 Phase 3, architecture §JWT). secret 필수.
    # mediness `jwt_secret`(직원 신원)과 **분리** — DocServer↔BE 문서 세션 무결성용(신뢰 도메인 다름).
    onlyoffice_jwt_secret: str = Field(...)
    # DocServer 가 해석 가능한 BE base URL — download/callback 절대 URL 구성용(하드코딩 금지).
    # 정확한 호스트(내부 네트워크명 vs 공개 도메인)는 배포 결정(architecture leg ③ "가정/미결").
    onlyoffice_callback_base_url: str = Field(...)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
