"""mediness provisioning 어댑터 단위 테스트 — WP-007 P3 (목 HTTP).

실 mediness 통합검증은 R-1/R-2(internal-auth 배선) 후. 여기서는 계약을 목으로 검증:
- 요청: `POST /api/v1/admin/users` body `{email,name,role}` + 헤더 `X-Internal-Auth`.
- 응답 `data.id` → 발급 id 채택(UUID).
- 에러 매핑: mediness 400(email 충돌) → 409 ConflictError · 5xx/무응답/연결불가 → 502/503.
- 트랜잭션 경계: provisioning 실패 시 employee 미생성.
- 비활성화: `DELETE /admin/users/{id}` + 헤더 · 실패 best-effort(예외 안 냄).

httpx.AsyncClient.{post,delete} 를 monkeypatch (test_auth 패턴 — ASGI 와 충돌 없는 레벨).
"""

import uuid

import httpx
import pytest

from app.config import settings
from app.core.errors import ConflictError
from app.repositories import employee as employee_repo
from app.schemas.employee import EmployeeCreate
from app.services import employee_admin
from app.services.auth_proxy import UpstreamTimeoutError, UpstreamUnavailableError
from app.services.employee_provisioning import MedinessProvisioningPort


def _resp(status: int, json_body: dict | None = None) -> httpx.Response:
    req = httpx.Request("POST", "http://m/api/v1/admin/users")
    if json_body is None:
        return httpx.Response(status, request=req)
    return httpx.Response(status, json=json_body, request=req)


# ---- provision_account 계약 + 발급 id 채택 --------------------------------


@pytest.mark.asyncio
async def test_provision_sends_internal_auth_and_adopts_id(monkeypatch) -> None:
    issued = uuid.uuid4()
    seen = {}

    async def _fake_post(self, url, **kw):
        seen["url"] = url
        seen["json"] = kw.get("json")
        seen["headers"] = kw.get("headers")
        return _resp(201, {"data": {"id": str(issued), "temp_password": "tmp", "email": "h@x.com"}})

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    got = await MedinessProvisioningPort().provision_account(
        email="h@x.com", name="홍길동", role="member"
    )
    assert got == issued                                            # data.id 채택
    assert seen["url"].endswith("/api/v1/admin/users")
    assert seen["json"] == {"email": "h@x.com", "name": "홍길동", "role": "member"}  # position 안 보냄
    assert seen["headers"]["X-Internal-Auth"] == settings.internal_auth_secret


@pytest.mark.asyncio
async def test_provision_email_conflict_400_maps_409(monkeypatch) -> None:
    async def _fake_post(self, url, **kw):
        return _resp(400, {"error_code": "VALIDATION_ERROR", "detail": {"email": "h@x.com"}})

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    with pytest.raises(ConflictError) as ei:
        await MedinessProvisioningPort().provision_account(email="h@x.com", name="n", role="member")
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_provision_5xx_maps_502(monkeypatch) -> None:
    async def _fake_post(self, url, **kw):
        return _resp(500, {"detail": "boom"})

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    with pytest.raises(UpstreamTimeoutError) as ei:
        await MedinessProvisioningPort().provision_account(email="h@x.com", name="n", role="member")
    assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_provision_connect_error_maps_503(monkeypatch) -> None:
    async def _fake_post(self, url, **kw):
        raise httpx.ConnectError("refused", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    with pytest.raises(UpstreamUnavailableError) as ei:
        await MedinessProvisioningPort().provision_account(email="h@x.com", name="n", role="member")
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_provision_missing_id_maps_502(monkeypatch) -> None:
    async def _fake_post(self, url, **kw):
        return _resp(201, {"data": {}})  # 발급 id 없음 = 비정상

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    with pytest.raises(UpstreamTimeoutError):
        await MedinessProvisioningPort().provision_account(email="h@x.com", name="n", role="member")


# ---- 트랜잭션 경계: provisioning 실패 → employee 미생성 -------------------


@pytest.mark.asyncio
async def test_create_provisioning_failure_no_employee(db_session, monkeypatch) -> None:
    async def _fake_post(self, url, **kw):
        return _resp(400, {"error_code": "VALIDATION_ERROR"})  # email 충돌

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    payload = EmployeeCreate(name="홍길동", email="dup@x.com", department="sales",
                             position="manager", role="member")
    with pytest.raises(ConflictError):
        await employee_admin.create(db_session, payload, MedinessProvisioningPort())
    # 트랜잭션 경계 — 발급 실패라 employee insert 전(미생성)
    assert await employee_repo.get_by_email(db_session, "dup@x.com") is None


# ---- deactivate_account: 계약 + best-effort -------------------------------


@pytest.mark.asyncio
async def test_deactivate_sends_delete_with_internal_auth(monkeypatch) -> None:
    acc = uuid.uuid4()
    seen = {}

    async def _fake_delete(self, url, **kw):
        seen["url"] = url
        seen["headers"] = kw.get("headers")
        return httpx.Response(200, json={"data": {"id": str(acc)}},
                              request=httpx.Request("DELETE", url))

    monkeypatch.setattr(httpx.AsyncClient, "delete", _fake_delete)
    result = await MedinessProvisioningPort().deactivate_account(acc)
    assert result is None
    assert seen["url"].endswith(f"/api/v1/admin/users/{acc}")
    assert seen["headers"]["X-Internal-Auth"] == settings.internal_auth_secret


@pytest.mark.asyncio
async def test_deactivate_failure_is_best_effort(monkeypatch) -> None:
    async def _fake_delete(self, url, **kw):
        raise httpx.ConnectError("refused", request=httpx.Request("DELETE", url))

    monkeypatch.setattr(httpx.AsyncClient, "delete", _fake_delete)
    # 예외 없이 None (ERP 비활성은 유지, 자동 재시도 없음)
    assert await MedinessProvisioningPort().deactivate_account(uuid.uuid4()) is None
