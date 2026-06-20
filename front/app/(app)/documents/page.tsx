"use client";

// 문서관리 모듈 (WP-006 Phase 4) — 트리 사이드바 + 새 폴더/파일·업로드·삭제 모달.
// GET /documents/tree(SpaceNode[]) 소비 → 좌측 트리, 우측 본문(빈 상태 / 문서 선택됨).
//  - U-1 트리(부서/개인 스페이스·폴더 토글·검색·문서 선택)  · U-2 새 폴더/파일  · U-3 업로드  · U-6 삭제확인.
//  - 생성/업로드/삭제 후 트리 refetch 반영.
// P5(범위 밖): 문서 선택 시 우측은 "편집기 준비 중" 빈 표면까지만 — ONLYOFFICE 임베드·버전이력·공유는 P5.
//   P5 핸드오프 접점 = 선택된 문서 id → GET /documents/files/{id}/editor-config 소비 지점(아래 editor 패널).
// 시안: 21-html/document-management.html. 셸 HR 사이드바는 /documents 에서 숨김(app-sidebar.tsx).
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, FileText, FolderTree, Loader2, Sheet, Trash2 } from "lucide-react";

import { DocumentDeleteDialog } from "@/components/document-delete-dialog";
import { DocumentNewFileDialog } from "@/components/document-new-file-dialog";
import { DocumentNewFolderDialog } from "@/components/document-new-folder-dialog";
import { DocumentTree } from "@/components/document-tree";
import {
  DocumentUploadDialog,
  type DocUploadLocation,
} from "@/components/document-upload-dialog";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import type { DocCreateTarget, DocDocument, DocFolderNode, DocSpaceNode } from "@/types";

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; tree: DocSpaceNode[] }
  | { kind: "error"; message: string };

const EMPTY_TREE: DocSpaceNode[] = [];

// 트리 → 업로드 위치 옵션 평탄화(스페이스 루트 + 각 폴더, 경로 라벨).
function flattenLocations(tree: DocSpaceNode[]): DocUploadLocation[] {
  const out: DocUploadLocation[] = [];
  const walk = (
    folders: DocFolderNode[],
    group: DocUploadLocation["group"],
    spaceId: string,
    prefix: string,
  ) => {
    for (const node of folders) {
      const label = prefix ? `${prefix} / ${node.folder.name}` : node.folder.name;
      out.push({
        key: `${spaceId}|${node.folder.id}`,
        spaceId,
        folderId: node.folder.id,
        label,
        group,
      });
      walk(node.folders, group, spaceId, label);
    }
  };
  for (const n of tree) {
    if (n.space.type === "department") {
      out.push({
        key: `${n.space.id}|`,
        spaceId: n.space.id,
        folderId: null,
        label: n.space.name,
        group: "부서스페이스",
      });
      walk(n.folders, "부서스페이스", n.space.id, n.space.name);
    } else {
      out.push({
        key: `${n.space.id}|`,
        spaceId: n.space.id,
        folderId: null,
        label: "(루트)",
        group: "개인스페이스",
      });
      walk(n.folders, "개인스페이스", n.space.id, "");
    }
  }
  return out;
}

export default function DocumentsPage() {
  const { authedFetch } = useAuth();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [selected, setSelected] = useState<DocDocument | null>(null);
  const [newFolderTarget, setNewFolderTarget] = useState<DocCreateTarget | null>(
    null,
  );
  const [newFileTarget, setNewFileTarget] = useState<DocCreateTarget | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [deleteDoc, setDeleteDoc] = useState<DocDocument | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // setState 는 await 이후(비동기 연속)라 effect 내 동기 setState 규칙에 안 걸림.
  const fetchTree = useCallback(async () => {
    try {
      const tree = await authedFetch<DocSpaceNode[]>("/documents/tree");
      setState({ kind: "ok", tree });
    } catch (err) {
      setState({
        kind: "error",
        message:
          err instanceof ApiError ? err.message : "문서 트리를 불러오지 못했습니다",
      });
    }
  }, [authedFetch]);

  const reload = useCallback(() => {
    setState({ kind: "loading" });
    fetchTree();
  }, [fetchTree]);

  // 마운트 시 fetch — 토큰이 클라(sessionStorage)에만 있어 effect-fetch 불가피.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchTree();
  }, [fetchTree]);

  // 토스트 자동 소거
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(t);
  }, [toast]);

  const tree = state.kind === "ok" ? state.tree : EMPTY_TREE;
  const locations = useMemo(() => flattenLocations(tree), [tree]);

  // 생성/업로드 성공 → 트리 갱신 + toast.
  const onMutated = useCallback(
    (summary: string) => {
      setToast(summary);
      fetchTree();
    },
    [fetchTree],
  );

  // 삭제 성공 → 선택된 문서면 편집기 닫고 트리 갱신.
  const onDeleted = useCallback(
    (summary: string) => {
      setSelected((cur) => (cur && deleteDoc && cur.id === deleteDoc.id ? null : cur));
      setDeleteDoc(null);
      onMutated(summary);
    },
    [deleteDoc, onMutated],
  );

  return (
    <div className="flex min-h-0 flex-1">
      {/* 좌측 트리 사이드바 */}
      {state.kind === "loading" ? (
        <aside className="flex w-[280px] shrink-0 items-center justify-center border-r border-mgray-100 bg-white text-mgray-400">
          <Loader2 className="size-5 animate-spin" />
        </aside>
      ) : state.kind === "error" ? (
        <aside className="flex w-[280px] shrink-0 flex-col items-center justify-center gap-3 border-r border-mgray-100 bg-white px-4 text-center">
          <p className="text-[13px] text-mred-500">{state.message}</p>
          <Button variant="outline" size="sm" onClick={reload}>
            다시 시도
          </Button>
        </aside>
      ) : (
        <DocumentTree
          spaceNodes={tree}
          selectedId={selected?.id ?? null}
          onSelectDoc={setSelected}
          onNewFolder={setNewFolderTarget}
          onNewFile={setNewFileTarget}
          onUpload={() => setUploadOpen(true)}
        />
      )}

      {/* 우측 본문 — 빈 상태 / 문서 선택됨(P5 편집기 자리). 시맨틱 <main> 은 셸 레이아웃 소유. */}
      <div className="flex min-w-0 flex-1 flex-col bg-mgray-50">
        {selected ? (
          <DocumentEditorPanel
            doc={selected}
            onClose={() => setSelected(null)}
            onDelete={() => setDeleteDoc(selected)}
          />
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center text-center">
            <div className="mb-4 flex size-16 items-center justify-center rounded-2xl bg-white shadow-sm">
              <FolderTree className="size-7 text-mgray-400" />
            </div>
            <h2 className="text-base font-semibold text-mgray-700">
              문서를 선택하세요
            </h2>
            <p className="mt-1 max-w-xs text-[13px] leading-relaxed text-mgray-500">
              왼쪽 트리에서 부서·개인 스페이스의 폴더나 문서를 열어보세요. 새로
              만들기로 폴더·문서를 추가할 수 있습니다.
            </p>
          </div>
        )}
      </div>

      {/* 모달 — 새 폴더 / 새 파일 / 업로드 / 삭제 */}
      <DocumentNewFolderDialog
        open={newFolderTarget !== null}
        onOpenChange={(next) => {
          if (!next) setNewFolderTarget(null);
        }}
        target={newFolderTarget}
        onCreated={onMutated}
      />
      <DocumentNewFileDialog
        open={newFileTarget !== null}
        onOpenChange={(next) => {
          if (!next) setNewFileTarget(null);
        }}
        target={newFileTarget}
        onCreated={onMutated}
      />
      <DocumentUploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        locations={locations}
        defaultKey={locations[0]?.key ?? null}
        onUploaded={onMutated}
      />
      <DocumentDeleteDialog
        open={deleteDoc !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteDoc(null);
        }}
        doc={deleteDoc}
        onDeleted={onDeleted}
      />

      {toast ? (
        <div
          role="status"
          className="fixed bottom-6 right-6 z-50 max-w-[360px] rounded-md border border-mgray-200 bg-card px-4 py-3 text-[13px] text-mgray-800 shadow-lg"
        >
          {toast}
        </div>
      ) : null}
    </div>
  );
}

// 문서 선택 패널 — P4 는 최소 헤더(닫기·제목·삭제) + "편집기 준비 중" 빈 표면.
// P5 가 이 자리에 ONLYOFFICE 편집기(editor-config 소비)·버전 이력·공유·공동편집을 채운다.
function DocumentEditorPanel({
  doc,
  onClose,
  onDelete,
}: {
  doc: DocDocument;
  onClose: () => void;
  onDelete: () => void;
}) {
  const isExcel = doc.type === "excel";
  const Icon = isExcel ? Sheet : FileText;
  const ext = isExcel ? ".xlsx" : ".docx";
  return (
    <>
      {/* 문서 헤더 (P4 최소) */}
      <div className="flex items-center gap-3 border-b border-mgray-100 bg-white px-4 py-2.5">
        <button
          type="button"
          onClick={onClose}
          aria-label="닫기"
          title="닫기"
          className="flex size-8 shrink-0 items-center justify-center rounded-md text-mgray-500 hover:bg-mgray-100"
        >
          <ArrowLeft className="size-[18px]" />
        </button>
        <span
          className={cn(
            "flex size-8 shrink-0 items-center justify-center rounded-md",
            isExcel ? "bg-mgreen-50 text-mgreen-500" : "bg-brand-50 text-brand-500",
          )}
        >
          <Icon className="size-[18px]" />
        </span>
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-sm font-semibold text-mgray-800">
            {doc.name}
          </span>
          <span className="shrink-0 text-[12px] text-mgray-400">{ext}</span>
        </div>
        <div className="flex-1" />
        <button
          type="button"
          onClick={onDelete}
          aria-label="삭제"
          title="삭제"
          className="flex size-8 items-center justify-center rounded-md text-mgray-500 hover:bg-mred-50 hover:text-mred-500"
        >
          <Trash2 className="size-4" />
        </button>
      </div>

      {/* 편집기 자리 — P5(ONLYOFFICE 임베드) 준비 중 빈 표면 */}
      <div className="flex flex-1 flex-col items-center justify-center text-center">
        <div className="mb-4 flex size-16 items-center justify-center rounded-2xl bg-white shadow-sm">
          <Icon className="size-7 text-mgray-400" />
        </div>
        <h2 className="text-base font-semibold text-mgray-700">{doc.name}</h2>
        <p className="mt-1 max-w-xs text-[13px] leading-relaxed text-mgray-500">
          편집기 준비 중입니다. 실시간 공동편집(ONLYOFFICE)은 다음 단계에서
          제공됩니다.
        </p>
      </div>
    </>
  );
}
