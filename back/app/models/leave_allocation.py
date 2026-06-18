"""leave_allocation — 승인 신청이 어느 lot 에서 얼마 차감했나(FEFO 기록·복원 근거).

정본 = 40-architecture/domains/leave_allocation.md §Schema/§Indexes. append-only → created_at
만(updated_at 없음 — TimestampMixin 미사용). WP-002 Phase 1 = 스키마만(차감 = WP-003 / 복원 = WP-004).

**만료소멸 표시 컬럼 결정(T-018 — 코드 SoT 위임)**: `expired_at timestamptz NULL`.
- 취소 복원 시 원 lot 이 이미 만료(`use_date <= expiry_date` 날짜 없음)라 복원 불가하면
  `restored_at` NULL 유지 + `expired_at` 에 만료소멸 판정 시각 기록(다른 lot 이전 안 함).
- `restored_at` 와 대칭(둘 다 nullable ts, 상호배타) — 복원/만료소멸 중 정확히 하나만 set.
- 선택 이유: 사유 텍스트보다 queryable·audit 명확, restored_at in-place 패턴과 일관.
  WP-004(취소 복원)가 이 컬럼을 소비(복원 불가 시 set).
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk


class LeaveAllocation(Base):
    __tablename__ = "leave_allocation"

    id: Mapped[UUID] = uuid_pk()
    request_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leave_request.id"), nullable=False
    )
    grant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leave_grant.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    # 취소 복원 시각 — 복원되면 NOT NULL (in-place, T-017)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 만료소멸 판정 시각 — 복원 불가(만료) 사실 기록 (T-018, 코드 SoT 결정)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # 한 신청이 쪼갠 lot 들 조회(복원 역산)
        Index("ix_leave_allocation_request", "request_id"),
        # lot 별 사용 추적(잔여 derive 교차검증)
        Index("ix_leave_allocation_grant", "grant_id"),
    )
