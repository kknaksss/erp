"use client";

// 문서 편집기 — ONLYOFFICE Docs 임베드 + 문서 헤더 (SPEC-006 §2 U-4·U-5, WP-006 Phase 5).
// P4 "편집기 준비 중" 빈 표면을 실 편집기로 교체. 문서 선택 시:
//   GET /documents/files/{id}/editor-config (BE 가 완성·서명) → DocServer api.js 동적 로드
//   → window.DocsAPI.DocEditor(surfaceId, config) 인스턴스화(config 그대로 주입, FE 재구성 금지).
// 문서 헤더: 제목·확장자·저장상태·공동편집자·버전이력·삭제·공유 + "ONLYOFFICE Docs" 배지.
//
// ⚠ DocServer 미배포(NEXT_PUBLIC_ONLYOFFICE_DS_URL 미설정) 시 graceful 안내(크래시·build 실패 금지).
//   실제 임베드/공동편집/저장 콜백 왕복 라이브 검증은 배포 후(이번 게이트 밖).
// 케이스 매트릭스: 연결 실패 502/503·미존재 404·DS URL 부재 → 편집기 영역 안내.
// 시안: 21-html/document-management.html 편집기 헤더·표면.
import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  FileText,
  History,
  Loader2,
  Share2,
  Sheet,
  Trash2,
  Wrench,
} from "lucide-react";

import { DocumentShareDialog } from "@/components/document-share-dialog";
import { DocumentVersionsDialog } from "@/components/document-versions-dialog";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import type { DocDocument } from "@/types";

// BE editor-config(서명 token 포함, ONLYOFFICE 규격 중첩 dict) — FE 는 그대로 소비(재구성 금지).
type OnlyOfficeConfig = Record<string, unknown>;

// 최소 DocsAPI 타입 — DocServer api.js 가 window 에 주입.
interface DocEditorInstance {
  destroyEditor?: () => void;
}
declare global {
  interface Window {
    DocsAPI?: {
      DocEditor: new (
        placeholderId: string,
        config: OnlyOfficeConfig,
      ) => DocEditorInstance;
    };
  }
}

const SURFACE_ID = "onlyoffice-surface";
const DS_URL = process.env.NEXT_PUBLIC_ONLYOFFICE_DS_URL;

// DocServer api.js 동적 로드 — 모듈 캐시(중복 주입 방지). 실패 시 캐시 비워 재시도 허용.
let apiPromise: Promise<void> | null = null;
function loadOnlyOfficeApi(dsUrl: string): Promise<void> {
  if (typeof window !== "undefined" && window.DocsAPI) return Promise.resolve();
  if (apiPromise) return apiPromise;
  apiPromise = new Promise<void>((resolve, reject) => {
    const src = `${dsUrl.replace(/\/$/, "")}/web-apps/apps/api/documents/api.js`;
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => {
      apiPromise = null;
      reject(new Error("ONLYOFFICE api.js 로드 실패"));
    };
    document.head.appendChild(script);
  });
  return apiPromise;
}

type EditorState =
  | { kind: "loading" }
  | { kind: "embedded" } // config fetch + api 로드 + DocEditor 인스턴스화 완료
  | { kind: "no-server" } // DS URL 미설정(미배포) — graceful 안내
  | { kind: "error"; message: string };

const CONN_FAIL = "편집기를 불러오지 못했습니다. 잠시 후 다시 시도해주세요";

function configErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 404) return "문서를 찾을 수 없습니다";
    if (err.status === 502 || err.status === 503) return CONN_FAIL;
    if (err.status === 403) return "이 문서에 접근할 권한이 없습니다";
    return err.message;
  }
  return CONN_FAIL;
}

export function DocumentEditor({
  doc,
  onClose,
  onDelete,
}: {
  doc: DocDocument;
  onClose: () => void;
  onDelete: () => void;
}) {
  const { authedFetch, user } = useAuth();
  const editorRef = useRef<DocEditorInstance | null>(null);
  const [state, setState] = useState<EditorState>(() =>
    DS_URL ? { kind: "loading" } : { kind: "no-server" },
  );
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);

  const isExcel = doc.type === "excel";
  const Icon = isExcel ? Sheet : FileText;
  const ext = isExcel ? ".xlsx" : ".docx";
  const initial = user?.name?.[0] ?? "·";

  // 부모가 key={doc.id} 로 마운트 → doc 당 1회. setState 는 await 이후만(effect 동기 setState 회피).
  useEffect(() => {
    if (!DS_URL) return; // 초기 state 이미 no-server
    let cancelled = false;
    (async () => {
      try {
        const config = await authedFetch<OnlyOfficeConfig>(
          `/documents/files/${doc.id}/editor-config`,
        );
        await loadOnlyOfficeApi(DS_URL);
        if (cancelled) return;
        if (!window.DocsAPI) {
          setState({ kind: "error", message: CONN_FAIL });
          return;
        }
        // config 그대로 주입 + 클라 이벤트 콜백(서명 대상 아님)만 부가.
        editorRef.current = new window.DocsAPI.DocEditor(SURFACE_ID, {
          ...config,
          events: {
            onError: () => {
              if (!cancelled) setState({ kind: "error", message: CONN_FAIL });
            },
          },
        });
        setState({ kind: "embedded" });
      } catch (err) {
        if (!cancelled) setState({ kind: "error", message: configErrorMessage(err) });
      }
    })();
    return () => {
      cancelled = true;
      try {
        editorRef.current?.destroyEditor?.();
      } catch {
        // 인스턴스 파기 실패는 무시(이미 파기/미생성)
      }
      editorRef.current = null;
    };
  }, [doc.id, authedFetch]);

  return (
    <>
      {/* 문서 헤더 */}
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
        <div className="min-w-0 leading-tight">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-semibold text-mgray-800">
              {doc.name}
            </span>
            <span className="shrink-0 text-[12px] text-mgray-400">{ext}</span>
          </div>
          <div className="flex items-center gap-1 text-[11px] text-mgray-500">
            <CheckCircle2 className="size-3 text-mgreen-500" />
            <span>저장됨</span>
          </div>
        </div>

        <div className="flex-1" />

        {/* 공동 편집자(본인) — 실시간 공동편집자 표시는 ONLYOFFICE 세션 제공 */}
        <span
          className="mr-1 flex size-7 items-center justify-center rounded-full border-2 border-white bg-brand-500 text-[11px] font-medium text-white"
          title={user?.name ?? "나"}
        >
          {initial}
        </span>
        <span className="mr-1 rounded-full bg-mgray-100 px-2 py-1 text-[11px] font-medium text-mgray-500">
          ONLYOFFICE Docs
        </span>
        <div className="mx-1 h-5 w-px bg-mgray-200" />

        <button
          type="button"
          onClick={() => setVersionsOpen(true)}
          className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[13px] font-medium text-mgray-600 hover:bg-mgray-100"
          title="버전 이력"
        >
          <History className="size-4" />
          버전 이력
        </button>
        <button
          type="button"
          onClick={onDelete}
          aria-label="삭제"
          title="삭제"
          className="flex size-8 items-center justify-center rounded-md text-mgray-500 hover:bg-mred-50 hover:text-mred-500"
        >
          <Trash2 className="size-4" />
        </button>
        <button
          type="button"
          onClick={() => setShareOpen(true)}
          className="flex items-center gap-1.5 rounded-md bg-brand-500 px-3 py-1.5 text-[13px] font-medium text-white hover:bg-brand-700"
        >
          <Share2 className="size-4" />
          공유
        </button>
      </div>

      {/* 편집기 표면 / graceful 상태 */}
      <div className="relative min-h-0 flex-1 bg-mgray-100">
        {/* DS URL 설정 시: DocEditor 마운트 placeholder(loading/embedded 동안 항상 존재) */}
        {state.kind === "loading" || state.kind === "embedded" ? (
          <div id={SURFACE_ID} className="size-full" />
        ) : null}

        {state.kind === "loading" ? (
          <div className="absolute inset-0 flex items-center justify-center text-mgray-400">
            <Loader2 className="size-5 animate-spin" />
          </div>
        ) : null}

        {state.kind === "no-server" ? (
          <div className="flex size-full flex-col items-center justify-center px-6 text-center">
            <div className="mb-4 flex size-16 items-center justify-center rounded-2xl bg-white shadow-sm">
              <Wrench className="size-7 text-mgray-400" />
            </div>
            <h2 className="text-base font-semibold text-mgray-700">
              편집기 준비 중
            </h2>
            <p className="mt-1 max-w-sm text-[13px] leading-relaxed text-mgray-500">
              ONLYOFFICE 편집 서버가 아직 연결되지 않았습니다. 배포 후 실시간
              공동편집이 제공됩니다. 그동안에도 버전 이력·삭제·공유는 사용할 수
              있습니다.
            </p>
          </div>
        ) : null}

        {state.kind === "error" ? (
          <div className="flex size-full flex-col items-center justify-center px-6 text-center">
            <div className="mb-4 flex size-16 items-center justify-center rounded-2xl bg-white shadow-sm">
              <Icon className="size-7 text-mgray-400" />
            </div>
            <p className="max-w-sm text-[13px] leading-relaxed text-mred-500">
              {state.message}
            </p>
          </div>
        ) : null}
      </div>

      {/* 버전 이력 / 공유 모달 */}
      <DocumentVersionsDialog
        open={versionsOpen}
        onOpenChange={setVersionsOpen}
        documentId={doc.id}
        documentName={doc.name}
      />
      <DocumentShareDialog
        open={shareOpen}
        onOpenChange={setShareOpen}
        documentName={doc.name}
      />
    </>
  );
}
