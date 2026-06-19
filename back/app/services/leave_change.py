"""연차 변경 = 원건 취소 + ERP 폼 재신청 묶음 — WP-004 Phase 2.

정본 = SPEC-005 §변경(한 항목)·§3 API 동작·§4 + work-004 §Phase 2 +
40-architecture/domains/{leave_request,leave_allocation,leave_grant}.md. **Phase 2 만** —
FE(Phase 3)·HR 부여/조정(WP-005)은 손대지 않는다. 새 컬럼/migration 없음(`change_group_id` =
WP-002 산물). **변경 id 계약 = `change_group_id`**(domains 묶음 연결 컬럼 = SoT, 임의 식별자 X).

변경 = 신청 레코드 직접 수정 X(로그 보존) — **취소 + 재신청**으로 실현. 원건↔재신청을 동일
`change_group_id` 로 묶어 직원·HR 에게 "변경" 단일 항목으로 노출(SPEC-005 §변경).

원건/재신청 판별 = `created_at` 단조 — 재신청은 변경 요청 시점(원건 intake 보다 나중 트랜잭션)
생성이라 **가장 오래된 = 원건, 가장 최신 = 재신청**(원건이 `승인됨`이든 `신청됨`이든 동형).

전이/원자성(한 트랜잭션 = 1 commit, 부분 실패 시 전체 롤백 — Pre-deploy Check 원자성):
- 변경 요청(본인): 새 `change_group_id` 생성 → 원건 set(상태 유지·아직 취소 X) + ERP 폼 재신청
  (`leave_intake.build_erp_request`, `신청됨`) 동일 group set.
- 변경 승인(HR): 원건 취소(`승인됨`이면 `leave_cancel._restore_original_lots` 복원·`신청됨`이면
  차감 전이라 복원 없음, 둘 다 `취소됨`+soft delete) + 재신청 승인(`leave_approval.apply_fefo_charge`
  FEFO 차감). 둘을 한 트랜잭션으로 — 하나라도 실패하면 함께 롤백.
- 변경 반려(HR): 원건 유지(복원·차감 불변) + 원건 `change_group_id` 해제(일반 큐 복귀) + 재신청
  폐기(`반려됨`+`reject_reason`). soft delete(`deleted_at`)는 `취소됨` 전용이라 반려 재신청엔 안 씀.
"""

from datetime import UTC, datetime
from itertools import groupby
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.models.employee import Employee
from app.models.enums import RequestStatus
from app.models.leave_request import LeaveRequest
from app.repositories import leave_request as request_repo
from app.schemas.leave_request import ErpIntakeIn
from app.services import leave_approval, leave_cancel, leave_intake

# 변경 요청 가능 원건 상태 = 취소 게이트와 동형(`신청됨`/`승인됨` 만). 그 외(반려/취소 계열) → 409.
_CHANGEABLE = (RequestStatus.REQUESTED, RequestStatus.APPROVED)


async def change_queue(
    session: AsyncSession,
) -> list[tuple[LeaveRequest, LeaveRequest, Employee]]:
    """HR 변경 큐 — pending 변경 묶음을 (원건, 재신청, 신청자) 한 항목으로. 신청/취소 큐와 별도.

    `list_change_bundles`(재신청 `신청됨` 살아있는 그룹 전 행) → `change_group_id` 별로 묶어
    원건(가장 오래된)·재신청(가장 최신)으로 분리. read-only(commit 없음).
    """
    rows = await request_repo.list_change_bundles(session)
    bundles: list[tuple[LeaveRequest, LeaveRequest, Employee]] = []
    for _gid, group in groupby(rows, key=lambda re: re[0].change_group_id):
        members = list(group)  # (req, emp) — 이미 created_at ASC 정렬
        original, emp = members[0]
        reapplication = members[-1][0]
        bundles.append((original, reapplication, emp))
    return bundles


async def request_change(
    session: AsyncSession, employee: Employee, original_id: UUID, payload: ErpIntakeIn
) -> tuple[LeaveRequest, LeaveRequest]:
    """변경 요청(본인) → (원건, 재신청). 원건 묶음 표시 + ERP 폼 재신청 생성 = 한 트랜잭션.

    원건이 본인 아니면 403·없으면 404·`신청됨`/`승인됨` 아니면 409(취소 게이트 동형)·이미 변경
    묶음이면 409(이중 변경 차단). **원건은 직접 수정 X**(상태 유지) — 새 `change_group_id` 만 set,
    실제 취소는 HR 변경 승인 시점. 재신청은 `build_erp_request`(SPEC-004 ERP 폼 채널, `신청됨`).
    """
    original = await request_repo.get_by_id(session, original_id)
    if original is None:
        raise NotFoundError("신청을 찾을 수 없습니다")
    if original.employee_id != employee.id:
        raise ForbiddenError("본인 신청만 변경할 수 있습니다")
    if original.change_group_id is not None:
        raise ConflictError("이미 변경 처리 중인 신청입니다")
    if original.status not in _CHANGEABLE:
        raise ConflictError("변경할 수 없는 상태입니다")

    group_id = uuid4()
    original.change_group_id = group_id  # 묶음 표시(상태 유지 — 취소는 HR 승인 시점)

    reapplication = await leave_intake.build_erp_request(session, employee.id, payload)
    reapplication.change_group_id = group_id  # 동일 묶음 = "변경" 한 항목

    await session.flush()
    await session.commit()
    await session.refresh(original)
    await session.refresh(reapplication)
    return original, reapplication


async def _load_pending_bundle(
    session: AsyncSession, change_group_id: UUID
) -> tuple[LeaveRequest, LeaveRequest]:
    """변경 승인/반려 대상 로드 + 게이트 → (원건, 재신청). 없으면 404 · 이미 처리분 409.

    `get_change_group` 전 행(생성순) → 원건=첫행·재신청=막행. 게이트 순서가 중요:
    - 행 0건(존재하지 않는 묶음) → 404.
    - 재신청(막행)이 `신청됨` 이 아니면 → 409(이미 승인/반려 처리). **status 우선** — 반려 후 원건은
      묶음에서 떨어져(change_group_id NULL) 재신청 1건만 남으므로, 재승인/재반려 모두 409 로 일관.
    - pending(재신청 `신청됨`)인데 원건이 없으면(<2) → 404 방어(정상 흐름엔 늘 2건).
    """
    rows = await request_repo.get_change_group(session, change_group_id)
    if not rows:
        raise NotFoundError("변경 묶음을 찾을 수 없습니다")
    reapplication = rows[-1]
    if reapplication.status != RequestStatus.REQUESTED:
        raise ConflictError("이미 처리된 변경입니다")
    if len(rows) < 2:
        raise NotFoundError("변경 묶음을 찾을 수 없습니다")
    return rows[0], reapplication


async def approve_change(
    session: AsyncSession, hr: Employee, change_group_id: UUID
) -> tuple[LeaveRequest, LeaveRequest]:
    """HR 변경 승인 → (원건=취소됨, 재신청=승인됨). 원건 취소 + 재신청 승인 = **한 트랜잭션**.

    원건 취소: `승인됨`이면 `_restore_original_lots` 로 원-lot 복원(만료 lot=만료소멸), `신청됨`이면
    차감 전이라 복원 없음 — 둘 다 `취소됨`+`deleted_at`(soft delete). 재신청 승인: `apply_fefo_charge`
    FEFO 차감. 둘 중 하나라도 실패하면 commit 미도달 → 함께 롤백(원자성). 비-HR 403(router).
    없음 404·이미 처리 409.
    """
    original, reapplication = await _load_pending_bundle(session, change_group_id)
    now = datetime.now(UTC)

    # 1) 원건 취소(승인분이면 복원 — Phase 1 역산 그대로 소비)
    if original.status == RequestStatus.APPROVED:
        await leave_cancel._restore_original_lots(session, original.id, now)
    original.status = RequestStatus.CANCELLED
    original.deleted_at = now  # soft delete (hard delete 금지)
    original.approved_by = hr.id
    original.approved_at = now

    # 2) 재신청 승인(FEFO 차감 — WP-003 코어 그대로 소비). 실패 시 위 취소도 롤백.
    await leave_approval.apply_fefo_charge(session, reapplication, hr)

    await session.flush()
    await session.commit()
    await session.refresh(original)
    await session.refresh(reapplication)
    return original, reapplication


async def reject_change(
    session: AsyncSession, hr: Employee, change_group_id: UUID, reason: str
) -> tuple[LeaveRequest, LeaveRequest]:
    """HR 변경 반려 → (원건 유지, 재신청 폐기). 원건 차감·복원 불변. 비-HR 403(router).

    재신청 → `반려됨`+`reject_reason`(공백/누락은 RejectIn 스키마 422). 원건은 `승인됨`/`신청됨`
    그대로 두고 **`change_group_id` 해제** → 일반 신청/사용 흐름 복귀(묶음에서 떼어 큐 누수 방지).
    없음 404·이미 처리 409.
    """
    original, reapplication = await _load_pending_bundle(session, change_group_id)
    now = datetime.now(UTC)

    reapplication.status = RequestStatus.REJECTED
    reapplication.reject_reason = reason
    reapplication.approved_by = hr.id
    reapplication.approved_at = now

    original.change_group_id = None  # 묶음 해제 — 원건 유지·일반 큐 복귀(차감/복원 불변)

    await session.flush()
    await session.commit()
    await session.refresh(original)
    await session.refresh(reapplication)
    return original, reapplication
