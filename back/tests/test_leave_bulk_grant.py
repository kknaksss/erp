"""HR 벌크 부여 테스트 — SPEC-003 §부여·§5(벌크 부여) + domains/leave_grant.md. WP-005 Phase 1.

- 부여(service·실제 DB·롤백): N 직원 전원 HR부여 lot(source·granted_by·expiry) + category_balance 가산.
- Off Day default(0.5·그달 말일) · 종류 게이트(연차 422) · 보상/포상 만료 필수(422) · dedup.
- 원자성: 미존재 대상 → 404 + 전원 미부여(롤백) · 비활성 → 422 + 미부여.
- 권한(endpoint): 비-HR 403 · 빈 대상 리스트 422(스키마).

bulk_grant service 가 commit 하지만 db_session fixture 가 outer 트랜잭션 롤백으로 격리한다
(test_leave_approval 패턴 — 실제 DB 미오염).
"""

import calendar
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select

from app.core.deps import get_current_employee, get_db, require_hr
from app.core.errors import InvalidBulkGrantError, NotFoundError
from app.main import app
from app.models.employee import Employee
from app.models.enums import GrantSource, GrantStatus, LeaveCategory
from app.models.leave_grant import LeaveGrant
from app.schemas.leave_grant import BulkGrantIn
from app.services import leave_balance, leave_grant_ops

HR = "hr"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_employee(session, *, department: str | None = None, active: bool = True) -> Employee:
    eid = uuid.uuid4()
    emp = Employee(id=eid, email=f"{eid.hex[:8]}@x.com", name=f"emp-{eid.hex[:6]}",
                   role="member", active=active, department=department)
    session.add(emp)
    await session.flush()
    return emp


async def _lots(session, employee_id) -> list[LeaveGrant]:
    stmt = select(LeaveGrant).where(LeaveGrant.employee_id == employee_id)
    return list((await session.execute(stmt)).scalars().all())


def _this_month_end() -> date:
    today = datetime.now(UTC).date()
    return date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])


# ---- 부여 happy (service) --------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_grant_creates_hr_lot_for_all_targets(db_session) -> None:
    """N 직원 전원 해당 종류 HR부여 lot(source=HR부여·granted_by=hr·expiry) + 잔여 가산."""
    hr = await _seed_employee(db_session, department=HR)
    e1 = await _seed_employee(db_session)
    e2 = await _seed_employee(db_session)
    e3 = await _seed_employee(db_session)
    expiry = date(2027, 3, 31)

    out = await leave_grant_ops.bulk_grant(
        db_session, hr,
        BulkGrantIn(employee_ids=[e1.id, e2.id, e3.id], category=LeaveCategory.COMP,
                    amount=Decimal("2.0"), expiry_date=expiry, reason="주말근무 보상"),
    )

    assert out.target_count == 3 and out.lot_count == 3
    assert out.source == GrantSource.HR_GRANT and out.granted_by == hr.id
    for emp in (e1, e2, e3):
        lots = await _lots(db_session, emp.id)
        assert len(lots) == 1
        lot = lots[0]
        assert lot.category == LeaveCategory.COMP
        assert lot.source == GrantSource.HR_GRANT       # HR부여
        assert lot.amount == Decimal("2.0") and lot.remaining == Decimal("2.0")
        assert lot.expiry_date == expiry                # 유효기간 보유
        assert lot.granted_by == hr.id                  # 부여 HR (NOT NULL)
        assert lot.reason == "주말근무 보상"
        assert lot.status == GrantStatus.ACTIVE
        # 잔여 derive(WP-002)에 자동 반영
        bal = await leave_balance.category_balance(db_session, emp.id, LeaveCategory.COMP)
        assert bal == Decimal("2.0")


@pytest.mark.asyncio
async def test_bulk_grant_reward_category(db_session) -> None:
    """포상휴가도 벌크 부여 대상 — category=포상 lot 생성."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)

    await leave_grant_ops.bulk_grant(
        db_session, hr,
        BulkGrantIn(employee_ids=[emp.id], category=LeaveCategory.REWARD,
                    amount=Decimal("1.0"), expiry_date=date(2027, 1, 31)),
    )
    lots = await _lots(db_session, emp.id)
    assert len(lots) == 1 and lots[0].category == LeaveCategory.REWARD


# ---- Off Day default -------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_grant_off_day_defaults(db_session) -> None:
    """Off Day 일수/만료 미지정 → 0.5 · 그달 말일 만료(SPEC-003 §부여 default)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)

    out = await leave_grant_ops.bulk_grant(
        db_session, hr,
        BulkGrantIn(employee_ids=[emp.id], category=LeaveCategory.OFF_DAY),
    )

    assert out.amount == Decimal("0.5")
    assert out.expiry_date == _this_month_end()
    lot = (await _lots(db_session, emp.id))[0]
    assert lot.category == LeaveCategory.OFF_DAY
    assert lot.amount == Decimal("0.5") and lot.remaining == Decimal("0.5")
    assert lot.expiry_date == _this_month_end()


@pytest.mark.asyncio
async def test_bulk_grant_off_day_explicit_overrides_default(db_session) -> None:
    """Off Day 도 명시 일수/만료 지정 시 그 값 사용(default 는 미지정 때만)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)

    out = await leave_grant_ops.bulk_grant(
        db_session, hr,
        BulkGrantIn(employee_ids=[emp.id], category=LeaveCategory.OFF_DAY,
                    amount=Decimal("1.0"), expiry_date=date(2027, 2, 28)),
    )
    assert out.amount == Decimal("1.0") and out.expiry_date == date(2027, 2, 28)


# ---- 종류 게이트 -----------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_grant_annual_rejected_422(db_session) -> None:
    """`연차` 벌크 부여 거부 — 발생·이월 전용(422), lot 미생성."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)

    with pytest.raises(InvalidBulkGrantError):
        await leave_grant_ops.bulk_grant(
            db_session, hr,
            BulkGrantIn(employee_ids=[emp.id], category=LeaveCategory.ANNUAL,
                        amount=Decimal("1.0"), expiry_date=date(2027, 3, 31)),
        )
    assert await _lots(db_session, emp.id) == []


# ---- 보상/포상 만료·일수 필수 + 일수>0 ------------------------------------


@pytest.mark.asyncio
async def test_bulk_grant_comp_requires_expiry_422(db_session) -> None:
    """보상은 만료일 필수(NOT NULL) — 누락 시 422."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    with pytest.raises(InvalidBulkGrantError):
        await leave_grant_ops.bulk_grant(
            db_session, hr,
            BulkGrantIn(employee_ids=[emp.id], category=LeaveCategory.COMP,
                        amount=Decimal("1.0")),  # expiry 누락
        )
    assert await _lots(db_session, emp.id) == []


@pytest.mark.asyncio
async def test_bulk_grant_non_positive_amount_422(db_session) -> None:
    """일수 ≤ 0 거부(422)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    with pytest.raises(InvalidBulkGrantError):
        await leave_grant_ops.bulk_grant(
            db_session, hr,
            BulkGrantIn(employee_ids=[emp.id], category=LeaveCategory.COMP,
                        amount=Decimal("0"), expiry_date=date(2027, 3, 31)),
        )
    assert await _lots(db_session, emp.id) == []


# ---- 원자성 (전체/롤백) ----------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_grant_missing_target_404_no_grant(db_session) -> None:
    """대상 중 1건 미존재(잘못된 id) → 404 + 전원 미부여(원자성·부분 성공 없음)."""
    hr = await _seed_employee(db_session, department=HR)
    e1, e2 = await _seed_employee(db_session), await _seed_employee(db_session)
    ghost = uuid.uuid4()

    with pytest.raises(NotFoundError):
        await leave_grant_ops.bulk_grant(
            db_session, hr,
            BulkGrantIn(employee_ids=[e1.id, ghost, e2.id], category=LeaveCategory.COMP,
                        amount=Decimal("2.0"), expiry_date=date(2027, 3, 31)),
        )
    # 유효 대상에도 lot 이 생기지 않음(전원 미부여)
    assert await _lots(db_session, e1.id) == []
    assert await _lots(db_session, e2.id) == []


@pytest.mark.asyncio
async def test_bulk_grant_inactive_target_422_no_grant(db_session) -> None:
    """대상 중 비활성(active=false) 직원 → 422 + 전원 미부여."""
    hr = await _seed_employee(db_session, department=HR)
    active_emp = await _seed_employee(db_session)
    inactive = await _seed_employee(db_session, active=False)

    with pytest.raises(InvalidBulkGrantError):
        await leave_grant_ops.bulk_grant(
            db_session, hr,
            BulkGrantIn(employee_ids=[active_emp.id, inactive.id], category=LeaveCategory.COMP,
                        amount=Decimal("1.0"), expiry_date=date(2027, 3, 31)),
        )
    assert await _lots(db_session, active_emp.id) == []
    assert await _lots(db_session, inactive.id) == []


# ---- dedup (call 안 중복 id) ----------------------------------------------


@pytest.mark.asyncio
async def test_bulk_grant_dedup_same_id(db_session) -> None:
    """같은 call 안 중복 id → lot 1건(각 직원 1건)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)

    out = await leave_grant_ops.bulk_grant(
        db_session, hr,
        BulkGrantIn(employee_ids=[emp.id, emp.id, emp.id], category=LeaveCategory.COMP,
                    amount=Decimal("1.0"), expiry_date=date(2027, 3, 31)),
    )
    assert out.target_count == 1 and out.lot_count == 1
    assert len(await _lots(db_session, emp.id)) == 1


# ---- reason 선택 -----------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_grant_reason_optional(db_session) -> None:
    """사유 미지정 허용 — lot.reason = NULL(누락 거부 규칙 없음)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)

    out = await leave_grant_ops.bulk_grant(
        db_session, hr,
        BulkGrantIn(employee_ids=[emp.id], category=LeaveCategory.COMP,
                    amount=Decimal("1.0"), expiry_date=date(2027, 3, 31)),
    )
    assert out.reason is None
    assert (await _lots(db_session, emp.id))[0].reason is None


# ---- 권한 / 빈 리스트 (endpoint) ------------------------------------------


@pytest.mark.asyncio
async def test_bulk_grant_non_hr_403(db_session) -> None:
    member = await _seed_employee(db_session, department="개발")
    emp = await _seed_employee(db_session)
    app.dependency_overrides[get_current_employee] = lambda: member
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post("/leave/admin/grants", headers={"Authorization": "Bearer t"},
                                json={"employee_ids": [str(emp.id)], "category": "보상",
                                      "amount": "1.0", "expiry_date": "2027-03-31"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_bulk_grant_hr_endpoint_200(db_session) -> None:
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    app.dependency_overrides[require_hr] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post("/leave/admin/grants", headers={"Authorization": "Bearer t"},
                                json={"employee_ids": [str(emp.id)], "category": "포상",
                                      "amount": "1.5", "expiry_date": "2027-01-31",
                                      "reason": "프로젝트 런칭"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["target_count"] == 1 and body["lot_count"] == 1
        assert body["category"] == "포상" and body["source"] == "HR부여"
        assert body["amount"] == "1.5" and body["expiry_date"] == "2027-01-31"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_bulk_grant_empty_target_list_422(db_session) -> None:
    """빈 대상 리스트 → 422(스키마 min_length)."""
    hr = await _seed_employee(db_session, department=HR)
    app.dependency_overrides[require_hr] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post("/leave/admin/grants", headers={"Authorization": "Bearer t"},
                                json={"employee_ids": [], "category": "보상",
                                      "amount": "1.0", "expiry_date": "2027-03-31"})
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()
