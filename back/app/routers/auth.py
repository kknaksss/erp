"""인증 프록시 라우터 — SPEC-001 §3 계약.

POST /auth/login·/auth/refresh·/auth/logout → mediness `/api/v1/auth/{login,refresh,revoke}` 프록시.
ERP 는 요청 형식만 검증(422)하고, mediness 응답(성공·에러 body·status)을 **verbatim passthrough**.
mediness 무응답은 service 가 502/503 으로 변환(케이스 매트릭스).
"""

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from app.core.deps import require_access_token
from app.schemas.auth import LoginRequest, RefreshRequest
from app.services import auth_proxy

router = APIRouter(prefix="/auth", tags=["auth"])


def _passthrough(resp) -> Response:
    """mediness 응답을 status + body 그대로 전달(순수 프록시)."""
    if not resp.content:  # revoke 등 빈 바디 2xx
        return Response(status_code=resp.status_code)
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype:
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    return Response(content=resp.content, status_code=resp.status_code, media_type=ctype or None)


@router.post("/login")
async def login(body: LoginRequest) -> Response:
    """mediness 로그인 프록시 → 토큰쌍 + user (passthrough). 자격 실패 401 / 무응답 502·503.

    인증만 — employee 미러 안 함(origin, SPEC-002). 직원 행은 HR provisioning 으로 생성(P3),
    권한 판정(미등록 거부)은 current_employee(P2).
    """
    resp = await auth_proxy.login(body.email, body.password)
    return _passthrough(resp)


@router.post("/refresh")
async def refresh(body: RefreshRequest) -> Response:
    """mediness 리프레시 프록시 → 새 토큰쌍(회전). 재사용 시 mediness 401(chain revoke) passthrough."""
    resp = await auth_proxy.refresh(body.refresh_token)
    return _passthrough(resp)


@router.post("/logout")
async def logout(access_token: str = Depends(require_access_token)) -> Response:
    """mediness revoke 프록시(현재 access 토큰 첨부) → 현재+짝 토큰 폐기. 토큰 부재 시 401."""
    resp = await auth_proxy.revoke(access_token)
    return _passthrough(resp)
