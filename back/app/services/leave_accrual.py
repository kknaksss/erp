"""연차 발생·이월 service (WP-002 Phase 2). 전부 멱등.

- `accrue_annual`: 회계 1/1 전 active 직원 `연차` base lot 발생(무만료·시스템 부여).
- `grant_new_hire`: 신규 입사자 `연차` base **전액** 부여(비례 없음).
- `carryover`: 연말 남은연차(>0) → `보상`+`source=이월` lot(유효기간).

오케스트레이션 + commit(WP-001 레이어 컨벤션 — repository 는 순수 쿼리). 멱등 키 =
(employee_id, source, category, year(granted_at)) — leave_grant repository. base 일수·이월
유효기간(만료일)은 **파라미터**(정책/HR 공급 — 코드에 하드코딩/발명 X). 트리거 배선
(endpoint/cron)은 WP-005, 여기선 service 함수만(직접 호출 테스트). 잔여 derive/FEFO/만료/
차감은 손대지 않는다(P3·WP-003+). 정본 = work-002 §Phase 2 · spec-003 §S-4 · domains/leave_grant.md.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import GrantSource, LeaveCategory
from app.models.leave_adjustment import LeaveAdjustment
from app.repositories import employee as employee_repo
from app.repositories import leave_grant as grant_repo
from app.services import leave_balance


async def accrue_annual(
    session: AsyncSession, base_days: Decimal, fiscal_year: int
) -> dict[str, int]:
    """회계연도 1/1 전 active 직원에게 `연차` base lot 발생. 반환 {granted, skipped}.

    base_days = 파라미터(정책/호출부 공급). 무만료(expiry NULL)·시스템 부여(granted_by NULL).
    이미 그 회계연도 발생 lot 보유 직원 skip(멱등). granted_at = 회계연도 1/1(UTC, 멱등 키 기준).
    """
    granted_at = datetime(fiscal_year, 1, 1, tzinfo=UTC)
    granted = skipped = 0
    for emp in await employee_repo.list_active(session):
        if await grant_repo.exists_lot_for_year(
            session, emp.id, GrantSource.ACCRUAL, LeaveCategory.ANNUAL, fiscal_year
        ):
            skipped += 1
            continue
        await grant_repo.create_lot(
            session,
            employee_id=emp.id,
            category=LeaveCategory.ANNUAL,
            amount=base_days,
            source=GrantSource.ACCRUAL,
            granted_at=granted_at,
        )
        granted += 1
    await session.commit()
    return {"granted": granted, "skipped": skipped}


async def grant_new_hire(
    session: AsyncSession, employee_id: UUID, base_days: Decimal
) -> bool:
    """신규 입사자 `연차` base 전액 lot(비례 없음). 입사 온보딩 시 호출. 반환 created?.

    granted_at = now(입사 시점). 멱등 키 = (발생, 연차, year(now)) — 코드 SoT 결정: 한 직원
    한 해 발생 lot 1개. 같은 해 재호출/그해 일괄 발생과 중복 차단(이미 보유 시 False).
    무만료·시스템 부여(granted_by NULL).
    """
    now = datetime.now(UTC)
    if await grant_repo.exists_lot_for_year(
        session, employee_id, GrantSource.ACCRUAL, LeaveCategory.ANNUAL, now.year
    ):
        return False
    await grant_repo.create_lot(
        session,
        employee_id=employee_id,
        category=LeaveCategory.ANNUAL,
        amount=base_days,
        source=GrantSource.ACCRUAL,
        granted_at=now,
    )
    await session.commit()
    return True


async def carryover(
    session: AsyncSession, fiscal_year: int, valid_until: date
) -> dict[str, int]:
    """연말 남은연차(>0) → `보상`+`source=이월` lot **+ 원 `연차` 리셋(0화)**. 반환 {carried, skipped, none}.

    **리셋형**(admin↔사용자 2026-06-18 결정 — T-008): 회계 갱신 시 `연차` 는 누적하지 않고 이월로
    *전환*. 남은연차(carried) = `leave_balance.category_balance(연차)`(active lot remaining 합 +
    연차 adjustment delta — derive 로 통일). `>0` 이면:
      1) `보상`+`source=이월` lot 으로 carried 보존(expiry_date = valid_until 파라미터·granted_by NULL).
      2) **원 `연차` 0화**: active `연차` lot remaining → 0 + 잔여 delta 가 있으면 상쇄 adjustment.
         불변식 = 리셋 후 `category_balance(연차) == 0` → 이듬해 발생이 작년분과 중복 집계 안 됨(P3 노출 중복 해소).
    `≤0` = no-op(보존·리셋할 것 없음). 이미 그 회계연도 이월 lot 보유 직원 skip(멱등 — 2회 호출도
    이월·리셋 1회분). granted_at = 연말 12/31(UTC, 멱등 키). audit 보존(lot/adjustment hard delete X).
    """
    granted_at = datetime(fiscal_year, 12, 31, tzinfo=UTC)
    carried = skipped = none = 0
    for emp in await employee_repo.list_active(session):
        if await grant_repo.exists_lot_for_year(
            session, emp.id, GrantSource.CARRYOVER, LeaveCategory.COMP, fiscal_year
        ):
            skipped += 1
            continue
        remaining = await leave_balance.category_balance(
            session, emp.id, LeaveCategory.ANNUAL
        )
        if remaining <= 0:
            none += 1
            continue
        # 1) 이월 보존 — 연말 남은연차를 `보상`(source=이월) lot 으로 전환
        await grant_repo.create_lot(
            session,
            employee_id=emp.id,
            category=LeaveCategory.COMP,
            amount=remaining,
            source=GrantSource.CARRYOVER,
            expiry_date=valid_until,
            granted_at=granted_at,
        )
        # 2) 원 `연차` 리셋 — lot 0화 + 잔여 delta 상쇄(불변식: category_balance(연차)==0)
        await grant_repo.zero_active_lots(session, emp.id, LeaveCategory.ANNUAL)
        adj_delta = await grant_repo.sum_adjustment_delta(
            session, emp.id, LeaveCategory.ANNUAL
        )
        if adj_delta != 0:
            session.add(
                LeaveAdjustment(
                    employee_id=emp.id,
                    category=LeaveCategory.ANNUAL,
                    delta=-adj_delta,
                    reason=f"회계 이월 리셋 (FY{fiscal_year})",
                    adjusted_by=emp.id,
                )
            )
            await session.flush()
        carried += 1
    await session.commit()
    return {"carried": carried, "skipped": skipped, "none": none}
