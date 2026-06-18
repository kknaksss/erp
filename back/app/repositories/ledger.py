"""연차관리기록(ledger) = 4 테이블 union **derived view**(별도 물리 테이블 없음).

정본 = 40-architecture/domains/leave_grant.md §Invariant("ledger = derived view") +
leave_adjustment.md §확정. 실대장의 "연차관리기록(날짜·성명·갯수·내용)" 시계열은 grant/
request/allocation/adjustment 4 테이블이 이미 근거이므로, 중복 SoT 를 두지 않고 union 으로
derive 한다. **구현 방식 = repository union 쿼리**(코드 SoT — SQL view + migration 대신, view
추가 없이 코드로 도출해 migration 0개·"1 task = 1 commit"). 한 직원으로 스코프.

entry_type 매핑(시계열 entry 종류):
- grant  → `source` 값 그대로(`발생`/`HR부여`/`이월`) — 발생·HR 벌크 부여·이월 실현.
- request→ `신청`(사용 신청 1건. status 는 detail).
- allocation → `사용`(승인 신청이 lot 에서 실제 차감한 기록 = 사용 실현). request `신청` 과
  쌍 — 신청(요청)과 사용(차감 확정)은 별 entry 다(double-list 아님, 4 테이블 union 정의).
- adjustment → `조정`(HR 종류별 ± 보정. amount = delta).
"""

from uuid import UUID

from sqlalchemy import Text, cast, literal, null, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leave_adjustment import LeaveAdjustment
from app.models.leave_allocation import LeaveAllocation
from app.models.leave_grant import LeaveGrant
from app.models.leave_request import LeaveRequest


async def ledger_entries(session: AsyncSession, employee_id: UUID) -> list[dict]:
    """직원의 연차관리기록 시계열(발생/부여/이월·신청·사용·조정), occurred_at ASC.

    각 entry = {entry_type, occurred_at, category, amount, detail, ref_id}. 컬럼 타입을 union
    호환되게 통일(enum→Text cast). amount 부호는 그대로(사용/음수 delta 등 해석은 표시단).
    """
    grant = select(
        cast(LeaveGrant.source, Text).label("entry_type"),
        LeaveGrant.granted_at.label("occurred_at"),
        cast(LeaveGrant.category, Text).label("category"),
        LeaveGrant.amount.label("amount"),
        LeaveGrant.reason.label("detail"),
        LeaveGrant.id.label("ref_id"),
    ).where(LeaveGrant.employee_id == employee_id)

    request = select(
        literal("신청").label("entry_type"),
        LeaveRequest.created_at.label("occurred_at"),
        cast(LeaveRequest.category, Text).label("category"),
        LeaveRequest.amount.label("amount"),
        cast(LeaveRequest.status, Text).label("detail"),
        LeaveRequest.id.label("ref_id"),
    ).where(LeaveRequest.employee_id == employee_id)

    # allocation 엔 employee_id 가 없어 grant 로 조인해 스코프 + category 도출.
    allocation = (
        select(
            literal("사용").label("entry_type"),
            LeaveAllocation.created_at.label("occurred_at"),
            cast(LeaveGrant.category, Text).label("category"),
            LeaveAllocation.amount.label("amount"),
            cast(null(), Text).label("detail"),
            LeaveAllocation.id.label("ref_id"),
        )
        .join(LeaveGrant, LeaveAllocation.grant_id == LeaveGrant.id)
        .where(LeaveGrant.employee_id == employee_id)
    )

    adjustment = select(
        literal("조정").label("entry_type"),
        LeaveAdjustment.created_at.label("occurred_at"),
        cast(LeaveAdjustment.category, Text).label("category"),
        LeaveAdjustment.delta.label("amount"),
        LeaveAdjustment.reason.label("detail"),
        LeaveAdjustment.id.label("ref_id"),
    ).where(LeaveAdjustment.employee_id == employee_id)

    sub = union_all(grant, request, allocation, adjustment).subquery()
    stmt = select(sub).order_by(sub.c.occurred_at.asc())
    return [dict(row) for row in (await session.execute(stmt)).mappings().all()]
