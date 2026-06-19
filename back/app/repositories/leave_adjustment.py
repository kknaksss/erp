"""leave_adjustment repository — 조정 row 생성(write path). WP-005 Phase 2.

순수 insert(flush 까지 — commit 은 호출 service, WP-001 레이어 컨벤션). **append-only** — 기존
row 수정/삭제 없음. 잔여 derive 의 합산(`grant_repo.sum_adjustment_delta`)이 이 row 의 delta 를
읽으므로, row 생성만으로 `leave_balance.category_balance` 에 자동 반영된다(별도 잔여 처리 금지).
정본 = 40-architecture/domains/leave_adjustment.md §Schema/§Invariant.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import LeaveCategory
from app.models.leave_adjustment import LeaveAdjustment


async def create_adjustment(
    session: AsyncSession,
    *,
    employee_id: UUID,
    category: LeaveCategory,
    delta: Decimal,
    adjusted_by: UUID,
    adjusted_at: datetime,
    reason: str | None = None,
) -> LeaveAdjustment:
    """조정 1행 insert(flush 까지, commit 은 호출 service). append-only — 기존 row 불변.

    `created_at` 은 service 가 결정한 `adjusted_at`(한 요청 내 전 항목 공유)을 명시 set 한다 —
    server_default(func.now()) 에 의존하지 않음(expire_on_commit=False 라 commit 후 in-memory
    값이 None 으로 남는 것 회피, P1 granted_at 패턴).
    """
    row = LeaveAdjustment(
        employee_id=employee_id,
        category=category,
        delta=delta,
        reason=reason,
        adjusted_by=adjusted_by,
        created_at=adjusted_at,
    )
    session.add(row)
    await session.flush()
    return row
