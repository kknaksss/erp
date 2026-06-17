"""직원 roster 라우터 — admin 동기 + 목록 조회(SPEC-002 §3).

- POST /admin/employees/sync — admin "동기화" → mediness `/admin/users` pull → upsert.
- GET  /admin/employees      — 직원 명부 목록(디렉토리). require_admin.

권한 = current_user employee.role=="admin"(require_admin, member 403).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_access_token, require_admin
from app.models.employee import Employee
from app.repositories import employee as employee_repo
from app.schemas.employee import EmployeeOut
from app.services import roster

router = APIRouter(prefix="/admin/employees", tags=["roster"])


@router.get("", response_model=list[EmployeeOut])
async def list_employees(
    _admin: Annotated[Employee, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[Employee]:
    """erp DB 전 직원 명부(이름순). require_admin(member 403·토큰없음 401)."""
    return await employee_repo.list_all(session)


@router.post("/sync")
async def sync_roster(
    _admin: Annotated[Employee, Depends(require_admin)],
    admin_token: Annotated[str, Depends(require_access_token)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, int]:
    """mediness `/admin/users` pull → upsert. 반환 {updated, new}. 무응답 502/503·비admin 403."""
    return await roster.sync_admin_users(session, admin_token)
