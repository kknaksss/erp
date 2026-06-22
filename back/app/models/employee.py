"""employee — ERP 직원. 전 필드 ERP 소유(origin).

정본 계약 = ERP-SPEC-002(직원 관리 origin), 스키마 정본 = 40-architecture/domains/employee.md.
ERP 가 직원 정보(이름·부서·직급·role·재직 등)를 완전 소유한다 — mediness 와 sync 하지 않는다
(pull·미러·동기 보존 로직 없음). 공유하는 것은 로그인 계정뿐이다. `id` = mediness 발급 계정 id
채택(로그인 연계키·토큰 `sub`) — mediness 로의 FK 아님(별도 DB). `password_hash` 없음(인증 위임, SPEC-001).
"""

from datetime import date
from uuid import UUID

from sqlalchemy import Boolean, Date, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import EmploymentType, employment_type_enum


class Employee(Base, TimestampMixin):
    __tablename__ = "employee"

    # mediness 발급 계정 id 채택 (로그인 연계키). default 없음 — 생성 시 provisioning 응답 id 주입(P3).
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)

    # ERP 소유 (origin). email = 로그인 아이디. role = admin | member (mediness 와 갈라져도 무방).
    email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="member")  # admin | member
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ERP 소유 (origin). position enum 8값(ceo~staff), department 단일 text(영문 코드, HR 판정="hr").
    position: Mapped[str] = mapped_column(Text, nullable=False, server_default="staff")
    department: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ERP 소유 HR 필드 (T-015 — 연차 대장 마이그레이션 준비). 전부 nullable(기존 행·값 없이 존재 가능).
    hire_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # 입사일
    resigned_at: Mapped[date | None] = mapped_column(Date, nullable=True)  # 퇴사일(재직중=NULL)
    employment_type: Mapped[EmploymentType | None] = mapped_column(employment_type_enum, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    corporate_card_no: Mapped[str | None] = mapped_column(Text, nullable=True)
