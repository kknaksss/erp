"""연차 라우터 — intake 2채널(SPEC-004) + 본인 조회 + HR 승인/반려(SPEC-003 §API).

WP-003 Phase 1:
- POST /leave/intake/slack — ① Slack 워크플로우 webhook(공개 수신, 바디 공유 시크릿 토큰 검증).
- POST /leave/intake      — ② ERP 신청 폼(로그인 본인, sub 식별).
- GET  /leave/me          — 본인 종류별 잔여+만료 안내+이력(P3 FE 가 소비, 본인 스코프).

WP-003 Phase 2 (HR — `department == "인사"` 게이트):
- GET  /leave/admin/requests                  — 신청 큐(`신청됨` 전 직원 + 신청자).
- POST /leave/admin/requests/{id}/approve     — 승인 → 선택 종류 FEFO 차감(음수 허용·경고).
- POST /leave/admin/requests/{id}/reject      — 반려 → `반려됨`(사유 필수·차감 없음).

취소·변경(WP-004)·HR 부여(WP-005)는 이 라우터 범위 밖. service 가 생성/처리 후 commit
(roster/leave_balance 와 동일 — service-commits 컨벤션), 라우터는 응답 매핑만.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_employee, get_db, require_hr
from app.models.employee import Employee
from app.schemas.leave_request import (
    ApprovalOut,
    ErpIntakeIn,
    ExpiringLotOut,
    LeaveRequestOut,
    LeaveSelfOut,
    PendingRequestOut,
    RejectIn,
    SlackIntakeIn,
)
from app.services import leave_approval, leave_intake, leave_self

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
    return [
        PendingRequestOut(
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
        for req, emp in rows
    ]


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
