"""문서관리 스키마 — WP-006 Phase 2. FE Phase 4 가 소비할 트리/폴더/문서/버전 계약.

이름 빈값·업로드 형식·space 경계는 service 가 판정(house 패턴 = service-side validation,
leave_intake/leave_grant 와 동일). enum 은 영문 value 직렬화(type="word"·"department").
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DocumentType, SpaceType


class FolderCreateIn(BaseModel):
    """폴더 생성 입력 — 위치(space·상위 폴더) + 이름. 이름 빈값은 service 가 422."""

    space_id: UUID
    parent_id: UUID | None = None  # NULL = space 직속(루트)
    name: str

    model_config = ConfigDict(str_strip_whitespace=True)


class FolderRenameIn(BaseModel):
    name: str

    model_config = ConfigDict(str_strip_whitespace=True)


class DocumentCreateIn(BaseModel):
    """빈 문서 생성 입력 — 위치 + 이름 + 형식(word/excel). 빈 .docx/.xlsx 로 생성."""

    space_id: UUID
    folder_id: UUID | None = None
    name: str
    type: DocumentType

    model_config = ConfigDict(str_strip_whitespace=True)


class FolderOut(BaseModel):
    id: UUID
    space_id: UUID
    parent_id: UUID | None
    name: str

    model_config = ConfigDict(from_attributes=True)


class DocumentOut(BaseModel):
    id: UUID
    space_id: UUID
    folder_id: UUID | None
    name: str
    type: DocumentType

    model_config = ConfigDict(from_attributes=True)


class VersionOut(BaseModel):
    id: UUID
    document_id: UUID
    version_no: int
    ext: str
    size_bytes: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FolderNode(BaseModel):
    """트리 노드 — 폴더 + 자기참조 하위(폴더/문서). 재귀 구조."""

    folder: FolderOut
    folders: list["FolderNode"] = Field(default_factory=list)
    documents: list[DocumentOut] = Field(default_factory=list)


class SpaceOut(BaseModel):
    id: UUID
    type: SpaceType
    name: str
    department: str | None
    owner_id: UUID | None

    model_config = ConfigDict(from_attributes=True)


class SpaceNode(BaseModel):
    """트리 최상위 — 스페이스 + 루트 직속 폴더/문서."""

    space: SpaceOut
    folders: list[FolderNode] = Field(default_factory=list)
    documents: list[DocumentOut] = Field(default_factory=list)


def to_space_node(raw: dict) -> SpaceNode:
    """service 의 nested dict(ORM 객체 포함) → 검증된 SpaceNode DTO."""
    return SpaceNode(
        space=SpaceOut.model_validate(raw["space"]),
        folders=[_to_folder_node(f) for f in raw["folders"]],
        documents=[DocumentOut.model_validate(d) for d in raw["documents"]],
    )


def _to_folder_node(raw: dict) -> FolderNode:
    return FolderNode(
        folder=FolderOut.model_validate(raw["folder"]),
        folders=[_to_folder_node(f) for f in raw["folders"]],
        documents=[DocumentOut.model_validate(d) for d in raw["documents"]],
    )
