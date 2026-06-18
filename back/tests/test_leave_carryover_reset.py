"""carryover 리셋(원 `연차` lot 0화) 검증 — WP-002 T-008 (리셋형 결정 2026-06-18).

실제 erp DB(트랜잭션-롤백). 모든 단언은 시드한 employee_id 로 스코프. 핵심 불변식 = 이월 후
`category_balance(연차) == 0` → 이듬해 발생이 작년분과 **중복 집계 안 됨**(P3 가 노출한 중복 해소).
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.employee import Employee
from app.models.enums import GrantSource, GrantStatus, LeaveCategory
from app.models.leave_adjustment import LeaveAdjustment
from app.models.leave_grant import LeaveGrant
from app.repositories import leave_grant as grant_repo
from app.services import leave_accrual, leave_balance

FY = 2026
VU = date(FY + 1, 6, 30)


async def _seed_employee(session) -> uuid.UUID:
    eid = uuid.uuid4()
    session.add(
        Employee(id=eid, email=f"{eid.hex[:8]}@x.com", name=f"emp-{eid.hex[:6]}",
                 role="member", active=True)
    )
    await session.flush()
    return eid


async def _annual_lot(session, eid, remaining) -> None:
    await grant_repo.create_lot(
        session, employee_id=eid, category=LeaveCategory.ANNUAL, amount=Decimal(remaining),
        source=GrantSource.ACCRUAL, granted_at=datetime(FY, 1, 1, tzinfo=UTC),
    )


async def _adjust(session, eid, category, delta) -> None:
    session.add(LeaveAdjustment(employee_id=eid, category=category,
                                delta=Decimal(delta), adjusted_by=eid))
    await session.flush()


async def _comp_lots(session, eid) -> list[LeaveGrant]:
    stmt = select(LeaveGrant).where(
        LeaveGrant.employee_id == eid, LeaveGrant.category == LeaveCategory.COMP
    )
    return list((await session.execute(stmt)).scalars().all())


# ---- 리셋 불변식 -----------------------------------------------------------


@pytest.mark.asyncio
async def test_carryover_resets_annual_to_zero(db_session) -> None:
    eid = await _seed_employee(db_session)
    await _annual_lot(db_session, eid, 5)

    await leave_accrual.carryover(db_session, FY, VU)

    comp = await _comp_lots(db_session, eid)
    assert len(comp) == 1 and comp[0].remaining == Decimal("5")  # 이월 보존
    # 핵심: 원 연차 리셋 → 잔여 0
    assert await leave_balance.category_balance(db_session, eid, LeaveCategory.ANNUAL) == Decimal("0")


@pytest.mark.asyncio
async def test_carryover_reset_with_adjustment_delta(db_session) -> None:
    eid = await _seed_employee(db_session)
    await _annual_lot(db_session, eid, 3)
    await _adjust(db_session, eid, LeaveCategory.ANNUAL, "2")  # lot 3 + adj 2 = 5

    await leave_accrual.carryover(db_session, FY, VU)

    comp = await _comp_lots(db_session, eid)
    assert len(comp) == 1 and comp[0].remaining == Decimal("5")  # carried = derive 5
    # delta 섞여도 리셋 후 0 (상쇄 adjustment)
    assert await leave_balance.category_balance(db_session, eid, LeaveCategory.ANNUAL) == Decimal("0")


@pytest.mark.asyncio
async def test_carryover_reset_no_double_count_next_year(db_session) -> None:
    """이번 task 핵심 — P3 가 노출한 회계 갱신 중복이 리셋으로 해소됨을 증명."""
    eid = await _seed_employee(db_session)
    await _annual_lot(db_session, eid, 5)

    await leave_accrual.carryover(db_session, FY, VU)          # 리셋 → 연차 0, 보상 5
    await leave_accrual.accrue_annual(db_session, Decimal("15"), FY + 1)  # 이듬해 발생

    # 작년 5 가 누적되지 않고 base 만
    assert await leave_balance.category_balance(db_session, eid, LeaveCategory.ANNUAL) == Decimal("15")
    # 이월분 보상은 그대로 유지
    assert await leave_balance.category_balance(db_session, eid, LeaveCategory.COMP) == Decimal("5")


# ---- 멱등 · no-op -----------------------------------------------------------


@pytest.mark.asyncio
async def test_carryover_reset_idempotent(db_session) -> None:
    eid = await _seed_employee(db_session)
    await _annual_lot(db_session, eid, 3)
    await _adjust(db_session, eid, LeaveCategory.ANNUAL, "2")

    await leave_accrual.carryover(db_session, FY, VU)
    await leave_accrual.carryover(db_session, FY, VU)  # 재실행

    assert len(await _comp_lots(db_session, eid)) == 1  # 이월 lot 1개분
    assert await leave_balance.category_balance(db_session, eid, LeaveCategory.ANNUAL) == Decimal("0")
    # 상쇄 adjustment 도 1회분만 (재0화/중복 상쇄 없음)
    n_reset_adj = (await db_session.execute(
        select(func.count()).select_from(LeaveAdjustment).where(
            LeaveAdjustment.employee_id == eid,
            LeaveAdjustment.reason == f"회계 이월 리셋 (FY{FY})",
        )
    )).scalar_one()
    assert n_reset_adj == 1


@pytest.mark.asyncio
async def test_carryover_not_positive_noop_no_reset(db_session) -> None:
    eid = await _seed_employee(db_session)
    await _annual_lot(db_session, eid, 2)
    await _adjust(db_session, eid, LeaveCategory.ANNUAL, "-2")  # carried = 0

    await leave_accrual.carryover(db_session, FY, VU)

    assert await _comp_lots(db_session, eid) == []  # no-op — 이월 없음
    # 리셋 안 함 — 원 lot remaining 보존(2)
    lot = (await db_session.execute(
        select(LeaveGrant).where(
            LeaveGrant.employee_id == eid, LeaveGrant.category == LeaveCategory.ANNUAL,
            LeaveGrant.status == GrantStatus.ACTIVE,
        )
    )).scalars().one()
    assert lot.remaining == Decimal("2")