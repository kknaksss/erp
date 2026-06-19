"""취소 전이 + 원-lot 복원 테스트 — SPEC-005 §취소 전이·§복원 정책·§4 + domains. WP-004 Phase 1.

- 개인 취소(service·실제 DB·롤백): `신청됨`=자유취소(취소됨·deleted_at·복원 없음) / `승인됨`=취소요청
  (취소요청됨·복원/soft delete 안 함·큐 노출) / 그 외 409 / 타인 403.
- HR 취소승인: 원-lot 복원(remaining 환원·restored_at·balance 원복) / 만료 lot 만료소멸(expired_at·
  remaining 불변·이전 X) / 무만료 연차 항상 복원 / 이중 복원 방지(재승인 409·restored_at skip).
- HR 취소반려: reason 누락 422 / reason 있으면 승인됨 복귀·휴가 유지(deleted_at NULL).
- 권한(endpoint): 비-HR cancel-approve/reject 403 · 본인 아닌 취소 403.

service 가 commit 하지만 db_session fixture 가 outer 트랜잭션 롤백으로 격리한다(test_leave_approval 동일).
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select

from app.core.deps import get_current_employee, get_db
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
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
from app.models.leave_allocation import LeaveAllocation
from app.models.leave_grant import LeaveGrant
from app.repositories import leave_allocation as allocation_repo
from app.repositories import leave_grant as grant_repo
from app.repositories import leave_request as request_repo
from app.services import leave_approval, leave_balance, leave_cancel

FY = 2026
HR = "hr"
PAST = date(2020, 1, 1)  # 항상 < today (만료) — wall-clock 무관
FUTURE = date(2099, 12, 31)  # 항상 >= today (미만료) — wall-clock 무관


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
                   amount=Decimal("1.0"), am_pm=None, use_date=date(FY, 5, 1),
                   status=RequestStatus.REQUESTED):
    req = await request_repo.create(
        session, employee_id=eid, category=category, unit=unit, amount=amount,
        am_pm=am_pm, use_date=use_date, note="n", channel=RequestChannel.ERP)
    if status != RequestStatus.REQUESTED:
        req.status = status
        await session.flush()
    return req


async def _allocs(session, request_id) -> list[LeaveAllocation]:
    """해당 신청의 전체 allocation(복원/만료소멸 포함) — restored_at/expired_at 검증용."""
    return list((await session.execute(
        select(LeaveAllocation).where(LeaveAllocation.request_id == request_id))).scalars().all())


# ---- 개인 취소: 대기 자유취소 / 승인분 취소요청 ----------------------------


@pytest.mark.asyncio
async def test_cancel_requested_free_immediate(db_session) -> None:
    """`신청됨` 자유취소 → 즉시 `취소됨` + deleted_at · 복원 없음(allocation 0)."""
    emp = await _seed_employee(db_session)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"))

    out = await leave_cancel.request_cancel(db_session, emp, req.id, reason="잘못 신청")

    assert out.status == RequestStatus.CANCELLED
    assert out.deleted_at is not None          # soft delete
    assert out.cancel_reason == "잘못 신청"
    assert await _allocs(db_session, req.id) == []  # 차감 전 — 복원할 것 없음


@pytest.mark.asyncio
async def test_cancel_requested_blank_reason_null(db_session) -> None:
    """reason 옵션 — 공백/누락은 cancel_reason NULL(SPEC-005 §API 입력 = 신청 id)."""
    emp = await _seed_employee(db_session)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"))

    out = await leave_cancel.request_cancel(db_session, emp, req.id, reason="   ")
    assert out.cancel_reason is None and out.status == RequestStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_other_employee_forbidden(db_session) -> None:
    """타인 신청 취소 시도 → 403(본인 한정 — SPEC-005 §권한)."""
    owner = await _seed_employee(db_session)
    other = await _seed_employee(db_session)
    req = await _request(db_session, owner.id, amount=Decimal("1.0"))
    with pytest.raises(ForbiddenError):
        await leave_cancel.request_cancel(db_session, other, req.id, reason=None)


@pytest.mark.asyncio
async def test_cancel_not_found_404(db_session) -> None:
    emp = await _seed_employee(db_session)
    with pytest.raises(NotFoundError):
        await leave_cancel.request_cancel(db_session, emp, uuid.uuid4(), reason=None)


@pytest.mark.asyncio
async def test_cancel_approved_becomes_cancel_requested(db_session) -> None:
    """`승인됨` 취소요청 → `취소요청됨`(HR 큐) · 아직 복원·soft delete 안 함 · 큐 노출."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    lot = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("1.0"), expiry=FUTURE)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"))
    await leave_approval.approve(db_session, hr, req.id)  # 승인됨 + allocation 생성

    out = await leave_cancel.request_cancel(db_session, emp, req.id, reason="일정 변경")

    assert out.status == RequestStatus.CANCEL_REQUESTED
    assert out.deleted_at is None              # 승인 후에만 soft delete
    assert lot.remaining == Decimal("0.00")    # 아직 복원 안 됨(차감 유지)
    allocs = await _allocs(db_session, req.id)
    assert len(allocs) == 1 and allocs[0].restored_at is None and allocs[0].expired_at is None
    # 취소 승인 큐 노출(신청 큐와 별도)
    queue_ids = {r.id for r, _e in await leave_cancel.cancel_queue(db_session)}
    assert req.id in queue_ids


@pytest.mark.asyncio
async def test_cancel_rejected_state_conflict_409(db_session) -> None:
    """그 외 상태(반려됨 등) 재취소 → 409."""
    emp = await _seed_employee(db_session)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"),
                         status=RequestStatus.REJECTED)
    with pytest.raises(ConflictError):
        await leave_cancel.request_cancel(db_session, emp, req.id, reason=None)


# ---- HR 취소승인 + 원-lot 복원 --------------------------------------------


@pytest.mark.asyncio
async def test_approve_cancel_restores_original_lot(db_session) -> None:
    """취소 승인 → `취소됨`+deleted_at + 원 grant.remaining 복원·restored_at set·balance 원복."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    lot = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("3.0"), expiry=FUTURE)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"))
    await leave_approval.approve(db_session, hr, req.id)   # remaining 3.0 → 2.0
    assert lot.remaining == Decimal("2.00")
    await leave_cancel.request_cancel(db_session, emp, req.id, reason=None)

    out = await leave_cancel.approve_cancel(db_session, hr, req.id)

    assert out.status == RequestStatus.CANCELLED and out.deleted_at is not None
    assert out.approved_by == hr.id
    restored = await grant_repo.get_by_id(db_session, lot.id)
    assert restored.remaining == Decimal("3.00")          # 원복
    allocs = await _allocs(db_session, req.id)
    assert allocs[0].restored_at is not None and allocs[0].expired_at is None
    balance = await leave_balance.category_balance(db_session, emp.id, LeaveCategory.COMP)
    assert balance == Decimal("3.00")                     # category_balance 원복


@pytest.mark.asyncio
async def test_approve_cancel_expired_lot_not_restored(db_session) -> None:
    """만료 원-lot(expiry < today) → 복원 X · expired_at set · remaining 불변 · 다른 lot 이전 X."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    expired_lot = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("0.0"), expiry=PAST)
    other_lot = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("2.0"), expiry=FUTURE)
    # 승인 당시 만료 lot 에서 차감된 상태를 직접 시드(현재는 만료) → 취소요청됨.
    req = await _request(db_session, emp.id, amount=Decimal("1.0"),
                         status=RequestStatus.CANCEL_REQUESTED)
    await allocation_repo.create(db_session, request_id=req.id, grant_id=expired_lot.id,
                                 amount=Decimal("1.0"))

    await leave_cancel.approve_cancel(db_session, hr, req.id)

    assert (await grant_repo.get_by_id(db_session, expired_lot.id)).remaining == Decimal("0.00")
    assert (await grant_repo.get_by_id(db_session, other_lot.id)).remaining == Decimal("2.00")  # 이전 X
    allocs = await _allocs(db_session, req.id)
    assert allocs[0].expired_at is not None and allocs[0].restored_at is None


@pytest.mark.asyncio
async def test_approve_cancel_annual_always_restored(db_session) -> None:
    """무만료 `연차`(expiry NULL) → 만료 개념 없음 · 항상 복원."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    lot = await _lot(db_session, emp.id, LeaveCategory.ANNUAL, Decimal("0.0"), expiry=None)
    req = await _request(db_session, emp.id, category=LeaveCategory.ANNUAL, amount=Decimal("1.0"),
                         status=RequestStatus.CANCEL_REQUESTED)
    await allocation_repo.create(db_session, request_id=req.id, grant_id=lot.id,
                                 amount=Decimal("1.0"))

    await leave_cancel.approve_cancel(db_session, hr, req.id)

    assert (await grant_repo.get_by_id(db_session, lot.id)).remaining == Decimal("1.00")
    allocs = await _allocs(db_session, req.id)
    assert allocs[0].restored_at is not None and allocs[0].expired_at is None


@pytest.mark.asyncio
async def test_approve_cancel_wrong_state_409(db_session) -> None:
    """`취소요청됨` 아닌 신청(승인됨) 취소승인 시도 → 409."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"),
                         status=RequestStatus.APPROVED)
    with pytest.raises(ConflictError):
        await leave_cancel.approve_cancel(db_session, hr, req.id)


@pytest.mark.asyncio
async def test_approve_cancel_idempotent_no_double_restore(db_session) -> None:
    """이중 복원 방지 — 재승인은 409(상태 게이트)·restored_at set allocation 은 재역산 skip."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    lot = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("3.0"), expiry=FUTURE)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"))
    await leave_approval.approve(db_session, hr, req.id)
    await leave_cancel.request_cancel(db_session, emp, req.id, reason=None)
    await leave_cancel.approve_cancel(db_session, hr, req.id)   # 1차 복원 → 3.0
    assert (await grant_repo.get_by_id(db_session, lot.id)).remaining == Decimal("3.00")

    with pytest.raises(ConflictError):                          # 재승인 차단(취소됨 상태)
        await leave_cancel.approve_cancel(db_session, hr, req.id)
    # 미처리 allocation 0건 → 재역산 대상 없음(remaining 불변)
    assert await allocation_repo.list_active_for_request(db_session, req.id) == []
    assert (await grant_repo.get_by_id(db_session, lot.id)).remaining == Decimal("3.00")


# ---- HR 취소반려 ----------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_cancel_restores_approved(db_session) -> None:
    """취소반려 → `승인됨` 복귀 + reject_reason · 휴가 유지(deleted_at NULL·복원 없음·차감 유지)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    lot = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("1.0"), expiry=FUTURE)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"))
    await leave_approval.approve(db_session, hr, req.id)
    await leave_cancel.request_cancel(db_session, emp, req.id, reason=None)

    out = await leave_cancel.reject_cancel(db_session, hr, req.id, "취소 불가 사유")

    assert out.status == RequestStatus.APPROVED and out.deleted_at is None
    assert out.reject_reason == "취소 불가 사유"
    assert (await grant_repo.get_by_id(db_session, lot.id)).remaining == Decimal("0.00")  # 차감 유지
    allocs = await _allocs(db_session, req.id)
    assert allocs[0].restored_at is None and allocs[0].expired_at is None  # 복원 없음


@pytest.mark.asyncio
async def test_reject_cancel_wrong_state_409(db_session) -> None:
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"),
                         status=RequestStatus.APPROVED)
    with pytest.raises(ConflictError):
        await leave_cancel.reject_cancel(db_session, hr, req.id, "사유")


# ---- 권한 / 엔드포인트 ----------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_endpoint_owner_200(db_session) -> None:
    """본인 취소 엔드포인트 — `신청됨` → 취소됨(200)."""
    emp = await _seed_employee(db_session)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"))
    app.dependency_overrides[get_current_employee] = lambda: emp
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post(f"/leave/requests/{req.id}/cancel",
                                headers={"Authorization": "Bearer t"}, json={})
        assert resp.status_code == 200 and resp.json()["status"] == "취소됨"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cancel_endpoint_non_owner_403(db_session) -> None:
    owner = await _seed_employee(db_session)
    other = await _seed_employee(db_session)
    req = await _request(db_session, owner.id, amount=Decimal("1.0"))
    app.dependency_overrides[get_current_employee] = lambda: other
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post(f"/leave/requests/{req.id}/cancel",
                                headers={"Authorization": "Bearer t"}, json={})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cancel_approve_non_hr_403(db_session) -> None:
    member = await _seed_employee(db_session, department="개발")
    emp = await _seed_employee(db_session)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"),
                         status=RequestStatus.CANCEL_REQUESTED)
    app.dependency_overrides[get_current_employee] = lambda: member
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post(f"/leave/admin/requests/{req.id}/cancel-approve",
                                headers={"Authorization": "Bearer t"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cancel_reject_reason_required_422(db_session) -> None:
    """취소반려 사유 누락/공백 → 422(RejectIn 스키마 거부)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"),
                         status=RequestStatus.CANCEL_REQUESTED)
    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            missing = await c.post(f"/leave/admin/requests/{req.id}/cancel-reject",
                                   headers={"Authorization": "Bearer t"}, json={})
            blank = await c.post(f"/leave/admin/requests/{req.id}/cancel-reject",
                                 headers={"Authorization": "Bearer t"}, json={"reason": "   "})
        assert missing.status_code == 422 and blank.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cancel_requests_queue_hr_200(db_session) -> None:
    """HR 취소 승인 큐 엔드포인트 — `취소요청됨` 노출(신청 큐와 별도). 비-HR 403."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    req = await _request(db_session, emp.id, amount=Decimal("1.0"),
                         status=RequestStatus.CANCEL_REQUESTED)
    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            ok = await c.get("/leave/admin/cancel-requests",
                             headers={"Authorization": "Bearer t"})
        assert ok.status_code == 200
        body = ok.json()
        assert any(r["id"] == str(req.id) and r["status"] == "취소요청됨" for r in body)
        assert set(body[0]) == {"id", "employee_id", "employee_name", "employee_email",
                                "category", "unit", "amount", "am_pm", "use_date",
                                "note", "status", "channel", "created_at"}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cancel_requests_queue_non_hr_403(db_session) -> None:
    member = await _seed_employee(db_session, department="개발")
    app.dependency_overrides[get_current_employee] = lambda: member
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.get("/leave/admin/cancel-requests",
                               headers={"Authorization": "Bearer t"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()
