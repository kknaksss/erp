"""HR 벌크 부여 service — 다중 직원에게 보상/포상/Off Day lot 일괄 부여. WP-005 Phase 1.

SPEC-003 §부여(HR 벌크) + §5 Acceptance Criteria(벌크 부여) + 40-architecture/domains/
leave_grant.md(부여 lot `source=HR부여`·`granted_by` NOT NULL·유효기간). 발생/이월(WP-002)·
신청/승인(WP-003)·취소/변경(WP-004)·연차수 조정/상세(WP-005 P2/P3)은 손대지 않는다.

- 종류 게이트: `보상`/`포상`/`Off Day` 만 — `연차`는 발생·이월 전용이라 거부(422).
- Off Day default: 일수 미지정 0.5 · 만료일 미지정 그달 말일(SPEC-003 §부여). 보상/포상은 일수>0·
  만료일 필수(보상/포상 = 만료 NOT NULL).
- 대상 검증: dedup(같은 call 안 중복 id → lot 1건). 미존재 404 · 비활성(`active=false`) 422 —
  **부분 무시 금지**(임의 skip 안 함). 검증 먼저 통과해야 lot 생성 진입.
- 원자성: 전 대상 lot 생성 후 **단일 commit**(전체/롤백). 검증 실패 시 어떤 lot 도 생성 전이라
  전원 미부여. **멱등 아님** — HR 수동 행위라 재호출 = 의도적 추가 부여(발생/이월의 멱등과 다름).
- lot 필드: `category`·`amount=remaining=일수`·`expiry_date`·`source=HR부여`·`granted_by=hr.id`·
  `granted_at=now`·`status=active`·`reason`. 잔여(WP-002 category_balance)에 자동 반영.

grant lot 생성은 `grant_repo.create_lot`(발생/이월과 동일 헬퍼) 그대로 소비(재정의 금지).
"""

import calendar
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidBulkGrantError, NotFoundError
from app.models.employee import Employee
from app.models.enums import GrantSource, GrantStatus, LeaveCategory
from app.repositories import employee as employee_repo
from app.repositories import leave_grant as grant_repo
from app.schemas.leave_grant import BulkGrantIn, BulkGrantOut

# 벌크 부여 허용 종류 — HR 부여형(연차는 발생·이월 전용이라 제외)
_GRANTABLE = (LeaveCategory.COMP, LeaveCategory.REWARD, LeaveCategory.OFF_DAY)
_OFF_DAY_DEFAULT_AMOUNT = Decimal("0.5")


def _month_end(d: date) -> date:
    """그달 말일 — Off Day 만료일 default(SPEC-003 §부여)."""
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _resolve_amount_expiry(
    category: LeaveCategory, amount: Decimal | None, expiry: date | None, today: date
) -> tuple[Decimal, date]:
    """종류별 일수·만료일 확정(Off Day default 채움 / 보상·포상 필수 강제). 위반 422.

    - Off Day: 일수 미지정 → 0.5, 만료일 미지정 → 그달 말일.
    - 보상/포상: 일수·만료일 필수(만료 NOT NULL).
    - 공통: 일수 > 0.
    """
    if category == LeaveCategory.OFF_DAY:
        amount = _OFF_DAY_DEFAULT_AMOUNT if amount is None else amount
        expiry = _month_end(today) if expiry is None else expiry
    else:  # 보상 / 포상 — 일수·만료일 필수
        if amount is None:
            raise InvalidBulkGrantError("일수(amount)는 필수입니다")
        if expiry is None:
            raise InvalidBulkGrantError("유효기간 만료일(expiry_date)은 필수입니다")
    if amount <= 0:
        raise InvalidBulkGrantError("일수(amount)는 0보다 커야 합니다")
    return amount, expiry


async def _validate_targets(
    session: AsyncSession, ids: list[UUID]
) -> list[Employee]:
    """대상 직원 검증 — 미존재 404 · 비활성 422(부분 무시 금지). 통과 시 dedup 순서 그대로 반환.

    lot 생성 전 전수 검증 → 한 건이라도 실패면 lot 미생성(전원 미부여·원자성). 실패 id 는
    error `detail` 에 실어 HR 이 선택을 정정하게 한다.
    """
    found = {e.id: e for e in await employee_repo.list_by_ids(session, ids)}

    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise NotFoundError(
            "존재하지 않는 직원이 포함되어 있습니다", detail={"missing": missing}
        )
    inactive = [str(i) for i in ids if not found[i].active]
    if inactive:
        raise InvalidBulkGrantError(
            "비활성(퇴사) 직원이 포함되어 있습니다", detail={"inactive": inactive}
        )
    return [found[i] for i in ids]


async def bulk_grant(
    session: AsyncSession, hr: Employee, payload: BulkGrantIn
) -> BulkGrantOut:
    """다중 직원에게 보상/포상/Off Day lot 일괄 부여. 반환 부여 결과 요약. commit 은 여기서.

    종류 게이트 → 일수·만료일 확정 → 대상 검증(404/422) → 각 대상 lot 1건 생성 → 단일 commit.
    전 단계가 통과해야 lot 생성에 진입하므로 부분 부여가 없다(전체/롤백).
    """
    if payload.category not in _GRANTABLE:
        raise InvalidBulkGrantError(
            "벌크 부여 대상 종류가 아닙니다(보상/포상/Off Day 만 가능)",
            detail={"category": payload.category.value},
        )

    now = datetime.now(UTC)
    amount, expiry = _resolve_amount_expiry(
        payload.category, payload.amount, payload.expiry_date, now.date()
    )

    # 같은 call 안 중복 id → lot 1건(순서 보존 dedup). dedup 만 — call 간 멱등 없음.
    target_ids = list(dict.fromkeys(payload.employee_ids))
    targets = await _validate_targets(session, target_ids)

    for emp in targets:
        await grant_repo.create_lot(
            session,
            employee_id=emp.id,
            category=payload.category,
            amount=amount,
            source=GrantSource.HR_GRANT,
            expiry_date=expiry,
            granted_by=hr.id,
            reason=payload.reason,
            granted_at=now,
            status=GrantStatus.ACTIVE,
        )
    await session.commit()

    return BulkGrantOut(
        target_count=len(targets),
        category=payload.category,
        amount=amount,
        expiry_date=expiry,
        reason=payload.reason,
        source=GrantSource.HR_GRANT,
        granted_by=hr.id,
        granted_at=now,
        lot_count=len(targets),
    )
