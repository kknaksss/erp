"""JWT 로컬 검증 — 업무 요청용.

mediness 가 발급한 access 토큰을 공유 `JWT_SECRET`(HS256)으로 ERP 가 로컬 검증한다
(매 요청 mediness 호출 없음 — SPEC-001 §S-2). 서명·만료·`type=access` 를 확인하고
`sub`(UUID) 를 추출한다. 검증 실패는 InvalidTokenError(401).
"""

from jose import JWTError, jwt

from app.config import settings
from app.core.errors import InvalidTokenError


def decode_access_token(token: str) -> dict:
    """access 토큰 로컬 검증 → payload. 실패 시 InvalidTokenError(401).

    검증: 서명(공유 secret·HS256) · 만료(`exp`) · `type == access`.
    토큰 claim: `sub`(UUID)·`email`·`jti`·`iat`·`exp`·`type` (mediness 발급).
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            # aud claim 미사용 (mediness 토큰에 없음) — 검증 끔
            options={"verify_aud": False},
        )
    except JWTError as exc:  # 서명 불일치·만료·디코드 실패 모두 포함
        raise InvalidTokenError() from exc

    if payload.get("type") != "access":
        raise InvalidTokenError("access 토큰이 아닙니다")
    if not payload.get("sub"):
        raise InvalidTokenError()

    return payload
