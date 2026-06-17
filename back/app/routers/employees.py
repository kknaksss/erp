"""직원 roster 라우터 — admin 동기(SPEC-002 §3).

POST /admin/employees/sync — admin 이 "동기화" → 그 admin 토큰으로 mediness `/admin/users`
pull → employee upsert. 권한 = current_user employee.role=="admin"(require_admin, member 403).
직원 목록 조회(GET)는 P4(FE 디렉토리) 범위 — 본 Phase 미포함.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_access_token, require_admin
from app.models.employee import Employee
from app.services import roster

router = APIRouter(prefix="/admin/employees", tags=["roster"])


@router.post("/sync")
async def sync_roster(
    _admin: Annotated[Employee, Depends(require_admin)],
    admin_token: Annotated[str, Depends(require_access_token)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, int]:
    """mediness `/admin/users` pull → upsert. 반환 {updated, new}. 무응답 502/503·비admin 403."""
    return await roster.sync_admin_users(session, admin_token)
