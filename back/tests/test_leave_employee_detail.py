"""HR 상세 연차 현황(임의 직원) 테스트 — SPEC-003 §상세 + §5(상세). WP-005 Phase 3 (BE).

- HR 임의 직원 조회: 직원 식별 + 종류별 잔여 4종 + 전체 + 이력(grant/request/allocation/adjustment).
- 잔여 정합: P1 부여 lot · P2 조정 delta 반영(category_balances 재사용 — 재정의 없음).
- 이력 정합: 부여/신청/사용/조정 이벤트가 ledger union view 에 시계열 노출.
- 권한: 비-HR 403 · HR 200. 미존재 404. 비활성 직원도 조회 가능(이력 열람). 음수 잔여 그대로 노출.

read-only(상태 변경 0). db_session fixture 가 트랜잭션 롤백으로 격리한다(P1/P2 패턴).
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport

from app.core.deps import get_current_employee, get_db, require_hr
from app.core.errors import NotFoundError
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
from app.models.leave_adjustment import LeaveAdjustment
from app.models.leave_allocation import LeaveAllocation
from app.models.leave_request import LeaveRequest
from app.repositories import leave_grant as grant_repo
from app.services import leave_admin

HR = "hr"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_employee(session, *, department: str | None = None, active: bool = True) -> Employee:
    eid = uuid.uuid4()
    emp = Employee(id=eid, email=f"{eid.hex[:8]}@x.com", name=f"emp-{eid.hex[:6]}",
                   role="member", active=active, department=department)
    session.add(emp)
    await session.flush()
    return emp


async def _seed_full_history(session, hr, emp) -> None:
    """부여(P1)·조정(P2)·신청·사용(WP-003) 이벤트를 한 직원에 시드 — ledger 정합 검증용.

    COMP: 부여 lot 3.0 + 조정 +2.0 = 잔여 5.0. 신청 1건 + 그 lot 에서 사용 1.0 allocation.
    """
    lot = await grant_repo.create_lot(
        session, employee_id=emp.id, category=LeaveCategory.COMP,
        amount=Decimal("3.0"), source=GrantSource.HR_GRANT, granted_at=datetime.now(UTC),
        expiry_date=date(2027, 3, 31), granted_by=hr.id, status=GrantStatus.ACTIVE,
    )
    session.add(LeaveAdjustment(
        employee_id=emp.id, category=LeaveCategory.COMP, delta=Decimal("2.0"),
        reason="정정", adjusted_by=hr.id, created_at=datetime.now(UTC),
    ))
    req = LeaveRequest(
        employee_id=emp.id, category=LeaveCategory.COMP, unit=LeaveUnit.FULL,
        amount=Decimal("1.0"), use_date=date(2026, 7, 1), status=RequestStatus.APPROVED,
        channel=RequestChannel.ERP, approved_by=hr.id, approved_at=datetime.now(UTC),
    )
    session.add(req)
    await session.flush()
    session.add(LeaveAllocation(
        request_id=req.id, grant_id=lot.id, amount=Decimal("1.0"),
        created_at=datetime.now(UTC),
    ))
    await session.flush()


# ---- HR 임의 직원 조회 (service) ------------------------------------------


@pytest.mark.asyncio
async def test_detail_returns_identity_balances_total_ledger(db_session) -> None:
    """HR 임의 직원 조회 — 식별 + 종류별 잔여 4종 + 전체 + 이력. 잔여 = P1 부여 + P2 조정 정합."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    await _seed_full_history(db_session, hr, emp)

    found, balances, total, ledger = await leave_admin.employee_detail(db_session, emp.id)

    assert found.id == emp.id
    # 4 종류 모두 키 존재(독립)
    assert set(balances.keys()) == set(LeaveCategory)
    # COMP = 부여 3.0 + 조정 2.0 (allocation 사용 1.0 은 lot.remaining 차감분 — 여기선 미차감 시드)
    assert balances[LeaveCategory.COMP] == Decimal("5.0")
    assert total == sum(balances.values(), Decimal(0))
    assert len(ledger) >= 4  # 부여·조정·신청·사용


@pytest.mark.asyncio
async def test_detail_ledger_has_all_event_types(db_session) -> None:
    """이력 정합 — 부여(HR부여)·조정·신청·사용 entry_type 이 ledger 에 시계열 노출."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    await _seed_full_history(db_session, hr, emp)

    _, _, _, ledger = await leave_admin.employee_detail(db_session, emp.id)
    types = {e["entry_type"] for e in ledger}
    assert {"HR부여", "조정", "신청", "사용"} <= types
    # occurred_at ASC 정렬
    occ = [e["occurred_at"] for e in ledger]
    assert occ == sorted(occ)


@pytest.mark.asyncio
async def test_detail_negative_balance_exposed(db_session) -> None:
    """음수 잔여 노출 — 조정으로 음수가 된 잔여도 그대로 반환(차단 X)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    db_session.add(LeaveAdjustment(
        employee_id=emp.id, category=LeaveCategory.COMP, delta=Decimal("-3.0"),
        reason="과차감 정정", adjusted_by=hr.id, created_at=datetime.now(UTC),
    ))
    await db_session.flush()

    _, balances, _, _ = await leave_admin.employee_detail(db_session, emp.id)
    assert balances[LeaveCategory.COMP] == Decimal("-3.0")


@pytest.mark.asyncio
async def test_detail_missing_employee_404(db_session) -> None:
    """미존재 직원 → 404."""
    ghost = uuid.uuid4()
    with pytest.raises(NotFoundError):
        await leave_admin.employee_detail(db_session, ghost)


@pytest.mark.asyncio
async def test_detail_inactive_employee_viewable(db_session) -> None:
    """비활성(퇴사) 직원도 조회 가능 — 이력 열람 목적(404/차단 아님)."""
    inactive = await _seed_employee(db_session, active=False)
    found, balances, _, _ = await leave_admin.employee_detail(db_session, inactive.id)
    assert found.id == inactive.id
    assert set(balances.keys()) == set(LeaveCategory)


# ---- 권한 / endpoint -------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_non_hr_403(db_session) -> None:
    """비-HR(임의 직원 조회 시도) → 403."""
    member = await _seed_employee(db_session, department="개발")
    target = await _seed_employee(db_session)
    app.dependency_overrides[get_current_employee] = lambda: member
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.get(f"/leave/admin/employees/{target.id}",
                               headers={"Authorization": "Bearer t"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_detail_hr_endpoint_200(db_session) -> None:
    """HR endpoint happy — 응답 JSON 형태(employee/balances/total/ledger) = FE T-023 계약."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    await _seed_full_history(db_session, hr, emp)
    app.dependency_overrides[require_hr] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.get(f"/leave/admin/employees/{emp.id}",
                               headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["employee"]["id"] == str(emp.id)
        assert body["employee"]["name"] == emp.name
        assert "email" in body["employee"] and "department" in body["employee"]
        # 종류별 잔여 한글 key (Numeric 직렬화 string)
        assert body["balances"]["보상"] == "5.00"
        assert set(body["balances"].keys()) == {"연차", "Off Day", "보상", "포상"}
        assert "total" in body
        # 이력 entry 형태
        kinds = {e["entry_type"] for e in body["ledger"]}
        assert {"HR부여", "조정", "신청", "사용"} <= kinds
        first = body["ledger"][0]
        assert {"entry_type", "occurred_at", "category", "amount", "detail", "ref_id"} <= set(first)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_detail_endpoint_missing_404(db_session) -> None:
    """HR endpoint — 미존재 직원 404."""
    hr = await _seed_employee(db_session, department=HR)
    app.dependency_overrides[require_hr] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.get(f"/leave/admin/employees/{uuid.uuid4()}",
                               headers={"Authorization": "Bearer t"})
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()
