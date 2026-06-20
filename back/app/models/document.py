"""문서관리 도메인 모델 4종 — space·folder·document·version. WP-006 Phase 1.

정본(aggregate 경계·invariant) = 40-architecture/document-management.md §aggregate.
컬럼/FK/인덱스는 코드/migration SoT. PG↔fs 책임 분리: 트리/메타/권한/버전 인덱스 = PG,
문서 바이너리 = fs(version.storage_path 가 fs 경로를 가리킴, 바이너리는 PG 에 두지 않음).

- space: 트리 최상위. 부서스페이스(`type=department` ↔ `department` 영문 코드, SPEC-002) /
  개인스페이스(`type=personal` ↔ owner `employee.id`). 멤버십 판정 단위.
  부서당 1 스페이스(`department` UNIQUE) · 직원당 개인 1 스페이스(`owner_id` UNIQUE) —
  PG 는 다중 NULL 을 UNIQUE 에 허용하므로 반대 타입 행(NULL)끼리 충돌 없음.
- folder: space 내부 디렉토리. 자기참조 계층(parent_id NULL=루트). space 경계 불횡단(서비스 enforce).
- document: 잎 노드. word/excel. 메타=PG, 바이너리는 version→fs.
- version: 수정 이력 1건. append-only(immutable, created_at 만). 인덱스=PG, 바이너리=fs.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk
from app.models.enums import (
    DocumentType,
    SpaceType,
    document_type_enum,
    space_type_enum,
)


class Space(Base, TimestampMixin):
    __tablename__ = "space"

    id: Mapped[UUID] = uuid_pk()
    type: Mapped[SpaceType] = mapped_column(space_type_enum, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)  # 표시 라벨(부서코드 / 개인 소유자명)
    # 부서스페이스 = SPEC-002 영문 부서 코드(NOT NULL) · 개인스페이스 = NULL
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 개인스페이스 = owner employee.id(NOT NULL) · 부서스페이스 = NULL
    owner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("employee.id"), nullable=True
    )

    __table_args__ = (
        # 부서당 1 스페이스 (개인스페이스는 department=NULL → 다중 NULL 허용, 충돌 없음)
        UniqueConstraint("department", name="uq_space_department"),
        # 직원당 개인 1 스페이스 (부서스페이스는 owner_id=NULL → 다중 NULL 허용)
        UniqueConstraint("owner_id", name="uq_space_owner"),
    )


class Folder(Base, TimestampMixin):
    __tablename__ = "folder"

    id: Mapped[UUID] = uuid_pk()
    space_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("space.id", ondelete="CASCADE"), nullable=False
    )
    # 자기참조 트리 — NULL = space 직속 루트. 부모 삭제 시 자식 폴더 CASCADE.
    parent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("folder.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_folder_space_parent", "space_id", "parent_id"),
    )


class Document(Base, TimestampMixin):
    __tablename__ = "document"

    id: Mapped[UUID] = uuid_pk()
    space_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("space.id", ondelete="CASCADE"), nullable=False
    )
    # NULL = space 직속(루트 문서) · 폴더 삭제 시 CASCADE
    folder_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("folder.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[DocumentType] = mapped_column(document_type_enum, nullable=False)

    __table_args__ = (
        Index("ix_document_space_folder", "space_id", "folder_id"),
    )


class Version(Base):
    """문서 버전 — append-only(immutable). created_at 만(updated_at 없음, leave_allocation 패턴)."""

    __tablename__ = "version"

    id: Mapped[UUID] = uuid_pk()
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based 누적
    ext: Mapped[str] = mapped_column(Text, nullable=False)  # docx | xlsx
    # fs volume 상대 경로(<space_id>/<document_id>/<version_no>.<ext>) — 바이너리는 fs
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # 문서별 버전 목록 조회 + (document, version_no) 유일(중복 버전번호 방지)
        UniqueConstraint("document_id", "version_no", name="uq_version_document_no"),
        Index("ix_version_document", "document_id"),
    )
