"""HR 연차수 조정 스키마 — SPEC-003 §연차수 조정(HR) + audit. WP-005 Phase 2.

한 직원의 잔여를 종류별로 **한 번에 다건** ± 보정. 입력은 구조만 검증(항목 비어있지 않음).
`delta != 0` · 대상 직원 존재/active 는 service 가 판정한다(house 패턴 = service-side
validation, leave_grant_ops 와 동일). `delta` 는 **음수 허용**(positivity 제약 없음 — 가감
모두). enum 은 한글 value 직렬화(category="연차"). 정본 = spec-003 §연차수 조정·§API 계약·
40-architecture/domains/leave_adjustment.md §Schema/§Invariant.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import LeaveCategory


class AdjustmentItemIn(BaseModel):
    """조정 1건 — 종류 + 증감 delta + 사유.

    `delta` 는 음수 가능(가감 모두)이라 schema 제약 없음. `delta != 0` 는 service 가 422 로
    거부(0 은 의미 없는 행). `reason` 은 선택(domains 모델 nullable — 누락 거부 규칙 없음).
    """

    category: LeaveCategory  # 4 종류(연차/Off Day/보상/포상) — Pydantic 이 enum 제약
    delta: Decimal  # ± 보정량. **음수 허용**(positivity 제약 없음). 0 거부는 service
    reason: str | None = None  # 보정 사유(선택)

    model_config = ConfigDict(str_strip_whitespace=True)


class LeaveAdjustmentIn(BaseModel):
    """연차수 조정 입력 — 한 직원 + 종류별 다건 조정(예: 연차 -1.0 + 보상 +0.5 동시).

    `items` 빈 리스트 → 422(min_length=1). 한 요청 = 한 트랜잭션(전체/롤백) — 항목 중 1건이라도
    위반이면 전원 미반영.
    """

    employee_id: UUID  # 조정 대상 직원(1명)
    items: list[AdjustmentItemIn] = Field(min_length=1)  # 종류별 다건


class AdjustmentResultItem(BaseModel):
    """조정 결과 1건 — 적용된 종류/delta/사유(audit 표시용)."""

    category: LeaveCategory
    delta: Decimal
    reason: str | None


class LeaveAdjustmentOut(BaseModel):
    """연차수 조정 결과 요약 — 직원·각 항목(category/delta) + 조정 후 종류별 잔여 + audit.

    FE Phase 3(상세·관리 화면)가 소비할 조정 결과 계약. `balances` = **조정된 종류만** 키로 하는
    조정 후 잔여(dedup — 같은 종류 다건이면 최종 잔여 1개). audit = 누가(`adjusted_by`)·언제
    (`adjusted_at`). 잔여는 derive(active lot 합 ± adjustment delta 합)라 본 조정이 자동 반영됨.
    """

    employee_id: UUID
    adjusted_by: UUID  # 조정한 HR(audit — 누가)
    adjusted_at: datetime  # 조정 시각(audit — 언제, 한 요청 내 전 항목 공유)
    items: list[AdjustmentResultItem]  # 적용된 조정 항목(요청 순서)
    balances: dict[LeaveCategory, Decimal]  # 조정된 종류별 조정 후 잔여(dedup)
