"""신청 취소 전이 + 승인분 원-lot 복원 — WP-004 Phase 1.

정본 = SPEC-005 §취소 전이·§복원 정책·§API 계약·§4 +
40-architecture/domains/{leave_request,leave_allocation,leave_grant}.md. **Phase 1 만** —
변경 묶음(Phase 2)·FE(Phase 3)·HR 부여/조정(WP-005)은 손대지 않는다. 새 컬럼/migration 없음
(deleted_at·cancel_reason·reject_reason·restored_at·expired_at·enum 취소요청됨/취소됨 = WP-002 산물).

전이(leave_request §State Machine):
- 개인 취소(본인): `신청됨` → 즉시 `취소됨` + soft delete(차감 전 — 복원 없음) /
  `승인됨` → `취소요청됨`(HR 큐, 아직 복원·soft delete 안 함) / 그 외 재취소 → 409.
- HR 취소승인: `취소요청됨` → `취소됨` + soft delete + **원-lot 복원**(역산).
- HR 취소반려: `취소요청됨` → `승인됨` 복귀 + `reject_reason`(필수) — 휴가·차감 유지.

원-lot 복원(leave_allocation/leave_grant §Invariant, 단일 규칙 `use_date <= expiry_date`):
- 미처리 allocation(restored_at·expired_at 둘 다 NULL)만 역산 — **이중 복원 방지**(idempotent).
- 원 lot 이 복원 시점 만료(`expiry_date` NOT NULL 이고 `expiry_date < today`) → **복원 안 함**:
  `allocation.expired_at` set(만료소멸 기록), `grant.remaining` 불변, **다른 lot 이전 금지**.
- 그 외(무만료 `연차`=expiry NULL 포함·미만료) → `grant.remaining += amount` + `allocation.restored_at` set.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.models.employee import Employee
from app.models.enums import RequestStatus
from app.models.leave_request import LeaveRequest
from app.repositories import leave_allocation as allocation_repo
from app.repositories import leave_grant as grant_repo
from app.repositories import leave_request as request_repo


async def cancel_queue(session: AsyncSession) -> list[tuple[LeaveRequest, Employee]]:
    """HR 취소 승인 큐 — `취소요청됨` 전 직원 + 신청자. 사용일 임박순(read-only, commit 없음)."""
    return await request_repo.list_cancel_requested(session)


async def request_cancel(
    session: AsyncSession, employee: Employee, request_id: UUID, reason: str | None
) -> LeaveRequest:
    """개인 취소(본인 신청만). 현재 status 로 분기. commit 은 본 service.

    - `신청됨`(대기) → **자유 취소**: 즉시 `취소됨` + `deleted_at`(차감 전 — 복원 없음).
    - `승인됨` → **취소 요청**: `취소요청됨`(HR 큐) — 이 시점 복원·soft delete 안 함(HR 승인 후).
    - 그 외(`반려됨`/`취소요청됨`/`취소됨`) 재취소 → 409.

    타인 신청이면 404 가 아니라 **403**(본인 한정 — SPEC-005 §권한). 누락/공백 reason 은 NULL(옵션).
    """
    req = await request_repo.get_by_id(session, request_id)
    if req is None:
        raise NotFoundError("신청을 찾을 수 없습니다")
    if req.employee_id != employee.id:
        raise ForbiddenError("본인 신청만 취소할 수 있습니다")

    if req.status == RequestStatus.REQUESTED:
        req.status = RequestStatus.CANCELLED
        req.deleted_at = datetime.now(UTC)  # soft delete (hard delete 금지)
    elif req.status == RequestStatus.APPROVED:
        req.status = RequestStatus.CANCEL_REQUESTED  # HR 큐로 — 복원·soft delete 는 승인 후
    else:
        raise ConflictError("취소할 수 없는 상태입니다")

    req.cancel_reason = (reason or "").strip() or None  # 공백-only → NULL(옵션)
    await session.flush()
    await session.commit()
    await session.refresh(req)
    return req


async def _load_cancel_requested(session: AsyncSession, request_id: UUID) -> LeaveRequest:
    """취소승인/반려 대상 로드 + 상태 게이트. 없으면 404 · `취소요청됨` 아니면 409.

    `취소요청됨` 에서만 처리 가능 — 이미 `취소됨`/`승인됨` 으로 확정된 건 재처리 차단(이중 복원 방지).
    """
    req = await request_repo.get_by_id(session, request_id)
    if req is None:
        raise NotFoundError("신청을 찾을 수 없습니다")
    if req.status != RequestStatus.CANCEL_REQUESTED:
        raise ConflictError("취소 요청 상태가 아닙니다")
    return req


async def _restore_original_lots(session: AsyncSession, request_id: UUID, now: datetime) -> None:
    """원-lot 복원 역산 — 미처리 allocation 만(이중 복원 방지). 만료 lot 은 만료소멸 기록.

    `expire_lapsed_lots` 와 동일 경계(strict `<`): 만료일 당일은 `use_date <= expiry_date` 로
    아직 소비 가능하므로 복원 대상. `expiry_date < today` 인 lot 만 만료소멸. status(denormalize)
    가 아니라 날짜 단일 규칙으로 판정(valid_lots_fefo 정합).
    """
    today = now.date()
    allocations = await allocation_repo.list_active_for_request(session, request_id)
    for alloc in allocations:
        lot = await grant_repo.get_by_id(session, alloc.grant_id)
        if lot is None:  # FK 보장 — 방어적
            continue
        if lot.expiry_date is not None and lot.expiry_date < today:
            # 복원 시점 만료 → 복원 안 함(만료소멸 기록·다른 lot 이전 X). remaining 불변.
            alloc.expired_at = now
        else:
            # 무만료 연차(expiry NULL) 또는 미만료 → 항상 복원(in-place).
            lot.remaining += alloc.amount
            alloc.restored_at = now


async def approve_cancel(
    session: AsyncSession, hr: Employee, request_id: UUID
) -> LeaveRequest:
    """HR 취소승인 → `취소됨` + soft delete + 원-lot 복원. commit 은 본 service.

    없음 404·`취소요청됨` 아니면 409(이중 복원 차단)·비-HR 403(require_hr). 승인분 차감을 역산해
    원 lot 으로 되돌린다(만료 lot 은 만료소멸).
    """
    req = await _load_cancel_requested(session, request_id)
    now = datetime.now(UTC)

    await _restore_original_lots(session, req.id, now)

    req.status = RequestStatus.CANCELLED
    req.deleted_at = now  # soft delete (hard delete 금지)
    req.approved_by = hr.id
    req.approved_at = now
    await session.flush()
    await session.commit()
    await session.refresh(req)
    return req


async def reject_cancel(
    session: AsyncSession, hr: Employee, request_id: UUID, reason: str
) -> LeaveRequest:
    """HR 취소반려 → `승인됨` 복귀 + `reject_reason`. **휴가 유지**(복원 없음·deleted_at NULL).

    없음 404·`취소요청됨` 아니면 409·비-HR 403. `reason` 공백/누락은 스키마(RejectIn)에서 422.
    차감·allocation 불변(역산 안 함) — 단순 상태 복귀.
    """
    req = await _load_cancel_requested(session, request_id)
    req.status = RequestStatus.APPROVED  # 별도 상태 신설 없이 승인됨 복귀
    req.reject_reason = reason
    req.approved_by = hr.id
    req.approved_at = datetime.now(UTC)
    await session.flush()
    await session.commit()
    await session.refresh(req)
    return req
