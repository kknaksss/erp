"use client";

// 직원 비활성(soft delete) 확인 모달 (SPEC-002 §U-3, WP-007 P4) → DELETE /admin/employees/{id}.
//  soft delete = active=false, 행 보존(연차 이력 FK 보존). hard delete 안 함.
//  mediness 로그인 계정도 비활성화 push 해 퇴사자 로그인 차단(SPEC-002 §3 비활성화 push).
//  문구는 SPEC-002 §U-3 / 시안 #deleteModal verbatim. 위험(destructive) 버튼.
//  ⚠ contract-first: BE P2 DELETE 미구현 — 경로 SPEC-002 §3 계약 기준 가정(리포트 참조).
import { useState } from "react";
import { Loader2, UserMinus } from "lucide-react";

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
import type { Employee } from "@/types";

export function EmployeeDeactivateDialog({
  open,
  onOpenChange,
  employee,
  onDeactivated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  employee: Employee | null;
  onDeactivated: (summary: string) => void;
}) {
  const { authedFetch } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function handleOpenChange(next: boolean) {
    if (!next) setError(null);
    onOpenChange(next);
  }

  async function onConfirm() {
    if (!employee) return;
    setSubmitting(true);
    setError(null);
    try {
      await authedFetch<void>(`/admin/employees/${employee.id}`, {
        method: "DELETE",
      });
      onOpenChange(false);
      onDeactivated("직원이 비활성 처리되었습니다");
    } catch (err) {
      setError(deactivateErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-[420px]">
        <DialogHeader>
          <span className="mb-1 flex size-10 items-center justify-center rounded-full bg-mred-50">
            <UserMinus className="size-5 text-mred-500" />
          </span>
          <DialogTitle>직원 비활성 처리</DialogTitle>
          <DialogDescription>
            이 직원을 비활성 처리할까요? (연차 이력은 보존됩니다)
          </DialogDescription>
        </DialogHeader>

        <div className="rounded-md bg-mgray-50 px-3 py-2 text-[12px] leading-relaxed text-mgray-500">
          soft delete — <span className="font-mono">active = false</span> 로
          표시하고 행은 보존합니다(연차 이력 FK 보존, hard delete 안 함).
          mediness 로그인 계정도 비활성화되어 퇴사자 로그인이 차단됩니다.
        </div>

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

function deactivateErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return "HR 권한이 필요합니다";
    if (err.status === 404) return "직원을 찾을 수 없습니다 (이미 처리됨)";
    return err.message;
  }
  return "비활성 처리에 실패했습니다. 잠시 후 다시 시도해주세요";
}
