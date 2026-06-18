"""leave_adjustment — HR 연차수 조정(종류별 ± delta) + audit. append-only.

정본 = 40-architecture/domains/leave_adjustment.md §Schema/§Indexes. append-only audit →
created_at 만(updated_at 없음 — TimestampMixin 미사용). 잔여 derive 에 delta 가산(P3).
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk
from app.models.enums import LeaveCategory, leave_category_enum


class LeaveAdjustment(Base):
    __tablename__ = "leave_adjustment"

    id: Mapped[UUID] = uuid_pk()
    employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employee.id"), nullable=False
    )
    category: Mapped[LeaveCategory] = mapped_column(leave_category_enum, nullable=False)
    # ± 보정량. 음수 가능(가감 모두)
    delta: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    adjusted_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employee.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # 직원 종류별 조정 합산(잔여 derive)
        Index("ix_leave_adjustment_emp_cat", "employee_id", "category"),
    )
