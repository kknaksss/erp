"""leave_grant — 연차 부여 단위(lot). 종류별 잔여의 SoT.

정본 = 40-architecture/domains/leave_grant.md §Schema/§Indexes. WP-002 Phase 1 = 스키마만
(발생/이월 source 생성 = P2, 잔여 derive·FEFO = P3). 컬럼/타입/제약/nullable 은 domains 그대로.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk
from app.models.enums import (
    GrantSource,
    GrantStatus,
    LeaveCategory,
    grant_source_enum,
    grant_status_enum,
    leave_category_enum,
)


class LeaveGrant(Base, TimestampMixin):
    __tablename__ = "leave_grant"

    id: Mapped[UUID] = uuid_pk()
    employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employee.id"), nullable=False
    )
    category: Mapped[LeaveCategory] = mapped_column(leave_category_enum, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    remaining: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    # 연차 = NULL(무만료) · 보상/포상/Off Day = NOT NULL (조건부 NN 은 P2/P3 invariant — 컬럼은 nullable)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[GrantSource] = mapped_column(grant_source_enum, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 발생·이월(시스템 부여) = NULL · HR 벌크 부여 = NOT NULL
    granted_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employee.id"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[GrantStatus] = mapped_column(grant_status_enum, nullable=False)

    __table_args__ = (
        # FEFO 차감 후보(만료 임박순)
        Index("ix_leave_grant_emp_cat_expiry", "employee_id", "category", "expiry_date"),
        # 종류별 active lot 잔여 합산
        Index("ix_leave_grant_emp_cat_status", "employee_id", "category", "status"),
        # FEFO 소진 후보 좁힘 (partial)
        Index(
            "ix_leave_grant_active_remaining",
            "employee_id",
            "category",
            postgresql_where=text("status = 'active' AND remaining > 0"),
        ),
    )
