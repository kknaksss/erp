"use client";

// 새 파일 모달 (SPEC-006 §2 U-2) → POST /documents/files {space_id, folder_id?, name, type}.
// 형식 선택(워드 .docx / 엑셀 .xlsx) + 위치 표시 + 이름 입력. 빈 .docx/.xlsx 생성(BE).
// 이름 미입력 시 검증 에러(BE 422 와 정합). 시안: document-management.html #fileModal.
import { useState } from "react";
import { FilePlus, FileText, Loader2, Sheet } from "lucide-react";

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
import { cn } from "@/lib/utils";
import type {
  DocCreateTarget,
  DocDocument,
  DocumentCreateBody,
  DocumentType,
} from "@/types";

const FILE_TYPES: {
  value: DocumentType;
  label: string;
  ext: string;
  icon: typeof FileText;
  iconCls: string;
}[] = [
  {
    value: "word",
    label: "워드 문서",
    ext: ".docx",
    icon: FileText,
    iconCls: "bg-brand-50 text-brand-500",
  },
  {
    value: "excel",
    label: "엑셀 시트",
    ext: ".xlsx",
    icon: Sheet,
    iconCls: "bg-mgreen-50 text-mgreen-500",
  },
];

export function DocumentNewFileDialog({
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
  const [type, setType] = useState<DocumentType>("word");
  const [name, setName] = useState("");
  const [touched, setTouched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const empty = !name.trim(); // 이름 미입력 → 만들기 비활성 + 검증 에러(SPEC §2 U-2)
  const showHint = touched && empty;

  // 닫힐 때 초기화(코드베이스 idiom). 형식은 기본 word 로 복원.
  function handleOpenChange(next: boolean) {
    if (!next) {
      setType("word");
      setName("");
      setTouched(false);
      setError(null);
    }
    onOpenChange(next);
  }

  const ext = type === "excel" ? ".xlsx" : ".docx";

  async function onSubmit() {
    if (empty || !target) return;
    const body: DocumentCreateBody = {
      space_id: target.spaceId,
      folder_id: target.parentId ?? undefined,
      name: name.trim(),
      type,
    };
    setSubmitting(true);
    setError(null);
    try {
      const doc = await authedFetch<DocDocument>("/documents/files", {
        method: "POST",
        body: JSON.stringify(body),
      });
      onOpenChange(false);
      onCreated(`파일 "${doc.name}"을(를) 만들었습니다`);
    } catch (err) {
      setError(fileErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-[440px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FilePlus className="size-[18px] text-brand-500" />새 파일
          </DialogTitle>
          <DialogDescription>현재 위치에 파일을 추가합니다.</DialogDescription>
        </DialogHeader>

        <div className="space-y-3.5">
          {/* 파일 형식 */}
          <div>
            <span className="mb-1.5 block text-[12px] font-medium text-mgray-500">
              파일 형식
            </span>
            <div className="grid grid-cols-2 gap-2.5">
              {FILE_TYPES.map((t) => {
                const Icon = t.icon;
                const active = type === t.value;
                return (
                  <button
                    key={t.value}
                    type="button"
                    onClick={() => setType(t.value)}
                    className={cn(
                      "flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left",
                      active
                        ? "border-brand-500 bg-brand-50/40 ring-2 ring-brand-100"
                        : "border-mgray-200 hover:border-mgray-300",
                    )}
                  >
                    <span
                      className={cn(
                        "flex size-9 shrink-0 items-center justify-center rounded-md",
                        t.iconCls,
                      )}
                    >
                      <Icon className="size-[18px]" />
                    </span>
                    <span className="leading-tight">
                      <span className="block text-[13px] font-medium text-mgray-800">
                        {t.label}
                      </span>
                      <span className="block text-[11px] text-mgray-500">
                        {t.ext}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* 위치 */}
          <div>
            <span className="mb-1.5 block text-[12px] font-medium text-mgray-500">
              위치
            </span>
            <div className="flex items-center gap-1.5 rounded-md border border-mgray-200 bg-mgray-50 px-2.5 py-2 text-[13px] text-mgray-600">
              <FilePlus className="size-4 shrink-0 text-mgray-400" />
              <span className="truncate">{target?.label ?? "—"}</span>
            </div>
          </div>

          {/* 파일명 */}
          <div className="space-y-1.5">
            <label
              htmlFor="doc-file-name"
              className="block text-[12px] font-medium text-mgray-500"
            >
              파일 이름
            </label>
            <div
              className={cn(
                "flex items-center gap-2 rounded-md border bg-card px-3 py-2 focus-within:ring-2 focus-within:ring-brand-100",
                showHint
                  ? "border-mred-500"
                  : "border-mgray-200 focus-within:border-brand-300",
              )}
            >
              <input
                id="doc-file-name"
                type="text"
                maxLength={60}
                placeholder="예: 2026 채용 계획"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onBlur={() => setTouched(true)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") onSubmit();
                }}
                className="min-w-0 flex-1 bg-transparent text-[13px] text-mgray-800 placeholder:text-mgray-400 focus:outline-none"
                autoFocus
              />
              <span className="shrink-0 text-[13px] text-mgray-400">{ext}</span>
            </div>
            {showHint ? (
              <p className="text-[12px] text-mred-500">파일 이름을 입력하세요.</p>
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

function fileErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 422) return "파일 이름을 입력하세요.";
    if (err.status === 403) return "해당 스페이스에 파일을 만들 권한이 없습니다";
    if (err.status === 404) return "상위 폴더를 찾을 수 없습니다";
    return err.message;
  }
  return "파일 생성에 실패했습니다. 잠시 후 다시 시도해주세요";
}
