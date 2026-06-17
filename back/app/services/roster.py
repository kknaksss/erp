"""roster 동기 — mediness 유저 → employee 미러.

- lazy 미러(SPEC-001): 로그인 직후 본인 `/auth/me` → 본인 employee upsert (best-effort).
- admin 동기(SPEC-002): admin 토큰으로 `/admin/users` pull → 전 직원 upsert.

mediness GET 호출은 토큰을 Authorization 헤더로 첨부. 무응답은 502/503 (auth_proxy 의
업스트림 에러 클래스 재사용). 미러 규칙·보존·hard-delete 금지는 repository 가 담당.
"""

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.repositories import employee as employee_repo
from app.services.auth_proxy import UpstreamTimeoutError, UpstreamUnavailableError

logger = logging.getLogger(__name__)

# roster pull 은 다건이라 약간 넉넉히 (connect 5s / total 15s). 재시도 없음(MVP).
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _base() -> str:
    return f"{settings.mediness_api_url.rstrip('/')}/api/v1"


async def _get(path: str, access_token: str) -> httpx.Response:
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            return await client.get(f"{_base()}{path}", headers=headers)
    except httpx.ConnectError as exc:
        raise UpstreamUnavailableError() from exc
    except httpx.TransportError as exc:
        raise UpstreamTimeoutError() from exc


async def lazy_mirror_me(session: AsyncSession, access_token: str) -> None:
    """로그인 직후 본인 `/auth/me` → 본인 employee upsert (best-effort).

    실패해도 로그인은 성공해야 하므로 예외를 삼킨다(로그만). 200 일 때만 미러.
    """
    try:
        resp = await _get("/auth/me", access_token)
        if resp.status_code != 200:
            logger.warning("lazy 미러 skip — /auth/me %s", resp.status_code)
            return
        data = resp.json().get("data", {})
        if data.get("id"):
            await employee_repo.upsert_mirror(session, [data])
            await session.commit()
    except Exception:  # noqa: BLE001 — best-effort, 로그인 흐름을 막지 않음
        logger.exception("lazy 미러 실패 (무시하고 로그인 진행)")


async def sync_admin_users(session: AsyncSession, admin_access_token: str) -> dict[str, int]:
    """admin 토큰으로 `/admin/users` pull → 전 직원 upsert. 반환 {updated, new}.

    무응답/비정상 → 502·503 (케이스 매트릭스). 미러 필드만 갱신, position/department 보존.
    """
    resp = await _get("/admin/users", admin_access_token)
    if resp.status_code != 200:
        # mediness 가 200 이외(권한/무응답) → 업스트림 실패로 취급 (ERP admin 게이트는 라우터에서 선처리)
        raise UpstreamTimeoutError()
    rows = resp.json().get("data", [])
    updated, new = await employee_repo.upsert_mirror(session, rows)
    await session.commit()
    return {"updated": updated, "new": new}
