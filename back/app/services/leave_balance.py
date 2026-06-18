"""종류별 잔여 derive + valid-lot/FEFO 후보 + 만료 처리 + ledger (WP-002 Phase 3, read-path).

정본 = work-002 §Phase 3 · spec-003 §잔여 모델/§5 · 40-architecture/domains/leave_grant.md
§Invariant. 이 모듈은 **읽기 경로만** — 실제 차감(allocation 생성)=WP-003, 복원=WP-004, HR
부여/조정 쓰기=WP-005, 트리거 배선=WP-005 는 손대지 않는다.

- 종류별 잔여 = 해당 category **active lot `remaining` 합 ± 해당 category adjustment delta 합**
  (단일 balance 컬럼 없음·**음수 허용**). 4 종류(연차/보상/포상/Off Day)는 독립·교환 불가.
  `전체` = 4 합산 **표시값**일 뿐(단일 교환 잔여 아님).
- FEFO 후보 = leave_grant 단일 규칙(repository `valid_lots_fefo`) — 차감은 WP-003 이 소비.
- 만료 처리 = `expiry_date` 경과 lot → `status=expired`(repository `expire_lapsed_lots`).
- ledger = 4 테이블 union derived view(repository `ledger`).
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import LeaveCategory
from app.models.leave_grant import LeaveGrant
from app.repositories import ledger as ledger_repo
from app.repositories import leave_grant as grant_repo


async def category_balance(
    session: AsyncSession, employee_id: UUID, category: LeaveCategory
) -> Decimal:
    """한 종류 잔여 = active lot remaining 합 ± 해당 category adjustment delta 합. 음수 허용.

    expired lot 은 `sum_category_remaining`(active 만)에서 제외된다 → 만료분 자동 미합산.
    """
    remaining = await grant_repo.sum_category_remaining(session, employee_id, category)
    delta = await grant_repo.sum_adjustment_delta(session, employee_id, category)
    return remaining + delta


async def category_balances(
    session: AsyncSession, employee_id: UUID
) -> dict[LeaveCategory, Decimal]:
    """4 종류(연차/보상/포상/Off Day) 각각의 잔여. 종류 독립·교환 불가."""
    return {
        category: await category_balance(session, employee_id, category)
        for category in LeaveCategory
    }


async def total_balance(session: AsyncSession, employee_id: UUID) -> Decimal:
    """`전체` = 4 종류 잔여 합산 **표시값**(교환 불가 — 표시 전용, 단일 잔여 아님)."""
    balances = await category_balances(session, employee_id)
    return sum(balances.values(), Decimal(0))


async def fefo_candidates(
    session: AsyncSession,
    employee_id: UUID,
    category: LeaveCategory,
    use_date: date,
) -> list[LeaveGrant]:
    """valid lot FEFO 후보(만료 임박순·NULL 최후미) — 차감 자체는 WP-003 이 소비.

    단일 규칙 `(expiry_date IS NULL OR use_date <= expiry_date) AND remaining > 0`. 종류 고정.
    """
    return await grant_repo.valid_lots_fefo(session, employee_id, category, use_date)


async def expire_lapsed_lots(session: AsyncSession, today: date) -> int:
    """만료일 경과 lot → `status=expired`(잔여 합산 제외). 반환 전환 행 수. 멱등.

    `연차`(무만료)는 전환 안 됨. 트리거 배선(cron/endpoint)은 WP-005 — 여기선 service 함수만.
    """
    expired = await grant_repo.expire_lapsed_lots(session, today)
    await session.commit()
    return expired


async def ledger(session: AsyncSession, employee_id: UUID) -> list[dict]:
    """연차관리기록 시계열 = grant/request/allocation/adjustment 4 테이블 union derived view."""
    return await ledger_repo.ledger_entries(session, employee_id)
