"""HR 벌크 부여 스키마 — SPEC-003 §부여(HR 벌크) + §5 Acceptance Criteria.

입력은 구조만 검증(대상 비어있지 않음). 종류 게이트·일수>0·보상/포상 만료 필수·Off Day
default(0.5·그달 말일)·대상 직원 존재/active 는 service 가 판정한다(house 패턴 = service-side
validation, leave_intake 와 동일). enum 은 한글 value 직렬화(category="보상"). 정본 =
spec-003 §부여·§API 계약·40-architecture/domains/leave_grant.md §Schema/§Invariant.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import GrantSource, LeaveCategory


class BulkGrantIn(BaseModel):
    """HR 벌크 부여 입력 — 대상 직원 다건 + 종류 + 일수·만료일·사유.

    `employee_ids` 빈 리스트 → 422(min_length=1). `amount`·`expiry_date` 는 Off Day default
    (미지정 시 0.5·그달 말일)를 service 가 채우므로 Optional. 보상/포상은 service 가 필수 강제.
    """

    employee_ids: list[UUID] = Field(min_length=1)  # FE 가 부서 필터/전체 선택으로 추려 보냄
    category: LeaveCategory  # 종류 게이트(보상/포상/Off Day)는 service — 연차 거부
    amount: Decimal | None = None  # 일수(>0). Off Day 미지정 시 0.5
    expiry_date: date | None = None  # 만료일. 보상/포상 필수 · Off Day 미지정 시 그달 말일
    reason: str | None = None  # 부여 사유(선택 — SPEC §부여 입력, 누락 거부 규칙 없음)

    model_config = ConfigDict(str_strip_whitespace=True)


class BulkGrantOut(BaseModel):
    """벌크 부여 결과 요약 — 대상 수·종류·일수·만료일·생성 lot 수 + audit(부여자/시각).

    FE Phase 3(상세·관리 화면)·P2(연차수 조정)가 소비할 부여 결과 계약. 생성 lot 은 각 대상
    1건이라 `lot_count == target_count`(= dedup 후 대상 수). source 는 항상 `HR부여`.
    """

    target_count: int  # 부여 대상 직원 수(dedup 후)
    category: LeaveCategory
    amount: Decimal  # 적용된 일수(Off Day default 반영분 포함)
    expiry_date: date  # 적용된 만료일(Off Day default 반영분 포함)
    reason: str | None
    source: GrantSource  # = HR부여
    granted_by: UUID  # 부여 HR
    granted_at: datetime
    lot_count: int  # 생성된 leave_grant lot 수(= target_count)
