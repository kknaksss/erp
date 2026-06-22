"""직원 명부 목록 테스트 — SPEC-002 §3 (origin, 미러/동기 제거 후).

- repository list_all 정렬(실제 erp DB, 트랜잭션-롤백): 이름순.
- GET /admin/employees 권한 게이트(admin 200 / member 403 / 토큰없음 401) + 응답 형태.

직원 정보는 ERP 소유(origin) — mediness pull·"동기화" 액션·lazy 미러는 제거됨(WP-007 P1).
"""

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from httpx import ASGITransport

from app.core.deps import get_current_employee, get_db
from app.main import app
from app.models.employee import Employee
from app.repositories import employee as employee_repo
from app.routers import employees as employees_router


def _emp(eid: str, *, name: str = "홍길동", role: str = "member", active: bool = True,
         email: str | None = None) -> Employee:
    return Employee(id=uuid.UUID(eid), email=email or f"{eid[:8]}@x.com", name=name,
                    role=role, active=active)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---- repository list_all 정렬 (실제 DB · 롤백) ----------------------------


@pytest.mark.asyncio
async def test_list_all_returns_ordered_by_name(db_session) -> None:
    # 고유 prefix 로 내가 넣은 행만 추출(실제 DB 에 기존 행이 있어도 안전). 상대 순서 = 이름순 검증.
    pfx = f"zzlist-{uuid.uuid4().hex[:6]}-"
    a, b, c = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    mine = {a, b, c}
    for eid, nm in ((a, "C"), (b, "A"), (c, "B")):
        db_session.add(_emp(eid, name=pfx + nm))
    await db_session.flush()
    rows = await employee_repo.list_all(db_session)
    ordered = [e.name for e in rows if str(e.id) in mine]
    assert ordered == [pfx + "A", pfx + "B", pfx + "C"]  # 이름순(상대 순서)


# ---- 목록 엔드포인트 권한 게이트 (require_hr — WP-007 P2 전환) ------------


@pytest.mark.asyncio
async def test_list_employees_hr_200(monkeypatch) -> None:
    # member-role 이라도 department=="hr" 이면 통과(게이트 = require_hr, role 아님)
    hr = Employee(id=uuid.uuid4(), email="hr@x.com", name="인사", role="member",
                  active=True, department="hr")
    sample = [
        Employee(id=uuid.uuid4(), email="e1@x.com", name="직원1", role="member", active=True,
                 position="staff", department="개발"),
        Employee(id=uuid.uuid4(), email="e2@x.com", name="직원2", role="admin", active=False,
                 position="staff", department=None),
    ]
    # ORM 직렬화에 필요한 timestamps 채움(실제는 server_default; 테스트 객체는 수동)
    now = datetime(2026, 6, 18, tzinfo=UTC)
    for e in sample:
        e.created_at = e.updated_at = now

    async def _fake_list(session):
        return sample

    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: None
    monkeypatch.setattr(employees_router.employee_repo, "list_all", _fake_list)
    try:
        async with _client() as c:
            resp = await c.get("/admin/employees", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert set(data[0]) == {"id", "email", "name", "role", "active",
                                "position", "department", "created_at", "updated_at"}
        assert data[0]["position"] == "staff" and data[1]["active"] is False
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_employees_non_hr_403() -> None:
    # admin-role 이라도 비-HR 부서면 403(게이트 = require_hr — require_admin 프록시 게이트 폐기)
    non_hr = Employee(id=uuid.uuid4(), email="m@x.com", name="개발자", role="admin",
                      active=True, department="개발")
    app.dependency_overrides[get_current_employee] = lambda: non_hr
    app.dependency_overrides[get_db] = lambda: None
    try:
        async with _client() as c:
            resp = await c.get("/admin/employees", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_employees_no_token_401() -> None:
    async with _client() as c:
        resp = await c.get("/admin/employees")
    assert resp.status_code == 401
