"""HR 승인/반려 + FEFO 차감 테스트 — SPEC-003 §S-2·§케이스 매트릭스 + domains. WP-003 Phase 2.

- 승인(service·실제 DB·롤백): FEFO(만료 임박 lot 부터)·차감량=종류×단위·분할(합 일치)·음수 흡수+경고.
- 반려: 사유 있으면 `반려됨`·allocation 0 / 사유 누락은 스키마(422)에서 거부.
- 권한(endpoint): 비-HR(department≠인사) 승인/반려/큐 403 · 자기 승인 허용 · 큐 = `신청됨` 만.

approve/reject service 가 commit 하지만 db_session fixture 가 outer 트랜잭션 롤백으로 격리한다
(test_leave_intake 와 동일 패턴 — 실제 DB 미오염).
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import func, select

from app.core.deps import get_current_employee, get_db
from app.core.errors import ConflictError, NotFoundError
from app.main import app
from app.models.employee import Employee
from app.models.enums import (
    AmPm,
    GrantSource,
    GrantStatus,
    LeaveCategory,
    LeaveUnit,
    RequestChannel,
    RequestStatus,
)
from app.models.leave_allocation import LeaveAllocation
from app.models.leave_grant import LeaveGrant
from app.repositories import leave_allocation as allocation_repo
from app.repositories import leave_grant as grant_repo
from app.repositories import leave_request as request_repo
from app.services import leave_approval

FY = 2026
HR = "인사"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_employee(session, *, department: str | None = None) -> Employee:
    eid = uuid.uuid4()
    emp = Employee(id=eid, email=f"{eid.hex[:8]}@x.com", name=f"emp-{eid.hex[:6]}",
                   role="member", active=True, department=department)
    session.add(emp)
    await session.flush()
    return emp


async def _lot(session, eid, category, remaining, *, expiry, granted_month=1) -> LeaveGrant:
    return await grant_repo.create_lot(
        session, employee_id=eid, category=category, amount=remaining, remaining=remaining,
        source=GrantSource.HR_GRANT, expiry_date=expiry, status=GrantStatus.ACTIVE,
        granted_at=datetime(FY, granted_month, 1, tzinfo=UTC),
    )


async def _request(session, eid, *, category=LeaveCategory.COMP, unit=LeaveUnit.FULL,
                   amount=Decimal("1.0"), am_pm=None, use_date=date(FY, 5, 1)):
    return await request_repo.create(
        session, employee_id=eid, category=category, unit=unit, amount=amount,
        am_pm=am_pm, use_date=use_date, note="n", channel=RequestChannel.ERP)


async def _alloc_sum_count(session, request_id) -> tuple[Decimal, int]:
    total = await allocation_repo.sum_for_request(session, request_id)
    n = (await session.execute(
        select(func.count()).select_from(LeaveAllocation).where(
            LeaveAllocation.request_id == request_id))).scalar_one()
    return total, n


# ---- 승인 FEFO 차감 (service) ---------------------------------------------


@pytest.mark.asyncio
async def test_approve_deducts_earliest_expiry_first(db_session) -> None:
    """동일 종류 만료 임박(expiry ASC) lot 부터 차감 — 늦은 만료 lot 은 손 안 댐."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    near = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("1.0"), expiry=date(FY, 6, 30))
    far = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("1.0"), expiry=date(FY, 12, 31))
    req = await _request(db_session, emp.id, amount=Decimal("1.0"))

    out_req, balance, warning = await leave_approval.approve(db_session, hr, req.id)

    assert out_req.status == RequestStatus.APPROVED
    assert out_req.approved_by == hr.id and out_req.approved_at is not None
    assert near.remaining == Decimal("0.00")    # 임박 lot 소진
    assert far.remaining == Decimal("1.00")     # 늦은 lot 보존
    total, n = await _alloc_sum_count(db_session, req.id)
    assert total == Decimal("1.0") and n == 1   # 합 = request.amount
    assert balance == Decimal("1.00") and warning is False


@pytest.mark.asyncio
async def test_approve_amount_by_unit_half(db_session) -> None:
    """차감량 = 사용 단위(반차 0.5) — lot remaining 에서 0.5 만 빠진다."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    lot = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("1.0"), expiry=date(FY, 12, 31))
    req = await _request(db_session, emp.id, unit=LeaveUnit.HALF, amount=Decimal("0.5"), am_pm=AmPm.AM)

    _out, balance, warning = await leave_approval.approve(db_session, hr, req.id)

    assert lot.remaining == Decimal("0.50")
    total, _n = await _alloc_sum_count(db_session, req.id)
    assert total == Decimal("0.5")
    assert balance == Decimal("0.50") and warning is False


@pytest.mark.asyncio
async def test_approve_splits_across_lots(db_session) -> None:
    """한 lot 부족 → 다음 lot 분할(0.3 + 0.2), 합 = 0.5."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    small = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("0.3"), expiry=date(FY, 6, 30))
    big = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("1.0"), expiry=date(FY, 12, 31))
    req = await _request(db_session, emp.id, unit=LeaveUnit.HALF, amount=Decimal("0.5"), am_pm=AmPm.AM)

    _out, balance, warning = await leave_approval.approve(db_session, hr, req.id)

    assert small.remaining == Decimal("0.00")   # 임박 lot 먼저 전부
    assert big.remaining == Decimal("0.80")     # 나머지 0.2
    total, n = await _alloc_sum_count(db_session, req.id)
    assert total == Decimal("0.5") and n == 2   # 2 lot 분할·합 일치
    assert balance == Decimal("0.80") and warning is False


@pytest.mark.asyncio
async def test_approve_negative_absorption_last_lot(db_session) -> None:
    """잔여 부족 승인 → 마지막 FEFO lot remaining 음수 흡수 + 경고. 하드 차단 안 됨."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    lot = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("0.3"), expiry=date(FY, 12, 31))
    req = await _request(db_session, emp.id, unit=LeaveUnit.HALF, amount=Decimal("0.5"), am_pm=AmPm.AM)

    out_req, balance, warning = await leave_approval.approve(db_session, hr, req.id)

    assert out_req.status == RequestStatus.APPROVED   # 차단 안 됨
    assert lot.remaining == Decimal("-0.20")          # 음수 흡수
    total, n = await _alloc_sum_count(db_session, req.id)
    assert total == Decimal("0.5") and n == 1         # 차감 기록 유지·합 일치
    assert balance == Decimal("-0.20") and warning is True


@pytest.mark.asyncio
async def test_approve_annual_no_expiry(db_session) -> None:
    """무만료 연차(expiry NULL) lot 도 차감 가능(use_date 무관 valid)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    lot = await _lot(db_session, emp.id, LeaveCategory.ANNUAL, Decimal("15.0"), expiry=None)
    req = await _request(db_session, emp.id, category=LeaveCategory.ANNUAL, amount=Decimal("1.0"))

    _out, balance, _warning = await leave_approval.approve(db_session, hr, req.id)

    assert lot.remaining == Decimal("14.00")
    assert balance == Decimal("14.00")


@pytest.mark.asyncio
async def test_approve_not_found_404(db_session) -> None:
    hr = await _seed_employee(db_session, department=HR)
    with pytest.raises(NotFoundError):
        await leave_approval.approve(db_session, hr, uuid.uuid4())


@pytest.mark.asyncio
async def test_approve_already_processed_409(db_session) -> None:
    """이미 승인된 신청 재승인 거부(409) — 차감 중복 방지(state machine)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("2.0"), expiry=date(FY, 12, 31))
    req = await _request(db_session, emp.id, amount=Decimal("1.0"))
    await leave_approval.approve(db_session, hr, req.id)
    with pytest.raises(ConflictError):
        await leave_approval.approve(db_session, hr, req.id)


# ---- 반려 (service) -------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_sets_rejected_no_allocation(db_session) -> None:
    """반려 → `반려됨` + reject_reason · 차감 없음(allocation 0·lot 불변)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    lot = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("2.0"), expiry=date(FY, 12, 31))
    req = await _request(db_session, emp.id, amount=Decimal("1.0"))

    out_req = await leave_approval.reject(db_session, hr, req.id, "사유 있음")

    assert out_req.status == RequestStatus.REJECTED
    assert out_req.reject_reason == "사유 있음" and out_req.approved_by == hr.id
    assert lot.remaining == Decimal("2.00")          # 차감 없음
    _total, n = await _alloc_sum_count(db_session, req.id)
    assert n == 0


@pytest.mark.asyncio
async def test_reject_already_processed_409(db_session) -> None:
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"))
    await leave_approval.reject(db_session, hr, req.id, "first")
    with pytest.raises(ConflictError):
        await leave_approval.reject(db_session, hr, req.id, "second")


# ---- 큐 조회 (service) ----------------------------------------------------


@pytest.mark.asyncio
async def test_pending_queue_only_requested(db_session) -> None:
    """큐 = `신청됨` 만(승인/반려 처리분 제외) + 신청자 조인."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("5.0"), expiry=date(FY, 12, 31))
    pending = await _request(db_session, emp.id, amount=Decimal("1.0"), use_date=date(FY, 5, 2))
    approved = await _request(db_session, emp.id, amount=Decimal("1.0"), use_date=date(FY, 5, 3))
    await leave_approval.approve(db_session, hr, approved.id)

    rows = await leave_approval.pending_queue(db_session)
    ids = {req.id for req, _emp in rows}
    assert pending.id in ids and approved.id not in ids
    # 신청자 조인 노출
    req, joined_emp = next(r for r in rows if r[0].id == pending.id)
    assert joined_emp.id == emp.id and joined_emp.name == emp.name


# ---- 권한 / 자기 승인 (endpoint) ------------------------------------------


@pytest.mark.asyncio
async def test_pending_queue_non_hr_403(db_session) -> None:
    member = await _seed_employee(db_session, department="개발")
    app.dependency_overrides[get_current_employee] = lambda: member
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.get("/leave/admin/requests", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_pending_queue_hr_200(db_session) -> None:
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    await _request(db_session, emp.id, amount=Decimal("1.0"))
    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.get("/leave/admin/requests", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) >= 1
        assert set(body[0]) == {"id", "employee_id", "employee_name", "employee_email",
                                "category", "unit", "amount", "am_pm", "use_date",
                                "note", "status", "channel", "created_at"}
        assert body[0]["status"] == "신청됨"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_approve_non_hr_403(db_session) -> None:
    member = await _seed_employee(db_session, department="개발")
    emp = await _seed_employee(db_session)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"))
    app.dependency_overrides[get_current_employee] = lambda: member
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post(f"/leave/admin/requests/{req.id}/approve",
                                headers={"Authorization": "Bearer t"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_self_approval_allowed(db_session) -> None:
    """HR 본인 신청을 본인이 승인 가능(별도 분리 승인자 없음 — SPEC-003)."""
    hr = await _seed_employee(db_session, department=HR)
    await _lot(db_session, hr.id, LeaveCategory.COMP, Decimal("2.0"), expiry=date(FY, 12, 31))
    req = await _request(db_session, hr.id, amount=Decimal("1.0"))
    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post(f"/leave/admin/requests/{req.id}/approve",
                                headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["request"]["status"] == "승인됨"
        assert body["warning"] is False and body["balance"] == "1.00"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reject_reason_required_422(db_session) -> None:
    """반려 사유 누락 → 422(스키마 거부)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"))
    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            missing = await c.post(f"/leave/admin/requests/{req.id}/reject",
                                   headers={"Authorization": "Bearer t"}, json={})
            blank = await c.post(f"/leave/admin/requests/{req.id}/reject",
                                 headers={"Authorization": "Bearer t"}, json={"reason": "   "})
        assert missing.status_code == 422
        assert blank.status_code == 422   # 공백도 strip → min_length 위반
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reject_endpoint_200(db_session) -> None:
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"))
    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post(f"/leave/admin/requests/{req.id}/reject",
                                headers={"Authorization": "Bearer t"}, json={"reason": "중복 신청"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "반려됨"
    finally:
        app.dependency_overrides.clear()
