"use client";

// 삭제 확인 모달 (SPEC-006 §2 U-6) → DELETE /documents/files/{id} (204).
// "되돌릴 수 없음" 경고 + 위험(destructive) 삭제 버튼. 성공 시 트리에서 제거 + 편집기 닫기(부모).
// 시안: document-management.html #deleteModal. ConfirmDialog 대신 별도 — 위험 스타일·비동기 처리.
import { useState } from "react";
import { Loader2, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { DocDocument } from "@/types";

export function DocumentDeleteDialog({
  open,
  onOpenChange,
  doc,
  onDeleted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  doc: DocDocument | null;
  onDeleted: (summary: string) => void;
}) {
  const { authedFetch } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // 닫힐 때 에러 초기화(코드베이스 idiom) — 다음 열림에 stale 에러 노출 방지.
  function handleOpenChange(next: boolean) {
    if (!next) setError(null);
    onOpenChange(next);
  }

  async function onConfirm() {
    if (!doc) return;
    setSubmitting(true);
    setError(null);
    try {
      await authedFetch<void>(`/documents/files/${doc.id}`, {
        method: "DELETE",
      });
      onOpenChange(false);
      onDeleted(`"${doc.name}"을(를) 삭제했습니다`);
    } catch (err) {
      setError(deleteErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-[380px]">
        <DialogHeader>
          <span className="mb-1 flex size-10 items-center justify-center rounded-full bg-mred-50">
            <Trash2 className="size-5 text-mred-500" />
          </span>
          <DialogTitle>문서를 삭제할까요?</DialogTitle>
          <DialogDescription>
            <span className="font-medium text-mgray-700">
              {doc?.name ?? "문서"}
            </span>{" "}
            파일이 삭제됩니다. 이 작업은 되돌릴 수 없습니다.
          </DialogDescription>
        </DialogHeader>

        {error ? (
          <p className="rounded-md bg-mred-50 px-3 py-2 text-[12px] text-mred-500">
            {error}
          </p>
        ) : null}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={submitting}
          >
            취소
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={submitting}>
            {submitting ? <Loader2 className="animate-spin" /> : null}
            삭제
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function deleteErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return "이 문서를 삭제할 권한이 없습니다";
    if (err.status === 404) return "문서를 찾을 수 없습니다 (이미 삭제됨)";
    return err.message;
  }
  return "삭제에 실패했습니다. 잠시 후 다시 시도해주세요";
}
