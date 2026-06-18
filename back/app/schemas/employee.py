"""employee 응답 스키마 — 직원 목록 조회(SPEC-002 §3 디렉토리).

외부 노출 필드 = 미러(`id`·`email`·`name`·`role`·`active`) + ERP 소유(`position`·`department`)
+ timestamps. `password_hash` 등 비노출 필드 없음(애초에 모델에 없음).
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # ORM Employee → 직렬화

    id: UUID
    email: str
    name: str
    role: str | None
    active: bool
    position: str | None
    department: str | None
    created_at: datetime
    updated_at: datetime


class MeOut(BaseModel):
    """내정보(self profile) — 로그인 본인 식별 + HR 여부. FE auth 컨텍스트의 단일 소스.

    `is_hr` = `department == "hr"` 를 **BE 가 계산**해 불리언으로 노출(FE 가 한글/코드 비교 안 하게).
    role(admin/member)·department 원값도 함께 — member-role HR 직원도 본인 department 를 안다.
    """

    id: UUID
    email: str
    name: str
    role: str | None
    department: str | None
    is_hr: bool
