"""employee repository — 조회 (전 필드 ERP 소유 origin, SPEC-002).

직원 정보는 ERP 소유이며 mediness 와 sync 하지 않는다(미러/pull upsert 없음). 생성·수정·비활성
CRUD 는 P2, mediness 계정 provisioning 은 P3 범위. 호출부(service)가 commit 책임.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee


async def create(
    session: AsyncSession,
    *,
    id: UUID,
    email: str,
    name: str,
    role: str,
    position: str,
    department: str,
    active: bool = True,
) -> Employee:
    """직원 행 생성(ERP-local origin). `id` = provisioning 발급 계정 id 채택(P3, P2 는 fake).

    flush 만(commit 은 호출 service). 전 필드 ERP 소유.
    """
    emp = Employee(
        id=id, email=email, name=name, role=role,
        position=position, department=department, active=active,
    )
    session.add(emp)
    await session.flush()
    return emp


async def get_by_id(session: AsyncSession, employee_id: UUID) -> Employee | None:
    return await session.get(Employee, employee_id)


async def get_by_email(session: AsyncSession, email: str) -> Employee | None:
    """email → employee (Slack intake 제출자 매핑, SPEC-004 §직원 매핑 — 1:1 확정).

    매칭은 email 1:1(이름 매칭 폐기). 미일치(None)는 호출 service 가 신청 미생성으로 처리.
    """
    result = await session.execute(select(Employee).where(Employee.email == email))
    return result.scalars().first()


async def list_all(session: AsyncSession) -> list[Employee]:
    """전 직원 명부 — 이름순. (디렉토리 목록 조회, SPEC-002 §3)"""
    result = await session.execute(select(Employee).order_by(Employee.name))
    return list(result.scalars().all())


async def list_active(session: AsyncSession) -> list[Employee]:
    """active=true 직원만 — 이름순. (발생/이월 대상 = SPEC-003 §S-4 '전 active 직원')"""
    result = await session.execute(
        select(Employee).where(Employee.active.is_(True)).order_by(Employee.name)
    )
    return list(result.scalars().all())


async def list_by_ids(session: AsyncSession, ids: list[UUID]) -> list[Employee]:
    """주어진 id 들의 employee 다건 조회 (HR 벌크 부여 대상 검증 — 존재/active 판정).

    반환 순서·완전성 보장 안 함(빠진 id = 미존재). 호출 service 가 입력 id 집합과 대조해
    미존재(404)·비활성(422)을 판정한다. 빈 입력은 빈 결과.
    """
    if not ids:
        return []
    result = await session.execute(select(Employee).where(Employee.id.in_(ids)))
    return list(result.scalars().all())
