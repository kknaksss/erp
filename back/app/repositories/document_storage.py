"""문서 바이너리 fs 저장소 레이어 — WP-006 Phase 1 (architecture §저장소).

PG↔fs 책임 분리: PG 는 트리/메타/권한/버전 인덱스, **바이너리는 fs volume**(PG bytea/S3 아님).
경로 규칙 = `<volume_root>/<space_id>/<document_id>/<version_no>.<ext>`(스페이스/문서/버전 단위 분리).
`storage_path`(DB 저장값) = volume_root 이하 **상대 경로**라 volume 이동에도 불변.

volume_root 는 호출부(service)가 주입한다 — `settings.document_volume_root` default 지만 테스트는
`tmp_path` 를 넘긴다(lru_cache settings 싱글톤 회피, `.env` 불가침). 함수는 root 를 받기만 한다.

빈 .docx/.xlsx 는 **최소 OOXML zip**(stdlib zipfile)로 생성한다 — Content_Types + _rels +
본문 1파트. ONLYOFFICE 편집기 적재(P3)·실제 유효성은 배포 전제라 여기선 구조만 갖춘 빈 문서.
"""

import io
import shutil
import zipfile
from pathlib import Path
from uuid import UUID

from app.models.enums import DocumentType

# 업로드/생성 허용 형식 — 확장자 ↔ DocumentType (SPEC-006 §Validation: .docx/.xlsx 만)
EXT_BY_TYPE: dict[DocumentType, str] = {DocumentType.WORD: "docx", DocumentType.EXCEL: "xlsx"}
TYPE_BY_EXT: dict[str, DocumentType] = {"docx": DocumentType.WORD, "xlsx": DocumentType.EXCEL}


def relative_path(space_id: UUID, document_id: UUID, version_no: int, ext: str) -> str:
    """fs volume 이하 상대 경로 — DB version.storage_path 에 저장되는 값."""
    return f"{space_id}/{document_id}/{version_no}.{ext}"


def _empty_docx() -> bytes:
    """최소 유효 구조의 빈 .docx (OOXML zip) — 본문 없는 워드 문서."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p/></w:body></w:document>"
    )
    return _zip({"[Content_Types].xml": content_types, "_rels/.rels": rels, "word/document.xml": document})


def _empty_xlsx() -> bytes:
    """최소 유효 구조의 빈 .xlsx (OOXML zip) — 시트 1개(빈) 엑셀."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData/></worksheet>"
    )
    return _zip(
        {
            "[Content_Types].xml": content_types,
            "_rels/.rels": rels,
            "xl/workbook.xml": workbook,
            "xl/_rels/workbook.xml.rels": wb_rels,
            "xl/worksheets/sheet1.xml": sheet,
        }
    )


def _zip(parts: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return buf.getvalue()


def empty_ooxml(doc_type: DocumentType) -> bytes:
    """빈 OOXML 바이너리(문서 생성용) — 타입별 최소 구조 .docx/.xlsx."""
    return _empty_docx() if doc_type == DocumentType.WORD else _empty_xlsx()


def write_version(
    volume_root: Path,
    space_id: UUID,
    document_id: UUID,
    version_no: int,
    ext: str,
    content: bytes,
) -> str:
    """바이너리를 fs volume 에 write → DB 저장용 상대 경로 반환. 디렉토리 자동 생성."""
    rel = relative_path(space_id, document_id, version_no, ext)
    abs_path = Path(volume_root) / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(content)
    return rel


def read_version(volume_root: Path, storage_path: str) -> bytes:
    """version.storage_path(상대) → 바이너리 read (P3 download/콜백 진입점)."""
    return (Path(volume_root) / storage_path).read_bytes()


def delete_document_dir(volume_root: Path, space_id: UUID, document_id: UUID) -> None:
    """문서의 모든 버전 바이너리 제거(완전 삭제·복구 불가). 디렉토리 통째 삭제."""
    doc_dir = Path(volume_root) / str(space_id) / str(document_id)
    shutil.rmtree(doc_dir, ignore_errors=True)
