"""인증 프록시 + 토큰 로컬 검증 테스트 — SPEC-001 §3·§5.

- 요청 형식 검증(422)
- 로컬 토큰 검증(200/401) + 검증 시 mediness 호출 0
- 프록시 passthrough(성공·에러 status+body verbatim) + 무응답 502/503
- logout revoke 전달

router 테스트는 `auth_proxy.{login,refresh,revoke}` 를 monkeypatch (ASGI 테스트 클라이언트도
httpx.AsyncClient 라 그 레벨 패치는 충돌). service 의 502/503 변환은 직접 호출로 단위 검증.
시간 의존 토큰은 직접 mint.
"""

import time
import uuid

import httpx
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport
from jose import jwt

from app.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.core.errors import AppError
from app.main import app, app_error_handler
from app.services import auth_proxy

# ---- 토큰 mint 헬퍼 (공유 secret) ----------------------------------------


def _mint(*, sub: str | None = None, token_type: str = "access", exp_delta: int = 3600,
          secret: str | None = None) -> str:
    now = int(time.time())
    claims = {
        "sub": sub or str(uuid.uuid4()),
        "email": "e@x.com",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + exp_delta,
        "type": token_type,
    }
    return jwt.encode(claims, secret or settings.jwt_secret, algorithm=settings.jwt_algorithm)


# 로컬 검증 검증용 보호 라우트 — 비-SPEC 엔드포인트를 prod app 에 안 싣기 위해 별도 앱에 mount.
# AppError 핸들러도 동일 등록(없으면 InvalidTokenError 가 401 로 변환 안 됨).
_probe_app = FastAPI()
_probe_app.include_router(app.router)
_probe_app.add_exception_handler(AppError, app_error_handler)


@_probe_app.get("/_protected")
async def _protected(user: CurrentUser = Depends(get_current_user)) -> dict:
    return {"id": str(user.id), "email": user.email}


def _client(target: FastAPI = app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=target), base_url="http://test")


def _fake_response(status: int, json_body: dict | None) -> httpx.Response:
    req = httpx.Request("POST", "http://m/api/v1/auth/x")
    if json_body is None:
        return httpx.Response(status, request=req)
    return httpx.Response(status, json=json_body, request=req)


# ---- 요청 형식 검증 (422) -------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"email": "not-an-email", "password": "x"},  # 이메일 형식 위반
    {"email": "a@b.com", "password": ""},          # 빈 비밀번호
    {"email": "a@b.com"},                          # password 누락
    {},                                            # 빈 바디
])
async def test_login_validation_422(body: dict) -> None:
    async with _client() as c:
        resp = await c.post("/auth/login", json=body)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_refresh_validation_422() -> None:
    async with _client() as c:
        resp = await c.post("/auth/refresh", json={"refresh_token": ""})
    assert resp.status_code == 422


# ---- 로컬 토큰 검증 (mediness 호출 없이) ----------------------------------


@pytest.mark.asyncio
async def test_protected_pass_with_valid_access_token(monkeypatch) -> None:
    # 로컬 검증 경로에서 mediness 가 절대 안 불려야 함(SPEC-001 §5)
    def _boom(*a, **k):
        raise AssertionError("로컬 검증 경로에서 mediness 호출 발생")

    monkeypatch.setattr(httpx.AsyncClient, "post", _boom)

    sub = str(uuid.uuid4())
    token = _mint(sub=sub)
    async with _client(_probe_app) as c:
        resp = await c.get("/_protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["id"] == sub


@pytest.mark.asyncio
@pytest.mark.parametrize("token_kw", [
    {"exp_delta": -10},              # 만료
    {"secret": "wrong-secret"},      # 위조(서명 불일치)
    {"token_type": "refresh"},       # type=refresh
])
async def test_protected_401(token_kw: dict) -> None:
    token = _mint(**token_kw)
    async with _client(_probe_app) as c:
        resp = await c.get("/_protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_protected_401_no_header() -> None:
    async with _client(_probe_app) as c:
        resp = await c.get("/_protected")
    assert resp.status_code == 401


# ---- 프록시 passthrough (성공 + 에러 verbatim) — service 레벨 patch -------


@pytest.mark.asyncio
async def test_login_success_passthrough(monkeypatch) -> None:
    # mediness 실제 형태: data 엔벨로프 (FE 는 data.* 소비 — 리포트 "다른 팀 영향" 참조)
    body = {"data": {"access_token": "a", "refresh_token": "r",
                     "access_expires_at": "2026-01-01T00:00:00Z",
                     "refresh_expires_at": "2026-01-08T00:00:00Z",
                     "user": {"id": str(uuid.uuid4()), "email": "e@x.com",
                              "name": "홍길동", "first_login": False}}}

    async def _fake(email, password):
        return _fake_response(200, body)

    monkeypatch.setattr(auth_proxy, "login", _fake)
    async with _client() as c:
        resp = await c.post("/auth/login", json={"email": "a@b.com", "password": "pw"})
    assert resp.status_code == 200
    assert resp.json() == body  # verbatim (엔벨로프 포함)


@pytest.mark.asyncio
async def test_login_401_passthrough(monkeypatch) -> None:
    # mediness 자격 실패 401 — 로컬 토큰 401 과 별개 경로(케이스 매트릭스의 두 401)
    body = {"detail": "invalid credentials"}

    async def _fake(email, password):
        return _fake_response(401, body)

    monkeypatch.setattr(auth_proxy, "login", _fake)
    async with _client() as c:
        resp = await c.post("/auth/login", json={"email": "a@b.com", "password": "pw"})
    assert resp.status_code == 401
    assert resp.json() == body


@pytest.mark.asyncio
async def test_refresh_reuse_chain_revoke_401_passthrough(monkeypatch) -> None:
    async def _fake(refresh_token):
        return _fake_response(401, {"detail": "token reused"})

    monkeypatch.setattr(auth_proxy, "refresh", _fake)
    async with _client() as c:
        resp = await c.post("/auth/refresh", json={"refresh_token": "reused"})
    assert resp.status_code == 401


# ---- mediness 무응답 → 502 / 503 (service 단위 + router 매핑) -------------


@pytest.mark.asyncio
async def test_service_connect_error_raises_503(monkeypatch) -> None:
    async def _fake(self, url, **kw):
        raise httpx.ConnectError("refused", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake)
    with pytest.raises(auth_proxy.UpstreamUnavailableError) as ei:
        await auth_proxy.login("a@b.com", "pw")
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_service_timeout_raises_502(monkeypatch) -> None:
    async def _fake(self, url, **kw):
        raise httpx.ReadTimeout("slow", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake)
    with pytest.raises(auth_proxy.UpstreamTimeoutError) as ei:
        await auth_proxy.refresh("r")
    assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_router_maps_upstream_error_to_503(monkeypatch) -> None:
    async def _fake(email, password):
        raise auth_proxy.UpstreamUnavailableError()

    monkeypatch.setattr(auth_proxy, "login", _fake)
    async with _client() as c:
        resp = await c.post("/auth/login", json={"email": "a@b.com", "password": "pw"})
    assert resp.status_code == 503
    assert resp.json()["error_code"] == "UPSTREAM_UNAVAILABLE"


# ---- logout → revoke 전달 -------------------------------------------------


@pytest.mark.asyncio
async def test_logout_forwards_token_to_revoke(monkeypatch) -> None:
    seen = {}

    async def _fake(access_token):
        seen["token"] = access_token
        return _fake_response(200, None)  # 빈 바디 2xx

    monkeypatch.setattr(auth_proxy, "revoke", _fake)
    token = _mint()
    async with _client() as c:
        resp = await c.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert seen["token"] == token


@pytest.mark.asyncio
async def test_logout_no_token_401() -> None:
    async with _client() as c:
        resp = await c.post("/auth/logout")
    assert resp.status_code == 401


# ---- service base URL prefix 정합 -----------------------------------------


def test_proxy_base_has_api_v1_prefix() -> None:
    assert auth_proxy._base().endswith("/api/v1/auth")
