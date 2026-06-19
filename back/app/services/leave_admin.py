"""HR 상세 연차 현황 service — 임의 직원의 종류별 잔여 + 전체 + 이력. WP-005 Phase 3 (BE).

SPEC-003 §상세(HR 임의 직원 조회) + §5 Acceptance Criteria(상세). `leave_self.overview`(본인
스코프)의 **임의 직원 버전** — 본인 한정 대신 HR 게이트(router `require_hr`)로 교체하고
employee_id 를 파라미터화한다. read-only(상태 변경 0)·migration 0.

**재사용(재정의 금지 — 이 task 핵심)**:
- `leave_balance.category_balances` — 종류별 잔여 4종(derive)
- `leave_balance.total_balance` — 전체 잔여(4 합산 표시값)
- `leave_balance.ledger` — 사용/부여/조정 이력(4 테이블 union derived view)
- `employee_repo.get_by_id` — 대상 직원 조회·존재 판정

새 집계 로직을 만들지 않는다 — 위 헬퍼를 employee_id 로 호출만 한다.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.employee import Employee
from app.models.enums import LeaveCategory
from app.repositories import employee as employee_repo
from app.services import leave_balance


async def employee_detail(
    session: AsyncSession, employee_id: UUID
) -> tuple[Employee, dict[LeaveCategory, Decimal], Decimal, list[dict]]:
    """임의 직원 (식별, 잔여 4종, 전체, 이력) 묶음. 미존재 404. 비활성도 조회 가능(이력 열람).

    잔여는 derive(P1 부여 lot·P2 조정 delta 반영·음수 허용 그대로), 이력은 ledger union view.
    HR 게이트는 router(require_hr) — 본 service 는 employee_id 스코프 read 만.
    """
    emp = await employee_repo.get_by_id(session, employee_id)
    if emp is None:
        raise NotFoundError(
            "존재하지 않는 직원입니다", detail={"employee_id": str(employee_id)}
        )
    balances = await leave_balance.category_balances(session, employee_id)
    total = await leave_balance.total_balance(session, employee_id)
    ledger = await leave_balance.ledger(session, employee_id)
    return emp, balances, total, ledger
