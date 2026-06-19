"""연차 대장 시드 스크립트 테스트 — PLAN-002-T-016.

실제 PII JSON(seeds/leave_ledger_2026.json)은 읽지 않고 인라인 dict 로 검증(트랜잭션-롤백):
- 매칭 직원: hire_date/department 세팅 + grant/request 행수·remaining·category_balance(=remaining 합).
- placeholder: DB 부재 직원(박소은) 신규 생성 + email placeholder + active.
- 멱등: 재실행 시 grant/request skip(중복 0).
- allocation 미생성(historical) 확인.

seed_ledger 는 flush 까지만(commit X) — db_session 롤백으로 격리(실 DB 미오염).
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.employee import Employee
from app.models.enums import EmploymentType, LeaveCategory  # noqa: F401
from app.models.leave_allocation import LeaveAllocation
from app.models.leave_grant import LeaveGrant
from app.models.leave_request import LeaveRequest
from app.repositories import employee as employee_repo
from app.services import leave_balance
from scripts.seed_leave_ledger import (
    SeedStats,
    seed_ledger,
)


async def _seed_existing_employee(session, name: str) -> Employee:
    eid = uuid.uuid4()
    emp = Employee(id=eid, email=f"{eid.hex[:8]}@x.com", name=name,
                   role="member", active=True)
    session.add(emp)
    await session.flush()
    return emp


def _entry(name: str, *, email: str, grants=None, requests=None) -> dict:
    return {
        "name": name, "email": email, "hire_date": "2025-04-07", "department": "hr",
        "grants": grants if grants is not None else [
            {"category": "연차", "amount": 15.0, "remaining": 13.0,
             "expiry_date": None, "source": "발생", "reason": None},
            {"category": "Off Day", "amount": 6.0, "remaining": 3.0,
             "expiry_date": "2026-12-31", "source": "HR부여", "reason": "매월"},
        ],
        "requests": requests if requests is not None else [
            {"use_date": "2026-03-10", "category": "연차", "unit": "전일",
             "am_pm": None, "amount": 1.0, "note": None,
             "status": "승인됨", "channel": "slack"},
            {"use_date": "2026-03-11", "category": "연차", "unit": "반차",
             "am_pm": "오전", "amount": 0.5, "note": "오전반차",
             "status": "승인됨", "channel": "slack"},
        ],
    }


async def _counts(session, employee_id) -> tuple[int, int, int]:
    g = (await session.execute(select(func.count()).select_from(LeaveGrant).where(
        LeaveGrant.employee_id == employee_id))).scalar_one()
    r = (await session.execute(select(func.count()).select_from(LeaveRequest).where(
        LeaveRequest.employee_id == employee_id))).scalar_one()
    a = (await session.execute(select(func.count()).select_from(LeaveAllocation).join(
        LeaveRequest, LeaveAllocation.request_id == LeaveRequest.id).where(
        LeaveRequest.employee_id == employee_id))).scalar_one()
    return g, r, a


# ---- 매칭 직원 시드 -------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_matched_employee(db_session) -> None:
    name = f"테스트김-{uuid.uuid4().hex[:6]}"
    emp = await _seed_existing_employee(db_session, name)

    stats = await seed_ledger(db_session, [_entry(name, email=emp.email)])

    assert stats.matched == 1 and stats.created == 0
    assert stats.grants == 2 and stats.requests == 2
    # employee HR 필드 세팅(email/role 보존)
    refreshed = await employee_repo.get_by_id(db_session, emp.id)
    assert refreshed.hire_date == date(2025, 4, 7) and refreshed.department == "hr"
    assert refreshed.role == "member"  # 기존 보존
    g, r, a = await _counts(db_session, emp.id)
    assert (g, r, a) == (2, 2, 0)      # allocation 미생성(historical)


@pytest.mark.asyncio
async def test_seed_remaining_inserted_as_is(db_session) -> None:
    """remaining = JSON 값 그대로(FEFO 재계산 X) → category_balance 가 대장 잔여 재현."""
    name = f"잔여검산-{uuid.uuid4().hex[:6]}"
    emp = await _seed_existing_employee(db_session, name)

    await seed_ledger(db_session, [_entry(name, email=emp.email)])

    # 연차 remaining 13 / Off Day remaining 3 (request 가 있어도 remaining 은 박힌 값 그대로)
    assert await leave_balance.category_balance(db_session, emp.id, LeaveCategory.ANNUAL) == Decimal("13.00")
    assert await leave_balance.category_balance(db_session, emp.id, LeaveCategory.OFF_DAY) == Decimal("3.00")


# ---- placeholder(DB 부재 직원) -------------------------------------------


@pytest.mark.asyncio
async def test_seed_placeholder_creates_employee(db_session) -> None:
    # DB 에 없는 email → placeholder 신규 생성(email = entry["email"] 그대로).
    # 유니크 email 로 커밋된 시드(박소은 placeholder)와 격리.
    name = f"부재직원-{uuid.uuid4().hex[:6]}"
    email = f"absent-{uuid.uuid4().hex[:8]}@x.com"
    stats = await seed_ledger(db_session, [_entry(name, email=email)])

    assert stats.created == 1 and stats.matched == 0
    assert any(email in line for line in stats.placeholder_emails)
    created = (await db_session.execute(
        select(Employee).where(Employee.email == email))).scalars().first()
    assert created is not None
    assert created.name == name and created.email == email
    assert created.active is True and created.department == "hr"
    assert created.hire_date == date(2025, 4, 7)


# ---- 멱등 -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_idempotent_skip(db_session) -> None:
    name = f"멱등-{uuid.uuid4().hex[:6]}"
    emp = await _seed_existing_employee(db_session, name)

    await seed_ledger(db_session, [_entry(name, email=emp.email)])
    g1, r1, _a = await _counts(db_session, emp.id)

    # 재실행 — 이미 grant 있음 → skip(중복 0)
    stats2 = await seed_ledger(db_session, [_entry(name, email=emp.email)], SeedStats())
    g2, r2, _a2 = await _counts(db_session, emp.id)

    assert stats2.skipped == 1 and stats2.grants == 0 and stats2.requests == 0
    assert (g2, r2) == (g1, r1)  # 행수 불변
