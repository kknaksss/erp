"""leave_request — 연차 사용 신청 1건(= 하루치). 상태 전이 주체.

정본 = 40-architecture/domains/leave_request.md §Schema/§Indexes. WP-002 Phase 1 = 스키마만
(intake = SPEC-004/WP-003, 취소·변경·soft delete = SPEC-005/WP-004). 조건부 NN(am_pm/reject_reason
/deleted_at)은 P2/P3 business invariant — Phase 1 컬럼은 domains 의 nullable 그대로.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk
from app.models.enums import (
    AmPm,
    LeaveCategory,
    LeaveUnit,
    RequestChannel,
    RequestStatus,
    am_pm_enum,
    leave_category_enum,
    leave_unit_enum,
    request_channel_enum,
    request_status_enum,
)


class LeaveRequest(Base, TimestampMixin):
    __tablename__ = "leave_request"

    id: Mapped[UUID] = uuid_pk()
    employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employee.id"), nullable=False
    )
    category: Mapped[LeaveCategory] = mapped_column(leave_category_enum, nullable=False)
    unit: Mapped[LeaveUnit] = mapped_column(leave_unit_enum, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    # 반차·반반차만 NOT NULL · 전일 NULL (조건부 NN = invariant, 컬럼은 nullable)
    am_pm: Mapped[AmPm | None] = mapped_column(am_pm_enum, nullable=True)
    use_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[RequestStatus] = mapped_column(request_status_enum, nullable=False)
    channel: Mapped[RequestChannel] = mapped_column(request_channel_enum, nullable=False)
    approved_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employee.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 원건↔재신청 변경 단위 연결(동일 그룹 공유). self-ref parent 아님 (T-017)
    change_group_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    # soft delete — 취소 확정 시 NOT NULL (hard delete 금지)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 본인 "내 신청" 목록
        Index("ix_leave_request_emp_status", "employee_id", "status"),
        # HR 처리 대기 큐 (partial)
        Index(
            "ix_leave_request_pending",
            "status",
            postgresql_where=text("status IN ('신청됨', '취소요청됨')"),
        ),
        # 본인 사용 이력
        Index("ix_leave_request_emp_use_date", "employee_id", "use_date"),
    )
