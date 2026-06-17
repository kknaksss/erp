"""FastAPI 공통 의존성.

인증 주체(current user) 의존성은 인증 라우터/JWT 구현 시 여기에 추가.
지금은 DB 세션만 re-export.
"""

from app.core.db import get_db

__all__ = ["get_db"]
