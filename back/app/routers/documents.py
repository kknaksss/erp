"""문서관리 라우터 — 트리·폴더 CRUD·문서 생성/업로드/삭제·버전 목록. WP-006 Phase 2.

SPEC-006 §3 API 계약. 권한 = 스페이스 멤버십(service 가 space 적재 후 enforce — resource-level,
`require_hr` 아님). 라우터는 `get_current_employee` 로 신원만 확인하고 service 에 위임.
fs volume root 는 `get_volume_root` dep 로 주입(테스트는 override 로 tmp_path).
ONLYOFFICE 3 엔드포인트(editor config·download·콜백)는 Phase 3 — 이 라우터 범위 밖.
"""

import io
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import get_current_employee, get_db
from app.core.errors import InvalidTokenError
from app.models.employee import Employee
from app.schemas.document import (
    DocumentCreateIn,
    DocumentOut,
    FolderCreateIn,
    FolderOut,
    FolderRenameIn,
    SpaceNode,
    VersionOut,
    to_space_node,
)
from app.schemas.onlyoffice import CallbackAck, CallbackIn
from app.services import document_tree, onlyoffice

router = APIRouter(prefix="/documents", tags=["documents"])

# ONLYOFFICE outbound(download/callback) 인증용 — employee 토큰 없음, DocServer JWT 만.
_onlyoffice_bearer = HTTPBearer(auto_error=False)


def get_volume_root() -> Path:
    """fs volume root — 기본 settings(배포 운영 경로). 테스트는 dependency_overrides 로 tmp_path."""
    return Path(settings.document_volume_root)


async def require_onlyoffice_jwt(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_onlyoffice_bearer)],
) -> dict:
    """DocServer→BE(download/callback) ONLYOFFICE JWT 검증 — 부재/위조 401. employee 토큰 아님."""
    if creds is None:
        raise InvalidTokenError("ONLYOFFICE 토큰이 없습니다")
    return onlyoffice.verify(creds.credentials)


@router.get("/tree", response_model=list[SpaceNode])
async def get_tree(
    employee: Annotated[Employee, Depends(get_current_employee)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[SpaceNode]:
    """접근 가능한 스페이스(부서=본인 부서·개인=본인, admin=부서 전체)의 트리. 멤버십 밖 미노출."""
    raw = await document_tree.tree(session, employee)
    return [to_space_node(r) for r in raw]


# ---- 폴더 CRUD ------------------------------------------------------------


@router.post("/folders", response_model=FolderOut)
async def create_folder(
    payload: FolderCreateIn,
    employee: Annotated[Employee, Depends(get_current_employee)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> FolderOut:
    """폴더 생성 — 이름 빈값 422·멤버십 밖 403·상위 폴더 다른 space 422·미존재 404."""
    folder = await document_tree.create_folder(
        session, employee, payload.space_id, payload.parent_id, payload.name
    )
    return FolderOut.model_validate(folder)


@router.patch("/folders/{folder_id}", response_model=FolderOut)
async def rename_folder(
    folder_id: UUID,
    payload: FolderRenameIn,
    employee: Annotated[Employee, Depends(get_current_employee)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> FolderOut:
    """폴더 이름변경 — 이름 빈값 422·멤버십 밖 403·미존재 404."""
    folder = await document_tree.rename_folder(session, employee, folder_id, payload.name)
    return FolderOut.model_validate(folder)


@router.delete("/folders/{folder_id}", status_code=204)
async def delete_folder(
    folder_id: UUID,
    employee: Annotated[Employee, Depends(get_current_employee)],
    session: Annotated[AsyncSession, Depends(get_db)],
    volume_root: Annotated[Path, Depends(get_volume_root)],
) -> None:
    """폴더 삭제 — 하위 폴더/문서/버전 + fs 바이너리 함께 제거. 멤버십 밖 403·미존재 404."""
    await document_tree.delete_folder(session, employee, volume_root, folder_id)


# ---- 문서 생성 / 업로드 / 삭제 / 버전 -------------------------------------


@router.post("/files", response_model=DocumentOut)
async def create_document(
    payload: DocumentCreateIn,
    employee: Annotated[Employee, Depends(get_current_employee)],
    session: Annotated[AsyncSession, Depends(get_db)],
    volume_root: Annotated[Path, Depends(get_volume_root)],
) -> DocumentOut:
    """빈 .docx/.xlsx 생성 — fs 에 빈 OOXML + version 1. 이름 빈값 422·멤버십 밖 403."""
    doc = await document_tree.create_document(
        session, employee, volume_root, payload.space_id, payload.folder_id, payload.name, payload.type
    )
    return DocumentOut.model_validate(doc)


@router.post("/files/upload", response_model=DocumentOut)
async def upload_document(
    employee: Annotated[Employee, Depends(get_current_employee)],
    session: Annotated[AsyncSession, Depends(get_db)],
    volume_root: Annotated[Path, Depends(get_volume_root)],
    space_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
    folder_id: Annotated[UUID | None, Form()] = None,
) -> DocumentOut:
    """문서 업로드 — .docx/.xlsx 만(레거시 .doc/.xls·그 외 422). fs write + version 1."""
    content = await file.read()
    doc = await document_tree.upload_document(
        session, employee, volume_root, space_id, folder_id, file.filename or "", content
    )
    return DocumentOut.model_validate(doc)


@router.delete("/files/{document_id}", status_code=204)
async def delete_document(
    document_id: UUID,
    employee: Annotated[Employee, Depends(get_current_employee)],
    session: Annotated[AsyncSession, Depends(get_db)],
    volume_root: Annotated[Path, Depends(get_volume_root)],
) -> None:
    """문서 완전 삭제 — 모든 version + fs 바이너리 제거(복구 불가). 멤버십 밖 403·미존재 404."""
    await document_tree.delete_document(session, employee, volume_root, document_id)


@router.get("/files/{document_id}/versions", response_model=list[VersionOut])
async def list_versions(
    document_id: UUID,
    employee: Annotated[Employee, Depends(get_current_employee)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[VersionOut]:
    """문서의 저장 version 목록. 멤버십 밖 403·미존재 404."""
    _doc, versions = await document_tree.list_versions(session, employee, document_id)
    return [VersionOut.model_validate(v) for v in versions]


# ---- ONLYOFFICE 통합 3 엔드포인트 (WP-006 Phase 3) ------------------------


@router.get("/files/{document_id}/editor-config")
async def editor_config(
    document_id: UUID,
    employee: Annotated[Employee, Depends(get_current_employee)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """① 편집기 세션 config 발급(서명) — 문서 열기(employee + 스페이스 멤버십). 비멤버 403·미존재 404.

    클라이언트 ONLYOFFICE JS 가 이 config(`token` 서명)로 DocServer 편집 세션을 연다(leg ②).
    """
    return await onlyoffice.editor_config(session, employee, document_id)


@router.get("/files/{document_id}/versions/{version_no}/download")
async def download_version(
    document_id: UUID,
    version_no: int,
    _claims: Annotated[dict, Depends(require_onlyoffice_jwt)],
    session: Annotated[AsyncSession, Depends(get_db)],
    volume_root: Annotated[Path, Depends(get_volume_root)],
) -> StreamingResponse:
    """② 파일 download(DocServer→BE) — ONLYOFFICE JWT 인증. 바이너리 stream. 미존재 version 404."""
    content, media_type = await onlyoffice.get_version_binary(
        session, volume_root, document_id, version_no
    )
    return StreamingResponse(io.BytesIO(content), media_type=media_type)


@router.post("/files/{document_id}/callback", response_model=CallbackAck)
async def save_callback(
    document_id: UUID,
    payload: CallbackIn,
    _claims: Annotated[dict, Depends(require_onlyoffice_jwt)],
    session: Annotated[AsyncSession, Depends(get_db)],
    volume_root: Annotated[Path, Depends(get_volume_root)],
) -> CallbackAck:
    """③ 저장 콜백(DocServer→BE) — ONLYOFFICE JWT 인증(위조 401).

    status 2/6 → 편집본 fetch(실패 502/503) + 새 version append. 1/4 저장없이 ack. 3/7 로그+ack.
    """
    result = await onlyoffice.handle_callback(
        session, volume_root, document_id, payload.status, payload.url
    )
    return CallbackAck(**result)
