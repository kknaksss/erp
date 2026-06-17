"""FastAPI 공통 의존성 — DB 세션 + 인증 주체(current_user).

업무 요청은 공유 `JWT_SECRET`(HS256) 로컬 검증 → `sub`(UUID) 로 직원을 식별한다
(매 요청 mediness 호출 없음 — SPEC-001 §S-2).

이번 Phase(P2)는 토큰 → `sub`(+email) 까지. `employee` 조회·`role` 권한 게이트는
P3(employee/roster) 에서 `CurrentUser.id` 로 employee 를 lookup 해 붙인다.
"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import ForbiddenError, InvalidTokenError
from app.core.security import decode_access_token
from app.models.employee import Employee
from app.repositories import employee as employee_repo

# auto_error=False — 헤더 없음/형식오류 시 FastAPI 기본 403 대신 우리 401(InvalidTokenError) 로 통일
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    """로컬 검증된 인증 주체. P3 에서 employee(role·name·dept) 가 붙는다."""

    id: UUID  # 토큰 `sub` = mediness users.id (연결키)
    email: str | None


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    """Authorization: Bearer <access> 로컬 검증 → CurrentUser. 실패 시 401."""
    if creds is None:
        raise InvalidTokenError()
    payload = decode_access_token(creds.credentials)
    return CurrentUser(id=UUID(payload["sub"]), email=payload.get("email"))


async def require_access_token(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """logout 용 — access 토큰 원문(raw) 을 mediness revoke 로 전달하기 위해 추출.

    로컬 만료 검증은 하지 않는다(만료된 토큰도 폐기 가능해야 함 — 폐기 권위는 mediness).
    presence 만 강제. 부재 시 401.
    """
    if creds is None:
        raise InvalidTokenError()
    return creds.credentials


async def get_current_employee(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Employee:
    """current_user(sub) → employee 조회. 미러 행 없으면 401(로그인 lazy 미러 선행 필요).

    employee 행은 로그인 시 본인 `/auth/me` lazy 미러로 생성된다(SPEC-001). 토큰은 유효한데
    행이 없으면(미러 실패/미로그인) 식별 불가 → 401 로 재로그인 유도.
    """
    emp = await employee_repo.get_by_id(session, user.id)
    if emp is None:
        raise InvalidTokenError("직원 정보를 찾을 수 없습니다")
    return emp


async def require_admin(
    employee: Annotated[Employee, Depends(get_current_employee)],
) -> Employee:
    """권한 게이트 — `role == "admin"` 아니면 403(SPEC-002 동기 권한)."""
    if employee.role != "admin":
        raise ForbiddenError()
    return employee


__all__ = [
    "get_db",
    "CurrentUser",
    "get_current_user",
    "require_access_token",
    "get_current_employee",
    "require_admin",
]
