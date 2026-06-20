"""문서관리 트리·CRUD service — WP-006 Phase 2 (SPEC-006 §3·§케이스 매트릭스·§5 AC).

트리 조회 + 폴더/문서 CRUD + 스페이스 멤버십 enforcement. 멤버십 게이트는 document_access,
바이너리 read/write 는 document_storage(fs), 트리/메타는 document repo(PG). service 가 commit.

권한·invariant(SPEC-006):
- 접근 = 스페이스 멤버십(document_access). 멤버십 밖 스페이스/문서 = 403. 미존재/삭제 = 404.
- 폴더/파일 이름 빈값 = 422. 업로드 .docx/.xlsx 만(그 외·레거시 .doc/.xls = 422).
- 폴더는 같은 space 안에서만 자기참조 계층 — 다른 space 부모 지정 = 422(경계 불횡단).
- 문서 삭제 = 완전 삭제(모든 version + fs 바이너리, 복구 불가). 폴더 삭제 = 하위 문서 fs 정리 + CASCADE.

트리 진입 시 본인 부서스페이스·개인스페이스를 lazy ensure(생성 엔드포인트 없음 — 멱등 보장은
space UNIQUE 제약). 개인스페이스는 항상, 부서스페이스는 employee.department 가 있을 때만.
"""

from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidDocumentError, NotFoundError
from app.models.document import Document, Folder, Space, Version
from app.models.employee import Employee
from app.models.enums import DocumentType
from app.repositories import document as repo
from app.repositories import document_storage as storage
from app.services.document_access import is_space_member, require_space_member


def _require_name(raw: str | None, what: str) -> str:
    """이름 빈값(공백 포함) 거부 — 422(SPEC-006 §Validation)."""
    name = (raw or "").strip()
    if not name:
        raise InvalidDocumentError(f"{what} 이름을 입력하세요")
    return name


async def _load_space(session: AsyncSession, space_id: UUID) -> Space:
    space = await repo.get_space(session, space_id)
    if space is None:
        raise NotFoundError("스페이스를 찾을 수 없습니다")
    return space


async def _load_document(session: AsyncSession, document_id: UUID) -> Document:
    doc = await repo.get_document(session, document_id)
    if doc is None:
        raise NotFoundError("문서를 찾을 수 없습니다")
    return doc


# ---- lazy 스페이스 ensure + 트리 조회 -------------------------------------


async def ensure_spaces(session: AsyncSession, employee: Employee) -> None:
    """본인 개인스페이스(+부서스페이스) lazy 생성. 이미 있으면 no-op(UNIQUE 멱등)."""
    if await repo.get_personal_space(session, employee.id) is None:
        await repo.create_personal_space(session, employee.id, name=employee.name)
    if employee.department and await repo.get_department_space(session, employee.department) is None:
        await repo.create_department_space(session, employee.department, name=employee.department)


async def tree(session: AsyncSession, employee: Employee) -> list[dict]:
    """접근 가능한 스페이스→folder→document 계층. 멤버십 밖 스페이스는 미노출.

    반환: 스페이스별 {space, folders(자기참조 트리), documents(루트 직속)} 의 nested dict.
    """
    await ensure_spaces(session, employee)
    await session.commit()  # lazy 생성분 영속화(다음 조회·CRUD 가 참조)

    spaces = [s for s in await repo.list_all_spaces(session) if is_space_member(employee, s)]
    out: list[dict] = []
    for space in spaces:
        folders = await repo.list_folders_in_space(session, space.id)
        documents = await repo.list_documents_in_space(session, space.id)
        out.append(_build_space_tree(space, folders, documents))
    return out


def _build_space_tree(space: Space, folders: list[Folder], documents: list[Document]) -> dict:
    """평평한 folder/document 목록 → space 직속을 루트로 한 nested 트리."""
    children: dict[UUID | None, list[Folder]] = {}
    for f in folders:
        children.setdefault(f.parent_id, []).append(f)
    docs_by_folder: dict[UUID | None, list[Document]] = {}
    for d in documents:
        docs_by_folder.setdefault(d.folder_id, []).append(d)

    def build_folder(folder: Folder) -> dict:
        return {
            "folder": folder,
            "folders": [build_folder(c) for c in children.get(folder.id, [])],
            "documents": docs_by_folder.get(folder.id, []),
        }

    return {
        "space": space,
        "folders": [build_folder(f) for f in children.get(None, [])],
        "documents": docs_by_folder.get(None, []),  # space 직속(루트) 문서
    }


# ---- 폴더 CRUD ------------------------------------------------------------


async def create_folder(
    session: AsyncSession, employee: Employee, space_id: UUID, parent_id: UUID | None, name: str
) -> Folder:
    name = _require_name(name, "폴더")
    space = await _load_space(session, space_id)
    require_space_member(employee, space)
    if parent_id is not None:
        parent = await repo.get_folder(session, parent_id)
        if parent is None:
            raise NotFoundError("상위 폴더를 찾을 수 없습니다")
        if parent.space_id != space_id:
            # 트리 무결성 — 폴더는 같은 space 안에서만(경계 불횡단)
            raise InvalidDocumentError("상위 폴더가 다른 스페이스에 있습니다")
    folder = await repo.create_folder(session, space_id, parent_id, name)
    await session.commit()
    return folder


async def rename_folder(
    session: AsyncSession, employee: Employee, folder_id: UUID, name: str
) -> Folder:
    name = _require_name(name, "폴더")
    folder = await repo.get_folder(session, folder_id)
    if folder is None:
        raise NotFoundError("폴더를 찾을 수 없습니다")
    space = await _load_space(session, folder.space_id)
    require_space_member(employee, space)
    folder.name = name
    await session.commit()
    return folder


async def delete_folder(
    session: AsyncSession, employee: Employee, volume_root: Path, folder_id: UUID
) -> None:
    """폴더 삭제 — 하위 폴더/문서/버전 CASCADE + 하위 문서들의 fs 바이너리 정리.

    fs 는 FK 가 못 지우므로, 삭제 전 서브트리의 모든 문서를 수집해 디렉토리를 제거한다.
    """
    folder = await repo.get_folder(session, folder_id)
    if folder is None:
        raise NotFoundError("폴더를 찾을 수 없습니다")
    space = await _load_space(session, folder.space_id)
    require_space_member(employee, space)

    # 서브트리 폴더 id 수집(같은 space 내 parent 체인 BFS) → 그 폴더들 직속 문서 fs 정리
    all_folders = await repo.list_folders_in_space(session, space.id)
    children: dict[UUID | None, list[Folder]] = {}
    for f in all_folders:
        children.setdefault(f.parent_id, []).append(f)
    subtree_ids: list[UUID] = []
    stack = [folder.id]
    while stack:
        fid = stack.pop()
        subtree_ids.append(fid)
        stack.extend(c.id for c in children.get(fid, []))

    for doc in await repo.list_documents_under_folders(session, subtree_ids):
        storage.delete_document_dir(volume_root, doc.space_id, doc.id)

    await repo.delete_folder(session, folder)  # CASCADE: 하위 폴더/문서/버전 행 제거
    await session.commit()


# ---- 문서 생성 / 업로드 / 삭제 / 버전 -------------------------------------


async def _create_with_initial_version(
    session: AsyncSession,
    volume_root: Path,
    space: Space,
    folder_id: UUID | None,
    name: str,
    doc_type: DocumentType,
    content: bytes,
) -> Document:
    """문서 행 + version 1 (fs write) 생성 공통부. folder_id 가 다른 space 면 422."""
    if folder_id is not None:
        folder = await repo.get_folder(session, folder_id)
        if folder is None:
            raise NotFoundError("폴더를 찾을 수 없습니다")
        if folder.space_id != space.id:
            raise InvalidDocumentError("폴더가 다른 스페이스에 있습니다")
    doc = await repo.create_document(session, space.id, folder_id, name, doc_type)
    ext = storage.EXT_BY_TYPE[doc_type]
    rel = storage.write_version(
        volume_root, space.id, doc.id, version_no=1, ext=ext, content=content
    )
    await repo.create_version(session, doc.id, 1, ext, rel, len(content))
    return doc


async def create_document(
    session: AsyncSession,
    employee: Employee,
    volume_root: Path,
    space_id: UUID,
    folder_id: UUID | None,
    name: str,
    doc_type: DocumentType,
) -> Document:
    """빈 .docx/.xlsx 문서 생성 — fs 에 빈 OOXML write + version 1. 이름 빈값 422·멤버십 밖 403."""
    name = _require_name(name, "파일")
    space = await _load_space(session, space_id)
    require_space_member(employee, space)
    content = storage.empty_ooxml(doc_type)
    doc = await _create_with_initial_version(
        session, volume_root, space, folder_id, name, doc_type, content
    )
    await session.commit()
    return doc


async def upload_document(
    session: AsyncSession,
    employee: Employee,
    volume_root: Path,
    space_id: UUID,
    folder_id: UUID | None,
    filename: str,
    content: bytes,
) -> Document:
    """문서 업로드 — .docx/.xlsx 만(레거시 .doc/.xls·그 외 422). fs write + version 1."""
    raw_name = (filename or "").strip()
    ext = raw_name.rsplit(".", 1)[-1].lower() if "." in raw_name else ""
    doc_type = storage.TYPE_BY_EXT.get(ext)
    if doc_type is None:
        raise InvalidDocumentError(
            "허용되지 않는 형식입니다(.docx/.xlsx 만 업로드 가능)", detail={"ext": ext}
        )
    name = _require_name(raw_name, "파일")
    space = await _load_space(session, space_id)
    require_space_member(employee, space)
    doc = await _create_with_initial_version(
        session, volume_root, space, folder_id, name, doc_type, content
    )
    await session.commit()
    return doc


async def delete_document(
    session: AsyncSession, employee: Employee, volume_root: Path, document_id: UUID
) -> None:
    """문서 완전 삭제 — 모든 version + fs 바이너리 제거(복구 불가). 미존재 404·멤버십 밖 403."""
    doc = await _load_document(session, document_id)
    space = await _load_space(session, doc.space_id)
    require_space_member(employee, space)
    storage.delete_document_dir(volume_root, doc.space_id, doc.id)  # fs 바이너리(모든 버전)
    await repo.delete_document(session, doc)  # CASCADE: version 행 제거
    await session.commit()


async def list_versions(
    session: AsyncSession, employee: Employee, document_id: UUID
) -> tuple[Document, list[Version]]:
    """문서의 저장 version 목록 — 멤버십 밖 403·미존재 404. read-only."""
    doc = await _load_document(session, document_id)
    space = await _load_space(session, doc.space_id)
    require_space_member(employee, space)
    versions = await repo.list_versions(session, document_id)
    return doc, versions
