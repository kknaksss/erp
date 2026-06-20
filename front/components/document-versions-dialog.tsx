"use client";

// 버전 이력 모달 (SPEC-006 §2 U-4·S-4) → GET /documents/files/{id}/versions (DocVersion[]).
// 모든 저장이 버전으로 보존 — 목록(버전 번호·확장자·크기·생성일). 열 때 fetch.
// 시안: document-management.html 편집기 헤더 "버전 이력".
import { useCallback, useEffect, useState } from "react";
import { History, Loader2 } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { DocVersion } from "@/types";

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; versions: DocVersion[] }
  | { kind: "error"; message: string };

function fmtSize(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

export function DocumentVersionsDialog({
  open,
  onOpenChange,
  documentId,
  documentName,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  documentId: string;
  documentName: string;
}) {
  const { authedFetch } = useAuth();
  const [state, setState] = useState<LoadState>({ kind: "idle" });

  const fetchVersions = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const versions = await authedFetch<DocVersion[]>(
        `/documents/files/${documentId}/versions`,
      );
      // 최신 버전이 위로(version_no DESC).
      const sorted = [...versions].sort((a, b) => b.version_no - a.version_no);
      setState({ kind: "ok", versions: sorted });
    } catch (err) {
      setState({
        kind: "error",
        message:
          err instanceof ApiError ? err.message : "버전 이력을 불러오지 못했습니다",
      });
    }
  }, [authedFetch, documentId]);

  // 열릴 때 fetch. 로딩 표시는 fetchVersions 내부(핸들러성 호출) — effect 본문 직접 setState 아님.
  useEffect(() => {
    if (!open) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchVersions();
  }, [open, fetchVersions]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[440px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <History className="size-[18px] text-brand-500" />버전 이력
          </DialogTitle>
          <DialogDescription>
            {documentName} 의 저장 버전 — 모든 수정이 버전으로 보존됩니다.
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-[120px]">
          {state.kind === "loading" || state.kind === "idle" ? (
            <div className="flex items-center justify-center py-10 text-mgray-400">
              <Loader2 className="size-5 animate-spin" />
            </div>
          ) : null}

          {state.kind === "error" ? (
            <p className="rounded-md bg-mred-50 px-3 py-2.5 text-[13px] text-mred-500">
              {state.message}
            </p>
          ) : null}

          {state.kind === "ok" ? (
            state.versions.length === 0 ? (
              <p className="rounded-md bg-mgray-50 px-3 py-2.5 text-[13px] text-mgray-400">
                저장된 버전이 없습니다.
              </p>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {state.versions.map((v) => (
                  <li
                    key={v.id}
                    className="flex items-center gap-3 rounded-md border border-mgray-200 px-3 py-2"
                  >
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-brand-50 font-mono text-[12px] font-semibold text-brand-500">
                      v{v.version_no}
                    </span>
                    <span className="min-w-0 flex-1 leading-tight">
                      <span className="block text-[13px] font-medium text-mgray-800">
                        버전 {v.version_no}
                        <span className="ml-1 text-[11px] font-normal text-mgray-400">
                          .{v.ext}
                        </span>
                      </span>
                      <span className="block text-[11px] text-mgray-400">
                        {new Date(v.created_at).toLocaleString("ko-KR")} ·{" "}
                        {fmtSize(v.size_bytes)}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            )
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
