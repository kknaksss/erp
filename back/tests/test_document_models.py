"""문서관리 모델·enum 검증 — WP-006 Phase 1.

스키마만(repo/service 동작은 test_document_tree). 범위 = 4 테이블 메타 등록 + enum 값(영문) +
version append-only(updated_at 없음) + 핵심 nullability(자기참조 트리·소속 FK). DB 통합(왕복)은
리포트 alembic 실행 결과 참조.
"""

from app.models import Document, Folder, Space, Version
from app.models.base import Base
from app.models.enums import DocumentType, SpaceType


def test_four_tables_registered() -> None:
    for model in (Space, Folder, Document, Version):
        assert model.__tablename__ in Base.metadata.tables


def test_enum_values_are_english() -> None:
    """SPEC-006 §Lifecycle — 값은 영문(연차 도메인 한글 enum 과 달리)."""
    assert [m.value for m in SpaceType] == ["department", "personal"]
    assert [m.value for m in DocumentType] == ["word", "excel"]


def test_version_is_append_only() -> None:
    """version = append-only(immutable) → created_at 만(updated_at 없음, leave_allocation 패턴)."""
    cols = set(Version.__table__.columns.keys())
    assert "created_at" in cols
    assert "updated_at" not in cols


def test_timestamped_tables_have_both() -> None:
    for model in (Space, Folder, Document):
        cols = set(model.__table__.columns.keys())
        assert {"created_at", "updated_at"} <= cols


def test_self_referential_folder_tree() -> None:
    """folder.parent_id = 자기참조(NULL=루트) + space 소속(NOT NULL)."""
    cols = Folder.__table__.columns
    assert cols["parent_id"].nullable  # 루트 허용
    assert not cols["space_id"].nullable
    # parent_id 가 folder.id 를 가리키는 자기참조 FK
    fk_targets = {fk.column.table.name for fk in cols["parent_id"].foreign_keys}
    assert "folder" in fk_targets


def test_space_membership_columns_nullable() -> None:
    """부서스페이스 ↔ department / 개인스페이스 ↔ owner_id — 타입별 한쪽만 채워지므로 둘 다 nullable."""
    cols = Space.__table__.columns
    assert cols["department"].nullable and cols["owner_id"].nullable
    assert not cols["type"].nullable


def test_document_folder_nullable_space_required() -> None:
    """document.folder_id NULL = space 직속(루트 문서) · space_id NOT NULL."""
    cols = Document.__table__.columns
    assert cols["folder_id"].nullable
    assert not cols["space_id"].nullable and not cols["type"].nullable
