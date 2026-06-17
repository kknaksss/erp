"""mediness 인증 라이프사이클 프록시 — login/refresh/revoke.

ERP 는 비밀번호를 보관하지 않고 인증 라이프사이클을 mediness 에 위임한다(SPEC-001 §1).
실제 호출 base = `${MEDINESS_API_URL}/api/v1/auth` (mediness openapi 확인 — `/api/v1` prefix).

응답·에러는 호출부(router)에서 **verbatim passthrough**. 여기서는 mediness 호출과
연결 실패(무응답) → 502/503 변환만 담당한다(SPEC-001 케이스 매트릭스 "mediness 연결 실패").
재시도/타임아웃 전략은 WP-001 Open Issue — 코드가 SoT (아래 합리적 최소값).
"""

import httpx

from app.config import settings
from app.core.errors import AppError

# 타임아웃 — connect 5s / 전체 10s (합리적 최소; WP Open Issue, 코드 SoT). 재시도 없음(MVP).
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class UpstreamUnavailableError(AppError):
    """mediness 연결 불가(connect 실패) — 503."""

    error_code = "UPSTREAM_UNAVAILABLE"
    status_code = 503
    message = "잠시 후 다시 시도해주세요"


class UpstreamTimeoutError(AppError):
    """mediness 무응답(timeout/read 실패) — 502."""

    error_code = "UPSTREAM_TIMEOUT"
    status_code = 502
    message = "잠시 후 다시 시도해주세요"


def _base() -> str:
    return f"{settings.mediness_api_url.rstrip('/')}/api/v1/auth"


async def _post(path: str, *, json: dict | None = None, headers: dict | None = None) -> httpx.Response:
    """mediness 로 POST. 연결 실패는 502/503 으로 변환, 그 외 응답은 그대로 반환(passthrough)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            return await client.post(f"{_base()}{path}", json=json, headers=headers)
    except httpx.ConnectError as exc:  # 대상 down/연결 거부 → 503
        raise UpstreamUnavailableError() from exc
    except httpx.TransportError as exc:  # timeout·read 실패 등 → 502 (ConnectError 는 위에서 선처리)
        raise UpstreamTimeoutError() from exc


async def login(email: str, password: str) -> httpx.Response:
    """mediness POST /api/v1/auth/login → 토큰쌍 + user (passthrough)."""
    return await _post("/login", json={"email": email, "password": password})


async def refresh(refresh_token: str) -> httpx.Response:
    """mediness POST /api/v1/auth/refresh → 새 토큰쌍(회전). 재사용 시 mediness 가 401(chain revoke)."""
    return await _post("/refresh", json={"refresh_token": refresh_token})


async def revoke(access_token: str) -> httpx.Response:
    """mediness POST /api/v1/auth/revoke (Authorization 헤더로 현재 access 토큰 첨부) → 현재+짝 폐기."""
    return await _post("/revoke", headers={"Authorization": f"Bearer {access_token}"})
