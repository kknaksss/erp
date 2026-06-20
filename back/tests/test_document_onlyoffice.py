"""ONLYOFFICE 통합 3 엔드포인트 검증 — WP-006 Phase 3 (architecture §JWT·§저장 콜백).

DocServer 는 mock(httpx fetch monkeypatch), JWT 는 conftest 테스트 시크릿. service + endpoint.
대조: editor config 서명·멤버십/404 · download stream·JWT 401·version 404 · callback 2/6 append·
1/4 무저장·fetch 실패 502/503·위조 401.
"""

import uuid

import httpx
import pytest
from httpx import ASGITransport
from jose import jwt

from app.config import settings
from app.core.deps import get_current_employee, get_db
from app.core.errors import ForbiddenError, NotFoundError
from app.main import app
from app.models.employee import Employee
from app.models.enums import DocumentType
from app.repositories import document as repo
from app.repositories import document_storage as storage
from app.routers.documents import get_volume_root
from app.services import document_tree, onlyoffice
from app.services.onlyoffice import DocServerTimeoutError, DocServerUnavailableError


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_employee(session, *, department=None, role="member") -> Employee:
    eid = uuid.uuid4()
    emp = Employee(id=eid, email=f"{eid.hex[:8]}@x.com", name=f"emp-{eid.hex[:6]}",
                   role=role, active=True, department=department)
    session.add(emp)
    await session.flush()
    return emp


async def _seed_doc(session, tmp_path, *, department="dev"):
    emp = await _seed_employee(session, department=department)
    space = await repo.create_department_space(session, department, name=department)
    await session.flush()
    doc = await document_tree.create_document(session, emp, tmp_path, space.id, None, "보고서", DocumentType.WORD)
    return emp, space, doc


def _onlyoffice_token(payload: dict | None = None) -> str:
    """유효 ONLYOFFICE JWT(테스트 시크릿 서명)."""
    return jwt.encode(payload or {"ok": True}, settings.onlyoffice_jwt_secret, algorithm="HS256")


# ---- ① editor config -------------------------------------------------------


@pytest.mark.asyncio
async def test_editor_config_signed_and_fields(db_session, tmp_path) -> None:
    """멤버 문서 열기 → config + token 서명. 같은 시크릿으로 검증 통과, 필드 정합."""
    emp, _space, doc = await _seed_doc(db_session, tmp_path)
    config = await onlyoffice.editor_config(db_session, emp, doc.id)

    assert config["document"]["key"] == f"{doc.id}_1"
    assert config["document"]["fileType"] == "docx"
    assert config["documentType"] == "word"  # docx → word 표면
    assert config["document"]["title"] == "보고서.docx"
    # url 2종 = config base 기반 절대 URL
    assert config["document"]["url"] == f"{settings.onlyoffice_callback_base_url}/documents/files/{doc.id}/versions/1/download"
    assert config["editorConfig"]["callbackUrl"] == f"{settings.onlyoffice_callback_base_url}/documents/files/{doc.id}/callback"
    # 서명 검증 통과 (token 은 token 제외 payload 서명)
    claims = onlyoffice.verify(config["token"])
    assert claims["document"]["key"] == f"{doc.id}_1"


@pytest.mark.asyncio
async def test_editor_config_excel_documenttype_cell(db_session, tmp_path) -> None:
    emp = await _seed_employee(db_session, department="dev")
    space = await repo.create_department_space(db_session, "dev", name="dev")
    await db_session.flush()
    doc = await document_tree.create_document(db_session, emp, tmp_path, space.id, None, "표", DocumentType.EXCEL)
    config = await onlyoffice.editor_config(db_session, emp, doc.id)
    assert config["document"]["fileType"] == "xlsx" and config["documentType"] == "cell"


@pytest.mark.asyncio
async def test_editor_config_non_member_403(db_session, tmp_path) -> None:
    _owner, _space, doc = await _seed_doc(db_session, tmp_path)
    intruder = await _seed_employee(db_session, department="sales")
    with pytest.raises(ForbiddenError):
        await onlyoffice.editor_config(db_session, intruder, doc.id)


@pytest.mark.asyncio
async def test_editor_config_missing_document_404(db_session, tmp_path) -> None:
    emp = await _seed_employee(db_session, department="dev")
    with pytest.raises(NotFoundError):
        await onlyoffice.editor_config(db_session, emp, uuid.uuid4())


# ---- ② download ------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_binary_matches(db_session, tmp_path) -> None:
    """유효 JWT → version 바이너리 stream(read_version 내용 일치)."""
    _emp, _space, doc = await _seed_doc(db_session, tmp_path)
    ver = (await repo.list_versions(db_session, doc.id))[0]
    expected = storage.read_version(tmp_path, ver.storage_path)

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_volume_root] = lambda: tmp_path
    try:
        async with _client() as c:
            resp = await c.get(f"/documents/files/{doc.id}/versions/1/download",
                               headers={"Authorization": f"Bearer {_onlyoffice_token()}"})
        assert resp.status_code == 200, resp.text
        assert resp.content == expected
        assert "wordprocessingml" in resp.headers["content-type"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_download_no_token_401(db_session, tmp_path) -> None:
    _emp, _space, doc = await _seed_doc(db_session, tmp_path)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_volume_root] = lambda: tmp_path
    try:
        async with _client() as c:
            resp = await c.get(f"/documents/files/{doc.id}/versions/1/download")
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_download_forged_token_401(db_session, tmp_path) -> None:
    _emp, _space, doc = await _seed_doc(db_session, tmp_path)
    forged = jwt.encode({"ok": True}, "wrong-secret", algorithm="HS256")
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_volume_root] = lambda: tmp_path
    try:
        async with _client() as c:
            resp = await c.get(f"/documents/files/{doc.id}/versions/1/download",
                               headers={"Authorization": f"Bearer {forged}"})
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_download_missing_version_404(db_session, tmp_path) -> None:
    _emp, _space, doc = await _seed_doc(db_session, tmp_path)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_volume_root] = lambda: tmp_path
    try:
        async with _client() as c:
            resp = await c.get(f"/documents/files/{doc.id}/versions/99/download",
                               headers={"Authorization": f"Bearer {_onlyoffice_token()}"})
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


# ---- ③ callback ------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [2, 6])
async def test_callback_save_appends_version(db_session, tmp_path, monkeypatch, status) -> None:
    """status 2/6 → 편집본 fetch(mock) → 새 version append(version_no max+1) + ack{error:0}."""
    _emp, _space, doc = await _seed_doc(db_session, tmp_path)
    before = await repo.list_versions(db_session, doc.id)
    assert len(before) == 1

    async def _fake_fetch(url: str) -> bytes:
        return b"EDITED-CONTENT"
    monkeypatch.setattr(onlyoffice, "_fetch_edited", _fake_fetch)

    result = await onlyoffice.handle_callback(
        db_session, tmp_path, doc.id, status, "http://docserver/edited.docx"
    )
    assert result == {"error": 0}

    after = await repo.list_versions(db_session, doc.id)
    assert len(after) == 2 and after[1].version_no == 2 and after[1].ext == "docx"
    # fs 에 새 버전 바이너리 기록(내용 = 편집본)
    assert storage.read_version(tmp_path, after[1].storage_path) == b"EDITED-CONTENT"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [1, 4])
async def test_callback_no_save_statuses_ack_unchanged(db_session, tmp_path, status) -> None:
    """status 1(editing)/4(closed·무변경) → 저장 없이 ack, version 불변."""
    _emp, _space, doc = await _seed_doc(db_session, tmp_path)
    result = await onlyoffice.handle_callback(db_session, tmp_path, doc.id, status, None)
    assert result == {"error": 0}
    assert len(await repo.list_versions(db_session, doc.id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [3, 7])
async def test_callback_error_statuses_logged_no_save(db_session, tmp_path, status) -> None:
    """status 3/7(error) → 로그만, 저장 안 함 + ack."""
    _emp, _space, doc = await _seed_doc(db_session, tmp_path)
    result = await onlyoffice.handle_callback(db_session, tmp_path, doc.id, status, None)
    assert result == {"error": 0}
    assert len(await repo.list_versions(db_session, doc.id)) == 1


@pytest.mark.asyncio
async def test_callback_fetch_connect_error_503(db_session, tmp_path, monkeypatch) -> None:
    """편집본 fetch ConnectError → 503(DocServerUnavailableError)."""
    _emp, _space, doc = await _seed_doc(db_session, tmp_path)

    async def _raise(self, url, *a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(httpx.AsyncClient, "get", _raise)

    with pytest.raises(DocServerUnavailableError):
        await onlyoffice.handle_callback(db_session, tmp_path, doc.id, 2, "http://docserver/x.docx")
    assert len(await repo.list_versions(db_session, doc.id)) == 1  # 미저장


@pytest.mark.asyncio
async def test_callback_fetch_timeout_502(db_session, tmp_path, monkeypatch) -> None:
    """편집본 fetch ReadTimeout(TransportError) → 502(DocServerTimeoutError)."""
    _emp, _space, doc = await _seed_doc(db_session, tmp_path)

    async def _raise(self, url, *a, **k):
        raise httpx.ReadTimeout("timeout")
    monkeypatch.setattr(httpx.AsyncClient, "get", _raise)

    with pytest.raises(DocServerTimeoutError):
        await onlyoffice.handle_callback(db_session, tmp_path, doc.id, 2, "http://docserver/x.docx")
    assert len(await repo.list_versions(db_session, doc.id)) == 1


@pytest.mark.asyncio
async def test_callback_endpoint_forged_jwt_401(db_session, tmp_path) -> None:
    """위조 JWT 콜백 → 401(저장 안 함)."""
    _emp, _space, doc = await _seed_doc(db_session, tmp_path)
    forged = jwt.encode({"x": 1}, "wrong", algorithm="HS256")
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_volume_root] = lambda: tmp_path
    try:
        async with _client() as c:
            resp = await c.post(f"/documents/files/{doc.id}/callback",
                                headers={"Authorization": f"Bearer {forged}"},
                                json={"status": 2, "url": "http://docserver/x.docx"})
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_callback_endpoint_save_200(db_session, tmp_path, monkeypatch) -> None:
    """유효 JWT + status 2 → append + ack{error:0} (endpoint)."""
    _emp, _space, doc = await _seed_doc(db_session, tmp_path)

    async def _fake_fetch(url: str) -> bytes:
        return b"EDITED"
    monkeypatch.setattr(onlyoffice, "_fetch_edited", _fake_fetch)

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_volume_root] = lambda: tmp_path
    try:
        async with _client() as c:
            resp = await c.post(f"/documents/files/{doc.id}/callback",
                                headers={"Authorization": f"Bearer {_onlyoffice_token()}"},
                                json={"status": 2, "url": "http://docserver/x.docx", "key": f"{doc.id}_1"})
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"error": 0}
    finally:
        app.dependency_overrides.clear()


# ---- editor-config endpoint (employee 인증 wiring) ------------------------


@pytest.mark.asyncio
async def test_editor_config_endpoint_200(db_session, tmp_path) -> None:
    emp, _space, doc = await _seed_doc(db_session, tmp_path)
    app.dependency_overrides[get_current_employee] = lambda: emp
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        async with _client() as c:
            resp = await c.get(f"/documents/files/{doc.id}/editor-config",
                               headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["document"]["key"] == f"{doc.id}_1" and "token" in body
    finally:
        app.dependency_overrides.clear()
