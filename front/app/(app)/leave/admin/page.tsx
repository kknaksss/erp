"use client";

// 연차관리 /leave/admin (HR 전용) — 통합 처리 대기 큐 + HR 운영 3종(WP-005 P3).
//  [큐 — WP-003 P4 + WP-004 P3]
//  - 유형 탭(전체/신규/변경/취소) — 시안 leave-admin-hr.html 의 통합 큐. 카운트 = 전체 대기(필터 무관).
//  - 신규 큐: GET /leave/admin/requests (신청됨) → [승인](차감, warning=음수 경고) · [반려](사유 필수)
//  - 취소 큐: GET /leave/admin/cancel-requests (취소요청됨, PendingRequestOut 동형 → 행 컴포넌트 재사용)
//       · 승인: POST /leave/admin/requests/{id}/cancel-approve → 취소됨 + 원-lot 복원
//       · 반려: POST /leave/admin/requests/{id}/cancel-reject {reason} → 승인됨 복귀(사유 필수)
//  - 변경 큐: GET /leave/admin/change-requests (ChangeRequestOut) → "원건 → 재신청" 한 항목
//       · 승인/반려: POST /leave/admin/change-requests/{change_group_id}/(approve|reject)
//  [HR 운영 — WP-005 P3]
//  - 직원별 잔여 현황(좌): GET /leave/admin/employees 명부 + 각 직원 GET /leave/admin/employees/{id} 상세를
//    fan-out 로 모아 종류별 잔여 4+전체 표시(부서 필터·행 클릭 선택). 음수 잔여 = 빨강.
//  - 상세 연차 현황(우): 선택 직원의 잔여 카드 4+전체 + 이력(ledger 시계열, 음수 경고).
//  - 연차수 조정: 상세 패널 → 모달(종류별 ± 다건) → POST /leave/admin/adjustments.
//  - 벌크 부여: 헤더 → 모달(다중 직원·부서 필터·종류·일수·만료·default) → POST /leave/admin/grants.
//  ⚠ 권한 축: 큐/부여/조정/상세 + 명부 모두 require_hr(/leave/admin/employees = department=="hr" 면 role 무관 200).
//    member-role HR 도 명부 200 — degrade 해소(WP-005 권한 갭 보강). forbidden 분기는 진짜 비-HR 방어용으로 유지.
//  - 페이지 가드: 큐 403 = 비-HR → forbidden degrade(nav 숨김과 이중). directory 패턴.
import { useCallback, useEffect, useState } from "react";
import {
  ArrowRight,
  Check,
  Gift,
  Inbox,
  Loader2,
  ShieldAlert,
  SlidersHorizontal,
  UserSearch,
  Wallet,
  X,
} from "lucide-react";

import { AppHeader } from "@/components/app-header";
import { LeaveAdjustDialog } from "@/components/leave-adjust-dialog";
import { LeaveGrantDialog } from "@/components/leave-grant-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDays } from "@/lib/utils";
import type {
  AmPm,
  ApprovalResult,
  ChangeRequest,
  ChangeSide,
  Employee,
  EmployeeLeaveDetail,
  LeaveCategory,
  LeaveRequest,
  LeaveUnit,
  LedgerEntry,
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

// 직원별 잔여 현황 — /leave/admin/employees = require_hr(member-HR 200). forbidden 은 진짜 비-HR 방어.
type RosterState =
  | { kind: "loading" }
  | {
      kind: "ok";
      rows: Employee[];
      details: Record<string, EmployeeLeaveDetail>;
    }
  | { kind: "forbidden" }
  | { kind: "error"; message: string };

type Tab = "전체" | "신규" | "변경" | "취소";
const TABS: { key: Tab; label: string }[] = [
  { key: "전체", label: "전체" },
  { key: "신규", label: "신규 신청" },
  { key: "변경", label: "변경" },
  { key: "취소", label: "취소 요청" },
];

// 잔여 표시 4 종류(전체 = 합산 표시값). 시안 표 컬럼 순서.
const BAL_CATEGORIES: LeaveCategory[] = ["연차", "Off Day", "보상", "포상"];

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

  // HR 운영(좌 명부/우 상세 + 모달) 상태
  const [roster, setRoster] = useState<RosterState>({ kind: "loading" });
  const [rosterDept, setRosterDept] = useState<string>("전체");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [grantOpen, setGrantOpen] = useState(false);
  const [adjustOpen, setAdjustOpen] = useState(false);

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

  // 명부 + 각 직원 상세 fan-out(소규모 조직 — Promise.allSettled 로 일부 실패 허용).
  // /leave/admin/employees = require_hr → member-HR 200(department=="hr"). forbidden = 진짜 비-HR 방어.
  const loadRoster = useCallback(async () => {
    try {
      const rows = await authedFetch<Employee[]>("/leave/admin/employees");
      // BE 는 en_US collation 정렬 → 한글 가나다순 재정렬(directory 와 동일).
      const sorted = [...rows].sort((a, b) => a.name.localeCompare(b.name, "ko"));
      const results = await Promise.allSettled(
        sorted.map((e) =>
          authedFetch<EmployeeLeaveDetail>(`/leave/admin/employees/${e.id}`),
        ),
      );
      const details: Record<string, EmployeeLeaveDetail> = {};
      results.forEach((r, i) => {
        if (r.status === "fulfilled") details[sorted[i].id] = r.value;
      });
      setRoster({ kind: "ok", rows: sorted, details });
      setSelectedId((prev) => prev ?? sorted[0]?.id ?? null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setRoster({ kind: "forbidden" });
      } else {
        setRoster({
          kind: "error",
          message:
            err instanceof ApiError
              ? err.message
              : "직원 명부를 불러오지 못했습니다",
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
    loadRoster();
  }, [fetchQueues, loadRoster]);

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
      loadRoster();
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
      loadRoster();
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
      loadRoster();
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

  // 부여·조정 성공 → toast + 명부/상세 재조회(잔여 갱신).
  function onGranted(summary: string) {
    setToast(summary);
    loadRoster();
  }
  function onAdjusted(summary: string) {
    setToast(summary);
    loadRoster();
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

  // 명부 파생값(ok 일 때만)
  const rosterRows = roster.kind === "ok" ? roster.rows : [];
  const rosterDetails = roster.kind === "ok" ? roster.details : {};
  const rosterDepts = [
    "전체",
    ...Array.from(
      new Set(rosterRows.map((e) => e.department).filter((d): d is string => !!d)),
    ).sort((a, b) => a.localeCompare(b, "ko")),
  ];
  const visibleRoster = rosterRows.filter(
    (e) => rosterDept === "전체" || e.department === rosterDept,
  );
  const selectedEmployee = rosterRows.find((e) => e.id === selectedId) ?? null;
  const selectedDetail = selectedId ? (rosterDetails[selectedId] ?? null) : null;

  return (
    <>
      <AppHeader
        title="연차관리"
        actions={
          state.kind === "ok" ? (
            <Button
              onClick={() => setGrantOpen(true)}
              disabled={roster.kind !== "ok"}
              title={
                roster.kind !== "ok"
                  ? "직원 명부 조회 권한이 필요합니다"
                  : undefined
              }
            >
              <Gift />
              보상연차/포상 부여
            </Button>
          ) : null
        }
      />

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
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_400px]">
            {/* ── 좌: 처리 대기 큐 + 직원별 잔여 현황 ── */}
            <div className="min-w-0 space-y-5">
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
                          {total}
                        </span>
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
                                  onChange={(e) =>
                                    setRejectReason(e.target.value)
                                  }
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

              {/* 직원별 잔여 현황 — 명부 + fan-out 잔여(행 클릭 = 상세 선택) */}
              <Card>
                <CardContent className="px-0 py-0">
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-mgray-100 px-5 py-3.5">
                    <div className="flex items-center gap-2">
                      <Wallet className="size-[18px] text-brand-500" />
                      <span className="text-sm font-semibold text-mgray-800">
                        직원별 잔여 현황
                      </span>
                    </div>
                    {roster.kind === "ok" ? (
                      <div className="flex items-center gap-1.5">
                        {rosterDepts.map((d) => {
                          const on = rosterDept === d;
                          return (
                            <button
                              key={d}
                              type="button"
                              onClick={() => setRosterDept(d)}
                              className={
                                on
                                  ? "rounded-full border border-brand-500 bg-brand-500 px-3 py-1 text-[11px] font-medium text-white"
                                  : "rounded-full border border-mgray-200 px-3 py-1 text-[11px] font-medium text-mgray-600 hover:bg-mgray-50"
                              }
                            >
                              {d}
                            </button>
                          );
                        })}
                      </div>
                    ) : null}
                  </div>

                  {roster.kind === "loading" ? (
                    <div className="flex items-center justify-center py-12 text-mgray-400">
                      <Loader2 className="size-5 animate-spin" />
                    </div>
                  ) : null}

                  {roster.kind === "forbidden" ? (
                    <div className="flex flex-col items-center gap-2 px-5 py-12 text-center">
                      <ShieldAlert className="size-6 text-mgray-400" />
                      <p className="max-w-[420px] text-[12px] text-mgray-500">
                        직원 명부 조회 권한(관리자)이 없어 잔여 현황·부여·조정을
                        사용할 수 없습니다. 처리 대기 큐는 정상 이용 가능합니다.
                      </p>
                    </div>
                  ) : null}

                  {roster.kind === "error" ? (
                    <div className="flex flex-col items-center gap-3 px-5 py-12 text-center">
                      <p className="text-[12px] text-mred-500">
                        {roster.message}
                      </p>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          setRoster({ kind: "loading" });
                          loadRoster();
                        }}
                      >
                        다시 시도
                      </Button>
                    </div>
                  ) : null}

                  {roster.kind === "ok" ? (
                    <table className="w-full table-fixed text-sm">
                      <colgroup>
                        <col className="w-[16%]" />
                        <col className="w-[20%]" />
                        <col className="w-[14%]" />
                        <col className="w-[12%]" />
                        <col className="w-[14%]" />
                        <col className="w-[12%]" />
                        <col className="w-[12%]" />
                      </colgroup>
                      <thead>
                        <tr className="border-b border-mgray-100 text-left text-[11px] font-medium uppercase tracking-wide text-mgray-500">
                          <th className="px-5 py-2.5 font-medium">부서</th>
                          <th className="px-3 py-2.5 font-medium">이름</th>
                          <th className="px-3 py-2.5 text-right font-medium">
                            전체
                          </th>
                          <th className="px-3 py-2.5 text-right font-medium">
                            연차
                          </th>
                          <th className="px-3 py-2.5 text-right font-medium">
                            Off Day
                          </th>
                          <th className="px-3 py-2.5 text-right font-medium">
                            보상
                          </th>
                          <th className="px-5 py-2.5 text-right font-medium">
                            포상
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-mgray-100">
                        {visibleRoster.map((e) => {
                          const d = rosterDetails[e.id];
                          const on = e.id === selectedId;
                          return (
                            <tr
                              key={e.id}
                              onClick={() => setSelectedId(e.id)}
                              className={
                                on
                                  ? "cursor-pointer bg-brand-50"
                                  : "cursor-pointer hover:bg-mgray-50"
                              }
                            >
                              <td className="px-5 py-3 text-mgray-600">
                                {e.department ?? "—"}
                              </td>
                              <td className="px-3 py-3 font-medium text-mgray-800">
                                {e.name}
                              </td>
                              <BalanceCell value={d?.total} bold />
                              {BAL_CATEGORIES.map((cat) => (
                                <BalanceCell
                                  key={cat}
                                  value={d?.balances[cat]}
                                  last={cat === "포상"}
                                />
                              ))}
                            </tr>
                          );
                        })}
                        {visibleRoster.length === 0 ? (
                          <tr>
                            <td
                              colSpan={7}
                              className="py-10 text-center text-mgray-400"
                            >
                              해당 부서 직원이 없습니다
                            </td>
                          </tr>
                        ) : null}
                      </tbody>
                    </table>
                  ) : null}
                </CardContent>
              </Card>
            </div>

            {/* ── 우: 상세 연차 현황 ── */}
            <aside className="min-w-0 space-y-4">
              <div className="flex items-center gap-2">
                <UserSearch className="size-[18px] text-mgray-600" />
                <span className="text-sm font-semibold text-mgray-800">
                  상세 연차 현황
                </span>
              </div>
              <EmployeeDetailPanel
                rosterKind={roster.kind}
                employee={selectedEmployee}
                detail={selectedDetail}
                onAdjust={() => setAdjustOpen(true)}
              />
            </aside>
          </div>
        ) : null}
      </div>

      {/* 모달 — 명부 ok 일 때만 (대상 선택이 명부 의존) */}
      {roster.kind === "ok" ? (
        <>
          <LeaveGrantDialog
            open={grantOpen}
            onOpenChange={setGrantOpen}
            roster={roster.rows}
            onGranted={onGranted}
          />
          <LeaveAdjustDialog
            open={adjustOpen}
            onOpenChange={setAdjustOpen}
            detail={selectedDetail}
            onAdjusted={onAdjusted}
          />
        </>
      ) : null}

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

// 잔여 표 셀 — Decimal 문자열 → 소수 1자리, 음수 빨강, 미로드/0 회색. 표시 전용(산술 X).
function BalanceCell({
  value,
  bold,
  last,
}: {
  value?: string;
  bold?: boolean;
  last?: boolean;
}) {
  const n = value != null ? parseFloat(value) : null;
  const neg = n != null && n < 0;
  const zero = n != null && n === 0;
  const color = neg
    ? "text-mred-500"
    : zero
      ? "text-mgray-400"
      : "text-mgray-700";
  return (
    <td
      className={`${last ? "px-5" : "px-3"} py-3 text-right ${
        bold ? "font-semibold" : ""
      } ${bold && !neg ? "text-mgray-800" : color}`}
    >
      {n != null ? formatDays(n) : "—"}
    </td>
  );
}

// 상세 연차 현황 패널 — 선택 직원 잔여 카드 4+전체 + 이력(ledger). 명부 권한 없으면 degrade.
function EmployeeDetailPanel({
  rosterKind,
  employee,
  detail,
  onAdjust,
}: {
  rosterKind: RosterState["kind"];
  employee: Employee | null;
  detail: EmployeeLeaveDetail | null;
  onAdjust: () => void;
}) {
  if (rosterKind === "forbidden") {
    return (
      <Card>
        <CardContent className="flex flex-col items-center gap-2 py-12 text-center">
          <ShieldAlert className="size-6 text-mgray-400" />
          <p className="max-w-[300px] text-[12px] text-mgray-500">
            직원 명부 권한이 없어 상세 현황을 볼 수 없습니다.
          </p>
        </CardContent>
      </Card>
    );
  }
  if (rosterKind !== "ok" || !employee) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12 text-[12px] text-mgray-400">
          {rosterKind === "loading"
            ? "불러오는 중…"
            : "직원을 선택하면 상세 현황이 표시됩니다"}
        </CardContent>
      </Card>
    );
  }
  if (!detail) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12 text-[12px] text-mgray-400">
          {employee.name} 상세를 불러오지 못했습니다
        </CardContent>
      </Card>
    );
  }

  const total = parseFloat(detail.total);
  const totalNeg = total < 0;
  return (
    <>
      <Card className={totalNeg ? "border-mred-100" : "border-brand-100"}>
        <CardContent className="p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="flex size-9 items-center justify-center rounded-full bg-mgray-100 text-sm font-medium text-mgray-700">
                {employee.name.charAt(0)}
              </div>
              <div className="leading-tight">
                <div className="text-sm font-semibold text-mgray-800">
                  {employee.name}
                </div>
                <div className="text-[11px] text-mgray-500">
                  {employee.department ?? "—"} · {employee.position ?? "—"} ·{" "}
                  {employee.role ?? "—"}
                </div>
              </div>
            </div>
            <Button variant="outline" size="sm" onClick={onAdjust}>
              <SlidersHorizontal />
              연차수 조정
            </Button>
          </div>

          <div
            className={`mt-3 rounded-md border px-3 py-2.5 text-center ${
              totalNeg
                ? "border-mred-100 bg-mred-50"
                : "border-brand-100 bg-brand-50"
            }`}
          >
            <div className="text-[10px] text-mgray-500">전체 잔여</div>
            <div
              className={`text-2xl font-semibold ${
                totalNeg ? "text-mred-500" : "text-brand-500"
              }`}
            >
              {formatDays(total)}
            </div>
          </div>

          <div className="mt-2 grid grid-cols-4 gap-1.5">
            {BAL_CATEGORIES.map((cat) => {
              const v = parseFloat(detail.balances[cat] ?? "0");
              const neg = v < 0;
              return (
                <div
                  key={cat}
                  className="rounded-md border border-mgray-200 bg-card px-2.5 py-2 text-center"
                >
                  <div className="text-[10px] text-mgray-500">{cat}</div>
                  <div
                    className={`text-[15px] font-semibold ${
                      neg ? "text-mred-500" : "text-mgray-800"
                    }`}
                  >
                    {formatDays(v)}
                  </div>
                </div>
              );
            })}
          </div>
          {totalNeg ? (
            <p className="mt-2 text-[11px] text-mred-500">
              ⚠ 전체 잔여가 음수입니다 (차감/조정 확인 필요).
            </p>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="px-0 py-0">
          <div className="border-b border-mgray-100 px-4 py-3 text-[12px] font-semibold text-mgray-700">
            사용 이력 · 연차관리기록
          </div>
          {detail.ledger.length === 0 ? (
            <div className="px-4 py-8 text-center text-[12px] text-mgray-400">
              기록이 없습니다
            </div>
          ) : (
            <ul className="divide-y divide-mgray-100 text-[12px]">
              {/* ledger 는 occurred_at ASC → 최신이 위로 오게 역순 표시 */}
              {[...detail.ledger].reverse().map((entry, i) => {
                const { label, variant } = ledgerStatus(entry);
                return (
                  <li
                    key={`${entry.ref_id}:${i}`}
                    className="flex items-center justify-between gap-2 px-4 py-2.5"
                  >
                    <div className="min-w-0">
                      <span className="font-medium text-mgray-800">
                        {entry.entry_type} · {entry.category}
                      </span>
                      <span className="text-mgray-500">
                        {" "}
                        · {entry.occurred_at.slice(0, 10)} ·{" "}
                        {signedAmount(entry)}
                      </span>
                    </div>
                    <Badge variant={variant}>{label}</Badge>
                  </li>
                );
              })}
            </ul>
          )}
          <p className="px-4 py-2.5 text-[11px] text-mgray-400">
            승인·반려된 신청은 큐가 아니라 이 이력에 쌓입니다.
          </p>
        </CardContent>
      </Card>
    </>
  );
}

// ledger 1건의 표시 상태 배지 — entry_type 기준(신청은 detail=상태로 세분).
function ledgerStatus(e: LedgerEntry): {
  label: string;
  variant: "default" | "neutral" | "success" | "warning" | "destructive";
} {
  switch (e.entry_type) {
    case "신청": {
      const s = e.detail ?? "신청";
      if (s === "반려됨") return { label: s, variant: "destructive" };
      if (s === "승인됨") return { label: s, variant: "success" };
      if (s === "취소됨") return { label: s, variant: "neutral" };
      return { label: s, variant: "warning" }; // 신청됨/취소요청됨 = 대기
    }
    case "사용":
      return { label: "사용", variant: "neutral" };
    case "발생":
      return { label: "발생", variant: "neutral" };
    case "HR부여":
      return { label: "HR 부여", variant: "default" };
    case "이월":
      return { label: "이월", variant: "default" };
    case "조정":
      return { label: "HR 조정", variant: "default" };
    default:
      return { label: e.entry_type, variant: "neutral" };
  }
}

// ledger amount 표시 — 부호 그대로(음수 유지), 가산형(발생/부여/이월·양수 조정)은 + 접두.
function signedAmount(e: LedgerEntry): string {
  const n = parseFloat(e.amount);
  if (Number.isNaN(n)) return `${e.amount}일`;
  if (n < 0) return `${formatDays(n)}일`; // formatDays 가 음수 부호 유지
  const credit =
    ["발생", "HR부여", "이월"].includes(e.entry_type) ||
    (e.entry_type === "조정" && n > 0);
  return `${credit ? "+" : ""}${formatDays(n)}일`;
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
