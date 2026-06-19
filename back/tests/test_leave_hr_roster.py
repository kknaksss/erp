"""HR 직원목록 endpoint 테스트 — WP-005 권한 갭 보강. `GET /leave/admin/employees`(require_hr).

연차 운영(부여 대상·조정/상세 직원 선택)이 쓸 전 직원 명부. admin 디렉토리(`GET /admin/employees`
·require_admin)와 별 권한 축 — member-role HR 도 200(현 차단 해소 핵심). 명부 쿼리·EmployeeOut
기존 그대로 소비(재정의 없음). read-only.

- HR member 200 · HR admin 200 · 비-HR(member/admin) 403 · 토큰 없음 401.
- 응답 = EmployeeOut 목록·이름순(`GET /admin/employees` 와 동일 스키마/정렬).

require_hr 실 로직(department 게이트)을 타게 get_current_employee 만 override(require_hr 직접
override X — 게이트 자체를 검증). db_session fixture 가 트랜잭션 롤백으로 격리한다.
"""

import uuid

import httpx
import pytest
from httpx import ASGITransport

from app.core.deps import get_current_employee, get_db
from app.main import app
from app.models.employee import Employee

HR = "hr"
_EMPLOYEE_OUT_FIELDS = {
    "id", "email", "name", "role", "active", "position", "department",
    "created_at", "updated_at",
}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_employee(session, *, name=None, department=None, role="member", active=True) -> Employee:
    eid = uuid.uuid4()
    emp = Employee(id=eid, email=f"{eid.hex[:8]}@x.com", name=name or f"emp-{eid.hex[:6]}",
                   role=role, active=active, department=department)
    session.add(emp)
    await session.flush()
    return emp


async def _get(db_session, *, actor=None, token="Bearer t"):
    """actor 가 있으면 get_current_employee override(require_hr 실 게이트 통과). 토큰 헤더 제어."""
    if actor is not None:
        app.dependency_overrides[get_current_employee] = lambda: actor
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        headers = {"Authorization": token} if token else {}
        async with _client() as c:
            return await c.get("/leave/admin/employees", headers=headers)
    finally:
        app.dependency_overrides.clear()


# ---- 권한 축 (핵심 — member-role HR 차단 해소) ----------------------------


@pytest.mark.asyncio
async def test_hr_roster_hr_member_200(db_session) -> None:
    """department=hr·role=member → 200 + 전 직원 명부(현 차단 해소 핵심)."""
    hr_member = await _seed_employee(db_session, department=HR, role="member")
    await _seed_employee(db_session, name="홍길동")

    resp = await _get(db_session, actor=hr_member)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list) and len(body) >= 2
    # 시드한 HR member 본인이 명부에 포함
    assert any(row["id"] == str(hr_member.id) for row in body)


@pytest.mark.asyncio
async def test_hr_roster_hr_admin_200(db_session) -> None:
    """department=hr·role=admin → 200(동일)."""
    hr_admin = await _seed_employee(db_session, department=HR, role="admin")
    resp = await _get(db_session, actor=hr_admin)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_hr_roster_non_hr_member_403(db_session) -> None:
    """department≠hr·role=member → 403(require_hr)."""
    member = await _seed_employee(db_session, department="개발", role="member")
    resp = await _get(db_session, actor=member)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_hr_roster_non_hr_admin_403(db_session) -> None:
    """department≠hr·role=admin → 403 — HR 게이트는 admin role 과 독립 축(부서 기준)."""
    admin = await _seed_employee(db_session, department="개발", role="admin")
    resp = await _get(db_session, actor=admin)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_hr_roster_no_token_401(db_session) -> None:
    """토큰 없음 → 401(인증 우선)."""
    resp = await _get(db_session, actor=None, token="")
    assert resp.status_code == 401


# ---- 응답 형태 / 정렬 (admin 디렉토리와 동일) ------------------------------


@pytest.mark.asyncio
async def test_hr_roster_schema_and_sort(db_session) -> None:
    """응답 = EmployeeOut 스키마 + admin 디렉토리(`employee_repo.list_all`)와 동일 쿼리·정렬."""
    from app.repositories import employee as employee_repo

    hr = await _seed_employee(db_session, department=HR, role="member", name="가나")
    await _seed_employee(db_session, name="Zoe")
    await _seed_employee(db_session, name="Amy")

    resp = await _get(db_session, actor=hr)
    assert resp.status_code == 200
    body = resp.json()
    # EmployeeOut 필드 집합 일치
    assert _EMPLOYEE_OUT_FIELDS <= set(body[0])
    # admin 디렉토리와 동일 명부 쿼리·정렬(DB collation order — 재정의 없음)
    expected_ids = [str(e.id) for e in await employee_repo.list_all(db_session)]
    assert [row["id"] for row in body] == expected_ids
