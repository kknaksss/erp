"""employee 스키마 — 직원 관리 CRUD(SPEC-002 §3, origin).

외부 노출 필드 = 전부 ERP 소유(`id`·`email`·`name`·`role`·`active`·`position`·`department`)
+ timestamps. `password_hash` 등 비노출 필드 없음(애초에 모델에 없음).
생성/수정 입력은 role(2값)·position(8값)·department(5값)을 Literal 로 검증(미허용 값 422).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# role(권한 2값)·position(직급 8값)·department(부서 5값) — SPEC-002 §Lifecycle enum.
# 모델은 Text 저장, 입력만 Literal 검증. department 저장값=영문 코드(FE 라벨=인사/개발/기획/QA/C레벨), HR 게이트=`hr`.
Role = Literal["admin", "member"]
Position = Literal["ceo", "coo", "cmo", "cto", "po", "manager", "leader", "staff"]
Department = Literal["hr", "dev", "planning", "qa", "clevel"]


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


class EmployeeCreate(BaseModel):
    """직원 생성 입력 — `{name,email,department,position,role}`(SPEC-002 U-1).

    email = 로그인 아이디(provisioning push 대상). 미허용 role/position·이메일 형식 위반·빈 값 422.
    """

    name: str = Field(min_length=1)
    email: EmailStr
    department: Department
    position: Position
    role: Role


class EmployeeUpdate(BaseModel):
    """직원 수정 입력 — 이름·부서·직급·role(ERP-local, email 변경 불가·디커플). PATCH 부분 갱신.

    제공된 필드만 반영(exclude_unset). 빈 값·미허용 role/position 422. email 필드는 무시(불변).
    """

    name: str | None = Field(default=None, min_length=1)
    department: Department | None = None
    position: Position | None = None
    role: Role | None = None


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
