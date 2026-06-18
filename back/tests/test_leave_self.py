"""본인 연차 조회 테스트 — SPEC-003 §API(본인 조회) + SPEC-004 §본인 조회. WP-003 Phase 1.

- overview(service·실제 DB·롤백): 4종류+전체 잔여·보상/포상 만료 안내·본인 이력, **본인 스코프**
  (타인 기록 비노출 — employee_id 로만 조회).
- GET /leave/me 엔드포인트: 인증 본인 응답 형태(JSON 키) + 타인 sub 로는 본인 것만.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport

from app.core.deps import get_current_employee, get_db
from app.main import app
from app.models.employee import Employee
from app.models.enums import (
    GrantSource,
    GrantStatus,
    LeaveCategory,
    LeaveUnit,
    RequestChannel,
    RequestStatus,
)
from app.models.leave_grant import LeaveGrant
from app.models.leave_request import LeaveRequest
from app.repositories import leave_grant as grant_repo
from app.services import leave_self

FY = 2026


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_employee(session) -> Employee:
    eid = uuid.uuid4()
    emp = Employee(id=eid, email=f"{eid.hex[:8]}@x.com", name=f"emp-{eid.hex[:6]}",
                   role="member", active=True)
    session.add(emp)
    await session.flush()
    return emp


async def _lot(session, eid, category, remaining, *, expiry=None,
               source=GrantSource.HR_GRANT) -> LeaveGrant:
    return await grant_repo.create_lot(
        session, employee_id=eid, category=category, amount=remaining, remaining=remaining,
        source=source, expiry_date=expiry, status=GrantStatus.ACTIVE,
        granted_at=datetime(FY, 1, 1, tzinfo=UTC),
    )


async def _request(session, eid, *, category=LeaveCategory.ANNUAL, use_date=date(FY, 5, 1)) -> None:
    session.add(LeaveRequest(employee_id=eid, category=category, unit=LeaveUnit.FULL,
                             amount=Decimal("1"), use_date=use_date,
                             status=RequestStatus.REQUESTED, channel=RequestChannel.ERP))
    await session.flush()


# ---- overview (service · 본인 스코프) -------------------------------------


@pytest.mark.asyncio
async def test_overview_balances_total_expiring_history(db_session) -> None:
    emp = await _seed_employee(db_session)
    await _lot(db_session, emp.id, LeaveCategory.ANNUAL, Decimal("12"), source=GrantSource.ACCRUAL)
    await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("2"), expiry=date(FY + 1, 6, 30))
    await _lot(db_session, emp.id, LeaveCategory.REWARD, Decimal("1"), expiry=date(FY, 12, 31))
    await _lot(db_session, emp.id, LeaveCategory.OFF_DAY, Decimal("0.5"), expiry=date(FY, 7, 31))
    await _request(db_session, emp.id)

    balances, total, expiring, history = await leave_self.overview(db_session, emp.id)
    # 4 종류 잔여 + 전체
    assert balances[LeaveCategory.ANNUAL] == Decimal("12")
    assert balances[LeaveCategory.COMP] == Decimal("2")
    assert balances[LeaveCategory.REWARD] == Decimal("1")
    assert balances[LeaveCategory.OFF_DAY] == Decimal("0.5")
    assert total == Decimal("15.5")
    # 만료 안내 = 보상/포상 유효기간 lot (Off Day·무만료 연차 제외), 임박 ASC
    assert [(g.category, g.expiry_date) for g in expiring] == [
        (LeaveCategory.REWARD, date(FY, 12, 31)),
        (LeaveCategory.COMP, date(FY + 1, 6, 30)),
    ]
    # 본인 이력
    assert len(history) == 1 and history[0].employee_id == emp.id


@pytest.mark.asyncio
async def test_overview_scopes_to_self(db_session) -> None:
    me = await _seed_employee(db_session)
    other = await _seed_employee(db_session)
    await _lot(db_session, me.id, LeaveCategory.ANNUAL, Decimal("5"), source=GrantSource.ACCRUAL)
    await _request(db_session, me.id)
    # 타인 데이터
    await _lot(db_session, other.id, LeaveCategory.ANNUAL, Decimal("99"), source=GrantSource.ACCRUAL)
    await _request(db_session, other.id, use_date=date(FY, 9, 9))

    balances, total, _expiring, history = await leave_self.overview(db_session, me.id)
    assert balances[LeaveCategory.ANNUAL] == Decimal("5")  # 타인 99 안 섞임
    assert total == Decimal("5")
    assert all(h.employee_id == me.id for h in history)     # 타인 이력 비노출
    assert len(history) == 1


# ---- GET /leave/me 엔드포인트 ---------------------------------------------


@pytest.mark.asyncio
async def test_me_endpoint_shape(db_session) -> None:
    emp = await _seed_employee(db_session)
    await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("2"), expiry=date(FY, 12, 31))
    await _request(db_session, emp.id)

    app.dependency_overrides[get_current_employee] = lambda: emp
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.get("/leave/me", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"balances", "total", "expiring", "history"}
        # 4 종류 키(한글 enum value)
        assert set(body["balances"]) == {"연차", "보상", "포상", "Off Day"}
        assert body["expiring"][0]["category"] == "보상"
        assert set(body["expiring"][0]) == {"category", "remaining", "expiry_date"}
        assert body["history"][0]["status"] == "신청됨"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_me_endpoint_no_token_401() -> None:
    async with _client() as c:
        resp = await c.get("/leave/me")
    assert resp.status_code == 401
