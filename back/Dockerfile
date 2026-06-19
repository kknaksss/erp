FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# uv (의존성 관리) — ERP back 은 lean REST(Redis/큐/WS/audio 없음)라 시스템 deps 불필요
RUN pip install uv

# 의존성 레이어 캐시 — lock 우선 sync
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev || uv sync --no-dev

# 앱 + 마이그레이션 (scripts/seeds 는 admin 수동 — 이미지 제외)
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

# non-root
RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 28082

# 시작 시 alembic 1회 적용 → uvicorn(no reload). 인증=mediness 위임이라 자체 seed 없음.
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 28082"]
