"""문서관리 트리·CRUD·권한 검증 — WP-006 Phase 2 (SPEC-006 §3·§케이스 매트릭스·§5 AC).

service(실제 DB·롤백) + 일부 endpoint(업로드 multipart·403/404). volume_root 는 tmp_path.
service 가 commit 하지만 db_session fixture 가 outer 트랜잭션(savepoint)으로 격리한다.

대조 항목: 트리/멤버십·admin 경계·폴더 CRUD/자기참조/경계·문서 생성·업로드 게이트·
삭제 복구불가·버전 목록·PG↔fs invariant.
"""

import uuid

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select

from app.core.errors import ForbiddenError, InvalidDocumentError, NotFoundError
from app.core.deps import get_current_employee, get_db
from app.main import app
from app.models.document import Version
from app.models.employee import Employee
from app.models.enums import DocumentType
from app.repositories import document as repo
from app.routers.documents import get_volume_root
from app.services import document_tree


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_employee(session, *, department=None, role="member") -> Employee:
    eid = uuid.uuid4()
    emp = Employee(id=eid, email=f"{eid.hex[:8]}@x.com", name=f"emp-{eid.hex[:6]}",
                   role=role, active=True, department=department)
    session.add(emp)
    await session.flush()
    return emp


async def _dept_space(session, department: str):
    return await repo.create_department_space(session, department, name=department)


async def _personal_space(session, owner_id):
    return await repo.create_personal_space(session, owner_id, name="me")


# ---- 트리 / 멤버십 ---------------------------------------------------------


@pytest.mark.asyncio
async def test_tree_shows_only_member_spaces(db_session, tmp_path) -> None:
    """직원 A(dept=dev)는 부서스페이스 dev + 본인 개인스페이스만. 타 부서/타인 개인 미노출."""
    a = await _seed_employee(db_session, department="dev")
    other = await _seed_employee(db_session, department="hr")
    await _dept_space(db_session, "dev")
    await _dept_space(db_session, "hr")        # 타 부서 — A 에게 미노출
    await _personal_space(db_session, other.id)  # 타인 개인 — A 에게 미노출
    await db_session.flush()

    nodes = await document_tree.tree(db_session, a)
    names = {(n["space"].type.value, n["space"].department, n["space"].owner_id) for n in nodes}
    # dev 부서스페이스 + A 개인스페이스(ensure 로 생성)
    assert ("department", "dev", None) in names
    assert ("personal", None, a.id) in names
    # 타 부서/타인 개인 없음
    assert ("department", "hr", None) not in names
    assert all(not (t == "personal" and o == other.id) for (t, _d, o) in names)


@pytest.mark.asyncio
async def test_tree_ensures_personal_and_department_space(db_session, tmp_path) -> None:
    """트리 진입이 본인 개인+부서 스페이스를 lazy 생성(생성 엔드포인트 없음)."""
    emp = await _seed_employee(db_session, department="design")
    nodes = await document_tree.tree(db_session, emp)
    kinds = {n["space"].type.value for n in nodes}
    assert kinds == {"department", "personal"}


@pytest.mark.asyncio
async def test_tree_nests_folders_and_documents(db_session, tmp_path) -> None:
    """트리가 space→folder(자기참조)→document 계층으로 nested 반환."""
    emp = await _seed_employee(db_session, department="dev")
    space = await _dept_space(db_session, "dev")
    root = await repo.create_folder(db_session, space.id, None, "회의록")
    child = await repo.create_folder(db_session, space.id, root.id, "2026")
    await document_tree.create_document(db_session, emp, tmp_path, space.id, child.id, "1월", DocumentType.WORD)
    await db_session.flush()

    nodes = await document_tree.tree(db_session, emp)
    dev = next(n for n in nodes if n["space"].department == "dev")
    assert dev["folders"][0]["folder"].name == "회의록"
    assert dev["folders"][0]["folders"][0]["folder"].name == "2026"
    assert dev["folders"][0]["folders"][0]["documents"][0].name == "1월"


# ---- admin 경계 ------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_sees_only_own_department_space(db_session, tmp_path) -> None:
    """admin 트리에 본인 부서스페이스만 노출 — cross-department 특권 없음(PLAN-003-T-005)."""
    admin = await _seed_employee(db_session, department="dev", role="admin")
    await _dept_space(db_session, "dev")
    await _dept_space(db_session, "hr")  # 타 부서 — admin 에게도 미노출
    await db_session.flush()

    nodes = await document_tree.tree(db_session, admin)
    depts = {n["space"].department for n in nodes if n["space"].type.value == "department"}
    assert depts == {"dev"}  # 본인 부서만, hr 미노출


@pytest.mark.asyncio
async def test_admin_cannot_access_other_personal_space_403(db_session, tmp_path) -> None:
    """admin 도 타인 개인스페이스 문서 생성/접근 → 403(override 없음)."""
    admin = await _seed_employee(db_session, department="dev", role="admin")
    other = await _seed_employee(db_session, department="hr")
    personal = await _personal_space(db_session, other.id)
    await db_session.flush()

    with pytest.raises(ForbiddenError):
        await document_tree.create_folder(db_session, admin, personal.id, None, "x")


# ---- 폴더 CRUD -------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rename_delete_folder(db_session, tmp_path) -> None:
    emp = await _seed_employee(db_session, department="dev")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()

    folder = await document_tree.create_folder(db_session, emp, space.id, None, "기획")
    assert folder.name == "기획"

    renamed = await document_tree.rename_folder(db_session, emp, folder.id, "기획문서")
    assert renamed.name == "기획문서"

    await document_tree.delete_folder(db_session, emp, tmp_path, folder.id)
    assert await repo.get_folder(db_session, folder.id) is None


@pytest.mark.asyncio
async def test_folder_self_referential_nesting(db_session, tmp_path) -> None:
    emp = await _seed_employee(db_session, department="dev")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()
    parent = await document_tree.create_folder(db_session, emp, space.id, None, "p")
    child = await document_tree.create_folder(db_session, emp, space.id, parent.id, "c")
    assert child.parent_id == parent.id and child.space_id == space.id


@pytest.mark.asyncio
async def test_folder_cannot_cross_space_422(db_session, tmp_path) -> None:
    """다른 space 폴더를 부모로 지정 → 422(경계 불횡단)."""
    emp = await _seed_employee(db_session, department="dev")
    dev = await _dept_space(db_session, "dev")
    personal = await _personal_space(db_session, emp.id)
    await db_session.flush()
    parent_in_dev = await document_tree.create_folder(db_session, emp, dev.id, None, "p")

    with pytest.raises(InvalidDocumentError):
        # personal space 에 만들되 부모는 dev 의 폴더 → 경계 횡단
        await document_tree.create_folder(db_session, emp, personal.id, parent_in_dev.id, "c")


@pytest.mark.asyncio
async def test_folder_empty_name_422(db_session, tmp_path) -> None:
    emp = await _seed_employee(db_session, department="dev")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()
    for bad in ("", "   "):
        with pytest.raises(InvalidDocumentError):
            await document_tree.create_folder(db_session, emp, space.id, None, bad)


@pytest.mark.asyncio
async def test_folder_non_member_403(db_session, tmp_path) -> None:
    intruder = await _seed_employee(db_session, department="sales")
    space = await _dept_space(db_session, "dev")  # intruder 비멤버
    await db_session.flush()
    with pytest.raises(ForbiddenError):
        await document_tree.create_folder(db_session, intruder, space.id, None, "x")


@pytest.mark.asyncio
async def test_delete_folder_cascades_documents_and_fs(db_session, tmp_path) -> None:
    """폴더 삭제 → 하위 문서 + version + fs 바이너리 함께 제거."""
    emp = await _seed_employee(db_session, department="dev")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()
    folder = await document_tree.create_folder(db_session, emp, space.id, None, "f")
    sub = await document_tree.create_folder(db_session, emp, space.id, folder.id, "sub")
    doc = await document_tree.create_document(db_session, emp, tmp_path, space.id, sub.id, "d", DocumentType.WORD)
    ver = (await repo.list_versions(db_session, doc.id))[0]
    folder_id, sub_id, doc_id, ver_path = folder.id, sub.id, doc.id, ver.storage_path  # expire 전 캡처
    assert (tmp_path / ver_path).exists()

    await document_tree.delete_folder(db_session, emp, tmp_path, folder_id)
    db_session.expire_all()  # DB CASCADE 결과를 ORM 캐시 우회하고 재조회(expire_on_commit=False)

    assert await repo.get_folder(db_session, folder_id) is None
    assert await repo.get_folder(db_session, sub_id) is None      # CASCADE 하위 폴더
    assert await repo.get_document(db_session, doc_id) is None     # CASCADE 문서
    assert not (tmp_path / ver_path).exists()                     # fs 정리


# ---- 문서 생성 -------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_document_writes_fs_and_version(db_session, tmp_path) -> None:
    """빈 .docx 생성 → fs 바이너리 존재 + version 1건 + PG 에 바이너리 없음(invariant)."""
    emp = await _seed_employee(db_session, department="dev")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()

    doc = await document_tree.create_document(db_session, emp, tmp_path, space.id, None, "보고서", DocumentType.WORD)
    assert doc.type == DocumentType.WORD and doc.folder_id is None

    versions = await repo.list_versions(db_session, doc.id)
    assert len(versions) == 1 and versions[0].version_no == 1 and versions[0].ext == "docx"
    # fs 바이너리 존재
    assert (tmp_path / versions[0].storage_path).exists()
    # PG↔fs invariant — version 행에 바이너리 컬럼 없음(경로/메타만)
    assert "storage_path" in Version.__table__.columns
    assert not any(c.name in ("content", "binary", "data") for c in Version.__table__.columns)


@pytest.mark.asyncio
async def test_create_excel_document(db_session, tmp_path) -> None:
    emp = await _seed_employee(db_session, department="dev")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()
    doc = await document_tree.create_document(db_session, emp, tmp_path, space.id, None, "표", DocumentType.EXCEL)
    ver = (await repo.list_versions(db_session, doc.id))[0]
    assert doc.type == DocumentType.EXCEL and ver.ext == "xlsx"


@pytest.mark.asyncio
async def test_create_document_empty_name_422(db_session, tmp_path) -> None:
    emp = await _seed_employee(db_session, department="dev")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()
    with pytest.raises(InvalidDocumentError):
        await document_tree.create_document(db_session, emp, tmp_path, space.id, None, "  ", DocumentType.WORD)


# ---- 업로드 게이트 ---------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_docx_xlsx_ok(db_session, tmp_path) -> None:
    emp = await _seed_employee(db_session, department="dev")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()
    for fname, expect in (("report.docx", DocumentType.WORD), ("sheet.xlsx", DocumentType.EXCEL)):
        doc = await document_tree.upload_document(db_session, emp, tmp_path, space.id, None, fname, b"PK\x03\x04data")
        assert doc.type == expect
        assert len(await repo.list_versions(db_session, doc.id)) == 1


@pytest.mark.asyncio
async def test_upload_legacy_and_other_formats_rejected_422(db_session, tmp_path) -> None:
    """레거시 .doc/.xls + 그 외 형식 거부(422)."""
    emp = await _seed_employee(db_session, department="dev")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()
    for fname in ("old.doc", "old.xls", "pic.png", "noext", "archive.zip"):
        with pytest.raises(InvalidDocumentError):
            await document_tree.upload_document(db_session, emp, tmp_path, space.id, None, fname, b"x")


# ---- 삭제 (복구 불가) ------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_document_removes_all_versions_and_fs(db_session, tmp_path) -> None:
    """문서 삭제 = 완전 삭제(모든 version + fs 바이너리, 복구 불가)."""
    emp = await _seed_employee(db_session, department="dev")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()
    doc = await document_tree.create_document(db_session, emp, tmp_path, space.id, None, "d", DocumentType.WORD)
    ver = (await repo.list_versions(db_session, doc.id))[0]
    assert (tmp_path / ver.storage_path).exists()

    await document_tree.delete_document(db_session, emp, tmp_path, doc.id)

    assert await repo.get_document(db_session, doc.id) is None
    # version 행 전부 제거(CASCADE)
    remaining = (await db_session.execute(select(Version).where(Version.document_id == doc.id))).scalars().all()
    assert remaining == []
    # fs 바이너리 제거
    assert not (tmp_path / ver.storage_path).exists()


@pytest.mark.asyncio
async def test_delete_missing_document_404(db_session, tmp_path) -> None:
    emp = await _seed_employee(db_session, department="dev")
    with pytest.raises(NotFoundError):
        await document_tree.delete_document(db_session, emp, tmp_path, uuid.uuid4())


@pytest.mark.asyncio
async def test_document_non_member_403(db_session, tmp_path) -> None:
    """멤버십 밖 문서 삭제 시도 → 403(문서는 존재, 스페이스 비멤버)."""
    owner = await _seed_employee(db_session, department="dev")
    intruder = await _seed_employee(db_session, department="sales")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()
    doc = await document_tree.create_document(db_session, owner, tmp_path, space.id, None, "d", DocumentType.WORD)
    with pytest.raises(ForbiddenError):
        await document_tree.delete_document(db_session, intruder, tmp_path, doc.id)


# ---- 버전 목록 -------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_versions(db_session, tmp_path) -> None:
    emp = await _seed_employee(db_session, department="dev")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()
    doc = await document_tree.create_document(db_session, emp, tmp_path, space.id, None, "d", DocumentType.WORD)
    _doc, versions = await document_tree.list_versions(db_session, emp, doc.id)
    assert len(versions) == 1 and versions[0].version_no == 1


@pytest.mark.asyncio
async def test_list_versions_non_member_403(db_session, tmp_path) -> None:
    owner = await _seed_employee(db_session, department="dev")
    intruder = await _seed_employee(db_session, department="sales")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()
    doc = await document_tree.create_document(db_session, owner, tmp_path, space.id, None, "d", DocumentType.WORD)
    with pytest.raises(ForbiddenError):
        await document_tree.list_versions(db_session, intruder, doc.id)


# ---- endpoint (업로드 multipart + 권한 wiring) ----------------------------


@pytest.mark.asyncio
async def test_upload_endpoint_multipart_200(db_session, tmp_path) -> None:
    emp = await _seed_employee(db_session, department="dev")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()

    app.dependency_overrides[get_current_employee] = lambda: emp
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_volume_root] = lambda: tmp_path
    try:
        async with _client() as c:
            resp = await c.post(
                "/documents/files/upload",
                headers={"Authorization": "Bearer t"},
                data={"space_id": str(space.id)},
                files={"file": ("report.docx", b"PK\x03\x04data", "application/octet-stream")},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["type"] == "word" and body["name"] == "report.docx"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_upload_endpoint_legacy_format_422(db_session, tmp_path) -> None:
    emp = await _seed_employee(db_session, department="dev")
    space = await _dept_space(db_session, "dev")
    await db_session.flush()

    app.dependency_overrides[get_current_employee] = lambda: emp
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_volume_root] = lambda: tmp_path
    try:
        async with _client() as c:
            resp = await c.post(
                "/documents/files/upload",
                headers={"Authorization": "Bearer t"},
                data={"space_id": str(space.id)},
                files={"file": ("legacy.doc", b"x", "application/msword")},
            )
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_tree_endpoint_200(db_session, tmp_path) -> None:
    emp = await _seed_employee(db_session, department="dev")
    await _dept_space(db_session, "dev")
    await db_session.flush()

    app.dependency_overrides[get_current_employee] = lambda: emp
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_volume_root] = lambda: tmp_path
    try:
        async with _client() as c:
            resp = await c.get("/documents/tree", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200, resp.text
        types = {n["space"]["type"] for n in resp.json()}
        assert "department" in types and "personal" in types
    finally:
        app.dependency_overrides.clear()
