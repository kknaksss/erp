"""HR 상세 연차 현황 스키마 — SPEC-003 §상세(HR 임의 직원 조회). WP-005 Phase 3 (BE).

HR 이 임의 직원의 **종류별 잔여 + 전체 + 사용/부여/조정 이력**을 열람하는 응답 계약. 잔여는
derive(active lot 합 ± adjustment delta 합), 이력은 4 테이블 union derived view(`ledger`)를
그대로 노출 — 새 집계 발명 없음. 정본 = spec-003 §상세·§API 계약·40-architecture/domains/
(ledger derive·balance).
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import LeaveCategory


class EmployeeIdentityOut(BaseModel):
    """조회 대상 직원 식별 — 누구 현황인지(id/name/email/department). ORM Employee → 직렬화."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: str
    department: str | None


class LedgerEntryOut(BaseModel):
    """연차관리기록 1건 — ledger derived view 행(발생/부여/이월·신청·사용·조정 시계열).

    `category`/`entry_type`/`detail` 은 ledger union 에서 Text 로 통일된 값(enum 객체 아님).
    `amount` 부호 그대로(사용/음수 delta 등 해석은 표시단 FE). occurred_at ASC 정렬.
    """

    entry_type: str  # 발생/HR부여/이월/신청/사용/조정
    occurred_at: datetime
    category: str  # 연차/Off Day/보상/포상 (Text)
    amount: Decimal
    detail: str | None
    ref_id: UUID


class EmployeeLeaveDetailOut(BaseModel):
    """HR 상세 연차 현황 — 직원 식별 + 종류별 잔여 + 전체 + 이력.

    `balances` = 4 종류(연차/Off Day/보상/포상) 각각(독립·교환 불가·**음수 허용**). `total` =
    4 합산 표시값(교환 불가). `ledger` = 사용/부여/조정 시계열. FE Phase 3(T-023)가 소비.
    """

    employee: EmployeeIdentityOut
    balances: dict[LeaveCategory, Decimal]
    total: Decimal  # 전체 = 4 종류 합산 표시값(음수 허용)
    ledger: list[LedgerEntryOut]
