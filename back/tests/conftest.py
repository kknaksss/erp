"""테스트 환경 — app import 전에 필수 env 주입 + DB 세션 픽스처.

실제 `.env` 가 있으면 그 값을 로드(통합 테스트가 실제 erp DB 에 트랜잭션-롤백으로 접근).
없으면(CI) 폴백 default. DB 픽스처는 employee migration 이 적용된 erp DB 를 전제로 한다.
"""

import os
from pathlib import Path

# 실제 .env 로드 (DB 통합 테스트용). setdefault 라 이미 set 된 env 는 보존.
_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# 폴백 (.env 없음 / CI)
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://erp:erp@localhost:5432/erp_test")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("MEDINESS_API_URL", "http://localhost:28080")

import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402


@pytest_asyncio.fixture
async def db_session():
    """실제 erp DB 에 연결 → 트랜잭션 안에서 세션 yield → 종료 시 rollback.

    repository upsert 는 flush 만(commit 안 함) 하므로 rollback 으로 모든 변경 폐기.
    erp DB(employee 테이블) 가 reachable 해야 함(`alembic upgrade head` 선행).
    """
    from app.config import settings

    engine = create_async_engine(settings.database_url)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()
