"use client";

// HR 연차수 조정 모달 (SPEC-003 §연차수 조정, WP-005 P2) → POST /leave/admin/adjustments.
// 한 직원의 종류별 잔여를 한 번에 ± 보정(연차/Off Day/보상/포상). delta ≠ 0(0 은 변화 없음),
// 음수 잔여 허용(경고는 표시단). 사유 1건이 전 항목에 공통 적용(시안 단일 사유 필드).
//  - delta=0/빈 항목은 제출 전 FE 가드 + BE 422 핸들. 성공 시 응답 balances 로 부모가 상세 갱신.
// 시안: 21-html/leave-admin-hr.html #adjustModal. balances/delta = Decimal 문자열(미리보기 외 산술 X).
import { useMemo, useState } from "react";
import { Loader2, Minus, Plus, SlidersHorizontal } from "lucide-react";

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
import { formatDays } from "@/lib/utils";
import type {
  AdjustmentBody,
  AdjustmentResult,
  EmployeeLeaveDetail,
  LeaveCategory,
} from "@/types";

// 시안 카드 순서 = 연차 / Off Day / 보상(보상연차) / 포상(포상휴가). 4 종류 전부 조정 대상.
const ADJ_CATEGORIES: { value: LeaveCategory; label: string; hint?: string }[] = [
  { value: "연차", label: "연차" },
  { value: "Off Day", label: "Off Day" },
  { value: "보상", label: "보상", hint: "(보상연차)" },
  { value: "포상", label: "포상", hint: "(포상휴가)" },
];

const ZERO_DELTAS: Record<LeaveCategory, string> = {
  연차: "0",
  "Off Day": "0",
  보상: "0",
  포상: "0",
};

// 0.5 step 가감 후 표시·전송 문자열 — 0.25 입력 보존(toFixed(1) 금지: 0.75→"0.8" 방지).
function step(value: string, by: number): string {
  return formatDays((parseFloat(value) || 0) + by);
}

export function LeaveAdjustDialog({
  open,
  onOpenChange,
  detail,
  onAdjusted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  detail: EmployeeLeaveDetail | null; // 조정 대상(현재 잔여 표시 기준)
  onAdjusted: (summary: string) => void; // 성공 요약(부모가 toast + 상세 재조회)
}) {
  const { authedFetch } = useAuth();
  const [deltas, setDeltas] = useState<Record<LeaveCategory, string>>(ZERO_DELTAS);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 닫힐 때 입력 초기화(다음 대상 잔존 방지) — effect 내 setState 회피, leave-request-dialog 패턴.
  function reset() {
    setDeltas({ ...ZERO_DELTAS });
    setReason("");
    setError(null);
  }
  function handleOpenChange(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

  // 현재 잔여(표시용) — balances 문자열을 parseFloat(미리보기 한정).
  const current = useMemo(() => {
    const map: Partial<Record<LeaveCategory, number>> = {};
    if (detail) {
      for (const c of ADJ_CATEGORIES)
        map[c.value] = parseFloat(detail.balances[c.value] ?? "0") || 0;
    }
    return map;
  }, [detail]);

  function setDelta(cat: LeaveCategory, value: string) {
    setDeltas((prev) => ({ ...prev, [cat]: value }));
  }

  async function onSubmit() {
    if (!detail) return;
    // delta ≠ 0 인 항목만 전송(0 은 변화 없음). 빈 항목이면 가드.
    const items = ADJ_CATEGORIES.map((c) => ({
      category: c.value,
      delta: deltas[c.value].trim(),
    })).filter((it) => (parseFloat(it.delta) || 0) !== 0);
    if (!items.length) {
      setError("변경할 종류의 증감을 입력해주세요 (0 은 변화 없음)");
      return;
    }
    const trimmedReason = reason.trim();
    const body: AdjustmentBody = {
      employee_id: detail.employee.id,
      items: items.map((it) =>
        trimmedReason ? { ...it, reason: trimmedReason } : it,
      ),
    };

    setSubmitting(true);
    setError(null);
    try {
      const res = await authedFetch<AdjustmentResult>(
        "/leave/admin/adjustments",
        { method: "POST", body: JSON.stringify(body) },
      );
      handleOpenChange(false);
      onAdjusted(
        `${detail.employee.name} 잔여를 ${res.items.length}개 종류 조정했습니다`,
      );
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 422) {
          setError("조정값을 확인해주세요 (0 또는 비활성 대상 불가)");
        } else if (err.status === 404) {
          setError("대상 직원을 찾을 수 없습니다");
        } else if (err.status === 403) {
          setError("HR 권한이 필요합니다");
        } else {
          setError(err.message);
        }
      } else {
        setError("조정에 실패했습니다. 잠시 후 다시 시도해주세요");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-[720px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <SlidersHorizontal className="size-[18px] text-brand-500" />
            연차수 조정{detail ? ` · ${detail.employee.name}` : ""}
          </DialogTitle>
          <DialogDescription>
            종류별 증감 (음수 가능 · 0 은 변화 없음) — 현재 → 변경 후
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {ADJ_CATEGORIES.map((c) => {
              const cur = current[c.value] ?? 0;
              const delta = parseFloat(deltas[c.value]) || 0;
              const next = cur + delta;
              const nextColor =
                next < 0
                  ? "text-mred-500"
                  : delta !== 0
                    ? "text-brand-500"
                    : "text-mgray-700";
              return (
                <div
                  key={c.value}
                  className="rounded-md border border-mgray-100 px-3 py-2.5"
                >
                  <div className="text-[13px] font-medium text-mgray-800">
                    {c.label}
                    {c.hint ? (
                      <span className="text-[11px] font-normal text-mgray-400">
                        {" "}
                        {c.hint}
                      </span>
                    ) : null}
                  </div>
                  <div className="mb-1.5 text-[11px] text-mgray-400">
                    현재{" "}
                    <span className="font-medium text-mgray-600">
                      {formatDays(cur)}
                    </span>{" "}
                    →{" "}
                    <span className={`font-medium ${nextColor}`}>
                      {formatDays(next)}
                    </span>
                  </div>
                  <div className="flex items-center">
                    <button
                      type="button"
                      aria-label={`${c.label} 감소`}
                      onClick={() => setDelta(c.value, step(deltas[c.value], -0.5))}
                      className="flex size-7 shrink-0 items-center justify-center rounded-l-md border border-mgray-200 text-mgray-600 hover:bg-mgray-50"
                    >
                      <Minus className="size-3.5" />
                    </button>
                    <input
                      type="number"
                      step="0.5"
                      aria-label={`${c.label} 증감`}
                      value={deltas[c.value]}
                      onChange={(e) => setDelta(c.value, e.target.value)}
                      className="h-7 w-full min-w-0 flex-1 border-y border-mgray-200 text-center text-[13px] focus:outline-none"
                    />
                    <button
                      type="button"
                      aria-label={`${c.label} 증가`}
                      onClick={() => setDelta(c.value, step(deltas[c.value], 0.5))}
                      className="flex size-7 shrink-0 items-center justify-center rounded-r-md border border-mgray-200 text-mgray-600 hover:bg-mgray-50"
                    >
                      <Plus className="size-3.5" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="adjust-reason"
              className="block text-[12px] font-medium text-mgray-700"
            >
              사유 <span className="font-normal text-mgray-400">(선택)</span>
            </label>
            <Input
              id="adjust-reason"
              type="text"
              placeholder="예: 시스템 누락 보정"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
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
          <Button onClick={onSubmit} disabled={submitting || !detail}>
            {submitting ? <Loader2 className="animate-spin" /> : null}
            적용
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
