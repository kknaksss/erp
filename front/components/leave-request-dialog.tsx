"use client";

// 연차 신청 폼 (SPEC-004 ERP 채널) → POST /leave/intake.
// 종류(연차/보상/포상/Off Day) × 사용 단위(전일/오전·오후 반차/오전·오후 반반차).
// UI "오전 반차" → { unit: "반차", am_pm: "오전" } 매핑. 전일 = am_pm 없음. amount 는 서버 derive(안 보냄).
// Off Day = 반차만(전일·반반차 옵션 숨김). 제출 성공 시 onSubmitted() 로 /leave/me 재조회.
import { useState } from "react";
import { CalendarPlus } from "lucide-react";

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type {
  AmPm,
  ErpIntakeBody,
  LeaveCategory,
  LeaveRequest,
  LeaveUnit,
} from "@/types";

const CATEGORY_OPTIONS: { value: LeaveCategory; label: string }[] = [
  { value: "연차", label: "연차" },
  { value: "보상", label: "보상 (보상연차)" },
  { value: "포상", label: "포상 (포상휴가)" },
  { value: "Off Day", label: "Off Day" },
];

// UI 단위 옵션 = (사용 단위 × 오전/오후) 평탄화. value 가 곧 셀렉트 키.
type UnitOption = {
  value: string;
  label: string;
  unit: LeaveUnit;
  am_pm?: AmPm;
  half: boolean; // 반차 여부(Off Day 필터용)
};

const UNIT_OPTIONS: UnitOption[] = [
  { value: "전일", label: "전일 (1.0)", unit: "전일", half: false },
  { value: "오전 반차", label: "오전 반차 (0.5)", unit: "반차", am_pm: "오전", half: true },
  { value: "오후 반차", label: "오후 반차 (0.5)", unit: "반차", am_pm: "오후", half: true },
  { value: "오전 반반차", label: "오전 반반차 (0.25)", unit: "반반차", am_pm: "오전", half: false },
  { value: "오후 반반차", label: "오후 반반차 (0.25)", unit: "반반차", am_pm: "오후", half: false },
];

export function LeaveRequestDialog({
  open,
  onOpenChange,
  onSubmitted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmitted: () => void;
}) {
  const { authedFetch } = useAuth();
  const [category, setCategory] = useState<LeaveCategory>("연차");
  const [unit, setUnit] = useState<string>("전일");
  const [useDate, setUseDate] = useState("");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Off Day = 반차만. 그 외는 전 옵션.
  const unitOptions =
    category === "Off Day" ? UNIT_OPTIONS.filter((o) => o.half) : UNIT_OPTIONS;

  function onCategoryChange(next: string) {
    const cat = next as LeaveCategory;
    setCategory(cat);
    // Off Day 로 바뀌면 비반차 선택을 반차로 보정(잘못된 조합 제출 방지).
    if (cat === "Off Day" && !UNIT_OPTIONS.find((o) => o.value === unit)?.half) {
      setUnit("오전 반차");
    }
  }

  function reset() {
    setCategory("연차");
    setUnit("전일");
    setUseDate("");
    setNote("");
    setError(null);
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

  async function onSubmit() {
    const opt = UNIT_OPTIONS.find((o) => o.value === unit);
    if (!opt || !useDate) return;
    setSubmitting(true);
    setError(null);
    const body: ErpIntakeBody = {
      category,
      unit: opt.unit,
      use_date: useDate,
    };
    if (opt.am_pm) body.am_pm = opt.am_pm;
    const trimmed = note.trim();
    if (trimmed) body.note = trimmed;
    try {
      await authedFetch<LeaveRequest>("/leave/intake", {
        method: "POST",
        body: JSON.stringify(body),
      });
      reset();
      onOpenChange(false);
      onSubmitted();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.status === 422
            ? "입력값을 확인해주세요 (종류·단위·날짜)"
            : err.message
          : "신청에 실패했습니다. 잠시 후 다시 시도해주세요",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-[460px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <CalendarPlus className="size-[18px] text-brand-500" />
            연차 신청
          </DialogTitle>
          <DialogDescription>
            소속·성함은 입력하지 않습니다 — 로그인 계정으로 신청됩니다.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="block text-[12px] font-medium text-mgray-700">
                종류
              </label>
              <Select value={category} onValueChange={onCategoryChange}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CATEGORY_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <label className="block text-[12px] font-medium text-mgray-700">
                사용 단위
              </label>
              <Select value={unit} onValueChange={setUnit}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {unitOptions.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <p className="text-[11px] text-mgray-400">
            보상·포상도 반차·반반차로 쪼개 쓸 수 있습니다. Off Day 는 반차만
            가능합니다.
          </p>

          <div className="space-y-1.5">
            <label
              htmlFor="leave-use-date"
              className="block text-[12px] font-medium text-mgray-700"
            >
              사용날짜{" "}
              <span className="font-normal text-mgray-400">
                (1 신청 = 하루치)
              </span>
            </label>
            <Input
              id="leave-use-date"
              type="date"
              value={useDate}
              onChange={(e) => setUseDate(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="leave-note"
              className="block text-[12px] font-medium text-mgray-700"
            >
              비고{" "}
              <span className="font-normal text-mgray-400">(사유 · 선택)</span>
            </label>
            <Textarea
              id="leave-note"
              rows={2}
              placeholder="예: 개인 사유"
              value={note}
              onChange={(e) => setNote(e.target.value)}
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
          <Button onClick={onSubmit} disabled={submitting || !useDate}>
            신청
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
