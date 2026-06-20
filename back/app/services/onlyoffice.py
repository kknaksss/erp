"""ONLYOFFICE 통합 service — WP-006 Phase 3 (architecture §JWT·§저장 콜백·§토폴로지 leg②③④).

임베드 협업 편집의 BE 측 계약 3가지: ① editor config 발급(서명) · ② 파일 download(원본 제공) ·
③ 저장 콜백(편집본 fetch → 새 version append). 실시간 세션은 DocServer 가 보유(ephemeral, PG 미적재).

JWT 는 `onlyoffice_jwt_secret`(HS256)으로 서명/검증한다 — **mediness `jwt_secret` 재사용 금지**
(신뢰 도메인 다름: 직원 신원 vs DocServer↔BE 문서 세션 무결성, architecture §JWT). 편집본 fetch 의
연결 실패는 auth_proxy 패턴대로 ConnectError→503 / TransportError→502(케이스 매트릭스 정합).
version append 는 기존 `document_storage.write_version` + `repo.create_version` 소비(재정의 금지).
"""

import logging
from pathlib import Path
from uuid import UUID

import httpx
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import AppError, InvalidTokenError, NotFoundError
from app.models.document import Document, Version
from app.models.employee import Employee
from app.models.enums import DocumentType
from app.repositories import document as repo
from app.repositories import document_storage as storage
from app.services.document_access import require_space_member

logger = logging.getLogger(__name__)

# 편집본 fetch 타임아웃 — auth_proxy 와 동일 합리적 최소(connect 5s / 전체 10s). 재시도 없음.
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

_ALG = "HS256"

# ONLYOFFICE documentType 매핑 — docx=word 표면 / xlsx=cell(스프레드시트) 표면.
_DOCUMENT_TYPE = {DocumentType.WORD: "word", DocumentType.EXCEL: "cell"}

# 저장 트리거 status (ONLYOFFICE 규격): 2=MustSave, 6=ForceSave → 새 버전 보존.
_SAVE_STATUSES = (2, 6)

# 다운로드 stream MIME — 확장자별 OOXML content-type.
_MEDIA_TYPE = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class DocServerUnavailableError(AppError):
    """DocServer 연결 불가(편집본 fetch connect 실패) — 503(케이스 매트릭스)."""

    error_code = "DOCSERVER_UNAVAILABLE"
    status_code = 503
    message = "편집기를 불러오지 못했습니다. 잠시 후 다시 시도해주세요"


class DocServerTimeoutError(AppError):
    """DocServer 무응답(편집본 fetch timeout/read 실패) — 502(케이스 매트릭스)."""

    error_code = "DOCSERVER_TIMEOUT"
    status_code = 502
    message = "편집기를 불러오지 못했습니다. 잠시 후 다시 시도해주세요"


# ---- JWT 서명 / 검증 -------------------------------------------------------


def sign(payload: dict) -> str:
    """editor config payload 를 onlyoffice secret 으로 HS256 서명 → token."""
    return jwt.encode(payload, settings.onlyoffice_jwt_secret, algorithm=_ALG)


def verify(token: str) -> dict:
    """ONLYOFFICE JWT 검증(download/callback 인증). 실패 시 401(InvalidTokenError)."""
    try:
        return jwt.decode(token, settings.onlyoffice_jwt_secret, algorithms=[_ALG])
    except JWTError as exc:
        raise InvalidTokenError("ONLYOFFICE 토큰이 유효하지 않습니다") from exc


# ---- URL 구성 (DocServer 가 해석 가능한 절대 URL — config 에서, 하드코딩 금지) ----


def _base() -> str:
    return settings.onlyoffice_callback_base_url.rstrip("/")


def download_url(document_id: UUID, version_no: int) -> str:
    return f"{_base()}/documents/files/{document_id}/versions/{version_no}/download"


def callback_url(document_id: UUID) -> str:
    return f"{_base()}/documents/files/{document_id}/callback"


# ---- ① editor config 발급 --------------------------------------------------


def build_editor_config(document: Document, version: Version) -> dict:
    """문서의 최신 version 으로 ONLYOFFICE editor config 구성 + `token` 서명.

    `document.key` = `{document_id}_{version_no}`(version 식별·캐시 무효화). url 2종 절대 URL.
    """
    config = {
        "document": {
            "key": f"{document.id}_{version.version_no}",
            "fileType": version.ext,
            "title": f"{document.name}.{version.ext}",
            "url": download_url(document.id, version.version_no),
        },
        "documentType": _DOCUMENT_TYPE[document.type],
        "editorConfig": {
            "callbackUrl": callback_url(document.id),
        },
    }
    config["token"] = sign(config)
    return config


async def editor_config(session: AsyncSession, employee: Employee, document_id: UUID) -> dict:
    """문서 열기 → editor config(서명). 스페이스 멤버십 enforce(비멤버 403)·미존재/버전없음 404."""
    doc = await repo.get_document(session, document_id)
    if doc is None:
        raise NotFoundError("문서를 찾을 수 없습니다")
    space = await repo.get_space(session, doc.space_id)
    if space is None:
        raise NotFoundError("스페이스를 찾을 수 없습니다")
    require_space_member(employee, space)  # 문서 열기 권한 = 스페이스 멤버십
    version = await repo.latest_version(session, document_id)
    if version is None:
        raise NotFoundError("문서 버전을 찾을 수 없습니다")
    return build_editor_config(doc, version)


# ---- ② 파일 download (DocServer→BE) ---------------------------------------


async def get_version_binary(
    session: AsyncSession, volume_root: Path, document_id: UUID, version_no: int
) -> tuple[bytes, str]:
    """특정 version 바이너리 + MIME — DocServer 가 원본 fetch. 미존재 version 404."""
    version = await repo.get_version_by_no(session, document_id, version_no)
    if version is None:
        raise NotFoundError("문서 버전을 찾을 수 없습니다")
    content = storage.read_version(volume_root, version.storage_path)
    media_type = _MEDIA_TYPE.get(version.ext, "application/octet-stream")
    return content, media_type


# ---- ③ 저장 콜백 (DocServer→BE) -------------------------------------------


async def _fetch_edited(url: str) -> bytes:
    """편집본 바이너리 fetch — 연결 실패 502/503(auth_proxy 패턴)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except httpx.ConnectError as exc:  # DocServer down/연결 거부 → 503
        raise DocServerUnavailableError() from exc
    except httpx.TransportError as exc:  # timeout·read 실패 → 502
        raise DocServerTimeoutError() from exc


async def handle_callback(
    session: AsyncSession, volume_root: Path, document_id: UUID, status: int, edited_url: str | None
) -> dict:
    """저장 콜백 status 처리 → ack `{"error": 0}`(ONLYOFFICE 규격).

    - status 2/6(MustSave/ForceSave): 편집본 fetch → 새 version append(version_no=max+1).
    - status 1/4(editing/closed·무변경): 저장 없이 ack.
    - status 3/7(error): 로그만, 저장 안 함 + ack.
    """
    if status in _SAVE_STATUSES:
        if not edited_url:
            raise NotFoundError("편집본 URL 이 콜백에 없습니다")
        await _append_version(session, volume_root, document_id, edited_url)
    elif status in (3, 7):
        logger.warning("ONLYOFFICE 콜백 오류 status=%s document=%s (저장 안 함)", status, document_id)
    # 1/4 및 기타 → 저장 없이 ack
    return {"error": 0}


async def _append_version(
    session: AsyncSession, volume_root: Path, document_id: UUID, edited_url: str
) -> Version:
    """편집본을 새 version 으로 보존(immutable append) — 모든 저장=버전 1건(architecture invariant)."""
    doc = await repo.get_document(session, document_id)
    if doc is None:
        raise NotFoundError("문서를 찾을 수 없습니다")
    latest = await repo.latest_version(session, document_id)
    if latest is None:
        raise NotFoundError("문서 버전을 찾을 수 없습니다")

    content = await _fetch_edited(edited_url)
    next_no = latest.version_no + 1
    rel = storage.write_version(
        volume_root, doc.space_id, doc.id, version_no=next_no, ext=latest.ext, content=content
    )
    version = await repo.create_version(session, doc.id, next_no, latest.ext, rel, len(content))
    await session.commit()
    return version
