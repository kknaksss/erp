"""변경 = 취소 + 재신청 묶음 테스트 — SPEC-005 §변경·§3 API·§4 + domains. WP-004 Phase 2.

- 변경 요청(대기/승인 원건): 새 change_group_id 묶음·원건 미수정(상태 유지)·재신청 신청됨·이 시점
  복원/취소 안 함.
- 변경 승인(원자): 승인 원건 → 취소됨+deleted_at+원-lot 복원 / 대기 원건 → 취소됨(복원 없음) +
  재신청 승인됨+FEFO 차감(둘 다 한 번에).
- 변경 반려: 원건 유지(차감/복원 불변)·change_group_id 해제 + 재신청 반려됨.
- 원자성: 재신청 승인 실패 시 commit 미도달(원건 취소도 미반영).
- 권한/큐: 타인 변경 403·비-HR 승인/반려 403·이중 처리 409·신청 큐 누수 차단·변경 큐 노출.

service 가 commit 하지만 db_session fixture 가 outer 트랜잭션 롤백으로 격리한다(test_leave_cancel 동일).
"""

import uuid
from datetime import UTC, datetime, date
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
from app.repositories import leave_grant as grant_repo
from app.repositories import leave_request as request_repo
from app.schemas.leave_request import ErpIntakeIn
from app.services import leave_approval, leave_change

FY = 2026
HR = "hr"
FUTURE = date(2099, 12, 31)
EARLY = datetime(2020, 1, 1, tzinfo=UTC)   # 원건 created_at — 재신청보다 항상 앞(판별자)
LATER = datetime(2020, 1, 2, tzinfo=UTC)   # 직접 시드 재신청 created_at


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_employee(session, *, department: str | None = None) -> Employee:
    eid = uuid.uuid4()
    emp = Employee(id=eid, email=f"{eid.hex[:8]}@x.com", name=f"emp-{eid.hex[:6]}",
                   role="member", active=True, department=department)
    session.add(emp)
    await session.flush()
    return emp


async def _lot(session, eid, category, remaining, *, expiry=FUTURE) -> LeaveGrant:
    return await grant_repo.create_lot(
        session, employee_id=eid, category=category, amount=remaining, remaining=remaining,
        source=GrantSource.HR_GRANT, expiry_date=expiry, status=GrantStatus.ACTIVE,
        granted_at=datetime(FY, 1, 1, tzinfo=UTC))


async def _request(session, eid, *, category=LeaveCategory.COMP, unit=LeaveUnit.FULL,
                   amount=Decimal("1.0"), am_pm=None, use_date=date(FY, 5, 1),
                   status=RequestStatus.REQUESTED, created_at=None, change_group_id=None):
    req = await request_repo.create(
        session, employee_id=eid, category=category, unit=unit, amount=amount,
        am_pm=am_pm, use_date=use_date, note="n", channel=RequestChannel.ERP,
        created_at=created_at)
    if status != RequestStatus.REQUESTED:
        req.status = status
    if change_group_id is not None:
        req.change_group_id = change_group_id
    await session.flush()
    return req


def _payload(*, category=LeaveCategory.COMP, unit=LeaveUnit.FULL, am_pm=None,
             use_date=date(FY, 6, 22), note="변경") -> ErpIntakeIn:
    return ErpIntakeIn(category=category, unit=unit, am_pm=am_pm, use_date=use_date, note=note)


async def _allocs(session, request_id) -> list[LeaveAllocation]:
    return list((await session.execute(
        select(LeaveAllocation).where(LeaveAllocation.request_id == request_id))).scalars().all())


# ---- 변경 요청 (묶음 생성, 원건 미수정) -----------------------------------


@pytest.mark.asyncio
async def test_request_change_pending_original(db_session) -> None:
    """변경 요청(대기 원건) → 둘 다 같은 change_group_id·원건 상태 유지(신청됨)·재신청 신청됨."""
    emp = await _seed_employee(db_session)
    original = await _request(db_session, emp.id, created_at=EARLY)

    orig, reapp = await leave_change.request_change(db_session, emp, original.id, _payload())

    assert orig.status == RequestStatus.REQUESTED       # 원건 미수정(상태 유지)
    assert orig.deleted_at is None
    assert reapp.status == RequestStatus.REQUESTED       # 재신청 신청됨
    assert orig.change_group_id is not None
    assert orig.change_group_id == reapp.change_group_id  # 동일 묶음
    assert reapp.use_date == date(FY, 6, 22) and reapp.id != orig.id
    assert await _allocs(db_session, orig.id) == []      # 이 시점 차감/복원 없음


@pytest.mark.asyncio
async def test_request_change_approved_original_no_restore_yet(db_session) -> None:
    """변경 요청(승인 원건) → 묶음 표시·상태 유지(승인됨)·이 시점 복원/취소 안 됨(차감 유지)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    lot = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("3.0"))
    original = await _request(db_session, emp.id, created_at=EARLY)
    await leave_approval.approve(db_session, hr, original.id)   # 승인됨, lot 3.0→2.0
    assert lot.remaining == Decimal("2.00")

    orig, reapp = await leave_change.request_change(db_session, emp, original.id, _payload())

    assert orig.status == RequestStatus.APPROVED         # 원건 미수정(취소는 HR 승인 시점)
    assert orig.change_group_id == reapp.change_group_id
    assert (await grant_repo.get_by_id(db_session, lot.id)).remaining == Decimal("2.00")  # 복원 X
    allocs = await _allocs(db_session, original.id)
    assert allocs[0].restored_at is None and allocs[0].expired_at is None  # 차감 유지


@pytest.mark.asyncio
async def test_request_change_other_employee_forbidden(db_session) -> None:
    """타인 원건 변경 요청 → 403(본인 한정)."""
    owner = await _seed_employee(db_session)
    other = await _seed_employee(db_session)
    original = await _request(db_session, owner.id, created_at=EARLY)
    with pytest.raises(ForbiddenError):
        await leave_change.request_change(db_session, other, original.id, _payload())


@pytest.mark.asyncio
async def test_request_change_bad_state_conflict(db_session) -> None:
    """변경 불가 상태(반려됨) → 409."""
    emp = await _seed_employee(db_session)
    original = await _request(db_session, emp.id, status=RequestStatus.REJECTED, created_at=EARLY)
    with pytest.raises(ConflictError):
        await leave_change.request_change(db_session, emp, original.id, _payload())


@pytest.mark.asyncio
async def test_request_change_already_in_group_conflict(db_session) -> None:
    """이미 변경 묶음에 속한 신청 재변경 → 409(이중 변경 차단)."""
    emp = await _seed_employee(db_session)
    original = await _request(db_session, emp.id, created_at=EARLY)
    await leave_change.request_change(db_session, emp, original.id, _payload())
    with pytest.raises(ConflictError):
        await leave_change.request_change(db_session, emp, original.id, _payload())


# ---- 변경 승인 (원자: 원건 취소 + 재신청 승인) ----------------------------


@pytest.mark.asyncio
async def test_approve_change_approved_original_restores_and_charges(db_session) -> None:
    """변경 승인(승인 원건) → 원건 취소됨+deleted_at+원-lot 복원 + 재신청 승인됨+FEFO 차감."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    lot = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("3.0"))
    original = await _request(db_session, emp.id, created_at=EARLY)
    await leave_approval.approve(db_session, hr, original.id)   # 승인됨, lot 3.0→2.0
    _o, reapp0 = await leave_change.request_change(db_session, emp, original.id, _payload())

    orig, reapp = await leave_change.approve_change(db_session, hr, reapp0.change_group_id)

    assert orig.status == RequestStatus.CANCELLED and orig.deleted_at is not None
    assert orig.approved_by == hr.id
    orig_allocs = await _allocs(db_session, original.id)
    assert orig_allocs[0].restored_at is not None        # 원건 원-lot 복원
    assert reapp.status == RequestStatus.APPROVED         # 재신청 승인
    assert len(await _allocs(db_session, reapp.id)) == 1  # 재신청 FEFO 차감
    # 복원(+1 → 3.0) 후 재신청 차감(-1 → 2.0). 한 번에 처리.
    assert (await grant_repo.get_by_id(db_session, lot.id)).remaining == Decimal("2.00")


@pytest.mark.asyncio
async def test_approve_change_pending_original_no_restore(db_session) -> None:
    """변경 승인(대기 원건) → 원건 취소됨(복원 없음·차감 전) + 재신청 승인됨+차감."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    lot = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("3.0"))
    original = await _request(db_session, emp.id, created_at=EARLY)  # 신청됨(차감 전)
    _o, reapp0 = await leave_change.request_change(db_session, emp, original.id, _payload())

    orig, reapp = await leave_change.approve_change(db_session, hr, reapp0.change_group_id)

    assert orig.status == RequestStatus.CANCELLED and orig.deleted_at is not None
    assert await _allocs(db_session, original.id) == []   # 차감 전이라 복원할 것 없음
    assert reapp.status == RequestStatus.APPROVED
    # 원건 차감 없었으므로 재신청 차감만(-1 → 2.0).
    assert (await grant_repo.get_by_id(db_session, lot.id)).remaining == Decimal("2.00")


@pytest.mark.asyncio
async def test_approve_change_unlinks_reapplication_group(db_session) -> None:
    """변경 승인 후 재신청 change_group_id = NULL(reject 대칭) — 일반 승인됨 신청 복귀.

    원건(취소됨)은 묶음 보존(이미 취소+soft delete, 재취소는 status 게이트로 무관).
    """
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("3.0"))
    original = await _request(db_session, emp.id, created_at=EARLY)
    _o, reapp0 = await leave_change.request_change(db_session, emp, original.id, _payload())

    orig, reapp = await leave_change.approve_change(db_session, hr, reapp0.change_group_id)

    assert reapp.status == RequestStatus.APPROVED
    assert reapp.change_group_id is None                  # 묶음 해제(일반 흐름 복귀)
    assert orig.status == RequestStatus.CANCELLED
    assert orig.change_group_id is not None               # 원건(취소됨)은 묶음 보존


@pytest.mark.asyncio
async def test_approve_change_reapplication_cancellable(db_session) -> None:
    """핵심 회귀: 변경 승인된 재신청 → 일반 취소(취소요청→HR 승인→복원) 가능. 409 데드락 없음."""
    from app.services import leave_cancel
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    lot = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("3.0"))
    original = await _request(db_session, emp.id, created_at=EARLY)
    _o, reapp0 = await leave_change.request_change(db_session, emp, original.id, _payload())
    _orig, reapp = await leave_change.approve_change(db_session, hr, reapp0.change_group_id)
    assert reapp.status == RequestStatus.APPROVED and reapp.change_group_id is None
    assert (await grant_repo.get_by_id(db_session, lot.id)).remaining == Decimal("2.00")  # 재신청 차감

    # 취소 요청(승인분 경로) → 취소요청됨. **409 안 남**(과거엔 change_group_id 잔존으로 데드락).
    creq = await leave_cancel.request_cancel(db_session, emp, reapp.id, reason="재변경")
    assert creq.status == RequestStatus.CANCEL_REQUESTED

    # HR 취소승인 → 취소됨 + soft delete + 원-lot 복원(2.0→3.0).
    done = await leave_cancel.approve_cancel(db_session, hr, reapp.id)
    assert done.status == RequestStatus.CANCELLED and done.deleted_at is not None
    assert (await grant_repo.get_by_id(db_session, lot.id)).remaining == Decimal("3.00")


@pytest.mark.asyncio
async def test_change_in_progress_reapplication_single_cancel_blocked(db_session) -> None:
    """변경 진행 중(승인 전) 재신청 단건 취소 → 409(묶음 원자성 보호 유지)."""
    from app.services import leave_cancel
    emp = await _seed_employee(db_session)
    original = await _request(db_session, emp.id, created_at=EARLY)
    _o, reapp = await leave_change.request_change(db_session, emp, original.id, _payload())
    with pytest.raises(ConflictError):
        await leave_cancel.request_cancel(db_session, emp, reapp.id, reason=None)


@pytest.mark.asyncio
async def test_approve_change_already_processed_409(db_session) -> None:
    """이미 처리된 변경 묶음 재승인 → 409(이중 처리 차단)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("3.0"))
    original = await _request(db_session, emp.id, created_at=EARLY)
    _o, reapp0 = await leave_change.request_change(db_session, emp, original.id, _payload())
    await leave_change.approve_change(db_session, hr, reapp0.change_group_id)
    with pytest.raises(ConflictError):
        await leave_change.approve_change(db_session, hr, reapp0.change_group_id)


@pytest.mark.asyncio
async def test_approve_change_not_found_404(db_session) -> None:
    hr = await _seed_employee(db_session, department=HR)
    with pytest.raises(NotFoundError):
        await leave_change.approve_change(db_session, hr, uuid.uuid4())


# ---- 변경 반려 (원건 유지 + 재신청 폐기) ----------------------------------


@pytest.mark.asyncio
async def test_reject_change_keeps_original_discards_reapplication(db_session) -> None:
    """변경 반려 → 원건 승인됨 유지·차감 불변·묶음 해제 + 재신청 반려됨+사유."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    lot = await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("3.0"))
    original = await _request(db_session, emp.id, created_at=EARLY)
    await leave_approval.approve(db_session, hr, original.id)   # 승인됨, lot 3.0→2.0
    _o, reapp0 = await leave_change.request_change(db_session, emp, original.id, _payload())

    orig, reapp = await leave_change.reject_change(db_session, hr, reapp0.change_group_id, "변경 불가")

    assert orig.status == RequestStatus.APPROVED and orig.deleted_at is None  # 원건 유지
    assert orig.change_group_id is None                  # 묶음 해제(일반 큐 복귀)
    assert reapp.status == RequestStatus.REJECTED and reapp.reject_reason == "변경 불가"
    orig_allocs = await _allocs(db_session, original.id)
    assert orig_allocs[0].restored_at is None            # 차감 유지(복원 없음)
    assert (await grant_repo.get_by_id(db_session, lot.id)).remaining == Decimal("2.00")


@pytest.mark.asyncio
async def test_reject_change_already_processed_409(db_session) -> None:
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    original = await _request(db_session, emp.id, created_at=EARLY)
    _o, reapp0 = await leave_change.request_change(db_session, emp, original.id, _payload())
    await leave_change.reject_change(db_session, hr, reapp0.change_group_id, "사유")
    with pytest.raises(ConflictError):
        await leave_change.reject_change(db_session, hr, reapp0.change_group_id, "사유")


# ---- 원자성 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_change_atomic_rollback_on_reapply_failure(db_session, monkeypatch) -> None:
    """재신청 승인 실패 → commit 미도달(원건 취소도 미반영). 단일-commit 원자성."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    gid = uuid.uuid4()
    await _lot(db_session, emp.id, LeaveCategory.COMP, Decimal("3.0"))
    await _request(db_session, emp.id, status=RequestStatus.REQUESTED,
                   created_at=EARLY, change_group_id=gid)  # 원건
    await _request(db_session, emp.id, status=RequestStatus.REQUESTED,
                   created_at=LATER, change_group_id=gid)  # 재신청

    async def _boom(*a, **k):
        raise RuntimeError("재신청 승인 실패 주입")
    monkeypatch.setattr(leave_approval, "apply_fefo_charge", _boom)

    committed: list[bool] = []
    orig_commit = db_session.commit

    async def _spy_commit():
        committed.append(True)
        await orig_commit()
    monkeypatch.setattr(db_session, "commit", _spy_commit)

    with pytest.raises(RuntimeError):
        await leave_change.approve_change(db_session, hr, gid)

    assert committed == []   # commit 미도달 → 원건 취소(앞 단계)도 영속 안 됨(둘 다 롤백)


# ---- 권한 / 큐 / endpoint -------------------------------------------------


@pytest.mark.asyncio
async def test_pending_queue_excludes_change_bundle_members(db_session) -> None:
    """변경 묶음 멤버(재신청 신청됨)는 일반 신청 큐에 누수 안 됨(별도 변경 큐로)."""
    emp = await _seed_employee(db_session)
    standalone = await _request(db_session, emp.id, created_at=EARLY)  # 일반 신청
    other = await _seed_employee(db_session)
    orig = await _request(db_session, other.id, created_at=EARLY)
    await leave_change.request_change(db_session, other, orig.id, _payload())

    pending_ids = {r.id for r, _e in await leave_approval.pending_queue(db_session)}
    assert standalone.id in pending_ids          # 일반 신청은 노출
    assert orig.id not in pending_ids            # 변경 원건 누수 X
    # 변경 묶음은 변경 큐에 한 항목으로
    bundles = await leave_change.change_queue(db_session)
    assert any(o.id == orig.id and r.change_group_id == o.change_group_id
               for o, r, _e in bundles)


@pytest.mark.asyncio
async def test_change_request_endpoint_owner_200(db_session) -> None:
    """변경 요청 endpoint(본인) → 200 + 변경 단위(change_group_id + 원건/재신청)."""
    emp = await _seed_employee(db_session)
    original = await _request(db_session, emp.id, created_at=EARLY)
    app.dependency_overrides[get_current_employee] = lambda: emp
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post(f"/leave/requests/{original.id}/change",
                                headers={"Authorization": "Bearer t"},
                                json={"category": "연차", "unit": "전일", "use_date": "2026-06-22"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["change_group_id"] and body["original"]["id"] == str(original.id)
        assert body["reapplication"]["status"] == "신청됨"
        assert body["reapplication"]["category"] == "연차"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_change_request_endpoint_non_owner_403(db_session) -> None:
    owner = await _seed_employee(db_session)
    other = await _seed_employee(db_session)
    original = await _request(db_session, owner.id, created_at=EARLY)
    app.dependency_overrides[get_current_employee] = lambda: other
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post(f"/leave/requests/{original.id}/change",
                                headers={"Authorization": "Bearer t"},
                                json={"category": "보상", "unit": "전일", "use_date": "2026-06-22"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_change_queue_endpoint_hr_200_non_hr_403(db_session) -> None:
    """변경 큐 endpoint — HR 200(묶음 노출)·비-HR 403."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    original = await _request(db_session, emp.id, created_at=EARLY)
    await leave_change.request_change(db_session, emp, original.id, _payload())

    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            ok = await c.get("/leave/admin/change-requests",
                             headers={"Authorization": "Bearer t"})
        assert ok.status_code == 200
        assert any(b["original"]["id"] == str(original.id) for b in ok.json())
    finally:
        app.dependency_overrides.clear()

    member = await _seed_employee(db_session, department="개발")
    app.dependency_overrides[get_current_employee] = lambda: member
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.get("/leave/admin/change-requests",
                               headers={"Authorization": "Bearer t"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_change_approve_reject_non_hr_403(db_session) -> None:
    """비-HR 변경 승인/반려 → 403."""
    member = await _seed_employee(db_session, department="개발")
    emp = await _seed_employee(db_session)
    original = await _request(db_session, emp.id, created_at=EARLY)
    _o, reapp = await leave_change.request_change(db_session, emp, original.id, _payload())
    gid = reapp.change_group_id

    app.dependency_overrides[get_current_employee] = lambda: member
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            ap = await c.post(f"/leave/admin/change-requests/{gid}/approve",
                              headers={"Authorization": "Bearer t"})
            rj = await c.post(f"/leave/admin/change-requests/{gid}/reject",
                              headers={"Authorization": "Bearer t"}, json={"reason": "x"})
        assert ap.status_code == 403 and rj.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_bundle_member_rejected_by_standalone_approve(db_session) -> None:
    """변경 묶음 재신청(신청됨)을 단건 승인 시도 → 409(묶음 원자성 보호 — 변경 승인으로만)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    original = await _request(db_session, emp.id, created_at=EARLY)
    _o, reapp = await leave_change.request_change(db_session, emp, original.id, _payload())
    with pytest.raises(ConflictError):                     # 재신청 단건 승인 차단
        await leave_approval.approve(db_session, hr, reapp.id)
    with pytest.raises(ConflictError):                     # 원건 단건 승인도 차단
        await leave_approval.approve(db_session, hr, original.id)


@pytest.mark.asyncio
async def test_bundle_member_rejected_by_standalone_cancel(db_session) -> None:
    """변경 묶음 원건을 단건 취소 시도 → 409(dangling 묶음 방지 — 변경 승인/반려로만)."""
    from app.services import leave_cancel
    emp = await _seed_employee(db_session)
    original = await _request(db_session, emp.id, created_at=EARLY)
    await leave_change.request_change(db_session, emp, original.id, _payload())
    with pytest.raises(ConflictError):
        await leave_cancel.request_cancel(db_session, emp, original.id, reason=None)


@pytest.mark.asyncio
async def test_change_reject_reason_required_422(db_session) -> None:
    """변경 반려 사유 누락/공백 → 422(RejectIn 스키마)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    original = await _request(db_session, emp.id, created_at=EARLY)
    _o, reapp = await leave_change.request_change(db_session, emp, original.id, _payload())
    gid = reapp.change_group_id

    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            missing = await c.post(f"/leave/admin/change-requests/{gid}/reject",
                                   headers={"Authorization": "Bearer t"}, json={})
            blank = await c.post(f"/leave/admin/change-requests/{gid}/reject",
                                 headers={"Authorization": "Bearer t"}, json={"reason": "   "})
        assert missing.status_code == 422 and blank.status_code == 422
    finally:
        app.dependency_overrides.clear()
