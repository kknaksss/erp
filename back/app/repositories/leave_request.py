"""leave_request repository — intake 생성 + dedup 판정 + 본인 이력 조회 (WP-003 Phase 1).

순수 쿼리/insert (flush 까지 — commit 은 호출 service, WP-001 레이어 컨벤션). 차감(allocation)
=WP-003 P2 승인, 취소·변경=WP-004 는 손대지 않는다. 정본 = 40-architecture/domains/leave_request.md.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.enums import AmPm, LeaveCategory, LeaveUnit, RequestChannel, RequestStatus
from app.models.leave_request import LeaveRequest


async def find_duplicate(
    session: AsyncSession,
    *,
    employee_id: UUID,
    use_date: date,
    category: LeaveCategory,
    unit: LeaveUnit,
    created_at: datetime,
) -> LeaveRequest | None:
    """dedup 판정 — 동일 제출 재전송 키 `(employee_id, use_date, category, unit, created_at)`.

    created_at = intake 타임스탬프(SPEC-004 §dedup·domains §Indexes dedup 후보). webhook 재시도가
    같은 타임스탬프를 다시 보내면 이 조회가 직전 flush 행을 보고 1건만 유지하게 한다(트랜잭션 내).
    """
    stmt = select(LeaveRequest).where(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.use_date == use_date,
        LeaveRequest.category == category,
        LeaveRequest.unit == unit,
        LeaveRequest.created_at == created_at,
    )
    return (await session.execute(stmt)).scalars().first()


async def create(
    session: AsyncSession,
    *,
    employee_id: UUID,
    category: LeaveCategory,
    unit: LeaveUnit,
    amount: Decimal,
    am_pm: AmPm | None,
    use_date: date,
    note: str | None,
    channel: RequestChannel,
    created_at: datetime | None = None,
) -> LeaveRequest:
    """신청 1행 insert (status=`신청됨`, flush 까지). created_at 명시 시 server_default 대신 사용.

    intake 는 `신청됨` 만 생성 — 차감/FEFO/승인은 P2(SPEC-003). created_at 은 Slack 경로의 dedup
    기준(제출 타임스탬프)이라 명시 주입, ERP 경로는 None → server_default(now).
    """
    req = LeaveRequest(
        employee_id=employee_id,
        category=category,
        unit=unit,
        amount=amount,
        am_pm=am_pm,
        use_date=use_date,
        note=note,
        status=RequestStatus.REQUESTED,
        channel=channel,
    )
    if created_at is not None:
        req.created_at = created_at
    session.add(req)
    await session.flush()
    # server_default(created_at/updated_at) 를 async 컨텍스트에서 eager 로드 — 직렬화 시 lazy
    # refresh(greenlet) 회피.
    await session.refresh(req)
    return req


async def get_by_id(session: AsyncSession, request_id: UUID) -> LeaveRequest | None:
    """단건 조회 — HR 승인/반려 대상 lookup(WP-003 P2). 없으면 None(호출 service 가 404)."""
    return await session.get(LeaveRequest, request_id)


async def list_pending(
    session: AsyncSession,
) -> list[tuple[LeaveRequest, Employee]]:
    """HR 신청 큐 = `신청됨` 전 직원 + 신청자(employee) 조인. 사용일 ASC(임박 우선)·동률 created_at ASC.

    **이 WP 는 `신청됨` 만**(`취소요청됨` 은 WP-004 — partial index `(status) WHERE IN(신청됨,취소요청됨)`
    은 공유하나 조회는 `신청됨` 한정). 처리분(승인/반려)은 status 변경으로 자연 제외 → 이력.
    """
    stmt = (
        select(LeaveRequest, Employee)
        .join(Employee, LeaveRequest.employee_id == Employee.id)
        .where(LeaveRequest.status == RequestStatus.REQUESTED)
        .order_by(LeaveRequest.use_date.asc(), LeaveRequest.created_at.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [(req, emp) for req, emp in rows]


async def list_cancel_requested(
    session: AsyncSession,
) -> list[tuple[LeaveRequest, Employee]]:
    """HR 취소 승인 큐 = `취소요청됨` 전 직원 + 신청자(employee) 조인. 사용일 ASC·동률 created_at ASC.

    WP-004 Phase 1 — `신청됨` 신청 큐(`list_pending`)와 **별도 조회**(WP-003 계약 불변 유지).
    partial index `(status) WHERE IN(신청됨,취소요청됨)` 를 공유한다. 처리분(취소승인/반려)은
    status 변경(`취소됨`/`승인됨`)으로 자연 제외 → 이력.
    """
    stmt = (
        select(LeaveRequest, Employee)
        .join(Employee, LeaveRequest.employee_id == Employee.id)
        .where(LeaveRequest.status == RequestStatus.CANCEL_REQUESTED)
        .order_by(LeaveRequest.use_date.asc(), LeaveRequest.created_at.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [(req, emp) for req, emp in rows]


async def list_for_employee(
    session: AsyncSession, employee_id: UUID
) -> list[LeaveRequest]:
    """본인 신청/사용 이력 — 사용일 최신순(동률 created_at 최신순). 상태 포함, 본인 스코프.

    `employee_id` 로만 필터 → 타인 기록 비노출은 구조적으로 보장(SPEC-004 §본인 조회). soft delete
    행도 이력 보존이라 포함(P1 은 취소 미생성 — WP-004 이후 의미).
    """
    stmt = (
        select(LeaveRequest)
        .where(LeaveRequest.employee_id == employee_id)
        .order_by(LeaveRequest.use_date.desc(), LeaveRequest.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())
