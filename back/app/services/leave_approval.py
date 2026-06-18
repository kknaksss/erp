"""HR 승인/반려 — 신청 큐 + 승인(FEFO 차감) + 반려(사유 필수). WP-003 Phase 2.

정본 = SPEC-003 §S-2(승인/반려)·§연차 승인 생명주기·§케이스 매트릭스 +
40-architecture/domains/{leave_request,leave_allocation,leave_grant}.md. 차감 = **승인 시점만**
(leave_request invariant). 취소·변경(WP-004)·HR 부여/조정(WP-005)은 손대지 않는다.

- 승인: `신청됨 → 승인됨` + 선택 종류 **valid lot FEFO 차감**(T-007 `valid_lots_fefo` 그대로 소비 —
  expiry ASC·NULL 최후미). 한 lot 부족 시 여러 lot 분할(allocation 다건, 합 = request.amount).
  음수 흡수 = 마지막 FEFO lot remaining 음수(별도 overflow 없음 — leave_grant §Invariant). 하드 차단 없음.
- 반려: `신청됨 → 반려됨` + `reject_reason` 필수. 차감 없음.
- 큐: `신청됨` 전 직원(처리분은 status 변경으로 자연 제외 → 이력).
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.employee import Employee
from app.models.enums import RequestStatus
from app.models.leave_request import LeaveRequest
from app.repositories import leave_allocation as allocation_repo
from app.repositories import leave_grant as grant_repo
from app.repositories import leave_request as request_repo
from app.services import leave_balance


async def pending_queue(session: AsyncSession) -> list[tuple[LeaveRequest, Employee]]:
    """HR 신청 큐 — `신청됨` 전 직원 + 신청자. 사용일 임박순(read-only, commit 없음)."""
    return await request_repo.list_pending(session)


async def _load_requested(session: AsyncSession, request_id: UUID) -> LeaveRequest:
    """승인/반려 대상 로드 + 상태 게이트. 없으면 404 · `신청됨` 아니면 409(state machine).

    신청 큐(`신청됨`)에서만 처리 가능 — 이미 승인/반려된 건 재처리 차단(차감 중복 방지).
    """
    req = await request_repo.get_by_id(session, request_id)
    if req is None:
        raise NotFoundError("신청을 찾을 수 없습니다")
    if req.status != RequestStatus.REQUESTED:
        raise ConflictError("이미 처리된 신청입니다")
    return req


async def approve(
    session: AsyncSession, hr: Employee, request_id: UUID
) -> tuple[LeaveRequest, Decimal, bool]:
    """승인 → 선택 종류 FEFO 차감. 반환 (신청, 차감후 잔여, 음수경고). commit 은 service.

    FEFO: `valid_lots_fefo(employee, category, use_date)`(expiry ASC·NULL 최후미)를 순서대로 소진.
    한 lot 으로 부족하면 다음 lot 으로 분할(allocation 다건, 합 = request.amount). 후보가 부족하면
    **마지막 FEFO lot 의 remaining 이 음수**로 흡수(별도 overflow 없음 — 하드 차단 없음). 차감 후
    해당 종류 잔여가 음수면 경고 플래그(승인은 가능 — SPEC-003 §케이스 매트릭스).
    """
    req = await _load_requested(session, request_id)

    lots = await grant_repo.valid_lots_fefo(session, req.employee_id, req.category, req.use_date)
    needed = req.amount
    last = len(lots) - 1
    for i, lot in enumerate(lots):
        if needed <= 0:
            break
        # 마지막 FEFO lot 은 부족분까지 전량 흡수(remaining 음수 허용). 그 외엔 lot 잔여 한도.
        take = needed if i == last else min(lot.remaining, needed)
        if take <= 0:
            continue
        lot.remaining -= take  # ORM dirty — flush 시 반영(음수 흡수)
        await allocation_repo.create(session, request_id=req.id, grant_id=lot.id, amount=take)
        needed -= take
    # 후보 lot 이 0건이면 차감 대상이 없어 allocation 미생성(grant_id NOT NULL FK) — 음수 흡수할 lot 도
    # 없음. 하드 차단은 SPEC 상 금지라 승인은 진행(코드 SoT — 리포트 §이슈/블로커 명시).

    req.status = RequestStatus.APPROVED
    req.approved_by = hr.id
    req.approved_at = datetime.now(UTC)
    await session.flush()  # 잔여 집계 전 lot 변경·상태 반영(category_balance 는 SQL 합산)

    balance = await leave_balance.category_balance(session, req.employee_id, req.category)
    await session.commit()
    await session.refresh(req)
    return req, balance, balance < 0


async def reject(
    session: AsyncSession, hr: Employee, request_id: UUID, reason: str
) -> LeaveRequest:
    """반려 → `반려됨` + `reject_reason`. **차감 없음**(allocation 0). commit 은 service.

    `reason` 공백/누락은 스키마(RejectIn min_length·strip)에서 422 로 거부 — 여기 도달분은 유효.
    """
    req = await _load_requested(session, request_id)
    req.status = RequestStatus.REJECTED
    req.reject_reason = reason
    req.approved_by = hr.id
    req.approved_at = datetime.now(UTC)
    await session.flush()
    await session.commit()
    await session.refresh(req)
    return req
