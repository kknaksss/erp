"""fs 저장소 레이어 검증 — WP-006 Phase 1 (PG↔fs 책임 분리, 바이너리는 fs).

빈 OOXML = 유효 zip(Content_Types 포함) · 경로 규칙 · write/read/delete. volume_root 는
tmp_path 주입(settings 싱글톤·.env 회피).
"""

import io
import uuid
import zipfile

from app.models.enums import DocumentType
from app.repositories import document_storage as storage


def test_empty_docx_is_valid_zip_with_content_types() -> None:
    data = storage.empty_ooxml(DocumentType.WORD)
    assert zipfile.is_zipfile(io.BytesIO(data))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    assert "[Content_Types].xml" in names
    assert "word/document.xml" in names


def test_empty_xlsx_is_valid_zip_with_sheet() -> None:
    data = storage.empty_ooxml(DocumentType.EXCEL)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    assert "[Content_Types].xml" in names
    assert "xl/workbook.xml" in names
    assert "xl/worksheets/sheet1.xml" in names


def test_relative_path_rule() -> None:
    """경로 = <space>/<document>/<version>.<ext> (architecture §저장소)."""
    sid, did = uuid.uuid4(), uuid.uuid4()
    assert storage.relative_path(sid, did, 1, "docx") == f"{sid}/{did}/1.docx"


def test_write_read_delete_roundtrip(tmp_path) -> None:
    sid, did = uuid.uuid4(), uuid.uuid4()
    content = storage.empty_ooxml(DocumentType.WORD)

    rel = storage.write_version(tmp_path, sid, did, 1, "docx", content)
    assert (tmp_path / rel).exists()
    assert storage.read_version(tmp_path, rel) == content

    storage.delete_document_dir(tmp_path, sid, did)
    assert not (tmp_path / rel).exists()
    assert not (tmp_path / str(sid) / str(did)).exists()


def test_ext_type_maps() -> None:
    assert storage.EXT_BY_TYPE[DocumentType.WORD] == "docx"
    assert storage.EXT_BY_TYPE[DocumentType.EXCEL] == "xlsx"
    assert storage.TYPE_BY_EXT["docx"] == DocumentType.WORD
    assert storage.TYPE_BY_EXT["xlsx"] == DocumentType.EXCEL
    assert "doc" not in storage.TYPE_BY_EXT and "xls" not in storage.TYPE_BY_EXT
