"""leave_grant repository — lot 생성 + 멱등 판정 조회 + 종류별 잔여/조정 합산.

순수 쿼리/insert (flush 까지 — commit 은 호출 service, WP-001 레이어 컨벤션). 발생·이월
service(P2)가 소비하고, `sum_category_remaining`/`sum_adjustment_delta` 는 P3 잔여 derive 가
재사용한다. 정본 = 40-architecture/domains/leave_grant.md.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select, update
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


async def get_by_id(session: AsyncSession, grant_id: UUID) -> LeaveGrant | None:
    """단건 lot 조회 — 취소 복원 역산(allocation → 원 lot remaining 환원, WP-004 P1)."""
    return await session.get(LeaveGrant, grant_id)


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


async def valid_lots_fefo(
    session: AsyncSession,
    employee_id: UUID,
    category: LeaveCategory,
    use_date: date,
) -> list[LeaveGrant]:
    """동일 category 의 **valid lot** FEFO 후보(만료 임박순). 차감은 WP-003 이 소비.

    valid lot = `(expiry_date IS NULL OR use_date <= expiry_date) AND remaining > 0`
    (= leave_grant §Invariant 단일 규칙 — **소비 판정 정본**). wall-clock today 가 아니라 신청
    `use_date` 와 lot `expiry_date` 비교. **status 필터 없음**(잔여 derive 는 active 만 보지만,
    소비 valid 판정은 status 가 아니라 이 단일 규칙이 정본 — denormalize 인 status 로 가리지 않음).
    정렬 = `expiry_date` ASC(임박 우선)·**NULL(연차 무만료) 최후미**·동률은 `granted_at` ASC.
    category 가로지르지 않음.
    """
    stmt = (
        select(LeaveGrant)
        .where(
            LeaveGrant.employee_id == employee_id,
            LeaveGrant.category == category,
            LeaveGrant.remaining > 0,
            or_(LeaveGrant.expiry_date.is_(None), LeaveGrant.expiry_date >= use_date),
        )
        .order_by(LeaveGrant.expiry_date.asc().nulls_last(), LeaveGrant.granted_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def zero_active_lots(
    session: AsyncSession, employee_id: UUID, category: LeaveCategory
) -> int:
    """해당 category active lot `remaining` → 0 (회계 이월 리셋). 반환 갱신 행 수(flush 까지).

    `amount`·행은 보존(audit — hard delete 금지, domains 보존 원칙). remaining 만 0화하여 잔여
    합산·FEFO(`remaining > 0`) 양쪽에서 빠진다. commit 은 호출 service. carryover 리셋 전용.
    """
    stmt = (
        update(LeaveGrant)
        .where(
            LeaveGrant.employee_id == employee_id,
            LeaveGrant.category == category,
            LeaveGrant.status == GrantStatus.ACTIVE,
        )
        .values(remaining=Decimal(0))
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount


async def expiring_lots(
    session: AsyncSession,
    employee_id: UUID,
    categories: list[LeaveCategory],
) -> list[LeaveGrant]:
    """만료 안내용 — 유효기간 있는 active lot(remaining>0·expiry NOT NULL). 만료 임박순.

    본인 조회의 **보상/포상 만료 안내**(SPEC-004 §본인 조회)용. 만료일 ASC 정렬(임박 우선).
    `연차`(무만료=expiry NULL)는 제외된다(IS NOT NULL 조건). status 필터(active)는 잔여
    derive 와 동일 — 이미 expired/소진 lot 은 안내 대상 아님.
    """
    stmt = (
        select(LeaveGrant)
        .where(
            LeaveGrant.employee_id == employee_id,
            LeaveGrant.category.in_(categories),
            LeaveGrant.status == GrantStatus.ACTIVE,
            LeaveGrant.remaining > 0,
            LeaveGrant.expiry_date.is_not(None),
        )
        .order_by(LeaveGrant.expiry_date.asc(), LeaveGrant.granted_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def expire_lapsed_lots(session: AsyncSession, today: date) -> int:
    """만료일 경과(wall-clock `today`) active lot → `status=expired`. 반환 전환 행 수(flush 까지).

    `expiry_date < today` (만료일 당일은 `use_date <= expiry` 로 아직 소비 가능 → 경과 아님,
    그 다음날부터 경과). `연차`(expiry NULL)는 매칭 안 됨 → 항상 active. 멱등 — 이미 expired 는
    `status = active` 조건에 안 걸려 재처리 무해. commit 은 호출 service(트리거 배선 = WP-005).
    """
    stmt = (
        update(LeaveGrant)
        .where(
            LeaveGrant.status == GrantStatus.ACTIVE,
            LeaveGrant.expiry_date.is_not(None),
            LeaveGrant.expiry_date < today,
        )
        .values(status=GrantStatus.EXPIRED)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount
