"""employee roster 테스트 — SPEC-002 §5 AC.

- repository upsert 불변식(실제 erp DB, 트랜잭션-롤백): 미러 갱신 / position·department 보존 /
  active=false no-hard-delete / 미러 필드만 / updated·new 카운트.
- admin 동기 엔드포인트 권한 게이트(403/401) + 결과 형태.
- 로그인 lazy 미러 best-effort(실패해도 로그인 진행).

라이브 동기(실제 mediness pull) 는 리포트 라이브 검증 참조.
"""

import uuid

import httpx
import pytest
from httpx import ASGITransport

from app.core.deps import get_current_employee, get_db, require_access_token
from app.main import app
from app.models.employee import Employee
from app.repositories import employee as employee_repo
from app.services import roster


def _row(eid: str, *, name: str = "홍길동", role: str = "member", active: bool = True,
         email: str | None = None, **extra) -> dict:
    """mediness 유저 row 모사 (AdminUserRow / UserMe 형태)."""
    return {"id": eid, "email": email or f"{eid[:8]}@x.com", "name": name,
            "role": role, "active": active, **extra}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---- repository upsert 불변식 (실제 DB · 롤백) -----------------------------


@pytest.mark.asyncio
async def test_upsert_new_then_update_counts(db_session) -> None:
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    updated, new = await employee_repo.upsert_mirror(db_session, [_row(a), _row(b)])
    assert (updated, new) == (0, 2)

    # 재동기: a 갱신 + 신규 c
    c = str(uuid.uuid4())
    updated, new = await employee_repo.upsert_mirror(
        db_session, [_row(a, name="변경됨", role="admin"), _row(c)]
    )
    assert (updated, new) == (1, 1)
    emp_a = await employee_repo.get_by_id(db_session, uuid.UUID(a))
    assert emp_a.name == "변경됨" and emp_a.role == "admin"  # 미러 갱신


@pytest.mark.asyncio
async def test_position_department_preserved_on_resync(db_session) -> None:
    eid = str(uuid.uuid4())
    await employee_repo.upsert_mirror(db_session, [_row(eid)])
    # ERP 가 소유 필드 입력(향후 HR 화면 역할 — 여기선 직접 set)
    emp = await employee_repo.get_by_id(db_session, uuid.UUID(eid))
    emp.position, emp.department = "manager", "인사"
    await db_session.flush()

    # 재동기 — mediness row 에 position 이 있어도 미러 안 함 + ERP 소유 보존
    await employee_repo.upsert_mirror(db_session, [_row(eid, name="새이름", position="staff")])
    emp = await employee_repo.get_by_id(db_session, uuid.UUID(eid))
    assert emp.position == "manager"      # ERP 소유 보존 (mediness 'staff' 미반영)
    assert emp.department == "인사"        # ERP 소유 보존
    assert emp.name == "새이름"            # 미러는 갱신


@pytest.mark.asyncio
async def test_active_false_no_hard_delete(db_session) -> None:
    eid = str(uuid.uuid4())
    await employee_repo.upsert_mirror(db_session, [_row(eid, active=True)])
    # mediness 에서 비활성화 → active=false, 행은 보존
    updated, new = await employee_repo.upsert_mirror(db_session, [_row(eid, active=False)])
    assert (updated, new) == (1, 0)
    emp = await employee_repo.get_by_id(db_session, uuid.UUID(eid))
    assert emp is not None and emp.active is False  # hard delete 아님


@pytest.mark.asyncio
async def test_mirror_only_known_fields(db_session) -> None:
    eid = str(uuid.uuid4())
    # mediness 응답의 무관 필드(voice_registered 등)·position 은 employee 에 들어가면 안 됨
    await employee_repo.upsert_mirror(
        db_session, [_row(eid, position="cto", voice_registered=True, first_login=False)]
    )
    emp = await employee_repo.get_by_id(db_session, uuid.UUID(eid))
    assert emp.position is None  # mediness position 미러 안 함 (ERP 소유, 신규는 None)
    assert not hasattr(emp, "voice_registered")


# ---- admin 동기 엔드포인트 권한 게이트 ------------------------------------


@pytest.mark.asyncio
async def test_sync_requires_admin_member_403() -> None:
    member = Employee(id=uuid.uuid4(), email="m@x.com", name="멤버", role="member", active=True)
    app.dependency_overrides[get_current_employee] = lambda: member
    app.dependency_overrides[require_access_token] = lambda: "tok"
    try:
        async with _client() as c:
            resp = await c.post("/admin/employees/sync", headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_sync_admin_success(monkeypatch) -> None:
    admin = Employee(id=uuid.uuid4(), email="a@x.com", name="관리자", role="admin", active=True)

    async def _fake_sync(session, token):
        return {"updated": 3, "new": 2}

    app.dependency_overrides[get_current_employee] = lambda: admin
    app.dependency_overrides[require_access_token] = lambda: "admintok"
    app.dependency_overrides[get_db] = lambda: None
    monkeypatch.setattr(roster, "sync_admin_users", _fake_sync)
    try:
        async with _client() as c:
            resp = await c.post("/admin/employees/sync", headers={"Authorization": "Bearer admintok"})
        assert resp.status_code == 200
        assert resp.json() == {"updated": 3, "new": 2}
    finally:
        app.dependency_overrides.clear()


# ---- lazy 미러 best-effort -------------------------------------------------


@pytest.mark.asyncio
async def test_lazy_mirror_swallows_upstream_error(monkeypatch) -> None:
    async def _boom(path, token):
        raise roster.UpstreamUnavailableError()

    monkeypatch.setattr(roster, "_get", _boom)
    # 예외 없이 None 반환(로그인 흐름을 막지 않음)
    result = await roster.lazy_mirror_me(object(), "tok")  # session 은 안 쓰임(에러 선발생)
    assert result is None


@pytest.mark.asyncio
async def test_login_success_triggers_lazy_mirror(monkeypatch) -> None:
    from app.routers import auth as auth_router
    from app.services import auth_proxy

    body = {"data": {"access_token": "ACC", "refresh_token": "r",
                     "access_expires_at": "2026-01-01T00:00:00Z",
                     "refresh_expires_at": "2026-01-08T00:00:00Z",
                     "user": {"id": str(uuid.uuid4()), "email": "e@x.com",
                              "name": "n", "first_login": False}}}

    async def _fake_login(email, password):
        return httpx.Response(200, json=body, request=httpx.Request("POST", "http://m/x"))

    seen = {}

    async def _fake_mirror(session, access):
        seen["access"] = access

    monkeypatch.setattr(auth_proxy, "login", _fake_login)
    monkeypatch.setattr(auth_router.roster, "lazy_mirror_me", _fake_mirror)
    async with _client() as c:
        resp = await c.post("/auth/login", json={"email": "a@b.com", "password": "pw"})
    assert resp.status_code == 200
    assert seen["access"] == "ACC"  # data.access_token 으로 lazy 미러 트리거
