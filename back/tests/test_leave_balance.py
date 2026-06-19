"""종류별 잔여 derive + valid/FEFO 후보 + 만료 처리 + ledger 검증 — WP-002 Phase 3 (read-path).

실제 erp DB(트랜잭션-롤백). DB 에 기존 직원/데이터가 있을 수 있으므로 **모든 단언은 시드한
employee_id 로 스코프**(test_roster/test_leave_accrual 패턴). 검증: 4 종류 잔여=lot remaining 합
± category delta(음수 허용·교환 불가)·전체=합산 / 만료 lot 합산 제외(멱등·연차 무만료) /
valid·FEFO 후보(use_date<=expiry·expiry ASC·NULL 최후미·remaining>0) / ledger 4 테이블 union.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.employee import Employee
from app.models.enums import (
    GrantSource,
    GrantStatus,
    LeaveCategory,
    LeaveUnit,
    RequestChannel,
    RequestStatus,
)
from app.models.leave_adjustment import LeaveAdjustment
from app.models.leave_allocation import LeaveAllocation
from app.models.leave_grant import LeaveGrant
from app.models.leave_request import LeaveRequest
from app.repositories import leave_grant as grant_repo
from app.services import leave_balance

FY = 2026


async def _seed_employee(session) -> uuid.UUID:
    eid = uuid.uuid4()
    session.add(
        Employee(id=eid, email=f"{eid.hex[:8]}@x.com", name=f"emp-{eid.hex[:6]}",
                 role="member", active=True)
    )
    await session.flush()
    return eid


async def _lot(
    session, eid, category, remaining, *, expiry=None, status=GrantStatus.ACTIVE,
    source=GrantSource.HR_GRANT, granted_at=None,
) -> LeaveGrant:
    return await grant_repo.create_lot(
        session, employee_id=eid, category=category, amount=remaining, remaining=remaining,
        source=source, expiry_date=expiry, status=status,
        granted_at=granted_at or datetime(FY, 1, 1, tzinfo=UTC),
    )


async def _adjust(session, eid, category, delta) -> None:
    session.add(LeaveAdjustment(employee_id=eid, category=category,
                                delta=Decimal(delta), adjusted_by=eid))
    await session.flush()


# ---- 종류별 잔여 derive (lot 합 ± delta · 음수 · 교환불가 · 전체) ----------------


@pytest.mark.asyncio
async def test_category_balance_lot_sum_plus_adjustment(db_session) -> None:
    eid = await _seed_employee(db_session)
    await _lot(db_session, eid, LeaveCategory.ANNUAL, Decimal("10"), source=GrantSource.ACCRUAL)
    await _lot(db_session, eid, LeaveCategory.ANNUAL, Decimal("3"), source=GrantSource.ACCRUAL)
    await _adjust(db_session, eid, LeaveCategory.ANNUAL, "2")  # +2

    bal = await leave_balance.category_balance(db_session, eid, LeaveCategory.ANNUAL)
    assert bal == Decimal("15")  # 10 + 3 + 2


@pytest.mark.asyncio
async def test_category_balance_negative_allowed(db_session) -> None:
    eid = await _seed_employee(db_session)
    await _lot(db_session, eid, LeaveCategory.ANNUAL, Decimal("1"), source=GrantSource.ACCRUAL)
    await _adjust(db_session, eid, LeaveCategory.ANNUAL, "-3")  # 음수 허용

    bal = await leave_balance.category_balance(db_session, eid, LeaveCategory.ANNUAL)
    assert bal == Decimal("-2")  # 하드 차단 없음


@pytest.mark.asyncio
async def test_balances_independent_and_total(db_session) -> None:
    eid = await _seed_employee(db_session)
    await _lot(db_session, eid, LeaveCategory.ANNUAL, Decimal("12"), source=GrantSource.ACCRUAL)
    await _lot(db_session, eid, LeaveCategory.COMP, Decimal("2"), expiry=date(FY + 1, 6, 30))
    await _lot(db_session, eid, LeaveCategory.REWARD, Decimal("1"), expiry=date(FY, 12, 31))
    await _lot(db_session, eid, LeaveCategory.OFF_DAY, Decimal("0.5"), expiry=date(FY, 7, 31))

    balances = await leave_balance.category_balances(db_session, eid)
    assert balances[LeaveCategory.ANNUAL] == Decimal("12")
    assert balances[LeaveCategory.COMP] == Decimal("2")
    assert balances[LeaveCategory.REWARD] == Decimal("1")
    assert balances[LeaveCategory.OFF_DAY] == Decimal("0.5")
    # 교환 불가 — Off Day 조정이 연차로 새지 않는다
    await _adjust(db_session, eid, LeaveCategory.OFF_DAY, "0.5")
    assert await leave_balance.category_balance(db_session, eid, LeaveCategory.ANNUAL) == Decimal("12")
    # 전체 = 4 합산 표시값
    assert await leave_balance.total_balance(db_session, eid) == Decimal("16")  # 12+2+1+1.0


# ---- 만료 처리 (expiry 경과 → expired · 합산 제외 · 멱등 · 연차 무만료) ----------


async def _lot_status(session, gid) -> GrantStatus:
    return (await session.execute(
        select(LeaveGrant).where(LeaveGrant.id == gid))).scalar_one().status


@pytest.mark.asyncio
async def test_expired_lot_excluded_from_balance(db_session) -> None:
    eid = await _seed_employee(db_session)
    lapsed = await _lot(db_session, eid, LeaveCategory.COMP, Decimal("3"), expiry=date(FY, 1, 31))  # 경과
    valid = await _lot(db_session, eid, LeaveCategory.COMP, Decimal("2"), expiry=date(FY + 1, 6, 30))  # 유효
    today = date(FY, 6, 1)

    assert await leave_balance.category_balance(db_session, eid, LeaveCategory.COMP) == Decimal("5")
    await leave_balance.expire_lapsed_lots(db_session, today)
    # 경과분만 expired 전환(본인 lot 기준 — 전역 카운트는 사전 시드 데이터에 의존하므로 검사 X)
    assert await _lot_status(db_session, lapsed.id) == GrantStatus.EXPIRED
    assert await _lot_status(db_session, valid.id) == GrantStatus.ACTIVE
    assert await leave_balance.category_balance(db_session, eid, LeaveCategory.COMP) == Decimal("2")
    # 멱등 — 재실행해도 본인 유효 lot 은 active 유지
    await leave_balance.expire_lapsed_lots(db_session, today)
    assert await _lot_status(db_session, valid.id) == GrantStatus.ACTIVE


@pytest.mark.asyncio
async def test_annual_never_expires(db_session) -> None:
    eid = await _seed_employee(db_session)
    await _lot(db_session, eid, LeaveCategory.ANNUAL, Decimal("5"), source=GrantSource.ACCRUAL)  # expiry NULL

    await leave_balance.expire_lapsed_lots(db_session, date(FY + 5, 1, 1))
    lots = (await db_session.execute(
        select(LeaveGrant).where(LeaveGrant.employee_id == eid)
    )).scalars().all()
    assert all(lot.status == GrantStatus.ACTIVE for lot in lots)  # 무만료 = 항상 active


@pytest.mark.asyncio
async def test_expiry_boundary_day_not_lapsed(db_session) -> None:
    eid = await _seed_employee(db_session)
    lot = await _lot(db_session, eid, LeaveCategory.COMP, Decimal("2"), expiry=date(FY, 6, 30))

    # 만료일 당일은 아직 소비 가능(use_date<=expiry) → 경과 아님(본인 lot 기준)
    await leave_balance.expire_lapsed_lots(db_session, date(FY, 6, 30))
    assert await _lot_status(db_session, lot.id) == GrantStatus.ACTIVE
    # 다음날부터 경과
    await leave_balance.expire_lapsed_lots(db_session, date(FY, 7, 1))
    assert await _lot_status(db_session, lot.id) == GrantStatus.EXPIRED


# ---- valid lot / FEFO 후보 (use_date<=expiry · expiry ASC · NULL 최후미) --------


@pytest.mark.asyncio
async def test_fefo_order_expiry_asc_null_last(db_session) -> None:
    eid = await _seed_employee(db_session)
    late = await _lot(db_session, eid, LeaveCategory.COMP, Decimal("1"), expiry=date(FY + 1, 6, 30))
    early = await _lot(db_session, eid, LeaveCategory.COMP, Decimal("1"), expiry=date(FY, 9, 30))
    no_exp = await _lot(db_session, eid, LeaveCategory.COMP, Decimal("1"), expiry=None)  # 무만료 → 최후미

    got = await leave_balance.fefo_candidates(db_session, eid, LeaveCategory.COMP, date(FY, 6, 1))
    assert [c.id for c in got] == [early.id, late.id, no_exp.id]  # 임박 ASC, NULL 최후미


@pytest.mark.asyncio
async def test_fefo_excludes_use_date_after_expiry(db_session) -> None:
    eid = await _seed_employee(db_session)
    expired_for_use = await _lot(db_session, eid, LeaveCategory.COMP, Decimal("1"), expiry=date(FY, 3, 31))
    valid = await _lot(db_session, eid, LeaveCategory.COMP, Decimal("1"), expiry=date(FY, 12, 31))

    # use_date 4/1 > 3/31 → 그 lot 제외, 12/31 lot 만
    got = await leave_balance.fefo_candidates(db_session, eid, LeaveCategory.COMP, date(FY, 4, 1))
    assert [c.id for c in got] == [valid.id]
    assert expired_for_use.id not in [c.id for c in got]


@pytest.mark.asyncio
async def test_fefo_excludes_zero_remaining_and_other_category(db_session) -> None:
    eid = await _seed_employee(db_session)
    await _lot(db_session, eid, LeaveCategory.COMP, Decimal("0"), expiry=date(FY, 12, 31))  # 소진
    other = await _lot(db_session, eid, LeaveCategory.REWARD, Decimal("1"), expiry=date(FY, 12, 31))
    keep = await _lot(db_session, eid, LeaveCategory.COMP, Decimal("1"), expiry=date(FY, 12, 31))

    got = await leave_balance.fefo_candidates(db_session, eid, LeaveCategory.COMP, date(FY, 6, 1))
    ids = [c.id for c in got]
    assert ids == [keep.id]  # remaining>0 · category 가로지르지 않음
    assert other.id not in ids


# ---- ledger = 4 테이블 union derived view -----------------------------------


@pytest.mark.asyncio
async def test_ledger_union_four_tables(db_session) -> None:
    eid = await _seed_employee(db_session)
    # 발생(grant) — 이른 시각으로 첫 entry 보장
    grant = await _lot(db_session, eid, LeaveCategory.ANNUAL, Decimal("10"),
                       source=GrantSource.ACCRUAL, granted_at=datetime(2020, 1, 1, tzinfo=UTC))
    # 신청(request)
    req = LeaveRequest(employee_id=eid, category=LeaveCategory.ANNUAL, unit=LeaveUnit.FULL,
                       amount=Decimal("1"), use_date=date(FY, 5, 1),
                       status=RequestStatus.APPROVED, channel=RequestChannel.ERP)
    db_session.add(req)
    await db_session.flush()
    # 사용(allocation) — 승인 신청이 lot 에서 차감한 기록
    db_session.add(LeaveAllocation(request_id=req.id, grant_id=grant.id, amount=Decimal("1")))
    # 조정(adjustment)
    db_session.add(LeaveAdjustment(employee_id=eid, category=LeaveCategory.ANNUAL,
                                   delta=Decimal("1"), adjusted_by=eid))
    await db_session.flush()

    entries = await leave_balance.ledger(db_session, eid)
    types = {e["entry_type"] for e in entries}
    assert {"발생", "신청", "사용", "조정"} <= types  # 4 테이블 union entry
    assert len(entries) == 4
    assert entries[0]["entry_type"] == "발생"  # occurred_at ASC — 2020 grant 최선두
    # ref_id·category·amount 도출 확인(사용 entry 는 grant.category 조인)
    use = next(e for e in entries if e["entry_type"] == "사용")
    assert use["category"] == "연차" and use["amount"] == Decimal("1")
