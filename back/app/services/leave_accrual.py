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
from app.repositories import employee as employee_repo
from app.repositories import leave_grant as grant_repo


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
    """연말 남은연차(>0) → `보상`+`source=이월` lot. 반환 {carried, skipped, none}.

    남은연차 = sum(active 연차 lot remaining) + sum(연차 adjustment delta). >0 일 때만 lot 생성
    (≤0 = lot 안 만듦). expiry_date = valid_until(파라미터 — HR/정책 공급). 이미 그 회계연도
    이월 lot 보유 직원 skip(멱등). granted_at = 연말 12/31(UTC, 멱등 키 기준). 원 `연차` lot 은
    건드리지 않는다(차감/만료 = P3·WP-003).
    """
    granted_at = datetime(fiscal_year, 12, 31, tzinfo=UTC)
    carried = skipped = none = 0
    for emp in await employee_repo.list_active(session):
        if await grant_repo.exists_lot_for_year(
            session, emp.id, GrantSource.CARRYOVER, LeaveCategory.COMP, fiscal_year
        ):
            skipped += 1
            continue
        remaining = await grant_repo.sum_category_remaining(
            session, emp.id, LeaveCategory.ANNUAL
        ) + await grant_repo.sum_adjustment_delta(session, emp.id, LeaveCategory.ANNUAL)
        if remaining <= 0:
            none += 1
            continue
        await grant_repo.create_lot(
            session,
            employee_id=emp.id,
            category=LeaveCategory.COMP,
            amount=remaining,
            source=GrantSource.CARRYOVER,
            expiry_date=valid_until,
            granted_at=granted_at,
        )
        carried += 1
    await session.commit()
    return {"carried": carried, "skipped": skipped, "none": none}
