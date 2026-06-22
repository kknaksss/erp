"""mediness 로그인 계정 라이프사이클 연동 — provisioning 포트(P3 seam). WP-007 P2.

직원 생성 시 mediness 로 로그인 계정을 1회 push(발급 id 채택) / 비활성 시 로그인 차단 push 하는
**계약 형태를 포트(인터페이스)로 고정**한다. P2 는 이 포트까지만 — 실 mediness internal-auth HTTP
호출·email 충돌(409)·실패(502/503)·발급 id 실연동은 **P3** 가 어댑터로 끼운다.

SPEC-002 §3 ERP↔mediness 계정 연동. create 는 provisioning 성공에 의존(id 수령 → employee.id
채택, 트랜잭션 경계) — 그 형태를 포트가 표현하고, P2 fake 는 로컬 UUID 로 happy-path 를 단위 검증한다.
"""

import logging
from typing import Protocol
from uuid import UUID, uuid4

import httpx

from app.config import settings
from app.core.errors import ConflictError
from app.services.auth_proxy import UpstreamTimeoutError, UpstreamUnavailableError

logger = logging.getLogger(__name__)

# mediness 계정 호출 타임아웃 — auth_proxy 와 동일(connect 5s / 전체 10s). 재시도 없음(OQ-4 v1).
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class ProvisioningPort(Protocol):
    """mediness 계정 생성/비활성 push 의 추상 계약(P3 이 실 internal-auth 어댑터로 구현)."""

    async def provision_account(self, *, email: str, name: str, role: str) -> UUID:
        """로그인 계정 발급(1회 push) → 발급 계정 id 반환(employee.id 로 채택).

        P3: mediness `POST /admin/users` internal-auth. 실패 시 예외(502/503)·email 충돌 409 —
        P2 범위 밖(포트 happy-path 까지).
        """
        ...

    async def deactivate_account(self, account_id: UUID) -> None:
        """로그인 계정 비활성화 push(퇴사자 로그인 차단). P3: mediness `DELETE /admin/users/{id}`."""
        ...


class FakeProvisioningPort:
    """단위 테스트용 어댑터 — 로컬 UUID 발급, 비활성 no-op. dependency override 로 주입."""

    async def provision_account(self, *, email: str, name: str, role: str) -> UUID:
        return uuid4()

    async def deactivate_account(self, account_id: UUID) -> None:
        return None


class MedinessProvisioningPort:
    """실 mediness internal-auth 어댑터 — `POST/DELETE /api/v1/admin/users`(R-1/R-2). WP-007 P3.

    인증 = `X-Internal-Auth: <internal_auth_secret>` 헤더(mediness `verify_internal_auth`). 행위자
    mediness role 무관. 실 mediness 통합검증은 R-1/R-2 배선 후(이 어댑터는 목 HTTP 로 계약 검증).
    """

    def _admin_base(self) -> str:
        return f"{settings.mediness_api_url.rstrip('/')}/api/v1/admin/users"

    def _headers(self) -> dict[str, str]:
        return {"X-Internal-Auth": settings.internal_auth_secret}

    async def provision_account(self, *, email: str, name: str, role: str) -> UUID:
        """mediness `POST /admin/users` → 발급 `data.id` 반환(employee.id 채택).

        body = `{email,name,role}`(position 은 mediness server_default `staff` — SPEC-002 §3 "ERP
        position push 안 함"과 정합). email 충돌(mediness 400 VALIDATION_ERROR) → 409 ConflictError
        (멱등키=email·중복 생성 금지, OQ-4). 그 외 실패(5xx·무응답·연결불가) → 502/503(employee 미생성).
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    self._admin_base(),
                    json={"email": email, "name": name, "role": role},
                    headers=self._headers(),
                )
        except httpx.ConnectError as exc:
            raise UpstreamUnavailableError() from exc
        except httpx.TransportError as exc:
            raise UpstreamTimeoutError() from exc

        if resp.status_code in (200, 201):
            account_id = (resp.json().get("data") or {}).get("id")
            if not account_id:
                raise UpstreamTimeoutError()  # 비정상 응답(발급 id 없음) — employee 미생성
            return UUID(str(account_id))
        if resp.status_code == 400:
            # mediness email UNIQUE 위반 = 400 VALIDATION_ERROR (ERP 는 role/position 선검증 → 400=email 충돌)
            raise ConflictError("이미 계정이 존재합니다")
        raise UpstreamTimeoutError()  # 5xx 등 업스트림 실패 — 트랜잭션 경계(employee 미생성)

    async def deactivate_account(self, account_id: UUID) -> None:
        """mediness `DELETE /admin/users/{id}`(로그인 차단 push) — best-effort.

        실패해도 ERP 직원 비활성은 유지(origin) + 자동 재시도 없음(OQ-4 v1) — 로그만 남긴다.
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.delete(
                    f"{self._admin_base()}/{account_id}", headers=self._headers()
                )
            if resp.status_code >= 400:
                logger.warning(
                    "mediness 계정 비활성화 push 실패 — %s (status %s)", account_id, resp.status_code
                )
        except httpx.HTTPError:
            logger.exception("mediness 계정 비활성화 push 오류 (무시 — ERP 비활성 유지)")
