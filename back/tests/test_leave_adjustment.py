"""HR 연차수 조정 테스트 — SPEC-003 §연차수 조정 + §5(조정) + domains/leave_adjustment.md. WP-005 Phase 2.

- 가산/감산(service·실제 DB·롤백): leave_adjustment row 생성 + category_balance 에 delta 반영.
- 다건 한 요청: 종류별 여러 (category, delta) → 다 row · 각 잔여 반영 · 단일 트랜잭션.
- 음수 허용: 잔여보다 큰 감산 → 음수 잔여 허용(하드 차단 X).
- delta=0 거부(422·row 미생성) · 원자성(다건 중 1건 0 → 전원 미반영).
- append-only/audit: 기존 row 불변·새 row 만 추가·adjusted_by/created_at 기록.
- 이중 반영 없음: 조정 후 category_balance 가 delta 를 1번만 반영(derive 합산 + service 미재정의).
- 4 종류 전부 조정(연차 포함 — 벌크 부여 종류 게이트와 정반대) · 미존재 404 · 비활성 422 · 권한 403.

adjust service 가 commit 하지만 db_session fixture 가 outer 트랜잭션 롤백으로 격리한다(P1 패턴).
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select

from app.core.deps import get_current_employee, get_db, require_hr
from app.core.errors import InvalidAdjustmentError, NotFoundError
from app.main import app
from app.models.employee import Employee
from app.models.enums import GrantSource, GrantStatus, LeaveCategory
from app.models.leave_adjustment import LeaveAdjustment
from app.repositories import leave_grant as grant_repo
from app.schemas.leave_adjustment import (
    AdjustmentItemIn,
    LeaveAdjustmentIn,
)
from app.services import leave_adjustment, leave_balance

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


async def _seed_lot(session, employee_id, category, remaining, *, expiry=None) -> None:
    """기존 잔여 lot 1건 — 조정이 기존 잔여 위에 더해지는지(이중 반영 없음) 검증용."""
    await grant_repo.create_lot(
        session, employee_id=employee_id, category=category,
        amount=remaining, source=GrantSource.HR_GRANT,
        granted_at=datetime.now(UTC), expiry_date=expiry,
        granted_by=employee_id, status=GrantStatus.ACTIVE,
    )


async def _adjustments(session, employee_id) -> list[LeaveAdjustment]:
    stmt = select(LeaveAdjustment).where(LeaveAdjustment.employee_id == employee_id)
    return list((await session.execute(stmt)).scalars().all())


def _item(category, delta, reason=None) -> AdjustmentItemIn:
    return AdjustmentItemIn(category=category, delta=Decimal(delta), reason=reason)


# ---- 가산 / 감산 (single item, service) -----------------------------------


@pytest.mark.asyncio
async def test_adjust_increment_annual(db_session) -> None:
    """가산: 연차 +2.0 → row(delta=2.0·adjusted_by=hr·reason) + category_balance +2.0.

    연차도 조정 대상(벌크 부여 종류 게이트와 정반대) — 종류 게이트 누수 검증.
    """
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)

    out = await leave_adjustment.adjust(
        db_session, hr,
        LeaveAdjustmentIn(employee_id=emp.id, items=[_item(LeaveCategory.ANNUAL, "2.0", "정정")]),
    )

    rows = await _adjustments(db_session, emp.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.category == LeaveCategory.ANNUAL
    assert row.delta == Decimal("2.0")
    assert row.adjusted_by == hr.id          # audit — 누가
    assert row.created_at is not None        # audit — 언제
    assert row.reason == "정정"
    # 잔여 derive 자동 반영
    bal = await leave_balance.category_balance(db_session, emp.id, LeaveCategory.ANNUAL)
    assert bal == Decimal("2.0")
    assert out.adjusted_by == hr.id
    assert out.balances[LeaveCategory.ANNUAL] == Decimal("2.0")


@pytest.mark.asyncio
async def test_adjust_decrement_comp(db_session) -> None:
    """감산: 보상 -1.0 → delta=-1.0 + 잔여 -1.0(기존 lot 5.0 → 4.0)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    await _seed_lot(db_session, emp.id, LeaveCategory.COMP, Decimal("5.0"), expiry=date(2027, 3, 31))

    await leave_adjustment.adjust(
        db_session, hr,
        LeaveAdjustmentIn(employee_id=emp.id, items=[_item(LeaveCategory.COMP, "-1.0")]),
    )

    rows = await _adjustments(db_session, emp.id)
    assert len(rows) == 1 and rows[0].delta == Decimal("-1.0")
    bal = await leave_balance.category_balance(db_session, emp.id, LeaveCategory.COMP)
    assert bal == Decimal("4.0")


# ---- 이중 반영 없음 (핵심) -------------------------------------------------


@pytest.mark.asyncio
async def test_adjust_no_double_counting(db_session) -> None:
    """기존 lot remaining 5.0 + 조정 +2.0 → 잔여 7.0(9.0/5.0 아님) · row 정확히 1건.

    derive(active lot 합 ± adjustment delta 합)가 delta 를 **1번만** 합산하는지 — service 가
    잔여를 따로 건드리면 이중 반영(9.0)이 난다.
    """
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    await _seed_lot(db_session, emp.id, LeaveCategory.COMP, Decimal("5.0"), expiry=date(2027, 3, 31))

    await leave_adjustment.adjust(
        db_session, hr,
        LeaveAdjustmentIn(employee_id=emp.id, items=[_item(LeaveCategory.COMP, "2.0")]),
    )

    bal = await leave_balance.category_balance(db_session, emp.id, LeaveCategory.COMP)
    assert bal == Decimal("7.0")             # 5.0 + 2.0, 이중 반영(9.0) 아님
    assert len(await _adjustments(db_session, emp.id)) == 1


# ---- 다건 한 요청 (단일 트랜잭션) ------------------------------------------


@pytest.mark.asyncio
async def test_adjust_multiple_items_single_request(db_session) -> None:
    """연차 -1.0 + 보상 +0.5 한 번에 → 2 row · 각 잔여 반영 · 단일 트랜잭션."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    await _seed_lot(db_session, emp.id, LeaveCategory.ANNUAL, Decimal("10.0"))
    await _seed_lot(db_session, emp.id, LeaveCategory.COMP, Decimal("3.0"), expiry=date(2027, 3, 31))

    out = await leave_adjustment.adjust(
        db_session, hr,
        LeaveAdjustmentIn(employee_id=emp.id, items=[
            _item(LeaveCategory.ANNUAL, "-1.0"),
            _item(LeaveCategory.COMP, "0.5"),
        ]),
    )

    rows = await _adjustments(db_session, emp.id)
    assert len(rows) == 2
    assert await leave_balance.category_balance(db_session, emp.id, LeaveCategory.ANNUAL) == Decimal("9.0")
    assert await leave_balance.category_balance(db_session, emp.id, LeaveCategory.COMP) == Decimal("3.5")
    assert out.balances[LeaveCategory.ANNUAL] == Decimal("9.0")
    assert out.balances[LeaveCategory.COMP] == Decimal("3.5")
    assert len(out.items) == 2


# ---- 음수 허용 -------------------------------------------------------------


@pytest.mark.asyncio
async def test_adjust_allows_negative_balance(db_session) -> None:
    """잔여보다 큰 감산 → 음수 잔여 허용(하드 차단 X·경고는 FE). lot 2.0 - 5.0 = -3.0."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    await _seed_lot(db_session, emp.id, LeaveCategory.COMP, Decimal("2.0"), expiry=date(2027, 3, 31))

    out = await leave_adjustment.adjust(
        db_session, hr,
        LeaveAdjustmentIn(employee_id=emp.id, items=[_item(LeaveCategory.COMP, "-5.0")]),
    )
    bal = await leave_balance.category_balance(db_session, emp.id, LeaveCategory.COMP)
    assert bal == Decimal("-3.0")            # 음수 허용
    assert out.balances[LeaveCategory.COMP] == Decimal("-3.0")


# ---- delta=0 거부 + 원자성 -------------------------------------------------


@pytest.mark.asyncio
async def test_adjust_delta_zero_rejected_422(db_session) -> None:
    """delta=0 항목 거부(422)·row 미생성."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)

    with pytest.raises(InvalidAdjustmentError):
        await leave_adjustment.adjust(
            db_session, hr,
            LeaveAdjustmentIn(employee_id=emp.id, items=[_item(LeaveCategory.ANNUAL, "0")]),
        )
    assert await _adjustments(db_session, emp.id) == []


@pytest.mark.asyncio
async def test_adjust_atomic_rollback_on_one_zero(db_session) -> None:
    """다건 중 1건 delta=0 섞임 → 전원 미반영(롤백) — 유효 항목도 row 미생성."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)

    with pytest.raises(InvalidAdjustmentError):
        await leave_adjustment.adjust(
            db_session, hr,
            LeaveAdjustmentIn(employee_id=emp.id, items=[
                _item(LeaveCategory.ANNUAL, "2.0"),   # 유효
                _item(LeaveCategory.COMP, "0"),       # 위반
            ]),
        )
    # 유효 항목(연차 +2.0)도 만들어지지 않음(원자성)
    assert await _adjustments(db_session, emp.id) == []


# ---- append-only / audit ---------------------------------------------------


@pytest.mark.asyncio
async def test_adjust_append_only(db_session) -> None:
    """기존 조정 row 불변 + 새 row 만 추가 — append-only(수정/삭제 X)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    # 기존 조정 row 1건 시드
    existing = LeaveAdjustment(
        employee_id=emp.id, category=LeaveCategory.COMP, delta=Decimal("1.0"),
        reason="기존", adjusted_by=hr.id, created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db_session.add(existing)
    await db_session.flush()
    existing_id, existing_delta, existing_at = existing.id, existing.delta, existing.created_at

    await leave_adjustment.adjust(
        db_session, hr,
        LeaveAdjustmentIn(employee_id=emp.id, items=[_item(LeaveCategory.COMP, "3.0", "추가")]),
    )

    rows = {r.id: r for r in await _adjustments(db_session, emp.id)}
    assert len(rows) == 2                                  # 기존 + 신규
    old = rows[existing_id]
    assert old.delta == existing_delta and old.created_at == existing_at  # 기존 불변
    assert old.reason == "기존"
    new = next(r for rid, r in rows.items() if rid != existing_id)
    assert new.delta == Decimal("3.0") and new.reason == "추가"


# ---- 대상 미존재 / 비활성 --------------------------------------------------


@pytest.mark.asyncio
async def test_adjust_missing_target_404(db_session) -> None:
    """미존재 직원 → 404 · row 미생성."""
    hr = await _seed_employee(db_session, department=HR)
    ghost = uuid.uuid4()
    with pytest.raises(NotFoundError):
        await leave_adjustment.adjust(
            db_session, hr,
            LeaveAdjustmentIn(employee_id=ghost, items=[_item(LeaveCategory.ANNUAL, "1.0")]),
        )
    assert await _adjustments(db_session, ghost) == []


@pytest.mark.asyncio
async def test_adjust_inactive_target_422(db_session) -> None:
    """비활성(퇴사) 직원 → 422 · row 미생성(P1 정합)."""
    hr = await _seed_employee(db_session, department=HR)
    inactive = await _seed_employee(db_session, active=False)
    with pytest.raises(InvalidAdjustmentError):
        await leave_adjustment.adjust(
            db_session, hr,
            LeaveAdjustmentIn(employee_id=inactive.id, items=[_item(LeaveCategory.ANNUAL, "1.0")]),
        )
    assert await _adjustments(db_session, inactive.id) == []


# ---- 권한 / 빈 리스트 (endpoint) ------------------------------------------


@pytest.mark.asyncio
async def test_adjust_non_hr_403(db_session) -> None:
    """비-HR → 403(require_hr)."""
    member = await _seed_employee(db_session, department="개발")
    emp = await _seed_employee(db_session)
    app.dependency_overrides[get_current_employee] = lambda: member
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post("/leave/admin/adjustments", headers={"Authorization": "Bearer t"},
                                json={"employee_id": str(emp.id),
                                      "items": [{"category": "연차", "delta": "1.0"}]})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_adjust_hr_endpoint_200(db_session) -> None:
    """HR endpoint happy — 다건 조정 + 조정 후 잔여 응답(FE Phase 3 계약)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    await _seed_lot(db_session, emp.id, LeaveCategory.COMP, Decimal("3.0"), expiry=date(2027, 3, 31))
    app.dependency_overrides[require_hr] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post("/leave/admin/adjustments", headers={"Authorization": "Bearer t"},
                                json={"employee_id": str(emp.id), "items": [
                                    {"category": "연차", "delta": "-1.0", "reason": "정정"},
                                    {"category": "보상", "delta": "0.5"},
                                ]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["employee_id"] == str(emp.id)
        assert body["adjusted_by"] == str(hr.id)
        assert len(body["items"]) == 2
        assert body["items"][0]["category"] == "연차" and body["items"][0]["delta"] == "-1.0"
        assert body["balances"]["연차"] == "-1.00"     # 0 + (-1.0), Numeric(5,2) 직렬화
        assert body["balances"]["보상"] == "3.50"      # 3.0 + 0.5
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_adjust_empty_items_422(db_session) -> None:
    """빈 항목 리스트 → 422(스키마 min_length)."""
    hr = await _seed_employee(db_session, department=HR)
    emp = await _seed_employee(db_session)
    app.dependency_overrides[require_hr] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.post("/leave/admin/adjustments", headers={"Authorization": "Bearer t"},
                                json={"employee_id": str(emp.id), "items": []})
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()
