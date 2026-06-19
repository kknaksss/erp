"""leave_allocation repository — 승인 FEFO 차감 기록 insert (WP-003 Phase 2).

순수 insert (flush 까지 — commit 은 호출 service, WP-001 레이어 컨벤션). 한 승인 신청이 여러
lot 에 분할 차감되면 allocation 다건 생성(합 = request.amount, domains §합 일치). 복원(restored_at)·
만료소멸(expired_at) 기록은 WP-004 가 소비 — 여기선 차감 생성만. 정본 = 40-architecture/domains/leave_allocation.md.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leave_allocation import LeaveAllocation


async def create(
    session: AsyncSession,
    *,
    request_id: UUID,
    grant_id: UUID,
    amount: Decimal,
) -> LeaveAllocation:
    """차감 1행 insert (flush 까지). request_id↔grant_id 별 차감량 기록(복원 역산 근거)."""
    alloc = LeaveAllocation(request_id=request_id, grant_id=grant_id, amount=amount)
    session.add(alloc)
    await session.flush()
    return alloc


async def list_active_for_request(
    session: AsyncSession, request_id: UUID
) -> list[LeaveAllocation]:
    """복원 역산 대상 — 해당 신청의 **미처리** allocation(restored_at·expired_at 둘 다 NULL).

    WP-004 Phase 1 취소 승인 복원: 이미 복원(`restored_at`)·만료소멸(`expired_at`)된 건은
    제외해 **이중 복원 방지**(idempotent — restored/expired 둘 다 NULL 인 것만 역산). created_at ASC.
    """
    stmt = (
        select(LeaveAllocation)
        .where(
            LeaveAllocation.request_id == request_id,
            LeaveAllocation.restored_at.is_(None),
            LeaveAllocation.expired_at.is_(None),
        )
        .order_by(LeaveAllocation.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def sum_for_request(session: AsyncSession, request_id: UUID) -> Decimal:
    """해당 신청 차감 총량 — `sum(amount) WHERE request_id`(domains §합 일치 교차검증)."""
    stmt = select(func.coalesce(func.sum(LeaveAllocation.amount), 0)).where(
        LeaveAllocation.request_id == request_id
    )
    return Decimal((await session.execute(stmt)).scalar_one())
