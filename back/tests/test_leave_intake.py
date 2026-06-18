"""연차 intake 2채널 테스트 — SPEC-004 §5 AC + domains §Invariant. WP-003 Phase 1.

- 폼 검증(validate_form): unit↔amount·am_pm 분기·Off Day 반차전용 (pure).
- Slack 채널(실제 erp DB·롤백): 토큰검증·email 매핑·dedup·`신청됨`(channel=slack).
- ERP 채널: 로그인 sub → `신청됨`(channel=erp, 시크릿 불요).
- 엔드포인트 배선: Slack 토큰 불일치 401 · 인증 게이트(erp/me 토큰없음 401) · 응답 형태.

intake service 가 commit 하지만 db_session fixture 가 outer 트랜잭션 롤백으로 격리한다
(test_leave_balance 와 동일 패턴 — 실제 DB 미오염 확인됨).
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import func, select

from app.core.deps import get_current_employee, get_db
from app.core.errors import InvalidLeaveRequestError, InvalidWebhookSecretError, NotFoundError
from app.main import app
from app.models.employee import Employee
from app.models.enums import (
    AmPm,
    LeaveCategory,
    LeaveUnit,
    RequestChannel,
    RequestStatus,
)
from app.models.leave_request import LeaveRequest
from app.schemas.leave_request import ErpIntakeIn, SlackIntakeIn
from app.services import leave_intake

SECRET = "test-webhook-secret"  # conftest setdefault 와 동일


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_employee(session, *, email: str | None = None) -> Employee:
    eid = uuid.uuid4()
    emp = Employee(id=eid, email=email or f"{eid.hex[:8]}@x.com",
                   name=f"emp-{eid.hex[:6]}", role="member", active=True)
    session.add(emp)
    await session.flush()
    return emp


def _slack(email: str, *, category=LeaveCategory.ANNUAL, unit=LeaveUnit.FULL,
           am_pm=None, use_date=date(2026, 5, 1),
           ts=datetime(2026, 4, 30, 9, 0, tzinfo=UTC), token=SECRET) -> SlackIntakeIn:
    return SlackIntakeIn(email=email, category=category, unit=unit, am_pm=am_pm,
                         use_date=use_date, note="사유", timestamp=ts, token=token)


# ---- 폼 검증 (pure invariant) ---------------------------------------------


def test_amount_mapping() -> None:
    assert leave_intake.validate_form(LeaveCategory.ANNUAL, LeaveUnit.FULL, None) == Decimal("1.0")
    assert leave_intake.validate_form(LeaveCategory.ANNUAL, LeaveUnit.HALF, AmPm.AM) == Decimal("0.5")
    assert leave_intake.validate_form(LeaveCategory.ANNUAL, LeaveUnit.QUARTER, AmPm.PM) == Decimal("0.25")


def test_full_day_rejects_am_pm() -> None:
    with pytest.raises(InvalidLeaveRequestError):
        leave_intake.validate_form(LeaveCategory.ANNUAL, LeaveUnit.FULL, AmPm.AM)


def test_half_requires_am_pm() -> None:
    with pytest.raises(InvalidLeaveRequestError):
        leave_intake.validate_form(LeaveCategory.ANNUAL, LeaveUnit.HALF, None)
    with pytest.raises(InvalidLeaveRequestError):
        leave_intake.validate_form(LeaveCategory.ANNUAL, LeaveUnit.QUARTER, None)


def test_off_day_half_only() -> None:
    # 반차만 허용
    assert leave_intake.validate_form(LeaveCategory.OFF_DAY, LeaveUnit.HALF, AmPm.AM) == Decimal("0.5")
    # 전일·반반차 거부
    with pytest.raises(InvalidLeaveRequestError):
        leave_intake.validate_form(LeaveCategory.OFF_DAY, LeaveUnit.FULL, None)
    with pytest.raises(InvalidLeaveRequestError):
        leave_intake.validate_form(LeaveCategory.OFF_DAY, LeaveUnit.QUARTER, AmPm.AM)


# ---- Slack 채널 (service · 실제 DB · 롤백) ---------------------------------


@pytest.mark.asyncio
async def test_slack_creates_requested(db_session) -> None:
    emp = await _seed_employee(db_session, email="alice@x.com")
    req = await leave_intake.create_from_slack(db_session, _slack("alice@x.com"))
    assert req.employee_id == emp.id            # email → employee 매핑
    assert req.status == RequestStatus.REQUESTED  # 신청됨
    assert req.channel == RequestChannel.SLACK
    assert req.amount == Decimal("1.0")


@pytest.mark.asyncio
async def test_slack_token_mismatch_no_create(db_session) -> None:
    await _seed_employee(db_session, email="bob@x.com")
    with pytest.raises(InvalidWebhookSecretError):
        await leave_intake.create_from_slack(db_session, _slack("bob@x.com", token="wrong"))
    # 미생성
    n = (await db_session.execute(
        select(func.count()).select_from(LeaveRequest).where(LeaveRequest.note == "사유")
    )).scalar_one()
    assert n == 0


@pytest.mark.asyncio
async def test_slack_unknown_email_no_create(db_session) -> None:
    with pytest.raises(NotFoundError):
        await leave_intake.create_from_slack(db_session, _slack("nobody@x.com"))


@pytest.mark.asyncio
async def test_slack_dedup_resend_single(db_session) -> None:
    emp = await _seed_employee(db_session, email="carol@x.com")
    p = _slack("carol@x.com")
    first = await leave_intake.create_from_slack(db_session, p)
    # 동일 타임스탬프 재전송 → 새 insert 없이 직전 1건
    again = await leave_intake.create_from_slack(db_session, _slack("carol@x.com", ts=p.timestamp))
    assert again.id == first.id
    n = (await db_session.execute(
        select(func.count()).select_from(LeaveRequest).where(LeaveRequest.employee_id == emp.id)
    )).scalar_one()
    assert n == 1


@pytest.mark.asyncio
async def test_slack_off_day_full_rejected(db_session) -> None:
    await _seed_employee(db_session, email="dave@x.com")
    with pytest.raises(InvalidLeaveRequestError):
        await leave_intake.create_from_slack(
            db_session, _slack("dave@x.com", category=LeaveCategory.OFF_DAY, unit=LeaveUnit.FULL))


# ---- ERP 채널 (service · sub 식별 · 시크릿 불요) ---------------------------


@pytest.mark.asyncio
async def test_erp_creates_erp_channel(db_session) -> None:
    emp = await _seed_employee(db_session)
    payload = ErpIntakeIn(category=LeaveCategory.COMP, unit=LeaveUnit.HALF,
                          am_pm=AmPm.AM, use_date=date(2026, 6, 1), note="보상반차")
    req = await leave_intake.create_from_erp(db_session, emp.id, payload)
    assert req.employee_id == emp.id
    assert req.channel == RequestChannel.ERP        # 시크릿·email 없이 sub 식별
    assert req.status == RequestStatus.REQUESTED
    assert req.amount == Decimal("0.5") and req.am_pm == AmPm.AM


# ---- 엔드포인트 배선 (인증/토큰 게이트·응답 형태) -------------------------


@pytest.mark.asyncio
async def test_slack_endpoint_200(db_session) -> None:
    emp = await _seed_employee(db_session, email="erin@x.com")
    app.dependency_overrides[get_db] = lambda: db_session  # 엔드포인트도 같은 트랜잭션
    try:
        async with _client() as c:
            resp = await c.post("/leave/intake/slack", json={
                "email": "erin@x.com", "category": "연차", "unit": "전일",
                "use_date": "2026-05-01", "note": "n",
                "timestamp": "2026-04-30T09:00:00Z", "token": SECRET,
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "신청됨" and body["channel"] == "slack"
        assert body["category"] == "연차" and body["amount"] == "1.00"
        assert set(body) == {"id", "category", "unit", "amount", "am_pm", "use_date",
                             "note", "status", "channel", "created_at"}
    finally:
        app.dependency_overrides.clear()
    assert emp is not None


@pytest.mark.asyncio
async def test_slack_endpoint_token_mismatch_401(db_session) -> None:
    await _seed_employee(db_session, email="frank@x.com")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post("/leave/intake/slack", json={
                "email": "frank@x.com", "category": "연차", "unit": "전일",
                "use_date": "2026-05-01", "timestamp": "2026-04-30T09:00:00Z",
                "token": "WRONG",
            })
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "INVALID_WEBHOOK_SECRET"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_erp_endpoint_no_token_401() -> None:
    async with _client() as c:
        resp = await c.post("/leave/intake", json={
            "category": "연차", "unit": "전일", "use_date": "2026-05-01"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_erp_endpoint_200(db_session) -> None:
    emp = await _seed_employee(db_session)
    app.dependency_overrides[get_current_employee] = lambda: emp
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post("/leave/intake", headers={"Authorization": "Bearer t"}, json={
                "category": "포상", "unit": "반반차", "am_pm": "오후",
                "use_date": "2026-07-01"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["channel"] == "erp" and body["category"] == "포상"
        assert body["amount"] == "0.25" and body["am_pm"] == "오후"
    finally:
        app.dependency_overrides.clear()
