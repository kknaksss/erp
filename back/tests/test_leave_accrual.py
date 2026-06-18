"""연차 발생·이월 service 검증 — WP-002 Phase 2 (work-002 §검증 + Pre-deploy Check 멱등).

실제 erp DB(트랜잭션-롤백). DB 에 기존 active 직원이 있을 수 있으므로 **모든 단언은 시드한
employee_id 로 스코프**한다(test_roster 패턴). 검증: 발생 base lot(무만료·시스템)·신규 전액·
이월(보상+이월, >0)·≤0 no-lot·active 필터(비활성 skip)·조정 delta 가산·멱등 2회 호출 중복 0.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.employee import Employee
from app.models.enums import GrantSource, GrantStatus, LeaveCategory
from app.models.leave_adjustment import LeaveAdjustment
from app.models.leave_grant import LeaveGrant
from app.repositories import leave_grant as grant_repo
from app.services import leave_accrual

FY = 2026
BASE = Decimal("15")


async def _seed_employee(session, *, active: bool = True) -> uuid.UUID:
    eid = uuid.uuid4()
    session.add(
        Employee(id=eid, email=f"{eid.hex[:8]}@x.com", name=f"emp-{eid.hex[:6]}",
                 role="member", active=active)
    )
    await session.flush()
    return eid


async def _annual_lot(session, employee_id: uuid.UUID, remaining: Decimal) -> None:
    """시드용 `연차` active lot(발생 source). granted_at = FY 1/1."""
    await grant_repo.create_lot(
        session, employee_id=employee_id, category=LeaveCategory.ANNUAL,
        amount=remaining, source=GrantSource.ACCRUAL,
        granted_at=datetime(FY, 1, 1, tzinfo=UTC),
    )


async def _lots(session, employee_id: uuid.UUID, **eq) -> list[LeaveGrant]:
    stmt = select(LeaveGrant).where(LeaveGrant.employee_id == employee_id)
    for col, val in eq.items():
        stmt = stmt.where(getattr(LeaveGrant, col) == val)
    return list((await session.execute(stmt)).scalars().all())


# ---- 발생 (accrue_annual) --------------------------------------------------


@pytest.mark.asyncio
async def test_accrue_annual_creates_base_lot_for_active(db_session) -> None:
    e1, e2 = await _seed_employee(db_session), await _seed_employee(db_session)
    inactive = await _seed_employee(db_session, active=False)

    await leave_accrual.accrue_annual(db_session, BASE, FY)

    for eid in (e1, e2):
        lots = await _lots(db_session, eid)
        assert len(lots) == 1
        lot = lots[0]
        assert lot.category == LeaveCategory.ANNUAL
        assert lot.source == GrantSource.ACCRUAL
        assert lot.amount == BASE and lot.remaining == BASE  # 잔여 = base
        assert lot.expiry_date is None  # 연차 = 무만료
        assert lot.granted_by is None  # 시스템 부여
        assert lot.status == GrantStatus.ACTIVE
        assert lot.granted_at.astimezone(UTC).year == FY
    # 비활성 직원은 발생 대상 아님 (active 필터)
    assert await _lots(db_session, inactive) == []


@pytest.mark.asyncio
async def test_accrue_annual_idempotent(db_session) -> None:
    e1 = await _seed_employee(db_session)
    await leave_accrual.accrue_annual(db_session, BASE, FY)
    await leave_accrual.accrue_annual(db_session, BASE, FY)  # 재실행
    lots = await _lots(db_session, e1, source=GrantSource.ACCRUAL)
    assert len(lots) == 1  # 중복 생성 0 (Pre-deploy Check)


# ---- 신규 입사 전액 (grant_new_hire) ---------------------------------------


@pytest.mark.asyncio
async def test_grant_new_hire_full_not_prorated(db_session) -> None:
    eid = await _seed_employee(db_session)
    created = await leave_accrual.grant_new_hire(db_session, eid, BASE)
    assert created is True
    lots = await _lots(db_session, eid)
    assert len(lots) == 1
    lot = lots[0]
    assert lot.amount == BASE and lot.remaining == BASE  # 전액(비례 아님)
    assert lot.category == LeaveCategory.ANNUAL and lot.source == GrantSource.ACCRUAL
    assert lot.expiry_date is None and lot.granted_by is None  # 무만료·시스템


@pytest.mark.asyncio
async def test_grant_new_hire_idempotent_same_year(db_session) -> None:
    eid = await _seed_employee(db_session)
    assert await leave_accrual.grant_new_hire(db_session, eid, BASE) is True
    assert await leave_accrual.grant_new_hire(db_session, eid, BASE) is False  # 같은 해 중복 차단
    assert len(await _lots(db_session, eid)) == 1


# ---- 이월 (carryover) ------------------------------------------------------


@pytest.mark.asyncio
async def test_carryover_creates_comp_lot_when_remaining_positive(db_session) -> None:
    eid = await _seed_employee(db_session)
    await _annual_lot(db_session, eid, Decimal("5"))
    valid_until = date(FY + 1, 6, 30)

    await leave_accrual.carryover(db_session, FY, valid_until)

    comp = await _lots(db_session, eid, category=LeaveCategory.COMP)
    assert len(comp) == 1
    lot = comp[0]
    assert lot.source == GrantSource.CARRYOVER  # 보상 + 이월
    assert lot.amount == Decimal("5") and lot.remaining == Decimal("5")
    assert lot.expiry_date == valid_until  # 파라미터 유효기간
    assert lot.granted_by is None  # 시스템


@pytest.mark.asyncio
async def test_carryover_includes_adjustment_delta(db_session) -> None:
    eid = await _seed_employee(db_session)
    await _annual_lot(db_session, eid, Decimal("3"))
    db_session.add(LeaveAdjustment(employee_id=eid, category=LeaveCategory.ANNUAL,
                                   delta=Decimal("2"), adjusted_by=eid))
    await db_session.flush()

    await leave_accrual.carryover(db_session, FY, date(FY + 1, 6, 30))

    comp = await _lots(db_session, eid, category=LeaveCategory.COMP)
    assert len(comp) == 1 and comp[0].amount == Decimal("5")  # 3(lot) + 2(delta)


@pytest.mark.asyncio
async def test_carryover_no_lot_when_remaining_not_positive(db_session) -> None:
    eid = await _seed_employee(db_session)
    await _annual_lot(db_session, eid, Decimal("2"))
    db_session.add(LeaveAdjustment(employee_id=eid, category=LeaveCategory.ANNUAL,
                                   delta=Decimal("-2"), adjusted_by=eid))  # 남은연차 0
    await db_session.flush()

    await leave_accrual.carryover(db_session, FY, date(FY + 1, 6, 30))
    assert await _lots(db_session, eid, category=LeaveCategory.COMP) == []
    # 재실행 — zero-remaining 은 이월 lot 이 없어 재평가되지만 여전히 lot 0
    await leave_accrual.carryover(db_session, FY, date(FY + 1, 6, 30))
    assert await _lots(db_session, eid, category=LeaveCategory.COMP) == []


@pytest.mark.asyncio
async def test_carryover_idempotent(db_session) -> None:
    eid = await _seed_employee(db_session)
    await _annual_lot(db_session, eid, Decimal("4"))
    vu = date(FY + 1, 6, 30)
    await leave_accrual.carryover(db_session, FY, vu)
    await leave_accrual.carryover(db_session, FY, vu)  # 재실행
    comp = await _lots(db_session, eid, category=LeaveCategory.COMP)
    assert len(comp) == 1  # 중복 이월 0 (Pre-deploy Check)
