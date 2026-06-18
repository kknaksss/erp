"use client";

// 연차관리 /leave/admin (HR 전용, WP-003 Phase 4) — 신청 대기 큐 승인/반려.
//  - GET /leave/admin/requests (신청됨) → 행별 [승인][반려]
//  - 승인: POST .../approve → warning=true(차감 후 음수) 경고 토스트(승인은 성공) · 409 이미처리
//  - 반려: POST .../reject body {reason} — 사유 필수(인라인) · 422/409 처리
//  - 페이지 가드: 비-HR 직접 진입 → 403 → forbidden degrade(nav 숨김과 이중). directory 패턴.
// 시안: 21-html/leave-admin-hr.html 의 "처리 대기 큐" 카드. 변경/취소 탭(WP-004)·잔여현황·부여/조정 모달(WP-005)은 범위 밖.
import { useCallback, useEffect, useState } from "react";
import { Check, Inbox, Loader2, ShieldAlert, X } from "lucide-react";

import { AppHeader } from "@/components/app-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ApprovalResult, LeaveRequest, PendingRequest } from "@/types";

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; rows: PendingRequest[] }
  | { kind: "forbidden" }
  | { kind: "error"; message: string };

// "사용" 자연어 라벨 — 전일은 종류명, 반차/반반차는 "오전 반차" 식(P3 leave 페이지와 동일 규칙).
function usageLabel(r: PendingRequest): string {
  if (r.unit === "전일") return r.category;
  return r.am_pm ? `${r.am_pm} ${r.unit}` : r.unit;
}

export default function LeaveAdminPage() {
  const { authedFetch } = useAuth();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [actingId, setActingId] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [toast, setToast] = useState<string | null>(null);

  // setState 는 await 이후(비동기 연속)라 effect 내 동기 setState 규칙에 안 걸림.
  // 페이지 가드 = directory 패턴: 비-HR 은 BE 가 403 → forbidden degrade(isHr 해석 타이밍 의존 회피).
  const fetchRows = useCallback(async () => {
    try {
      const rows = await authedFetch<PendingRequest[]>("/leave/admin/requests");
      setState({ kind: "ok", rows });
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setState({ kind: "forbidden" });
      } else {
        setState({
          kind: "error",
          message:
            err instanceof ApiError
              ? err.message
              : "신청 큐를 불러오지 못했습니다",
        });
      }
    }
  }, [authedFetch]);

  const reload = useCallback(() => {
    setState({ kind: "loading" });
    fetchRows();
  }, [fetchRows]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchRows();
  }, [fetchRows]);

  // 토스트 자동 소거
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  async function onApprove(row: PendingRequest) {
    setActingId(row.id);
    try {
      const res = await authedFetch<ApprovalResult>(
        `/leave/admin/requests/${row.id}/approve`,
        { method: "POST" },
      );
      setToast(
        res.warning
          ? `${row.employee_name} 승인 완료 — 차감 후 ${row.category} 잔여 음수(${res.balance}일)`
          : `${row.employee_name} 신청을 승인했습니다`,
      );
      await fetchRows();
    } catch (err) {
      setToast(approveErrorMessage(err));
      await fetchRows(); // 409/404 면 큐에서 빠지도록 재조회
    } finally {
      setActingId(null);
    }
  }

  function openReject(row: PendingRequest) {
    setRejectingId(row.id);
    setRejectReason("");
  }

  async function onRejectConfirm(row: PendingRequest) {
    const reason = rejectReason.trim();
    if (!reason) {
      setToast("반려 사유를 입력해주세요");
      return;
    }
    setActingId(row.id);
    try {
      await authedFetch<LeaveRequest>(
        `/leave/admin/requests/${row.id}/reject`,
        { method: "POST", body: JSON.stringify({ reason }) },
      );
      setToast(`${row.employee_name} 신청을 반려했습니다`);
      setRejectingId(null);
      setRejectReason("");
      await fetchRows();
    } catch (err) {
      setToast(rejectErrorMessage(err));
      await fetchRows();
    } finally {
      setActingId(null);
    }
  }

  const rows = state.kind === "ok" ? state.rows : [];

  return (
    <>
      <AppHeader title="연차관리" />

      <div className="w-full px-7 py-6">
        {state.kind === "loading" ? (
          <div className="flex items-center justify-center py-20 text-mgray-400">
            <Loader2 className="size-5 animate-spin" />
          </div>
        ) : null}

        {state.kind === "forbidden" ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-2 py-16 text-center">
              <ShieldAlert className="size-7 text-mgray-400" />
              <div className="text-sm font-medium text-mgray-700">HR 전용</div>
              <p className="max-w-[360px] text-[13px] text-mgray-500">
                연차관리는 인사(HR) 부서 직원만 접근할 수 있습니다.
              </p>
            </CardContent>
          </Card>
        ) : null}

        {state.kind === "error" ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
              <p className="text-[13px] text-mred-500">{state.message}</p>
              <Button variant="outline" size="sm" onClick={reload}>
                다시 시도
              </Button>
            </CardContent>
          </Card>
        ) : null}

        {state.kind === "ok" ? (
          <Card>
            <CardContent className="px-0 py-0">
              <div className="flex items-center justify-between border-b border-mgray-100 px-5 py-3.5">
                <div className="flex items-center gap-2">
                  <Inbox className="size-[18px] text-brand-500" />
                  <span className="text-sm font-semibold text-mgray-800">
                    처리 대기 큐
                  </span>
                  <span className="text-[12px] text-mgray-500">
                    대기{" "}
                    <span className="font-semibold text-mgray-700">
                      {rows.length}
                    </span>
                    건
                  </span>
                  <Badge variant="neutral">Slack + ERP intake</Badge>
                </div>
                <span className="text-[11px] text-mgray-500">
                  1 신청 = 하루치
                </span>
              </div>

              {rows.length === 0 ? (
                <div className="px-5 py-12 text-center text-[13px] text-mgray-400">
                  대기 중인 항목이 없습니다
                </div>
              ) : (
                <div className="divide-y divide-mgray-100">
                  {rows.map((row) => {
                    const busy = actingId === row.id;
                    return (
                      <div key={row.id} className="px-5 py-3.5">
                        <div className="flex items-center justify-between gap-3">
                          <div className="min-w-0">
                            <div className="flex items-center gap-1.5 text-sm font-semibold text-mgray-800">
                              <Badge variant="default" className="px-1.5">
                                신규
                              </Badge>
                              <span className="truncate">
                                {row.employee_name}
                              </span>
                              <span className="truncate text-[11px] font-normal text-mgray-500">
                                {row.employee_email}
                              </span>
                            </div>
                            <div className="mt-1 flex flex-wrap items-center gap-2">
                              <Badge variant="neutral">{usageLabel(row)}</Badge>
                              <span className="text-[12px] text-mgray-600">
                                {row.use_date} · {row.amount}일 · {row.category}
                              </span>
                            </div>
                            {row.note ? (
                              <p className="mt-1 text-[12px] text-mgray-500">
                                사유: {row.note}
                              </p>
                            ) : null}
                          </div>
                          <div className="flex shrink-0 items-center gap-1.5">
                            <Button
                              size="sm"
                              onClick={() => onApprove(row)}
                              disabled={busy}
                            >
                              {busy && rejectingId !== row.id ? (
                                <Loader2 className="animate-spin" />
                              ) : (
                                <Check />
                              )}
                              승인
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => openReject(row)}
                              disabled={busy}
                              className="text-mred-500"
                            >
                              <X />
                              반려
                            </Button>
                          </div>
                        </div>

                        {rejectingId === row.id ? (
                          <div className="mt-2 flex items-center gap-2">
                            <Input
                              autoFocus
                              value={rejectReason}
                              onChange={(e) => setRejectReason(e.target.value)}
                              placeholder="반려 사유 (필수)"
                              className="flex-1"
                            />
                            <Button
                              size="sm"
                              variant="destructive"
                              onClick={() => onRejectConfirm(row)}
                              disabled={busy}
                            >
                              반려 확정
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => setRejectingId(null)}
                              disabled={busy}
                            >
                              닫기
                            </Button>
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        ) : null}
      </div>

      {toast ? (
        <div
          role="status"
          className="fixed bottom-6 right-6 z-50 max-w-[360px] rounded-md border border-mgray-200 bg-card px-4 py-3 text-[13px] text-mgray-800 shadow-lg"
        >
          {toast}
        </div>
      ) : null}
    </>
  );
}

function approveErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return "이미 처리된 신청입니다";
    if (err.status === 404) return "신청을 찾을 수 없습니다 (이미 처리됨)";
    return err.message;
  }
  return "승인에 실패했습니다. 잠시 후 다시 시도해주세요";
}

function rejectErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 422) return "반려 사유를 입력해주세요";
    if (err.status === 409) return "이미 처리된 신청입니다";
    if (err.status === 404) return "신청을 찾을 수 없습니다 (이미 처리됨)";
    return err.message;
  }
  return "반려에 실패했습니다. 잠시 후 다시 시도해주세요";
}
