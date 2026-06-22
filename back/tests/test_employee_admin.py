"""직원 관리 CRUD + 권한 게이트(require_hr) + 미등록 거부 테스트 — SPEC-002 §5, WP-007 P2.

- 생성: provisioning 포트(fake)로 발급 id 채택 → employee.id · ERP 소유 필드 반영 · 201.
- 수정: 이름·부서·직급·role ERP-local 갱신 · 미존재 404 · 미허용 값 422.
- 비활성: soft delete(active=false, 행 보존) · 멱등(이미 false 면 204) · 미존재 404.
- 게이트: require_hr — 비-HR 403(role 무관). 미등록(유효 토큰·employee 행 없음) 403.

실 mediness provisioning HTTP·email 충돌(409)·실패(502/503)는 P3(포트 fake happy-path 까지).
get_provisioning_port override 로 발급 id 를 고정해 채택을 검증한다.
"""

import time
import uuid

import httpx
import pytest
from httpx import ASGITransport
from jose import jwt

from app.config import settings
from app.core.deps import get_current_employee, get_db, get_provisioning_port
from app.main import app
from app.models.employee import Employee
from app.repositories import employee as employee_repo


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_hr(session) -> Employee:
    """게이트 통과용 HR 주체(member-role + department=hr)."""
    eid = uuid.uuid4()
    emp = Employee(id=eid, email=f"{eid.hex[:8]}@x.com", name="HR", role="member",
                   active=True, department="hr", position="staff")
    session.add(emp)
    await session.flush()
    return emp


class _FakePort:
    """provisioning 포트 fake — 발급 id 고정 + 비활성 호출 기록."""

    def __init__(self, account_id: uuid.UUID) -> None:
        self.account_id = account_id
        self.deactivated: list[uuid.UUID] = []

    async def provision_account(self, *, email: str, name: str, role: str) -> uuid.UUID:
        return self.account_id

    async def deactivate_account(self, account_id: uuid.UUID) -> None:
        self.deactivated.append(account_id)


def _mint(sub: str) -> str:
    now = int(time.time())
    claims = {"sub": sub, "email": "e@x.com", "jti": str(uuid.uuid4()),
              "iat": now, "exp": now + 3600, "type": "access"}
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


_VALID = {"name": "홍길동", "email": "hong@x.com", "department": "dev",
          "position": "manager", "role": "member"}


# ---- 생성 (provisioning 발급 id 채택) -------------------------------------


@pytest.mark.asyncio
async def test_create_adopts_provisioned_id_and_persists(db_session) -> None:
    hr = await _seed_hr(db_session)
    issued = uuid.uuid4()
    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_provisioning_port] = lambda: _FakePort(issued)
    try:
        async with _client() as c:
            resp = await c.post("/admin/employees", json=_VALID,
                                headers={"Authorization": "Bearer t"})
        assert resp.status_code == 201
        body = resp.json()
        assert uuid.UUID(body["id"]) == issued          # 발급 id 채택
        assert body["role"] == "member" and body["position"] == "manager"
        assert body["department"] == "dev" and body["active"] is True
        # 실제 적재 확인(ERP-local)
        emp = await employee_repo.get_by_id(db_session, issued)
        assert emp is not None and emp.email == "hong@x.com"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {**_VALID, "role": "superuser"},      # 미허용 role
    {**_VALID, "position": "intern"},     # 미허용 position
    {**_VALID, "email": "not-an-email"},  # 이메일 형식
    {**_VALID, "name": ""},               # 빈 이름
    {k: v for k, v in _VALID.items() if k != "email"},  # 필수 누락
])
async def test_create_validation_422(db_session, bad: dict) -> None:
    hr = await _seed_hr(db_session)
    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_provisioning_port] = lambda: _FakePort(uuid.uuid4())
    try:
        async with _client() as c:
            resp = await c.post("/admin/employees", json=bad,
                                headers={"Authorization": "Bearer t"})
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


# ---- 수정 (ERP-local) -----------------------------------------------------


@pytest.mark.asyncio
async def test_update_applies_fields(db_session) -> None:
    hr = await _seed_hr(db_session)
    issued = uuid.uuid4()
    target = await employee_repo.create(
        db_session, id=issued, email="t@x.com", name="구이름",
        role="member", position="staff", department="dev")
    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.patch(f"/admin/employees/{target.id}",
                                 json={"name": "새이름", "position": "leader", "role": "admin"},
                                 headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "새이름" and body["position"] == "leader"
        assert body["role"] == "admin" and body["email"] == "t@x.com"  # email 불변
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_update_missing_404(db_session) -> None:
    hr = await _seed_hr(db_session)
    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.patch(f"/admin/employees/{uuid.uuid4()}",
                                 json={"name": "x"}, headers={"Authorization": "Bearer t"})
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_update_invalid_value_422(db_session) -> None:
    hr = await _seed_hr(db_session)
    issued = uuid.uuid4()
    await employee_repo.create(db_session, id=issued, email="t@x.com", name="n",
                               role="member", position="staff", department="dev")
    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.patch(f"/admin/employees/{issued}",
                                 json={"role": "root"}, headers={"Authorization": "Bearer t"})
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


# ---- 비활성 (soft delete) -------------------------------------------------


@pytest.mark.asyncio
async def test_deactivate_soft_and_idempotent(db_session) -> None:
    hr = await _seed_hr(db_session)
    issued = uuid.uuid4()
    await employee_repo.create(db_session, id=issued, email="t@x.com", name="n",
                               role="member", position="staff", department="dev")
    port = _FakePort(uuid.uuid4())
    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_provisioning_port] = lambda: port
    try:
        async with _client() as c:
            r1 = await c.delete(f"/admin/employees/{issued}",
                                headers={"Authorization": "Bearer t"})
            r2 = await c.delete(f"/admin/employees/{issued}",  # 멱등
                                headers={"Authorization": "Bearer t"})
        assert r1.status_code == 204 and r2.status_code == 204
        emp = await employee_repo.get_by_id(db_session, issued)
        assert emp is not None and emp.active is False     # 행 보존 + active=false
        assert port.deactivated == [issued]                # 비활성 push 1회(멱등 — 두번째 no-op)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_deactivate_missing_404(db_session) -> None:
    hr = await _seed_hr(db_session)
    app.dependency_overrides[get_current_employee] = lambda: hr
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_provisioning_port] = lambda: _FakePort(uuid.uuid4())
    try:
        async with _client() as c:
            resp = await c.delete(f"/admin/employees/{uuid.uuid4()}",
                                  headers={"Authorization": "Bearer t"})
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


# ---- 권한 게이트 (require_hr) + 미등록 거부 -------------------------------


@pytest.mark.asyncio
async def test_create_non_hr_403(db_session) -> None:
    # admin-role 이라도 비-HR 부서면 403(게이트 = require_hr)
    non_hr = Employee(id=uuid.uuid4(), email="d@x.com", name="개발", role="admin",
                      active=True, department="dev", position="staff")
    db_session.add(non_hr)
    await db_session.flush()
    app.dependency_overrides[get_current_employee] = lambda: non_hr
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_provisioning_port] = lambda: _FakePort(uuid.uuid4())
    try:
        async with _client() as c:
            resp = await c.post("/admin/employees", json=_VALID,
                                headers={"Authorization": "Bearer t"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_unregistered_token_forbidden_403() -> None:
    """유효 mediness JWT 지만 ERP employee 행 없음 → 403(미등록 거부, 자동 생성 금지)."""
    token = _mint(str(uuid.uuid4()))  # DB 에 없는 sub (get_current_employee override 안 함)
    async with _client() as c:
        resp = await c.get("/admin/employees", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
