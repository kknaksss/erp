"""본인 연차 조회 — 4종류+전체 잔여 · 보상/포상 만료 안내 · 본인 이력 (WP-003 Phase 1).

정본 = SPEC-003 §API(본인 연차 조회) + SPEC-004 §본인 조회. WP-002 산물 재사용:
`leave_balance`(잔여 derive)·`leave_grant`(만료 lot)·`leave_request`(이력). **본인 스코프만** —
employee_id 로만 조회해 타인 기록 비노출을 구조적으로 보장(필터 누락 위험 없음).
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import LeaveCategory
from app.models.leave_grant import LeaveGrant
from app.models.leave_request import LeaveRequest
from app.repositories import leave_grant as grant_repo
from app.repositories import leave_request as request_repo
from app.services import leave_balance

# 만료 안내 대상 = 유효기간 종류(보상·포상). Off Day(그달 말일 소멸)는 §본인 조회 안내 범위 밖(P1).
_EXPIRING_CATEGORIES = [LeaveCategory.COMP, LeaveCategory.REWARD]


async def overview(
    session: AsyncSession, employee_id: UUID
) -> tuple[dict[LeaveCategory, object], object, list[LeaveGrant], list[LeaveRequest]]:
    """본인 (잔여 4종류, 전체, 만료 lot, 이력) 묶음. 전부 employee_id 스코프(본인만)."""
    balances = await leave_balance.category_balances(session, employee_id)
    total = await leave_balance.total_balance(session, employee_id)
    expiring = await grant_repo.expiring_lots(session, employee_id, _EXPIRING_CATEGORIES)
    history = await request_repo.list_for_employee(session, employee_id)
    return balances, total, expiring, history
