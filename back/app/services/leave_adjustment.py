"""HR 연차수 조정 service — 한 직원의 잔여를 종류별로 한 번에 ± 보정 + audit. WP-005 Phase 2.

SPEC-003 §연차수 조정(HR) + §5 Acceptance Criteria(조정) + 40-architecture/domains/
leave_adjustment.md(delta≠0·음수 허용·append-only·audit `adjusted_by`/`created_at`/`reason`).
벌크 부여(P1)·상세 조회/FE(P3)·발생/이월(WP-002)·신청/승인/취소/변경(WP-003/004)은 손대지 않는다.

- **4 종류 전부 조정 대상** — `연차` 포함(벌크 부여의 종류 게이트와 정반대. category 제약은
  schema 의 enum 만, service 에 종류 게이트 없음).
- **delta ≠ 0**: 0 항목 거부(422). delta **음수 허용** — 결과 잔여가 음수가 돼도 하드 차단 없음
  (SPEC-003 음수 허용·경고는 FE). 빈 항목 리스트는 schema(min_length=1)가 422.
- **대상 검증**: 미존재 404 · 비활성(`active=false`) 422 — P1(벌크 부여)과 일관(위임 결정).
- **원자성**: 한 요청 다건 = 전 항목 검증 먼저 통과 → 각 row 생성 → **단일 commit**(전체/롤백).
  검증 실패(미존재/비활성/delta=0) 시 어떤 row 도 생성 전이라 전원 미반영.
- **append-only**: 기존 row 수정/삭제 없음 — 새 row 만 추가. audit = `adjusted_by=hr.id`·
  `created_at=now`(한 요청 전 항목 공유)·`reason`.
- **잔여 자동 반영(이중 반영 없음)**: `leave_balance.category_balance` = active lot remaining 합
  ± `sum_adjustment_delta`. derive 가 이미 delta 를 합산하므로 row 생성만으로 잔여 반영 —
  service 가 잔여를 따로 건드리지 않는다(재정의 금지).

조정 row 생성은 `adjustment_repo.create_adjustment`(append-only insert) 그대로 소비.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidAdjustmentError, NotFoundError
from app.models.employee import Employee
from app.models.enums import LeaveCategory
from app.repositories import employee as employee_repo
from app.repositories import leave_adjustment as adjustment_repo
from app.schemas.leave_adjustment import (
    AdjustmentResultItem,
    LeaveAdjustmentIn,
    LeaveAdjustmentOut,
)
from app.services import leave_balance


async def _validate_target(session: AsyncSession, employee_id: UUID) -> Employee:
    """대상 직원 검증 — 미존재 404 · 비활성 422(P1 벌크 부여와 일관). row 생성 전 선검증."""
    emp = await employee_repo.get_by_id(session, employee_id)
    if emp is None:
        raise NotFoundError(
            "존재하지 않는 직원입니다", detail={"employee_id": str(employee_id)}
        )
    if not emp.active:
        raise InvalidAdjustmentError(
            "비활성(퇴사) 직원은 조정할 수 없습니다",
            detail={"employee_id": str(employee_id)},
        )
    return emp


async def adjust(
    session: AsyncSession, hr: Employee, payload: LeaveAdjustmentIn
) -> LeaveAdjustmentOut:
    """한 직원의 종류별 다건 ± 조정. 반환 조정 결과 요약. commit 은 여기서(전체/롤백).

    전 항목 검증(대상 404/422 + 모든 delta≠0) → 각 항목 row 생성 → 단일 commit → 조정 후
    종류별 잔여 derive. 검증이 전부 끝난 뒤에야 첫 row 를 만들므로 부분 조정이 없다(원자성).
    """
    await _validate_target(session, payload.employee_id)

    # 전 항목 delta≠0 선검증 — 1건이라도 0 이면 row 생성 전에 전체 거부(원자성).
    for idx, item in enumerate(payload.items):
        if item.delta == 0:
            raise InvalidAdjustmentError(
                "delta 가 0 인 조정 항목은 허용되지 않습니다(가산/감산만)",
                detail={"index": idx, "category": item.category.value},
            )

    now = datetime.now(UTC)
    for item in payload.items:
        await adjustment_repo.create_adjustment(
            session,
            employee_id=payload.employee_id,
            category=item.category,
            delta=item.delta,
            adjusted_by=hr.id,
            adjusted_at=now,
            reason=item.reason,
        )
    await session.commit()

    # 조정 후 종류별 잔여 — 조정된 종류만(dedup). derive 가 방금 만든 delta 를 합산해 자동 반영.
    adjusted_categories: list[LeaveCategory] = list(
        dict.fromkeys(item.category for item in payload.items)
    )
    balances: dict[LeaveCategory, Decimal] = {
        category: await leave_balance.category_balance(
            session, payload.employee_id, category
        )
        for category in adjusted_categories
    }

    return LeaveAdjustmentOut(
        employee_id=payload.employee_id,
        adjusted_by=hr.id,
        adjusted_at=now,
        items=[
            AdjustmentResultItem(
                category=item.category, delta=item.delta, reason=item.reason
            )
            for item in payload.items
        ],
        balances=balances,
    )
