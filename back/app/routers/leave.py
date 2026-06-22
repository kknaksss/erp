"""연차 라우터 — intake 2채널(SPEC-004) + 본인 조회 + HR 승인/반려(SPEC-003 §API).

WP-003 Phase 1:
- POST /leave/intake/slack — ① Slack 워크플로우 webhook(공개 수신, 바디 공유 시크릿 토큰 검증).
- POST /leave/intake      — ② ERP 신청 폼(로그인 본인, sub 식별).
- GET  /leave/me          — 본인 종류별 잔여+만료 안내+이력(P3 FE 가 소비, 본인 스코프).

WP-003 Phase 2 (HR — `department == "hr"` 게이트):
- GET  /leave/admin/requests                  — 신청 큐(`신청됨` 전 직원 + 신청자).
- POST /leave/admin/requests/{id}/approve     — 승인 → 선택 종류 FEFO 차감(음수 허용·경고).
- POST /leave/admin/requests/{id}/reject      — 반려 → `반려됨`(사유 필수·차감 없음).

WP-004 Phase 1 (취소 전이 + 원-lot 복원):
- POST /leave/requests/{id}/cancel             — 개인 취소(본인): 신청됨=자유취소·승인됨=취소요청.
- GET  /leave/admin/cancel-requests            — HR 취소 승인 큐(`취소요청됨`, 신청 큐와 별도).
- POST /leave/admin/requests/{id}/cancel-approve — 취소승인 → `취소됨`+soft delete+원-lot 복원.
- POST /leave/admin/requests/{id}/cancel-reject  — 취소반려 → `승인됨` 복귀(사유 필수·휴가 유지).

WP-005 Phase 1 (HR 벌크 부여 — `department == "hr"` 게이트):
- POST /leave/admin/grants — 보상/포상/Off Day 를 다중 직원에게 한 번에 부여(전체/롤백).

연차수 조정·상세 조회(WP-005 P2/P3)·FE 는 이 라우터 범위 밖. service 가 생성/처리 후
commit(roster/leave_balance 와 동일 — service-commits 컨벤션), 라우터는 응답 매핑만.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_employee, get_db, require_hr
from app.models.employee import Employee
from app.repositories import employee as employee_repo
from app.schemas.leave_adjustment import LeaveAdjustmentIn, LeaveAdjustmentOut
from app.schemas.leave_admin import (
    EmployeeIdentityOut,
    EmployeeLeaveDetailOut,
    LedgerEntryOut,
)
from app.schemas.employee import EmployeeOut
from app.schemas.leave_grant import BulkGrantIn, BulkGrantOut
from app.schemas.leave_request import (
    ApprovalOut,
    CancelIn,
    ChangeRequestOut,
    ChangeSideOut,
    ErpIntakeIn,
    ExpiringLotOut,
    LeaveRequestOut,
    LeaveSelfOut,
    PendingRequestOut,
    RejectIn,
    SlackIntakeIn,
)
from app.services import (
    leave_adjustment,
    leave_admin,
    leave_approval,
    leave_cancel,
    leave_change,
    leave_grant_ops,
    leave_intake,
    leave_self,
)


def _to_pending_out(req, emp) -> PendingRequestOut:
    """`신청됨`/`취소요청됨` 큐 행 → 신청 내용 + 신청자(이름/email) DTO(WP-003/004 공용)."""
    return PendingRequestOut(
        id=req.id,
        employee_id=req.employee_id,
        employee_name=emp.name,
        employee_email=emp.email,
        category=req.category,
        unit=req.unit,
        amount=req.amount,
        am_pm=req.am_pm,
        use_date=req.use_date,
        note=req.note,
        status=req.status,
        channel=req.channel,
        created_at=req.created_at,
    )


def _to_change_out(original, reapplication, emp) -> ChangeRequestOut:
    """(원건, 재신청, 신청자) → 변경 단위(묶음) DTO. `change_group_id`=재신청 측(반려도 보존)."""
    return ChangeRequestOut(
        change_group_id=reapplication.change_group_id,
        employee_id=emp.id,
        employee_name=emp.name,
        employee_email=emp.email,
        original=ChangeSideOut.model_validate(original),
        reapplication=ChangeSideOut.model_validate(reapplication),
    )

router = APIRouter(prefix="/leave", tags=["leave"])


@router.post("/intake/slack", response_model=LeaveRequestOut)
async def slack_intake(
    payload: SlackIntakeIn,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LeaveRequestOut:
    """① Slack webhook 수신 → `신청됨`(channel=slack). 토큰 불일치 401·email 미일치 404.

    재전송(동일 타임스탬프)은 dedup 으로 1건만 유지(직전 건 반환). 인증 미들웨어 없는 공개 수신 —
    출처 검증은 바디 공유 시크릿 토큰(service).
    """
    req = await leave_intake.create_from_slack(session, payload)
    return LeaveRequestOut.model_validate(req)


@router.post("/intake", response_model=LeaveRequestOut)
async def erp_intake(
    payload: ErpIntakeIn,
    employee: Annotated[Employee, Depends(get_current_employee)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LeaveRequestOut:
    """② ERP 폼 → `신청됨`(channel=erp). 로그인 본인(sub) 식별 — 토큰없음 401."""
    req = await leave_intake.create_from_erp(session, employee.id, payload)
    return LeaveRequestOut.model_validate(req)


@router.get("/me", response_model=LeaveSelfOut)
async def my_leave(
    employee: Annotated[Employee, Depends(get_current_employee)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LeaveSelfOut:
    """본인 종류별 잔여(4+전체)·보상/포상 만료 안내·본인 이력. 본인 스코프(타인 비노출)."""
    balances, total, expiring, history = await leave_self.overview(session, employee.id)
    return LeaveSelfOut(
        balances=balances,
        total=total,
        expiring=[ExpiringLotOut.model_validate(lot) for lot in expiring],
        history=[LeaveRequestOut.model_validate(req) for req in history],
    )


# ---- HR 승인/반려 (Phase 2 — require_hr) -----------------------------------


@router.get("/admin/requests", response_model=list[PendingRequestOut])
async def pending_requests(
    _hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[PendingRequestOut]:
    """신청 큐 = `신청됨` 전 직원 + 신청자(이름/email). 비-HR 403·토큰없음 401."""
    rows = await leave_approval.pending_queue(session)
    return [_to_pending_out(req, emp) for req, emp in rows]


@router.post("/admin/requests/{request_id}/approve", response_model=ApprovalOut)
async def approve_request(
    request_id: UUID,
    hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ApprovalOut:
    """승인 → 선택 종류 FEFO 차감(분할·음수 흡수). 없음 404·이미 처리 409·비-HR 403.

    **자기 승인 허용**(require_hr 가 신청자=본인을 막지 않음 — SPEC-003). 차감 후 잔여 음수면 warning.
    """
    req, balance, warning = await leave_approval.approve(session, hr, request_id)
    return ApprovalOut(
        request=LeaveRequestOut.model_validate(req), balance=balance, warning=warning
    )


@router.post("/admin/requests/{request_id}/reject", response_model=LeaveRequestOut)
async def reject_request(
    request_id: UUID,
    payload: RejectIn,
    hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LeaveRequestOut:
    """반려 → `반려됨` + 사유(필수, 누락/공백 422). 차감 없음. 없음 404·이미 처리 409·비-HR 403."""
    req = await leave_approval.reject(session, hr, request_id, payload.reason)
    return LeaveRequestOut.model_validate(req)


# ---- 취소·취소요청·취소승인/반려 (WP-004 Phase 1) -------------------------


@router.post("/requests/{request_id}/cancel", response_model=LeaveRequestOut)
async def cancel_request(
    request_id: UUID,
    payload: CancelIn,
    employee: Annotated[Employee, Depends(get_current_employee)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LeaveRequestOut:
    """개인 취소(본인) — `신청됨`=즉시 `취소됨`+soft delete / `승인됨`=`취소요청됨`(HR 큐).

    그 외 상태 재취소 409·타인 신청 403·없음 404·토큰없음 401. reason 선택(cancel_reason).
    """
    req = await leave_cancel.request_cancel(session, employee, request_id, payload.reason)
    return LeaveRequestOut.model_validate(req)


@router.get("/admin/cancel-requests", response_model=list[PendingRequestOut])
async def cancel_requests_queue(
    _hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[PendingRequestOut]:
    """HR 취소 승인 큐 = `취소요청됨` 전 직원 + 신청자. 신청 큐(`/admin/requests`)와 별도. 비-HR 403."""
    rows = await leave_cancel.cancel_queue(session)
    return [_to_pending_out(req, emp) for req, emp in rows]


@router.post("/admin/requests/{request_id}/cancel-approve", response_model=LeaveRequestOut)
async def approve_cancel_request(
    request_id: UUID,
    hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LeaveRequestOut:
    """취소승인 → `취소됨` + soft delete + 원-lot 복원(만료 lot=만료소멸).

    없음 404·`취소요청됨` 아니면 409(이중 복원 차단)·비-HR 403.
    """
    req = await leave_cancel.approve_cancel(session, hr, request_id)
    return LeaveRequestOut.model_validate(req)


@router.post("/admin/requests/{request_id}/cancel-reject", response_model=LeaveRequestOut)
async def reject_cancel_request(
    request_id: UUID,
    payload: RejectIn,
    hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LeaveRequestOut:
    """취소반려 → `승인됨` 복귀 + 사유(필수, 누락/공백 422). 휴가 유지(복원 없음).

    없음 404·`취소요청됨` 아니면 409·비-HR 403.
    """
    req = await leave_cancel.reject_cancel(session, hr, request_id, payload.reason)
    return LeaveRequestOut.model_validate(req)


# ---- 변경 = 취소 + 재신청 묶음 (WP-004 Phase 2) ---------------------------


@router.post("/requests/{request_id}/change", response_model=ChangeRequestOut)
async def change_request(
    request_id: UUID,
    payload: ErpIntakeIn,
    employee: Annotated[Employee, Depends(get_current_employee)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ChangeRequestOut:
    """변경 요청(본인) — 원건(`request_id`) 묶음 표시 + ERP 폼 재신청(`신청됨`) = 한 묶음.

    원건 `신청됨`/`승인됨` 만 변경(그 외 409)·타인 403·없음 404·이미 변경중 409·토큰없음 401.
    응답 = 변경 단위(`change_group_id` + 원건/재신청). 원건은 직접 수정 X(취소는 HR 승인 시점).
    """
    original, reapplication = await leave_change.request_change(
        session, employee, request_id, payload
    )
    return _to_change_out(original, reapplication, employee)


@router.get("/admin/change-requests", response_model=list[ChangeRequestOut])
async def change_requests_queue(
    _hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[ChangeRequestOut]:
    """HR 변경 큐 = pending 변경 묶음(원건+재신청 한 항목). 신청/취소 큐와 별도. 비-HR 403."""
    bundles = await leave_change.change_queue(session)
    return [_to_change_out(orig, reapp, emp) for orig, reapp, emp in bundles]


@router.post(
    "/admin/change-requests/{change_group_id}/approve", response_model=ChangeRequestOut
)
async def approve_change_request(
    change_group_id: UUID,
    hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ChangeRequestOut:
    """변경 승인(HR) — 원건 취소(승인분이면 원-lot 복원) + 재신청 승인(FEFO) **한 번에**(원자).

    없음 404·이미 처리 409(이중 처리 차단)·비-HR 403. 응답 = 원건(취소됨)+재신청(승인됨) 묶음.
    """
    original, reapplication = await leave_change.approve_change(session, hr, change_group_id)
    emp = await employee_repo.get_by_id(session, original.employee_id)
    return _to_change_out(original, reapplication, emp)


@router.post(
    "/admin/change-requests/{change_group_id}/reject", response_model=ChangeRequestOut
)
async def reject_change_request(
    change_group_id: UUID,
    payload: RejectIn,
    hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ChangeRequestOut:
    """변경 반려(HR) — 원건 유지(차감·복원 불변) + 재신청 폐기(`반려됨`+사유, 누락/공백 422).

    없음 404·이미 처리 409·비-HR 403. 응답 = 원건(유지)+재신청(반려됨) 묶음.
    """
    original, reapplication = await leave_change.reject_change(
        session, hr, change_group_id, payload.reason
    )
    emp = await employee_repo.get_by_id(session, original.employee_id)
    return _to_change_out(original, reapplication, emp)


# ---- HR 벌크 부여 (WP-005 Phase 1 — require_hr) ---------------------------


@router.post("/admin/grants", response_model=BulkGrantOut)
async def bulk_grant(
    payload: BulkGrantIn,
    hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> BulkGrantOut:
    """보상/포상/Off Day 를 다중 직원에게 한 번에 부여(전체/롤백). 비-HR 403·토큰없음 401.

    종류 게이트(연차 거부)·일수>0·보상/포상 만료 필수·Off Day default(0.5·그달 말일) 위반 422 ·
    미존재 대상 404 · 비활성 대상 422(부분 무시 없음). 빈 대상 리스트 422(스키마).
    """
    return await leave_grant_ops.bulk_grant(session, hr, payload)


# ---- HR 연차수 조정 (WP-005 Phase 2 — require_hr) -------------------------


@router.post("/admin/adjustments", response_model=LeaveAdjustmentOut)
async def adjust_leave(
    payload: LeaveAdjustmentIn,
    hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LeaveAdjustmentOut:
    """한 직원의 잔여를 종류별로 한 번에 ± 보정(전체/롤백) + audit. 비-HR 403·토큰없음 401.

    4 종류 전부 조정 대상(연차 포함). delta≠0(0 항목 422)·delta 음수 허용(결과 음수 잔여 허용,
    경고는 FE)·미존재 대상 404·비활성 대상 422·빈 항목 리스트 422(스키마). 잔여는 derive 가
    delta 를 합산해 자동 반영(이중 반영 없음).
    """
    return await leave_adjustment.adjust(session, hr, payload)


# ---- HR 직원목록 — 연차 운영 대상 선택 (WP-005 권한 갭 보강 — require_hr) -


@router.get("/admin/employees", response_model=list[EmployeeOut])
async def hr_employee_roster(
    _hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[Employee]:
    """HR 연차 운영(부여 대상·조정/상세 직원 선택)이 쓸 전 직원 명부(이름순). 비-HR 403·토큰없음 401.

    직원 관리 디렉토리(`/admin/employees`·SPEC-002)와 같은 require_hr 게이트·별 목적(연차 운영) —
    member-role HR 도 200(department=="hr"). 부서 필터 안 함(전체 반환·FE 가 client-side 추림,
    P1 벌크 부여 계약과 일관). 명부 쿼리·EmployeeOut 은 직원 디렉토리와 동일 소비(재정의 없음).
    """
    return await employee_repo.list_all(session)


# ---- HR 상세 연차 현황 — 임의 직원 (WP-005 Phase 3 BE — require_hr) -------


@router.get("/admin/employees/{employee_id}", response_model=EmployeeLeaveDetailOut)
async def employee_leave_detail(
    employee_id: UUID,
    _hr: Annotated[Employee, Depends(require_hr)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> EmployeeLeaveDetailOut:
    """HR 이 임의 직원의 종류별 잔여(4+전체) + 사용/부여/조정 이력 열람. 비-HR 403·토큰없음 401.

    미존재 직원 404(비활성은 조회 가능 — 이력 열람 목적). 음수 잔여도 그대로 노출(차단 X·경고는
    FE). read-only — 잔여 derive·ledger union view 를 employee_id 로 조회만(재정의 없음).
    """
    emp, balances, total, ledger = await leave_admin.employee_detail(session, employee_id)
    return EmployeeLeaveDetailOut(
        employee=EmployeeIdentityOut.model_validate(emp),
        balances=balances,
        total=total,
        ledger=[LedgerEntryOut.model_validate(entry) for entry in ledger],
    )
