"""연차 라우터 — intake 2채널(SPEC-004) + 본인 조회(SPEC-003 §API). WP-003 Phase 1.

- POST /leave/intake/slack — ① Slack 워크플로우 webhook(공개 수신, 바디 공유 시크릿 토큰 검증).
- POST /leave/intake      — ② ERP 신청 폼(로그인 본인, sub 식별).
- GET  /leave/me          — 본인 종류별 잔여+만료 안내+이력(P3 FE 가 소비, 본인 스코프).

승인/반려(P2)·취소·변경(WP-004)·HR 부여(WP-005)는 이 라우터 범위 밖. intake service 가 생성 후
commit(roster/leave_balance 와 동일 — service-commits 컨벤션), 라우터는 응답 매핑만.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_employee, get_db
from app.models.employee import Employee
from app.schemas.leave_request import (
    ErpIntakeIn,
    ExpiringLotOut,
    LeaveRequestOut,
    LeaveSelfOut,
    SlackIntakeIn,
)
from app.services import leave_intake, leave_self

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
