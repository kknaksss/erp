"""연차 intake/조회 스키마 — SPEC-004 폼 필드 + SPEC-003 §API(본인 조회).

enum 필드는 한글 value 로 직렬화/파싱(예: `category="연차"`). amount 는 서버 derive(클라이언트
입력 무시 — unit↔amount invariant) 라 입력 스키마에 두지 않는다. 정본 = spec-004 §폼 필드,
40-architecture/domains/leave_request.md §Schema.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AmPm, LeaveCategory, LeaveUnit, RequestChannel, RequestStatus


class _FormFields(BaseModel):
    """두 채널 공통 폼 필드 — 종류·단위·(오전/오후)·사용날짜·비고(SPEC-004 §폼 필드)."""

    category: LeaveCategory
    unit: LeaveUnit
    am_pm: AmPm | None = None  # 반차·반반차 필수 / 전일 None (service invariant)
    use_date: date
    note: str | None = None


class SlackIntakeIn(_FormFields):
    """① Slack 워크플로우 webhook 수신 — 제출자 email + 공유 시크릿 토큰 + 제출 타임스탬프."""

    email: str  # 제출자 email → employee.email 매핑(이름 매칭 금지)
    timestamp: datetime  # 제출 시각 = dedup 기준(created_at)
    token: str  # 공유 시크릿 — settings 와 대조(불일치 → 미생성)


class ErpIntakeIn(_FormFields):
    """② ERP 신청 폼 — 로그인 토큰 sub 로 신청자 식별(email·토큰 불요)."""


class LeaveRequestOut(BaseModel):
    """생성·이력 노출 신청 표현 — 상태·채널·derive 된 amount 포함."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category: LeaveCategory
    unit: LeaveUnit
    amount: Decimal
    am_pm: AmPm | None
    use_date: date
    note: str | None
    status: RequestStatus
    channel: RequestChannel
    created_at: datetime


class ExpiringLotOut(BaseModel):
    """보상/포상 만료 안내 1건 — 종류·남은량·만료일(SPEC-004 §본인 조회)."""

    model_config = ConfigDict(from_attributes=True)

    category: LeaveCategory
    remaining: Decimal
    expiry_date: date


class LeaveSelfOut(BaseModel):
    """본인 연차 조회 — 4종류+전체 잔여 · 만료 안내 · 본인 이력(SPEC-003 §API 본인 조회)."""

    balances: dict[LeaveCategory, Decimal]  # 4 종류 각각(독립·교환 불가)
    total: Decimal  # `전체` = 4 합산 표시값
    expiring: list[ExpiringLotOut]  # 보상/포상 유효기간 lot
    history: list[LeaveRequestOut]  # 본인 신청/사용 이력(상태 포함)


# ---- HR 승인/반려 (WP-003 Phase 2) ---------------------------------------


class PendingRequestOut(BaseModel):
    """HR 신청 큐 1건 — 신청 내용 + 신청자 식별(이름/email). `신청됨` 만(SPEC-003 §S-2)."""

    id: UUID
    employee_id: UUID
    employee_name: str  # 신청자 이름(큐 표시)
    employee_email: str
    category: LeaveCategory
    unit: LeaveUnit
    amount: Decimal
    am_pm: AmPm | None
    use_date: date
    note: str | None
    status: RequestStatus
    channel: RequestChannel
    created_at: datetime


class RejectIn(BaseModel):
    """반려 입력 — `reason` 필수(누락/공백 → 422, SPEC-003 §케이스 매트릭스 '반려 사유 누락')."""

    reason: str = Field(min_length=1)
    model_config = ConfigDict(str_strip_whitespace=True)


class ApprovalOut(BaseModel):
    """승인 결과 — 처리된 신청 + 차감 후 해당 종류 잔여 + 음수 경고 플래그(하드 차단 X)."""

    request: LeaveRequestOut
    balance: Decimal  # 차감 후 해당 category 잔여(음수 가능)
    warning: bool  # balance < 0 (SPEC-003 §케이스 매트릭스 음수 경고)


# ---- 취소·취소요청·취소승인/반려 (WP-004 Phase 1) -------------------------


class CancelIn(BaseModel):
    """개인 취소·취소요청 입력 — `reason` 선택(SPEC-005 §API 입력 = 신청 id, 사유는 옵션).

    `신청됨` 자유취소 / `승인됨` 취소요청 공용. cancel_reason 컬럼에 저장(누락 시 NULL).
    """

    reason: str | None = None
    model_config = ConfigDict(str_strip_whitespace=True)
