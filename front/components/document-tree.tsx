"use client";

// 디렉토리 트리 사이드바 (SPEC-006 §2 U-1) — GET /documents/tree(SpaceNode[]) 소비.
//  - 부서스페이스: 각 부서 SpaceNode = 펼침/접힘 노드(라벨=space.name), 하위 폴더/문서 중첩.
//  - 개인스페이스: 단일 개인 SpaceNode 의 루트 폴더/문서를 직접 렌더(스페이스 노드 없음 — 본인 1개라 안전).
//  - 폴더 접기/펼치기(chevron), 문서=잎(워드 file-text / 엑셀 sheet 아이콘 구분, 클릭=우측 선택).
//  - 파일·폴더명 검색(이름 일치로 트리 필터, 일치 시 경로 펼침 — 본문검색 아님).
//  - 각 스페이스/폴더의 + (새 폴더/새 파일 메뉴) · 하단 파일 업로드.
// 시안: document-management.html <aside>. 멤버십 밖 스페이스는 BE 가 트리에서 미반환(방어적으로 안 그림).
import { useEffect, useMemo, useState } from "react";
import {
  Building2,
  ChevronDown,
  ChevronRight,
  FilePlus,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  Plus,
  Search,
  Sheet,
  Upload,
  User,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type {
  DocCreateTarget,
  DocDocument,
  DocFolderNode,
  DocSpaceNode,
} from "@/types";

function matches(name: string, q: string): boolean {
  return name.toLowerCase().includes(q.toLowerCase());
}

// 검색 필터 — 폴더명 일치 시 전체 유지, 아니면 일치하는 하위만 남김(없으면 제거).
function filterFolder(node: DocFolderNode, q: string): DocFolderNode | null {
  if (matches(node.folder.name, q)) return node;
  const folders = node.folders
    .map((f) => filterFolder(f, q))
    .filter((f): f is DocFolderNode => f !== null);
  const documents = node.documents.filter((d) => matches(d.name, q));
  if (folders.length || documents.length) {
    return { folder: node.folder, folders, documents };
  }
  return null;
}

function filterSpace(node: DocSpaceNode, q: string): DocSpaceNode | null {
  if (matches(node.space.name, q)) return node;
  const folders = node.folders
    .map((f) => filterFolder(f, q))
    .filter((f): f is DocFolderNode => f !== null);
  const documents = node.documents.filter((d) => matches(d.name, q));
  if (folders.length || documents.length) {
    return { space: node.space, folders, documents };
  }
  return null;
}

export function DocumentTree({
  spaceNodes,
  selectedId,
  onSelectDoc,
  onNewFolder,
  onNewFile,
  onUpload,
}: {
  spaceNodes: DocSpaceNode[];
  selectedId: string | null;
  onSelectDoc: (doc: DocDocument) => void;
  onNewFolder: (target: DocCreateTarget) => void;
  onNewFile: (target: DocCreateTarget) => void;
  onUpload: () => void;
}) {
  const [query, setQuery] = useState("");
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());
  // "새로 만들기" 메뉴 — + 클릭 시 위치 앵커.
  const [menu, setMenu] = useState<{
    target: DocCreateTarget;
    x: number;
    y: number;
  } | null>(null);

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("scroll", close, true);
    return () => window.removeEventListener("scroll", close, true);
  }, [menu]);

  const q = query.trim();
  const searching = q.length > 0;

  const departments = useMemo(
    () => spaceNodes.filter((n) => n.space.type === "department"),
    [spaceNodes],
  );
  const personal = useMemo(
    () => spaceNodes.find((n) => n.space.type === "personal") ?? null,
    [spaceNodes],
  );

  const deptFiltered = useMemo(
    () =>
      searching
        ? departments
            .map((n) => filterSpace(n, q))
            .filter((n): n is DocSpaceNode => n !== null)
        : departments,
    [departments, q, searching],
  );
  const personalFiltered = useMemo(
    () => (searching && personal ? filterSpace(personal, q) : personal),
    [personal, q, searching],
  );

  function toggle(id: string) {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  // 검색 중엔 일치 경로를 모두 펼침(openIds 무시).
  const isOpen = (id: string) => searching || openIds.has(id);

  function openMenu(e: React.MouseEvent, target: DocCreateTarget) {
    e.stopPropagation();
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenu({ target, x: Math.min(r.left, window.innerWidth - 188), y: r.bottom + 4 });
  }

  function renderDoc(doc: DocDocument, depth: number) {
    const isExcel = doc.type === "excel";
    const Icon = isExcel ? Sheet : FileText;
    const selected = selectedId === doc.id;
    return (
      <button
        key={doc.id}
        type="button"
        onClick={() => onSelectDoc(doc)}
        style={{ paddingLeft: 8 + depth * 20 }}
        className={cn(
          "group flex w-full items-center gap-1 rounded-md py-1.5 pr-2 text-left hover:bg-mgray-50",
          selected && "bg-brand-50",
        )}
      >
        <Icon
          className={cn(
            "size-[18px] shrink-0",
            isExcel ? "text-mgreen-500" : "text-brand-500",
          )}
        />
        <span
          className={cn(
            "flex-1 truncate text-[13px]",
            selected ? "font-medium text-brand-700" : "text-mgray-600",
          )}
        >
          {doc.name}
        </span>
      </button>
    );
  }

  function renderFolder(node: DocFolderNode, depth: number) {
    const id = node.folder.id;
    const open = isOpen(id);
    const target: DocCreateTarget = {
      spaceId: node.folder.space_id,
      parentId: node.folder.id,
      label: node.folder.name,
    };
    return (
      <div key={id}>
        <div
          className="group flex items-center gap-1 rounded-md py-1.5 pr-2 hover:bg-mgray-50"
          style={{ paddingLeft: 8 + depth * 20 }}
        >
          <button
            type="button"
            onClick={() => toggle(id)}
            className="flex min-w-0 flex-1 items-center gap-1 text-left"
          >
            {open ? (
              <ChevronDown className="size-4 shrink-0 text-mgray-400" />
            ) : (
              <ChevronRight className="size-4 shrink-0 text-mgray-400" />
            )}
            {open ? (
              <FolderOpen className="size-[18px] shrink-0 text-brand-500" />
            ) : (
              <Folder className="size-[18px] shrink-0 text-mgray-400" />
            )}
            <span className="flex-1 truncate text-[13px] text-mgray-700">
              {node.folder.name}
            </span>
          </button>
          <button
            type="button"
            title="여기에 만들기"
            onClick={(e) => openMenu(e, target)}
            className="flex size-5 shrink-0 items-center justify-center rounded text-mgray-400 opacity-0 hover:bg-mgray-200 hover:text-mgray-700 group-hover:opacity-100"
          >
            <Plus className="size-3.5" />
          </button>
        </div>
        {open ? (
          <div>
            {node.folders.map((f) => renderFolder(f, depth + 1))}
            {node.documents.map((d) => renderDoc(d, depth + 1))}
          </div>
        ) : null}
      </div>
    );
  }

  // 부서 SpaceNode = 펼침 노드(폴더처럼). 라벨 = space.name, + → 스페이스 루트 생성.
  function renderDeptSpace(node: DocSpaceNode) {
    const id = node.space.id;
    const open = isOpen(id);
    const target: DocCreateTarget = {
      spaceId: node.space.id,
      parentId: null,
      label: node.space.name,
    };
    return (
      <div key={id}>
        <div
          className="group flex items-center gap-1 rounded-md py-1.5 pr-2 hover:bg-mgray-50"
          style={{ paddingLeft: 8 }}
        >
          <button
            type="button"
            onClick={() => toggle(id)}
            className="flex min-w-0 flex-1 items-center gap-1 text-left"
          >
            {open ? (
              <ChevronDown className="size-4 shrink-0 text-mgray-400" />
            ) : (
              <ChevronRight className="size-4 shrink-0 text-mgray-400" />
            )}
            {open ? (
              <FolderOpen className="size-[18px] shrink-0 text-brand-500" />
            ) : (
              <Folder className="size-[18px] shrink-0 text-mgray-400" />
            )}
            <span className="flex-1 truncate text-[13px] text-mgray-700">
              {node.space.name}
            </span>
          </button>
          <button
            type="button"
            title="여기에 만들기"
            onClick={(e) => openMenu(e, target)}
            className="flex size-5 shrink-0 items-center justify-center rounded text-mgray-400 opacity-0 hover:bg-mgray-200 hover:text-mgray-700 group-hover:opacity-100"
          >
            <Plus className="size-3.5" />
          </button>
        </div>
        {open ? (
          <div>
            {node.folders.map((f) => renderFolder(f, 1))}
            {node.documents.map((d) => renderDoc(d, 1))}
          </div>
        ) : null}
      </div>
    );
  }

  // 개인스페이스 루트 + (정확히 1개 — 본인). 없으면 null.
  const personalTarget: DocCreateTarget | null = personal
    ? { spaceId: personal.space.id, parentId: null, label: "개인스페이스" }
    : null;

  const empty = departments.length === 0 && !personal;

  return (
    <aside className="flex w-[280px] shrink-0 flex-col border-r border-mgray-100 bg-white">
      {/* 검색 */}
      <div className="px-3 pb-2 pt-3.5">
        <div className="flex items-center gap-2 rounded-md border border-mgray-200 bg-mgray-50 px-2.5 py-1.5">
          <Search className="size-4 shrink-0 text-mgray-400" />
          <input
            type="text"
            placeholder="문서·폴더 검색"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full bg-transparent text-[13px] text-mgray-700 placeholder:text-mgray-400 focus:outline-none"
          />
        </div>
      </div>

      {/* 트리 본문 */}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {empty ? (
          <p className="px-3 py-6 text-center text-[12px] text-mgray-400">
            접근 가능한 스페이스가 없습니다.
          </p>
        ) : null}

        {/* 부서스페이스 */}
        {departments.length ? (
          <>
            <div className="mt-1.5 flex items-center gap-1.5 px-2 py-1.5">
              <Building2 className="size-4 shrink-0 text-mgray-500" />
              <span className="flex-1 text-[11px] font-semibold uppercase tracking-wide text-mgray-500">
                부서스페이스
              </span>
              {departments.length === 1 ? (
                <button
                  type="button"
                  title="새로 만들기"
                  onClick={(e) =>
                    openMenu(e, {
                      spaceId: departments[0].space.id,
                      parentId: null,
                      label: departments[0].space.name,
                    })
                  }
                  className="flex size-6 items-center justify-center rounded-md text-mgray-400 hover:bg-mgray-100 hover:text-mgray-700"
                >
                  <Plus className="size-4" />
                </button>
              ) : null}
            </div>
            {(searching ? deptFiltered : departments).map(renderDeptSpace)}
            {searching && deptFiltered.length === 0 ? (
              <p className="px-3 py-2 text-[12px] text-mgray-400">
                검색 결과 없음
              </p>
            ) : null}
          </>
        ) : null}

        {/* 개인스페이스 */}
        {personal ? (
          <>
            <div className="mt-4 flex items-center gap-1.5 px-2 py-1.5">
              <User className="size-4 shrink-0 text-mgray-500" />
              <span className="flex-1 text-[11px] font-semibold uppercase tracking-wide text-mgray-500">
                개인스페이스
              </span>
              {personalTarget ? (
                <button
                  type="button"
                  title="새로 만들기"
                  onClick={(e) => openMenu(e, personalTarget)}
                  className="flex size-6 items-center justify-center rounded-md text-mgray-400 hover:bg-mgray-100 hover:text-mgray-700"
                >
                  <Plus className="size-4" />
                </button>
              ) : null}
            </div>
            {(searching ? personalFiltered : personal) ? (
              <div>
                {(searching ? personalFiltered! : personal).folders.map((f) =>
                  renderFolder(f, 0),
                )}
                {(searching ? personalFiltered! : personal).documents.map((d) =>
                  renderDoc(d, 0),
                )}
              </div>
            ) : (
              <p className="px-3 py-2 text-[12px] text-mgray-400">
                검색 결과 없음
              </p>
            )}
          </>
        ) : null}
      </div>

      {/* 하단 파일 업로드 */}
      <div className="border-t border-mgray-100 p-2.5">
        <button
          type="button"
          onClick={onUpload}
          className="flex w-full items-center justify-center gap-1.5 rounded-md bg-brand-500 px-3 py-2.5 text-[13px] font-medium text-white hover:bg-brand-700"
        >
          <Upload className="size-4" />
          파일 업로드
        </button>
      </div>

      {/* 새로 만들기 메뉴 (새 폴더 / 새 파일) */}
      {menu ? (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setMenu(null)} />
          <div
            className="fixed z-50 w-44 rounded-lg border border-mgray-200 bg-white py-1 shadow-xl"
            style={{ left: menu.x, top: menu.y }}
          >
            <button
              type="button"
              onClick={() => {
                onNewFolder(menu.target);
                setMenu(null);
              }}
              className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] text-mgray-700 hover:bg-mgray-50"
            >
              <FolderPlus className="size-[18px] text-mgray-500" />새 폴더
            </button>
            <button
              type="button"
              onClick={() => {
                onNewFile(menu.target);
                setMenu(null);
              }}
              className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] text-mgray-700 hover:bg-mgray-50"
            >
              <FilePlus className="size-[18px] text-mgray-500" />새 파일
            </button>
          </div>
        </>
      ) : null}
    </aside>
  );
}
