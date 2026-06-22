"""직원 관리 CRUD service (ERP-local origin) — WP-007 P2. commit 은 여기서(service-commits).

- 생성: provisioning 포트로 mediness 계정 발급 → **발급 id 를 employee.id 채택** → insert.
  실 mediness HTTP·email 충돌(409)·실패(502/503) 는 P3(P2 는 포트 happy-path).
- 수정: 이름·부서·직급·role 만 ERP-local 갱신(mediness push 없음 — 디커플). email 변경 불가.
- 비활성: soft delete(`active=false`, 행 보존·연차 FK). 멱등 — 이미 비활성이면 no-op. 비활성 시
  provisioning 포트로 로그인 차단 push(P2 fake no-op, P3 실 연동).

미존재 대상(수정·비활성) 404. 입력 검증(422)은 스키마(EmployeeCreate/Update) 가 선처리.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.employee import Employee
from app.repositories import employee as employee_repo
from app.schemas.employee import EmployeeCreate, EmployeeUpdate
from app.services.employee_provisioning import ProvisioningPort


async def create(
    session: AsyncSession, payload: EmployeeCreate, port: ProvisioningPort
) -> Employee:
    """직원 생성 + 로그인 계정 provisioning(발급 id 채택). commit 은 여기서.

    provisioning push(id 수령) → 그 id 로 employee insert → commit(트랜잭션 경계 형태).
    P2: port=fake(로컬 UUID). P3: 실 mediness internal-auth + 실패/409 시 employee 미생성.
    """
    account_id = await port.provision_account(
        email=payload.email, name=payload.name, role=payload.role
    )
    emp = await employee_repo.create(
        session,
        id=account_id,
        email=payload.email,
        name=payload.name,
        role=payload.role,
        position=payload.position,
        department=payload.department,
    )
    await session.commit()
    await session.refresh(emp)
    return emp


async def update(
    session: AsyncSession, employee_id: UUID, payload: EmployeeUpdate
) -> Employee:
    """이름·부서·직급·role ERP-local 갱신(제공 필드만). 미존재 404. mediness push 없음(디커플)."""
    emp = await employee_repo.get_by_id(session, employee_id)
    if emp is None:
        raise NotFoundError("직원을 찾을 수 없습니다")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(emp, field, value)

    await session.commit()
    await session.refresh(emp)
    return emp


async def deactivate(
    session: AsyncSession, employee_id: UUID, port: ProvisioningPort
) -> None:
    """soft delete(`active=false`, 행 보존). 미존재 404. 멱등 — 이미 비활성이면 no-op.

    비활성 전환 시 provisioning 포트로 mediness 로그인 차단 push(P2 fake no-op, P3 실 연동).
    """
    emp = await employee_repo.get_by_id(session, employee_id)
    if emp is None:
        raise NotFoundError("직원을 찾을 수 없습니다")

    if emp.active:
        emp.active = False
        await port.deactivate_account(emp.id)
        await session.commit()
