"""문서관리 PG 저장소 — space/folder/document/version 트리·메타 CRUD. WP-006 Phase 1/2.

PG = 트리/메타/권한/버전 인덱스(바이너리는 document_storage=fs). 호출부(service)가 commit 책임.
멤버십 판정·invariant(이름 빈값·space 경계)은 service 가 enforce — repo 는 순수 조회/쓰기만.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, Folder, Space, Version
from app.models.enums import DocumentType, SpaceType


# ---- space ----------------------------------------------------------------


async def get_space(session: AsyncSession, space_id: UUID) -> Space | None:
    return await session.get(Space, space_id)


async def list_all_spaces(session: AsyncSession) -> list[Space]:
    result = await session.execute(select(Space).order_by(Space.type, Space.name))
    return list(result.scalars().all())


async def get_department_space(session: AsyncSession, department: str) -> Space | None:
    result = await session.execute(
        select(Space).where(Space.type == SpaceType.DEPARTMENT, Space.department == department)
    )
    return result.scalars().first()


async def get_personal_space(session: AsyncSession, owner_id: UUID) -> Space | None:
    result = await session.execute(
        select(Space).where(Space.type == SpaceType.PERSONAL, Space.owner_id == owner_id)
    )
    return result.scalars().first()


async def create_department_space(session: AsyncSession, department: str, name: str) -> Space:
    space = Space(type=SpaceType.DEPARTMENT, department=department, name=name)
    session.add(space)
    await session.flush()
    return space


async def create_personal_space(session: AsyncSession, owner_id: UUID, name: str) -> Space:
    space = Space(type=SpaceType.PERSONAL, owner_id=owner_id, name=name)
    session.add(space)
    await session.flush()
    return space


# ---- folder ---------------------------------------------------------------


async def get_folder(session: AsyncSession, folder_id: UUID) -> Folder | None:
    return await session.get(Folder, folder_id)


async def list_folders_in_space(session: AsyncSession, space_id: UUID) -> list[Folder]:
    result = await session.execute(
        select(Folder).where(Folder.space_id == space_id).order_by(Folder.name)
    )
    return list(result.scalars().all())


async def create_folder(
    session: AsyncSession, space_id: UUID, parent_id: UUID | None, name: str
) -> Folder:
    folder = Folder(space_id=space_id, parent_id=parent_id, name=name)
    session.add(folder)
    await session.flush()
    return folder


async def delete_folder(session: AsyncSession, folder: Folder) -> None:
    """폴더 삭제 — FK ondelete CASCADE 가 하위 폴더/문서/버전 행을 함께 제거(fs 는 service)."""
    await session.delete(folder)
    await session.flush()


# ---- document -------------------------------------------------------------


async def get_document(session: AsyncSession, document_id: UUID) -> Document | None:
    return await session.get(Document, document_id)


async def list_documents_in_space(session: AsyncSession, space_id: UUID) -> list[Document]:
    result = await session.execute(
        select(Document).where(Document.space_id == space_id).order_by(Document.name)
    )
    return list(result.scalars().all())


async def list_documents_under_folders(
    session: AsyncSession, folder_ids: list[UUID]
) -> list[Document]:
    """주어진 폴더들에 직접 속한 문서 — 폴더 삭제 시 fs 정리 대상 수집용."""
    if not folder_ids:
        return []
    result = await session.execute(
        select(Document).where(Document.folder_id.in_(folder_ids))
    )
    return list(result.scalars().all())


async def create_document(
    session: AsyncSession, space_id: UUID, folder_id: UUID | None, name: str, doc_type: DocumentType
) -> Document:
    doc = Document(space_id=space_id, folder_id=folder_id, name=name, type=doc_type)
    session.add(doc)
    await session.flush()
    return doc


async def delete_document(session: AsyncSession, doc: Document) -> None:
    """문서 행 삭제 — FK ondelete CASCADE 가 version 행을 함께 제거(fs 는 service)."""
    await session.delete(doc)
    await session.flush()


# ---- version --------------------------------------------------------------


async def create_version(
    session: AsyncSession,
    document_id: UUID,
    version_no: int,
    ext: str,
    storage_path: str,
    size_bytes: int,
) -> Version:
    version = Version(
        document_id=document_id,
        version_no=version_no,
        ext=ext,
        storage_path=storage_path,
        size_bytes=size_bytes,
    )
    session.add(version)
    await session.flush()
    return version


async def list_versions(session: AsyncSession, document_id: UUID) -> list[Version]:
    """문서의 버전 목록 — version_no 순(생성 순)."""
    result = await session.execute(
        select(Version).where(Version.document_id == document_id).order_by(Version.version_no)
    )
    return list(result.scalars().all())


async def get_version_by_no(
    session: AsyncSession, document_id: UUID, version_no: int
) -> Version | None:
    """특정 버전 1건 — download(DocServer→BE)가 version 단위로 원본 fetch."""
    result = await session.execute(
        select(Version).where(
            Version.document_id == document_id, Version.version_no == version_no
        )
    )
    return result.scalars().first()


async def latest_version(session: AsyncSession, document_id: UUID) -> Version | None:
    """최신 버전 1건 — editor config(현재 열 버전) + callback append(version_no max) 산정."""
    result = await session.execute(
        select(Version)
        .where(Version.document_id == document_id)
        .order_by(Version.version_no.desc())
        .limit(1)
    )
    return result.scalars().first()
