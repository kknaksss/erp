"use client";

// HR 벌크 부여 모달 (SPEC-003 §부여, WP-005 P1) → POST /leave/admin/grants.
// 종류(보상/포상/Off Day — `연차` 제외) × 다중 직원(부서 필터 + 전체 선택) × 일수·만료일·사유.
//  - 보상/포상: 일수·만료일 필수(FE 가드 + BE 422). Off Day: 비우면 BE default(0.5·그달 말일).
//  - Off Day 선택 시 시안 디폴트 프리필(전체 선택 + 0.5 + 그달 말일 + 사유 "off-day").
//  - 성공 시 결과 요약 toast(onGranted 콜백) · 422 detail.missing/inactive 안내.
// 시안: 21-html/leave-admin-hr.html #grantModal. amount/delta = Decimal 문자열(산술 X).
import { useMemo, useState } from "react";
import { Gift, Loader2, X } from "lucide-react";

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
import type { BulkGrantBody, BulkGrantResult, Employee, GrantCategory } from "@/types";

// 종류 토글 — 시안: 보상연차(특별연차) / 포상휴가 / Off Day. value = BE category enum.
const GRANT_TYPES: { value: GrantCategory; label: string; hint?: string }[] = [
  { value: "보상", label: "보상연차", hint: "(특별연차)" },
  { value: "포상", label: "포상휴가" },
  { value: "Off Day", label: "Off Day" },
];

// 그달 말일 — Off Day default(이월 안 됨). YYYY-MM-DD.
function endOfMonth(): string {
  const d = new Date();
  const last = new Date(d.getFullYear(), d.getMonth() + 1, 0);
  const mm = String(last.getMonth() + 1).padStart(2, "0");
  const dd = String(last.getDate()).padStart(2, "0");
  return `${last.getFullYear()}-${mm}-${dd}`;
}

export function LeaveGrantDialog({
  open,
  onOpenChange,
  roster,
  onGranted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  roster: Employee[]; // 부여 대상 후보(활성·비활성 모두 — 비활성은 BE 가 422)
  onGranted: (summary: string) => void; // 성공 요약(부모가 toast + 상세 재조회)
}) {
  const { authedFetch } = useAuth();
  const [grantType, setGrantType] = useState<GrantCategory>("보상");
  const [dept, setDept] = useState<string>("전체");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [amount, setAmount] = useState("2");
  const [expiry, setExpiry] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 부서 필터 칩 = 전체 + roster 의 distinct department(가나다순). null 부서는 칩에 안 노출.
  const depts = useMemo(() => {
    const set = new Set<string>();
    for (const e of roster) if (e.department) set.add(e.department);
    return ["전체", ...Array.from(set).sort((a, b) => a.localeCompare(b, "ko"))];
  }, [roster]);

  const visible = useMemo(
    () => roster.filter((e) => dept === "전체" || e.department === dept),
    [roster, dept],
  );
  const selectedRows = useMemo(
    () => roster.filter((e) => selected.has(e.id)),
    [roster, selected],
  );
  // 전체 선택 = 현재 부서 필터로 보이는 행 기준.
  const allVisibleChecked =
    visible.length > 0 && visible.every((e) => selected.has(e.id));

  function reset() {
    setGrantType("보상");
    setDept("전체");
    setSelected(new Set());
    setAmount("2");
    setExpiry("");
    setReason("");
    setError(null);
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

  function toggleOne(id: string, on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function toggleAllVisible(on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const e of visible) {
        if (on) next.add(e.id);
        else next.delete(e.id);
      }
      return next;
    });
  }

  // 종류 전환 — Off Day 는 시안 디폴트 프리필, 보상/포상은 기본값 복원.
  function onGrantTypeChange(next: GrantCategory) {
    setGrantType(next);
    if (next === "Off Day") {
      setAmount("0.5");
      setExpiry(endOfMonth());
      setReason("off-day");
      // 보이는 행 전체 선택(매월 전사 제공 성격)
      setSelected((prev) => {
        const ns = new Set(prev);
        for (const e of visible) ns.add(e.id);
        return ns;
      });
    } else {
      setAmount("2");
      setExpiry("");
      setReason((r) => (r === "off-day" ? "" : r));
    }
  }

  // 422 detail({ missing?, inactive? }) → 직원명 안내(roster 로 UUID→이름 매핑, 없으면 원문).
  function describeDetail(detail: unknown): string | null {
    if (!detail || typeof detail !== "object") return null;
    const d = detail as { missing?: unknown; inactive?: unknown };
    const nameOf = (id: unknown) => {
      const hit = roster.find((e) => e.id === id);
      return hit ? hit.name : String(id);
    };
    const parts: string[] = [];
    if (Array.isArray(d.missing) && d.missing.length)
      parts.push(`미존재: ${d.missing.map(nameOf).join(", ")}`);
    if (Array.isArray(d.inactive) && d.inactive.length)
      parts.push(`비활성: ${d.inactive.map(nameOf).join(", ")}`);
    return parts.length ? parts.join(" · ") : null;
  }

  async function onSubmit() {
    const ids = Array.from(selected);
    if (!ids.length) {
      setError("대상 직원을 선택해주세요");
      return;
    }
    // 보상/포상은 일수·만료일 필수(Off Day 는 비우면 BE default).
    const trimmedAmount = amount.trim();
    if (grantType !== "Off Day") {
      if (!trimmedAmount || Number(trimmedAmount) <= 0) {
        setError("일수를 입력해주세요 (0보다 큰 값)");
        return;
      }
      if (!expiry) {
        setError("유효기간(만료일)을 입력해주세요");
        return;
      }
    }
    const body: BulkGrantBody = { employee_ids: ids, category: grantType };
    if (trimmedAmount) body.amount = trimmedAmount;
    if (expiry) body.expiry_date = expiry;
    const trimmedReason = reason.trim();
    if (trimmedReason) body.reason = trimmedReason;

    setSubmitting(true);
    setError(null);
    try {
      const res = await authedFetch<BulkGrantResult>("/leave/admin/grants", {
        method: "POST",
        body: JSON.stringify(body),
      });
      reset();
      onOpenChange(false);
      onGranted(
        `${res.category} ${res.amount}일을 ${res.target_count}명에게 부여했습니다 (만료 ${res.expiry_date})`,
      );
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 422) {
          const desc = describeDetail(err.detail);
          setError(
            desc
              ? `부여할 수 없는 대상이 있습니다 — ${desc}`
              : "입력값을 확인해주세요 (종류·일수·만료일·대상)",
          );
        } else if (err.status === 404) {
          setError("대상 직원을 찾을 수 없습니다");
        } else if (err.status === 403) {
          setError("HR 권한이 필요합니다");
        } else {
          setError(err.message);
        }
      } else {
        setError("부여에 실패했습니다. 잠시 후 다시 시도해주세요");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-[520px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Gift className="size-[18px] text-brand-500" />
            보상연차 / 포상휴가 부여 (벌크)
          </DialogTitle>
          <DialogDescription>
            여러 직원에게 한 번에 HR 부여형 잔여를 부여합니다.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* 종류 */}
          <div>
            <div className="mb-1.5 text-[12px] font-medium text-mgray-700">
              종류
            </div>
            <div className="flex gap-2">
              {GRANT_TYPES.map((t) => {
                const on = grantType === t.value;
                return (
                  <button
                    key={t.value}
                    type="button"
                    onClick={() => onGrantTypeChange(t.value)}
                    className={
                      on
                        ? "flex-1 rounded-md border border-brand-500 bg-brand-50 px-3 py-2 text-[13px] font-medium text-brand-500"
                        : "flex-1 rounded-md border border-mgray-200 px-3 py-2 text-[13px] font-medium text-mgray-600 hover:bg-mgray-50"
                    }
                  >
                    {t.label}
                    {t.hint ? (
                      <span className="text-mgray-400"> {t.hint}</span>
                    ) : null}
                  </button>
                );
              })}
            </div>
            <p className="mt-1 text-[11px] text-mgray-400">
              Off Day 는 원래 매월 자동 제공 — 스케줄러 도입 전까지 수동 벌크 부여.
            </p>
          </div>

          {/* 대상 직원 */}
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-[12px] font-medium text-mgray-700">
                대상 직원
              </span>
              <div className="flex items-center gap-1">
                {depts.map((d) => {
                  const on = dept === d;
                  return (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setDept(d)}
                      className={
                        on
                          ? "rounded-full border border-brand-500 bg-brand-500 px-2.5 py-0.5 text-[10px] font-medium text-white"
                          : "rounded-full border border-mgray-200 px-2.5 py-0.5 text-[10px] font-medium text-mgray-600 hover:bg-mgray-50"
                      }
                    >
                      {d}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="max-h-[180px] overflow-y-auto rounded-md border border-mgray-200">
              <table className="w-full table-fixed text-[13px]">
                <colgroup>
                  <col className="w-[12%]" />
                  <col className="w-[30%]" />
                  <col className="w-[33%]" />
                  <col className="w-[25%]" />
                </colgroup>
                <thead>
                  <tr className="border-b border-mgray-100 bg-mgray-50 text-left text-[11px] font-medium text-mgray-500">
                    <th className="px-3 py-2">
                      <input
                        type="checkbox"
                        aria-label="보이는 직원 전체 선택"
                        className="size-3.5 align-middle accent-brand-500"
                        checked={allVisibleChecked}
                        onChange={(e) => toggleAllVisible(e.target.checked)}
                      />
                    </th>
                    <th className="px-2 py-2 font-medium">부서</th>
                    <th className="px-2 py-2 font-medium">이름</th>
                    <th className="px-3 py-2 font-medium">직급</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-mgray-100">
                  {visible.map((e) => (
                    <tr key={e.id} className="hover:bg-mgray-50">
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          aria-label={`${e.name} 선택`}
                          className="size-4 align-middle accent-brand-500"
                          checked={selected.has(e.id)}
                          onChange={(ev) => toggleOne(e.id, ev.target.checked)}
                        />
                      </td>
                      <td className="px-2 py-2 text-mgray-600">
                        {e.department ?? "—"}
                      </td>
                      <td className="px-2 py-2 font-medium text-mgray-800">
                        {e.name}
                      </td>
                      <td className="px-3 py-2 text-mgray-600">
                        {e.position ?? "—"}
                      </td>
                    </tr>
                  ))}
                  {visible.length === 0 ? (
                    <tr>
                      <td
                        colSpan={4}
                        className="px-3 py-6 text-center text-[12px] text-mgray-400"
                      >
                        해당 부서 직원이 없습니다
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
            {/* 선택 태그 */}
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {selectedRows.length === 0 ? (
                <span className="text-[11px] text-mgray-400">
                  선택된 직원 없음
                </span>
              ) : (
                <>
                  <span className="text-[11px] text-mgray-500">
                    선택 {selectedRows.length}명
                  </span>
                  {selectedRows.map((e) => (
                    <span
                      key={e.id}
                      className="inline-flex items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-[11px] font-medium text-brand-500"
                    >
                      {e.name}
                      <button
                        type="button"
                        aria-label={`${e.name} 해제`}
                        onClick={() => toggleOne(e.id, false)}
                        className="ml-0.5 leading-none text-brand-300 hover:text-brand-500"
                      >
                        <X className="size-3" />
                      </button>
                    </span>
                  ))}
                </>
              )}
            </div>
          </div>

          {/* 일수 / 유효기간 */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label
                htmlFor="grant-amount"
                className="block text-[12px] font-medium text-mgray-700"
              >
                일수{" "}
                {grantType === "Off Day" ? (
                  <span className="font-normal text-mgray-400">
                    (비우면 0.5)
                  </span>
                ) : null}
              </label>
              <Input
                id="grant-amount"
                type="number"
                step="0.5"
                min="0"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label
                htmlFor="grant-expiry"
                className="block text-[12px] font-medium text-mgray-700"
              >
                유효기간{" "}
                <span className="font-normal text-mgray-400">
                  {grantType === "Off Day" ? "(비우면 그달 말일)" : "(만료일)"}
                </span>
              </label>
              <Input
                id="grant-expiry"
                type="date"
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
              />
            </div>
          </div>

          {/* 사유 */}
          <div className="space-y-1.5">
            <label
              htmlFor="grant-reason"
              className="block text-[12px] font-medium text-mgray-700"
            >
              사유{" "}
              <span className="font-normal text-mgray-400">(선택)</span>
            </label>
            <Input
              id="grant-reason"
              type="text"
              placeholder="예: Q2 프로젝트 런칭 보상"
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
          <Button onClick={onSubmit} disabled={submitting || selected.size === 0}>
            {submitting ? <Loader2 className="animate-spin" /> : <Gift />}
            부여
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
