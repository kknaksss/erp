"use client";

// 연차관리 /leave/admin (HR 전용, WP-003 Phase 4 + WP-004 Phase 3) — 통합 처리 대기 큐.
//  - 유형 탭(전체/신규/변경/취소) — 시안 leave-admin-hr.html 의 통합 큐. 카운트 = 전체 대기(필터 무관).
//  - 신규 큐: GET /leave/admin/requests (신청됨) → [승인](차감, warning=음수 경고) · [반려](사유 필수)
//  - 취소 큐: GET /leave/admin/cancel-requests (취소요청됨, PendingRequestOut 동형 → 행 컴포넌트 재사용)
//       · 승인: POST /leave/admin/requests/{id}/cancel-approve → 취소됨 + 원-lot 복원
//       · 반려: POST /leave/admin/requests/{id}/cancel-reject {reason} → 승인됨 복귀(사유 필수)
//     ⚠ 실제 배포 경로는 /admin/requests/{id}/cancel-(approve|reject) (태스크 계약표의 /admin/cancel-requests/... 아님 — 코드 SoT).
//  - 변경 큐: GET /leave/admin/change-requests (ChangeRequestOut) → "원건 → 재신청" 한 항목
//       · 승인: POST /leave/admin/change-requests/{change_group_id}/approve (원건 취소+복원 + 재신청 승인 한 번에)
//       · 반려: POST /leave/admin/change-requests/{change_group_id}/reject {reason} (원건 유지·재신청 폐기)
//  - 페이지 가드: 비-HR → 403 → forbidden degrade(nav 숨김과 이중). directory 패턴.
// 범위 밖(WP-005): 직원별 잔여현황·보상/포상 부여·연차수 조정·상세 패널.
import { useCallback, useEffect, useState } from "react";
import { ArrowRight, Check, Inbox, Loader2, ShieldAlert, X } from "lucide-react";

import { AppHeader } from "@/components/app-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type {
  AmPm,
  ApprovalResult,
  ChangeRequest,
  ChangeSide,
  LeaveCategory,
  LeaveRequest,
  LeaveUnit,
  PendingRequest,
} from "@/types";

interface Queues {
  news: PendingRequest[];
  cancels: PendingRequest[];
  changes: ChangeRequest[];
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; queues: Queues }
  | { kind: "forbidden" }
  | { kind: "error"; message: string };

type Tab = "전체" | "신규" | "변경" | "취소";
const TABS: { key: Tab; label: string }[] = [
  { key: "전체", label: "전체" },
  { key: "신규", label: "신규 신청" },
  { key: "변경", label: "변경" },
  { key: "취소", label: "취소 요청" },
];

type QueueItem =
  | { kind: "new"; key: string; row: PendingRequest }
  | { kind: "cancel"; key: string; row: PendingRequest }
  | { kind: "change"; key: string; row: ChangeRequest };

// "사용" 자연어 라벨 — 전일은 종류명, 반차/반반차는 "오전 반차" 식(P3 leave 페이지와 동일 규칙).
function usageLabel(r: {
  unit: LeaveUnit;
  am_pm: AmPm | null;
  category: LeaveCategory;
}): string {
  if (r.unit === "전일") return r.category;
  return r.am_pm ? `${r.am_pm} ${r.unit}` : r.unit;
}

export default function LeaveAdminPage() {
  const { authedFetch } = useAuth();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [tab, setTab] = useState<Tab>("전체");
  const [actingKey, setActingKey] = useState<string | null>(null);
  const [rejectingKey, setRejectingKey] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [toast, setToast] = useState<string | null>(null);

  // setState 는 await 이후(비동기 연속)라 effect 내 동기 setState 규칙에 안 걸림.
  // 페이지 가드 = directory 패턴: 비-HR 은 BE 가 403 → forbidden degrade(isHr 해석 타이밍 의존 회피).
  // 큐 3종을 병렬 조회 — 어느 하나라도 403 이면 비-HR(전체 forbidden).
  const fetchQueues = useCallback(async () => {
    try {
      const [news, cancels, changes] = await Promise.all([
        authedFetch<PendingRequest[]>("/leave/admin/requests"),
        authedFetch<PendingRequest[]>("/leave/admin/cancel-requests"),
        authedFetch<ChangeRequest[]>("/leave/admin/change-requests"),
      ]);
      setState({ kind: "ok", queues: { news, cancels, changes } });
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setState({ kind: "forbidden" });
      } else {
        setState({
          kind: "error",
          message:
            err instanceof ApiError
              ? err.message
              : "처리 대기 큐를 불러오지 못했습니다",
        });
      }
    }
  }, [authedFetch]);

  const reload = useCallback(() => {
    setState({ kind: "loading" });
    fetchQueues();
  }, [fetchQueues]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchQueues();
  }, [fetchQueues]);

  // 토스트 자동 소거
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  // ---- 액션 핸들러 (성공·실패 모두 재조회로 큐 정합) ------------------------

  async function onNewApprove(row: PendingRequest, key: string) {
    setActingKey(key);
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
      await fetchQueues();
    } catch (err) {
      setToast(actionErrorMessage(err, "승인"));
      await fetchQueues();
    } finally {
      setActingKey(null);
    }
  }

  async function onCancelApprove(row: PendingRequest, key: string) {
    setActingKey(key);
    try {
      await authedFetch<LeaveRequest>(
        `/leave/admin/requests/${row.id}/cancel-approve`,
        { method: "POST" },
      );
      setToast(`${row.employee_name} 취소를 승인했습니다 (잔여 복원)`);
      await fetchQueues();
    } catch (err) {
      setToast(actionErrorMessage(err, "취소 승인"));
      await fetchQueues();
    } finally {
      setActingKey(null);
    }
  }

  async function onChangeApprove(item: ChangeRequest, key: string) {
    setActingKey(key);
    try {
      await authedFetch<ChangeRequest>(
        `/leave/admin/change-requests/${item.change_group_id}/approve`,
        { method: "POST" },
      );
      setToast(`${item.employee_name} 변경을 승인했습니다`);
      await fetchQueues();
    } catch (err) {
      setToast(actionErrorMessage(err, "변경 승인"));
      await fetchQueues();
    } finally {
      setActingKey(null);
    }
  }

  // 반려 — 한 인라인 입력으로 신규/취소/변경 공용. confirm 시 item.kind 로 분기.
  function openReject(key: string) {
    setRejectingKey(key);
    setRejectReason("");
  }

  async function onRejectConfirm(item: QueueItem) {
    const reason = rejectReason.trim();
    if (!reason) {
      setToast("반려 사유를 입력해주세요");
      return;
    }
    setActingKey(item.key);
    try {
      if (item.kind === "new") {
        await authedFetch<LeaveRequest>(
          `/leave/admin/requests/${item.row.id}/reject`,
          { method: "POST", body: JSON.stringify({ reason }) },
        );
        setToast(`${item.row.employee_name} 신청을 반려했습니다`);
      } else if (item.kind === "cancel") {
        await authedFetch<LeaveRequest>(
          `/leave/admin/requests/${item.row.id}/cancel-reject`,
          { method: "POST", body: JSON.stringify({ reason }) },
        );
        setToast(`${item.row.employee_name} 취소 요청을 반려했습니다 (승인됨 복귀)`);
      } else {
        await authedFetch<ChangeRequest>(
          `/leave/admin/change-requests/${item.row.change_group_id}/reject`,
          { method: "POST", body: JSON.stringify({ reason }) },
        );
        setToast(`${item.row.employee_name} 변경을 반려했습니다 (원건 유지)`);
      }
      setRejectingKey(null);
      setRejectReason("");
      await fetchQueues();
    } catch (err) {
      setToast(actionErrorMessage(err, "반려"));
      await fetchQueues();
    } finally {
      setActingKey(null);
    }
  }

  // 통합 큐 아이템 — 전체 = 신규 + 변경 + 취소 합본(시안 통합 큐).
  const queues = state.kind === "ok" ? state.queues : null;
  const items: QueueItem[] = queues
    ? [
        ...queues.news.map(
          (row): QueueItem => ({ kind: "new", key: `new:${row.id}`, row }),
        ),
        ...queues.changes.map(
          (row): QueueItem => ({
            kind: "change",
            key: `change:${row.change_group_id}`,
            row,
          }),
        ),
        ...queues.cancels.map(
          (row): QueueItem => ({
            kind: "cancel",
            key: `cancel:${row.id}`,
            row,
          }),
        ),
      ]
    : [];
  const total = items.length;
  const visible = items.filter((it) => {
    if (tab === "전체") return true;
    if (tab === "신규") return it.kind === "new";
    if (tab === "변경") return it.kind === "change";
    return it.kind === "cancel";
  });

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
                    <span className="font-semibold text-mgray-700">{total}</span>
                    건
                  </span>
                  <Badge variant="neutral">신규 · 변경 · 취소</Badge>
                </div>
                <span className="text-[11px] text-mgray-500">
                  1 신청 = 하루치
                </span>
              </div>

              {/* 유형 탭 — 카운트는 전체 대기(필터 무관) */}
              <div className="flex items-center gap-1.5 border-b border-mgray-100 px-5 py-2.5">
                {TABS.map((t) => {
                  const on = tab === t.key;
                  return (
                    <button
                      key={t.key}
                      type="button"
                      onClick={() => setTab(t.key)}
                      className={
                        on
                          ? "rounded-full border border-brand-500 bg-brand-500 px-3 py-1 text-[11px] font-medium text-white"
                          : "rounded-full border border-mgray-200 px-3 py-1 text-[11px] font-medium text-mgray-600 hover:bg-mgray-50"
                      }
                    >
                      {t.label}
                    </button>
                  );
                })}
              </div>

              {visible.length === 0 ? (
                <div className="px-5 py-12 text-center text-[13px] text-mgray-400">
                  대기 중인 항목이 없습니다
                </div>
              ) : (
                <div className="divide-y divide-mgray-100">
                  {visible.map((item) => {
                    const busy = actingKey === item.key;
                    const rejecting = rejectingKey === item.key;
                    return (
                      <div key={item.key} className="px-5 py-3.5">
                        <div className="flex items-center justify-between gap-3">
                          <div className="min-w-0">
                            {item.kind === "change" ? (
                              <ChangeSummary row={item.row} />
                            ) : (
                              <PendingSummary
                                row={item.row}
                                kind={item.kind}
                              />
                            )}
                          </div>
                          <div className="flex shrink-0 items-center gap-1.5">
                            <Button
                              size="sm"
                              onClick={() => {
                                if (item.kind === "new")
                                  onNewApprove(item.row, item.key);
                                else if (item.kind === "cancel")
                                  onCancelApprove(item.row, item.key);
                                else onChangeApprove(item.row, item.key);
                              }}
                              disabled={busy}
                            >
                              {busy && !rejecting ? (
                                <Loader2 className="animate-spin" />
                              ) : (
                                <Check />
                              )}
                              승인
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => openReject(item.key)}
                              disabled={busy}
                              className="text-mred-500"
                            >
                              <X />
                              반려
                            </Button>
                          </div>
                        </div>

                        {rejecting ? (
                          <div className="mt-2 flex items-center gap-2">
                            <Input
                              autoFocus
                              value={rejectReason}
                              onChange={(e) => setRejectReason(e.target.value)}
                              placeholder={
                                item.kind === "cancel"
                                  ? "반려 사유 (필수) · 반려 시 승인됨 복귀"
                                  : item.kind === "change"
                                    ? "반려 사유 (필수) · 반려 시 원건 유지"
                                    : "반려 사유 (필수)"
                              }
                              className="flex-1"
                            />
                            <Button
                              size="sm"
                              variant="destructive"
                              onClick={() => onRejectConfirm(item)}
                              disabled={busy}
                            >
                              반려 확정
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => setRejectingKey(null)}
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

// 신규·취소 행 요약 — PendingRequestOut 동형이라 한 컴포넌트로 재사용(유형 배지만 분기).
function PendingSummary({
  row,
  kind,
}: {
  row: PendingRequest;
  kind: "new" | "cancel";
}) {
  return (
    <>
      <div className="flex items-center gap-1.5 text-sm font-semibold text-mgray-800">
        <Badge variant={kind === "new" ? "default" : "destructive"} className="px-1.5">
          {kind === "new" ? "신규" : "취소"}
        </Badge>
        <span className="truncate">{row.employee_name}</span>
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
        <p className="mt-1 text-[12px] text-mgray-500">사유: {row.note}</p>
      ) : null}
    </>
  );
}

// 변경 행 요약 — "원건 → 재신청" 한 항목(시안: 오전 반차 06-20 → 연차 06-22).
function ChangeSummary({ row }: { row: ChangeRequest }) {
  return (
    <>
      <div className="flex items-center gap-1.5 text-sm font-semibold text-mgray-800">
        <Badge variant="warning" className="px-1.5">
          변경
        </Badge>
        <span className="truncate">{row.employee_name}</span>
        <span className="truncate text-[11px] font-normal text-mgray-500">
          {row.employee_email}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-[12px]">
        <Badge variant="neutral" className="line-through">
          {usageLabel(row.original)}
        </Badge>
        <span className="text-mgray-400 line-through">
          {sideDetail(row.original)}
        </span>
        <ArrowRight className="size-3.5 text-mgray-400" />
        <Badge variant="neutral">{usageLabel(row.reapplication)}</Badge>
        <span className="text-mgray-600">{sideDetail(row.reapplication)}</span>
      </div>
    </>
  );
}

function sideDetail(s: ChangeSide): string {
  return `${s.use_date} · ${s.amount}일 · ${s.category}`;
}

function actionErrorMessage(err: unknown, verb: string): string {
  if (err instanceof ApiError) {
    if (err.status === 422) return "반려 사유를 입력해주세요";
    if (err.status === 409) return "이미 처리된 항목입니다";
    if (err.status === 404) return "항목을 찾을 수 없습니다 (이미 처리됨)";
    return err.message;
  }
  return `${verb}에 실패했습니다. 잠시 후 다시 시도해주세요`;
}
