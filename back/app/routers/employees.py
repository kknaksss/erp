"""직원 관리 CRUD 라우터 — SPEC-002 §3 (origin). WP-007 P2.

- GET    /admin/employees        — 직원 명부 목록(디렉토리)
- POST   /admin/employees        — 직원 생성 + 로그인 계정 provisioning(발급 id 채택)
- PATCH  /admin/employees/{id}   — 이름·부서·직급·role 수정(ERP-local, email 불변)
- DELETE /admin/employees/{id}   — 비활성(soft delete `active=false`, 행 보존) — 204

권한 게이트 = ERP 자체 `department=="hr"`(require_hr) — 이전 mediness `require_admin` 프록시 게이트
교체(권한 모순 해소). 비-HR 403·토큰없음 401·미등록 403. 실 mediness provisioning HTTP·email
충돌(409)·실패(502/503)는 P3(포트로 추상화 — P2 는 fake happy-path).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_provisioning_port, require_hr
from app.models.employee import Employee
from app.repositories import employee as employee_repo
from app.schemas.employee import EmployeeCreate, EmployeeOut, EmployeeUpdate
from app.services import employee_admin
from app.services.employee_provisioning import ProvisioningPort

router = APIRouter(prefix="/admin/employees", tags=["employee-admin"])


@router.get("", response_model=list[EmployeeOut])
async def list_employees(
    _hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[Employee]:
    """erp DB 전 직원 명부(이름순). require_hr(비-HR 403·토큰없음 401·미등록 403)."""
    return await employee_repo.list_all(session)


@router.post("", response_model=EmployeeOut, status_code=201)
async def create_employee(
    payload: EmployeeCreate,
    _hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
    port: Annotated[ProvisioningPort, Depends(get_provisioning_port)],
) -> Employee:
    """직원 생성 + 로그인 계정 provisioning(발급 id 채택). 검증 위반 422·비-HR 403."""
    return await employee_admin.create(session, payload, port)


@router.patch("/{employee_id}", response_model=EmployeeOut)
async def update_employee(
    employee_id: UUID,
    payload: EmployeeUpdate,
    _hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Employee:
    """이름·부서·직급·role ERP-local 수정(email 불변·mediness push 없음). 미존재 404·422·403."""
    return await employee_admin.update(session, employee_id, payload)


@router.delete("/{employee_id}", status_code=204)
async def deactivate_employee(
    employee_id: UUID,
    _hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
    port: Annotated[ProvisioningPort, Depends(get_provisioning_port)],
) -> Response:
    """비활성(soft delete `active=false`, 행 보존). 멱등 — 이미 비활성도 204. 미존재 404·비-HR 403."""
    await employee_admin.deactivate(session, employee_id, port)
    return Response(status_code=204)
