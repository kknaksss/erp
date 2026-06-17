"""employee — ERP 직원. mediness `users` 미러 + ERP 소유 필드.

정본 계약 = ERP-SPEC-002(roster), 스키마 정본 = 40-architecture/domains/employee.md.
신원 origin 은 mediness(단방향 미러). `id` = mediness `users.id`(연결키·토큰 `sub`) —
mediness 로의 FK 아님(별도 DB). `password_hash` 없음(인증 위임, SPEC-001).
"""

from uuid import UUID

from sqlalchemy import Boolean, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Employee(Base, TimestampMixin):
    __tablename__ = "employee"

    # mediness users.id 그대로 (연결키). default 없음 — origin 이 mediness.
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)

    # mediness 미러 (로그인 lazy /auth/me · admin 동기 /admin/users 에서 갱신)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str | None] = mapped_column(Text, nullable=True)  # admin | member
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ERP 소유 (동기에서 보존 — 미러로 덮어쓰지 않음). position enum = mediness 8값 재사용(값은 ERP 입력)
    position: Mapped[str | None] = mapped_column(Text, nullable=True)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
