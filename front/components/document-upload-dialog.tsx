"use client";

// 파일 업로드 모달 (SPEC-006 §2 U-3) → POST /documents/files/upload (multipart).
// 업로드 위치(스페이스/폴더) + 드롭존(드래그·클릭) + 선택 파일 목록(파일별 제거).
// accept = .docx/.xlsx 만 (레거시 .doc/.xls·그 외 제외 — SPEC §4, 시안 잔재 배제):
//   file input accept + JS 확장자 필터 양쪽 모두에 적용. 파일 0개면 업로드 비활성.
// 시안: document-management.html #uploadModal. 여러 파일은 순차 업로드(BE 는 1파일=1문서).
import { useRef, useState } from "react";
import { FileText, Loader2, Sheet, UploadCloud, X } from "lucide-react";

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
import type { DocDocument } from "@/types";

// 업로드 위치 옵션 — 페이지가 트리에서 평탄화해 전달(스페이스 루트 + 각 폴더).
export interface DocUploadLocation {
  key: string; // spaceId + "|" + (folderId ?? "")
  spaceId: string;
  folderId: string | null;
  label: string; // "개발팀" / "개발팀 / 회의록"
  group: "부서스페이스" | "개인스페이스";
}

const ALLOWED_EXT = ["docx", "xlsx"]; // .docx/.xlsx 만 (레거시 .doc/.xls 제외)
const ACCEPT = ".docx,.xlsx";

function extOf(name: string): string {
  return name.split(".").pop()?.toLowerCase() ?? "";
}
function isAllowed(name: string): boolean {
  return ALLOWED_EXT.includes(extOf(name));
}
function fmtSize(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

export function DocumentUploadDialog({
  open,
  onOpenChange,
  locations,
  defaultKey,
  onUploaded,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  locations: DocUploadLocation[];
  defaultKey: string | null;
  onUploaded: (summary: string) => void;
}) {
  const { authedFetch } = useAuth();
  const inputRef = useRef<HTMLInputElement>(null);
  // locKey=null = 사용자 미선택 → defaultKey 따름. 닫힐 때 null 로 리셋해 다음 열림에 default 재적용.
  const [locKey, setLocKey] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const effectiveKey = locKey ?? defaultKey ?? locations[0]?.key ?? "";

  // 닫힐 때 초기화(코드베이스 idiom — effect-on-open 회피).
  function handleOpenChange(next: boolean) {
    if (!next) {
      setLocKey(null);
      setFiles([]);
      setError(null);
      setDragging(false);
    }
    onOpenChange(next);
  }

  // .docx/.xlsx 만 수용 — 그 외는 조용히 제외(시안과 동일, 목록에 안 담김).
  function addFiles(list: FileList | File[]) {
    const next: File[] = [];
    let rejected = 0;
    for (const f of Array.from(list)) {
      if (isAllowed(f.name)) next.push(f);
      else rejected += 1;
    }
    if (next.length) setFiles((prev) => [...prev, ...next]);
    setError(
      rejected > 0
        ? "워드(.docx)·엑셀(.xlsx) 형식만 업로드할 수 있습니다"
        : null,
    );
  }

  function removeAt(i: number) {
    setFiles((prev) => prev.filter((_, idx) => idx !== i));
  }

  async function onSubmit() {
    if (!files.length) return;
    const loc = locations.find((l) => l.key === effectiveKey);
    if (!loc) {
      setError("업로드 위치를 선택해주세요");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      let count = 0;
      for (const file of files) {
        const form = new FormData();
        form.append("space_id", loc.spaceId);
        if (loc.folderId) form.append("folder_id", loc.folderId);
        form.append("file", file);
        await authedFetch<DocDocument>("/documents/files/upload", {
          method: "POST",
          body: form,
        });
        count += 1;
      }
      onOpenChange(false);
      onUploaded(`${count}개 파일을 업로드했습니다`);
    } catch (err) {
      setError(uploadErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  const groups: DocUploadLocation["group"][] = ["부서스페이스", "개인스페이스"];

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-[460px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <UploadCloud className="size-[18px] text-brand-500" />파일 업로드
          </DialogTitle>
          <DialogDescription>
            워드·엑셀 파일을 업로드할 위치를 선택하세요.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3.5">
          {/* 업로드 위치 */}
          <div className="space-y-1.5">
            <label
              htmlFor="doc-upload-loc"
              className="block text-[12px] font-medium text-mgray-500"
            >
              업로드 위치
            </label>
            <select
              id="doc-upload-loc"
              value={effectiveKey}
              onChange={(e) => setLocKey(e.target.value)}
              className="h-9 w-full rounded-md border border-mgray-200 bg-card px-2.5 text-[13px] text-mgray-800 focus:border-brand-300 focus:outline-none focus:ring-2 focus:ring-brand-100"
            >
              {groups.map((g) => {
                const opts = locations.filter((l) => l.group === g);
                if (!opts.length) return null;
                return (
                  <optgroup key={g} label={g}>
                    {opts.map((l) => (
                      <option key={l.key} value={l.key}>
                        {l.label}
                      </option>
                    ))}
                  </optgroup>
                );
              })}
            </select>
          </div>

          {/* 드롭존 */}
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files) addFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <div
            role="button"
            tabIndex={0}
            onClick={() => inputRef.current?.click()}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
            }}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={(e) => {
              e.preventDefault();
              setDragging(false);
            }}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (e.dataTransfer.files) addFiles(e.dataTransfer.files);
            }}
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-4 py-7 text-center",
              dragging
                ? "border-brand-300 bg-brand-50"
                : "border-mgray-300 bg-mgray-50 hover:border-brand-300 hover:bg-brand-50",
            )}
          >
            <span className="flex size-10 items-center justify-center rounded-full bg-card text-mgray-400 shadow-sm">
              <UploadCloud className="size-5" />
            </span>
            <span className="text-[13px] font-medium text-mgray-700">
              파일을 끌어다 놓거나 <span className="text-brand-500">클릭</span>해서
              선택
            </span>
            <span className="text-[11px] text-mgray-400">
              워드(.docx) · 엑셀(.xlsx)
            </span>
          </div>

          {/* 선택된 파일 목록 */}
          {files.length ? (
            <ul className="flex flex-col gap-1.5">
              {files.map((f, i) => {
                const isExcel = extOf(f.name) === "xlsx";
                const Icon = isExcel ? Sheet : FileText;
                return (
                  <li
                    key={`${f.name}-${i}`}
                    className="flex items-center gap-2.5 rounded-md border border-mgray-200 px-2.5 py-2"
                  >
                    <span
                      className={cn(
                        "flex size-8 shrink-0 items-center justify-center rounded-md",
                        isExcel
                          ? "bg-mgreen-50 text-mgreen-500"
                          : "bg-brand-50 text-brand-500",
                      )}
                    >
                      <Icon className="size-[18px]" />
                    </span>
                    <span className="min-w-0 flex-1 leading-tight">
                      <span className="block truncate text-[13px] text-mgray-800">
                        {f.name}
                      </span>
                      <span className="block text-[11px] text-mgray-400">
                        {fmtSize(f.size)}
                      </span>
                    </span>
                    <button
                      type="button"
                      aria-label={`${f.name} 제거`}
                      onClick={() => removeAt(i)}
                      disabled={submitting}
                      className="flex size-6 shrink-0 items-center justify-center rounded text-mgray-400 hover:bg-mgray-100 hover:text-mred-500"
                    >
                      <X className="size-4" />
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}

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
          <Button onClick={onSubmit} disabled={submitting || files.length === 0}>
            {submitting ? <Loader2 className="animate-spin" /> : null}
            업로드
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function uploadErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 422)
      return "워드(.docx)·엑셀(.xlsx) 형식만 업로드할 수 있습니다";
    if (err.status === 403) return "해당 스페이스에 업로드할 권한이 없습니다";
    if (err.status === 404) return "업로드 위치를 찾을 수 없습니다";
    return err.message;
  }
  return "업로드에 실패했습니다. 잠시 후 다시 시도해주세요";
}
