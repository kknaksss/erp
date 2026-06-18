"""leave_grant repository — lot 생성 + 멱등 판정 조회 + 종류별 잔여/조정 합산.

순수 쿼리/insert (flush 까지 — commit 은 호출 service, WP-001 레이어 컨벤션). 발생·이월
service(P2)가 소비하고, `sum_category_remaining`/`sum_adjustment_delta` 는 P3 잔여 derive 가
재사용한다. 정본 = 40-architecture/domains/leave_grant.md.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import GrantSource, GrantStatus, LeaveCategory
from app.models.leave_adjustment import LeaveAdjustment
from app.models.leave_grant import LeaveGrant


async def create_lot(
    session: AsyncSession,
    *,
    employee_id: UUID,
    category: LeaveCategory,
    amount: Decimal,
    source: GrantSource,
    granted_at: datetime,
    remaining: Decimal | None = None,
    expiry_date: date | None = None,
    granted_by: UUID | None = None,
    reason: str | None = None,
    status: GrantStatus = GrantStatus.ACTIVE,
) -> LeaveGrant:
    """lot 1행 insert (flush 까지, commit 은 호출 service). remaining 기본 = amount(미차감)."""
    lot = LeaveGrant(
        employee_id=employee_id,
        category=category,
        amount=amount,
        remaining=amount if remaining is None else remaining,
        source=source,
        expiry_date=expiry_date,
        granted_by=granted_by,
        reason=reason,
        granted_at=granted_at,
        status=status,
    )
    session.add(lot)
    await session.flush()
    return lot


def _year_range(year: int) -> tuple[datetime, datetime]:
    """[year-01-01, (year+1)-01-01) UTC — granted_at 연도 필터(instant 비교 → tz-safe)."""
    return (datetime(year, 1, 1, tzinfo=UTC), datetime(year + 1, 1, 1, tzinfo=UTC))


async def exists_lot_for_year(
    session: AsyncSession,
    employee_id: UUID,
    source: GrantSource,
    category: LeaveCategory,
    year: int,
) -> bool:
    """멱등 판정 — 해당 연도(granted_at)·source·category lot 이 이미 있나.

    멱등 키 = (employee_id, source, category, year(granted_at)). P1 스키마에 fiscal_year 컬럼이
    없어 granted_at 연도가 유일 신호(코드 SoT). granted_at 은 service 가 fiscal_year 에서
    deterministic 하게 set(wall-clock 아님)하므로 연-경계 재실행도 정확. 발생/이월/신규 공용.
    """
    lo, hi = _year_range(year)
    stmt = (
        select(LeaveGrant.id)
        .where(
            LeaveGrant.employee_id == employee_id,
            LeaveGrant.source == source,
            LeaveGrant.category == category,
            LeaveGrant.granted_at >= lo,
            LeaveGrant.granted_at < hi,
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def sum_category_remaining(
    session: AsyncSession, employee_id: UUID, category: LeaveCategory
) -> Decimal:
    """동일 category active lot `remaining` 합(이월의 남은연차 계산용; P3 잔여 derive 재사용)."""
    stmt = select(func.coalesce(func.sum(LeaveGrant.remaining), 0)).where(
        LeaveGrant.employee_id == employee_id,
        LeaveGrant.category == category,
        LeaveGrant.status == GrantStatus.ACTIVE,
    )
    return Decimal((await session.execute(stmt)).scalar_one())


async def sum_adjustment_delta(
    session: AsyncSession, employee_id: UUID, category: LeaveCategory
) -> Decimal:
    """동일 category `leave_adjustment.delta` 합(이월 남은연차에 가산 — 연차 1종만.

    P3 의 full multi-category derive 가 아니라 이월에 필요한 단일 category 합산 헬퍼).
    """
    stmt = select(func.coalesce(func.sum(LeaveAdjustment.delta), 0)).where(
        LeaveAdjustment.employee_id == employee_id,
        LeaveAdjustment.category == category,
    )
    return Decimal((await session.execute(stmt)).scalar_one())
