"""테스트 환경 — app import 전에 필수 env 주입."""

import os

os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://erp:erp@localhost:5432/erp_test")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("MEDINESS_API_URL", "http://localhost:28080")
