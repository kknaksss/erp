"""내정보(self profile) + require_hr "hr" 코드값 테스트. PLAN-002-T-014.

- GET /me: 로그인 본인 {id,email,name,role,department,is_hr}. is_hr = department=="hr".
- 핵심: member-role + department="hr" 직원도 /me 200 + is_hr=true(admin 아님에도 self 조회).
- require_hr 게이트: department="hr" 통과 / 구값 "인사"·그 외 403(코드값 영문화 회귀 가드).

get_current_employee override 로 인증 주체 주입(test_leave_approval/intake 와 동일 패턴).
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
)
from app.repositories import leave_grant as grant_repo
from app.repositories import leave_request as request_repo


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_employee(session, *, role="member", department=None) -> Employee:
    eid = uuid.uuid4()
    emp = Employee(id=eid, email=f"{eid.hex[:8]}@x.com", name=f"emp-{eid.hex[:6]}",
                   role=role, active=True, department=department)
    session.add(emp)
    await session.flush()
    return emp


# ---- GET /me -------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_shape_and_is_hr_true(db_session) -> None:
    emp = await _seed_employee(db_session, role="member", department="hr")
    app.dependency_overrides[get_current_employee] = lambda: emp
    try:
        async with _client() as c:
            resp = await c.get("/me", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"id", "email", "name", "role", "department", "is_hr"}
        assert body["role"] == "member" and body["department"] == "hr"
        assert body["is_hr"] is True   # member-role 이라도 HR 부서면 true (핵심)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_me_is_hr_false_other_dept(db_session) -> None:
    emp = await _seed_employee(db_session, role="member", department="개발")
    app.dependency_overrides[get_current_employee] = lambda: emp
    try:
        async with _client() as c:
            resp = await c.get("/me", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200
        assert resp.json()["is_hr"] is False
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_me_is_hr_false_none_dept(db_session) -> None:
    emp = await _seed_employee(db_session, role="admin", department=None)
    app.dependency_overrides[get_current_employee] = lambda: emp
    try:
        async with _client() as c:
            resp = await c.get("/me", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_hr"] is False and body["department"] is None and body["role"] == "admin"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_me_no_token_401() -> None:
    async with _client() as c:
        resp = await c.get("/me")
    assert resp.status_code == 401


# ---- require_hr "hr" 코드값 게이트 (회귀 가드) ----------------------------


async def _pending_request(session, eid):
    await grant_repo.create_lot(
        session, employee_id=eid, category=LeaveCategory.COMP, amount=Decimal("2.0"),
        remaining=Decimal("2.0"), source=GrantSource.HR_GRANT, expiry_date=date(2026, 12, 31),
        status=GrantStatus.ACTIVE, granted_at=datetime(2026, 1, 1, tzinfo=UTC))
    return await request_repo.create(
        session, employee_id=eid, category=LeaveCategory.COMP, unit=LeaveUnit.FULL,
        amount=Decimal("1.0"), am_pm=None, use_date=date(2026, 5, 1), note="n",
        channel=RequestChannel.ERP)


@pytest.mark.asyncio
async def test_require_hr_passes_hr_code(db_session) -> None:
    hr = await _seed_employee(db_session, department="hr")
    req = await _pending_request(db_session, hr.id)
    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post(f"/leave/admin/requests/{req.id}/approve",
                                headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_require_hr_rejects_old_korean_value(db_session) -> None:
    """구 한글값 "인사" 는 더 이상 HR 아님 → 403(코드값 영문화 회귀 가드)."""
    legacy = await _seed_employee(db_session, department="인사")
    req = await _pending_request(db_session, legacy.id)
    app.dependency_overrides[get_current_employee] = lambda: legacy
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post(f"/leave/admin/requests/{req.id}/approve",
                                headers={"Authorization": "Bearer t"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()
