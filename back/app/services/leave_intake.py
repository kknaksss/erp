"""연차 신청 intake — 2채널(Slack webhook / ERP 폼) → `신청됨` 생성 (WP-003 Phase 1).

정본 = SPEC-004(intake 계약·2채널·검증·dedup) + 40-architecture/domains/leave_request.md
§Invariant(unit↔amount·am_pm·Off Day). intake 는 **생성만** — 차감/FEFO/승인은 P2(SPEC-003),
취소·변경은 WP-004 이라 손대지 않는다.

채널별 신청자 식별:
- ① Slack = 제출자 email → employee.email(1:1, 이름 매칭 금지) + 공유 시크릿 토큰 검증 + dedup.
- ② ERP 폼 = 로그인 토큰 sub → employee(인증된 본인이라 시크릿·email 매칭 불요).
"""

import secrets
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import (
    InvalidLeaveRequestError,
    InvalidWebhookSecretError,
    NotFoundError,
)
from app.models.enums import AmPm, LeaveCategory, LeaveUnit, RequestChannel
from app.models.leave_request import LeaveRequest
from app.repositories import employee as employee_repo
from app.repositories import leave_request as request_repo
from app.schemas.leave_request import ErpIntakeIn, SlackIntakeIn

# unit → amount 불변 매핑(domains §Invariant unit↔amount). 클라이언트 입력 무시 — 서버 derive.
_UNIT_AMOUNT: dict[LeaveUnit, Decimal] = {
    LeaveUnit.FULL: Decimal("1.0"),
    LeaveUnit.HALF: Decimal("0.5"),
    LeaveUnit.QUARTER: Decimal("0.25"),
}


def validate_form(category: LeaveCategory, unit: LeaveUnit, am_pm: AmPm | None) -> Decimal:
    """폼 invariant 검증 → amount 반환(domains §Invariant). 위반 시 InvalidLeaveRequestError(422).

    - unit↔amount: 전일1.0 / 반차0.5 / 반반차0.25 (매핑 derive).
    - am_pm 분기: 반차·반반차 → NOT NULL · 전일 → NULL.
    - Off Day 제약: category=Off Day 면 unit=반차(0.5)만 (전일·반반차 거부).
    (category∈4 종류는 enum 파싱이 강제.)
    """
    if category == LeaveCategory.OFF_DAY and unit != LeaveUnit.HALF:
        raise InvalidLeaveRequestError("Off Day 는 반차(0.5)만 신청할 수 있습니다")

    if unit == LeaveUnit.FULL:
        if am_pm is not None:
            raise InvalidLeaveRequestError("전일 신청은 오전/오후를 지정하지 않습니다")
    else:  # 반차·반반차
        if am_pm is None:
            raise InvalidLeaveRequestError("반차·반반차는 오전/오후를 지정해야 합니다")

    return _UNIT_AMOUNT[unit]


def _verify_secret(token: str) -> None:
    """공유 시크릿 토큰 상수시간 대조 — 불일치 시 신청 미생성(401)."""
    if not secrets.compare_digest(token, settings.erp_slack_webhook_secret):
        raise InvalidWebhookSecretError()


async def create_from_slack(session: AsyncSession, payload: SlackIntakeIn) -> LeaveRequest:
    """① Slack webhook → `신청됨`. 토큰 검증 → email 매핑 → 검증 → dedup → 생성(channel=slack).

    토큰 불일치/email 미일치는 신청을 만들지 않는다(401/404). 재전송(동일 타임스탬프)은 dedup 으로
    직전 1건을 그대로 반환한다(중복 적재 없음). commit 은 호출 router.
    """
    _verify_secret(payload.token)

    emp = await employee_repo.get_by_email(session, payload.email)
    if emp is None:
        raise NotFoundError("제출자 email 에 해당하는 직원을 찾을 수 없습니다")

    amount = validate_form(payload.category, payload.unit, payload.am_pm)

    dup = await request_repo.find_duplicate(
        session,
        employee_id=emp.id,
        use_date=payload.use_date,
        category=payload.category,
        unit=payload.unit,
        created_at=payload.timestamp,
    )
    if dup is not None:
        return dup  # 재전송 — 1건만 유지(새 insert·commit 없음)

    req = await request_repo.create(
        session,
        employee_id=emp.id,
        category=payload.category,
        unit=payload.unit,
        amount=amount,
        am_pm=payload.am_pm,
        use_date=payload.use_date,
        note=payload.note,
        channel=RequestChannel.SLACK,
        created_at=payload.timestamp,
    )
    await session.commit()
    return req


async def create_from_erp(
    session: AsyncSession, employee_id: UUID, payload: ErpIntakeIn
) -> LeaveRequest:
    """② ERP 폼(로그인 본인) → `신청됨`. 시크릿·email 불요 — sub 로 신청자 직접 식별(channel=erp).

    created_at = server_default(now) — 동기 로그인 호출이라 재전송 dedup 불요. commit 은 호출 router.
    """
    amount = validate_form(payload.category, payload.unit, payload.am_pm)
    req = await request_repo.create(
        session,
        employee_id=employee_id,
        category=payload.category,
        unit=payload.unit,
        amount=amount,
        am_pm=payload.am_pm,
        use_date=payload.use_date,
        note=payload.note,
        channel=RequestChannel.ERP,
    )
    await session.commit()
    return req
