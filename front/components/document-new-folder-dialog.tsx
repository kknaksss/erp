"use client";

// 새 폴더 모달 (SPEC-006 §2 U-2) → POST /documents/folders {space_id, parent_id?, name}.
// 위치(현재 선택 스페이스/폴더) 표시 + 이름 입력. 이름 미입력 시 검증 에러(BE 422 와 정합).
// 시안: 21-html/document-management.html #folderModal.
import { useState } from "react";
import { FolderPlus, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { DocCreateTarget, DocFolder, FolderCreateBody } from "@/types";

export function DocumentNewFolderDialog({
  open,
  onOpenChange,
  target,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  target: DocCreateTarget | null;
  onCreated: (summary: string) => void;
}) {
  const { authedFetch } = useAuth();
  const [name, setName] = useState("");
  const [touched, setTouched] = useState(false); // 포커스 이탈 후 빈값이면 검증 에러 노출
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const empty = !name.trim(); // 이름 미입력 → 만들기 비활성 + 검증 에러(SPEC §2 U-2)
  const showHint = touched && empty;

  // 닫힐 때 입력 초기화(코드베이스 idiom — leave-grant-dialog 동일). 다음 열림은 깨끗하게 시작.
  function handleOpenChange(next: boolean) {
    if (!next) {
      setName("");
      setTouched(false);
      setError(null);
    }
    onOpenChange(next);
  }

  async function onSubmit() {
    if (empty || !target) return;
    const body: FolderCreateBody = {
      space_id: target.spaceId,
      parent_id: target.parentId ?? undefined,
      name: name.trim(),
    };
    setSubmitting(true);
    setError(null);
    try {
      const folder = await authedFetch<DocFolder>("/documents/folders", {
        method: "POST",
        body: JSON.stringify(body),
      });
      onOpenChange(false);
      onCreated(`폴더 "${folder.name}"을(를) 만들었습니다`);
    } catch (err) {
      setError(folderErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-[420px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FolderPlus className="size-[18px] text-brand-500" />새 폴더
          </DialogTitle>
          <DialogDescription>현재 위치에 폴더를 만듭니다.</DialogDescription>
        </DialogHeader>

        <div className="space-y-3.5">
          {/* 위치 */}
          <div>
            <span className="mb-1.5 block text-[12px] font-medium text-mgray-500">
              위치
            </span>
            <div className="flex items-center gap-1.5 rounded-md border border-mgray-200 bg-mgray-50 px-2.5 py-2 text-[13px] text-mgray-600">
              <FolderPlus className="size-4 shrink-0 text-mgray-400" />
              <span className="truncate">{target?.label ?? "—"}</span>
            </div>
          </div>

          {/* 폴더명 */}
          <div className="space-y-1.5">
            <label
              htmlFor="doc-folder-name"
              className="block text-[12px] font-medium text-mgray-500"
            >
              폴더 이름
            </label>
            <Input
              id="doc-folder-name"
              type="text"
              maxLength={50}
              placeholder="예: 회의록"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onBlur={() => setTouched(true)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onSubmit();
              }}
              className={showHint ? "border-mred-500" : undefined}
              autoFocus
            />
            {showHint ? (
              <p className="text-[12px] text-mred-500">폴더 이름을 입력하세요.</p>
            ) : null}
          </div>

          {error ? (
            <p className="rounded-md bg-mred-50 px-3 py-2 text-[12px] text-mred-500">
              {error}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={submitting}
          >
            취소
          </Button>
          <Button onClick={onSubmit} disabled={submitting || empty}>
            {submitting ? <Loader2 className="animate-spin" /> : null}
            만들기
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function folderErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 422) return "폴더 이름을 입력하세요.";
    if (err.status === 403) return "해당 스페이스에 폴더를 만들 권한이 없습니다";
    if (err.status === 404) return "상위 폴더를 찾을 수 없습니다";
    return err.message;
  }
  return "폴더 생성에 실패했습니다. 잠시 후 다시 시도해주세요";
}
