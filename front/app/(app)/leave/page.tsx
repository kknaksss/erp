"use client";

// 연차조회 /leave (전 직원 기본 진입, WP-003 Phase 3 + WP-004 Phase 3) — GET /leave/me 본인 스코프.
//  - 종류별 잔여(전체 + 연차/Off Day/보상/포상, 교환 불가)
//  - 보상·포상 만료 안내(만료일 lot)
//  - 본인 신청/사용 이력(날짜·사용·단위·종류·상태) + 상태별 변경/취소 액션(본인 신청만, SPEC-005)
//     · 신청됨/승인됨 → 변경(폼 재사용 → /change) · 취소(신청됨=즉시 / 승인됨=취소요청)
//     · 취소요청됨 = 결과 대기 / 취소됨·반려됨 = 액션 없음
//  - 연차 신청 폼 → POST /leave/intake (모달, 성공 시 재조회)
// 시안: 21-html/leave-inquiry-my.html. 만료 임박 임계는 범위 밖(리포트 참조).
import { useCallback, useEffect, useState } from "react";
import { Clock, History, Loader2, Pencil, Plus, Smartphone, X } from "lucide-react";

import { AppHeader } from "@/components/app-header";
import { LeaveRequestDialog } from "@/components/leave-request-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type {
  LeaveCategory,
  LeaveRequest,
  LeaveSelf,
  RequestStatus,
} from "@/types";

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; data: LeaveSelf }
  | { kind: "error"; message: string };

// 잔여 종류별(전체 제외) — 시안 고정 순서.
const BALANCE_ORDER: { key: LeaveCategory; label: string; sub?: string }[] = [
  { key: "연차", label: "연차" },
  { key: "Off Day", label: "Off Day" },
  { key: "보상", label: "보상", sub: "보상연차" },
  { key: "포상", label: "포상", sub: "포상휴가" },
];

// 만료 안내 lot 라벨(보상→보상연차, 포상→포상휴가).
const LOT_LABEL: Partial<Record<LeaveCategory, string>> = {
  보상: "보상연차",
  포상: "포상휴가",
};
const LOT_SUB: Partial<Record<LeaveCategory, string>> = {
  보상: "행사·주말근무 보상",
  포상: "프로젝트 보상",
};

const STATUS_VARIANT: Record<
  RequestStatus,
  "default" | "success" | "destructive" | "neutral"
> = {
  신청됨: "default",
  승인됨: "success",
  반려됨: "destructive",
  취소요청됨: "neutral",
  취소됨: "neutral",
};

// 직원에게 보이는 상태 라벨 — 취소요청됨은 "취소 대기"(내부 상태 노출 회피, 시안 정합).
const STATUS_LABEL: Partial<Record<RequestStatus, string>> = {
  취소요청됨: "취소 대기",
};

// "사용" 컬럼 자연어 라벨 — 전일은 종류명, 반차/반반차는 "오전 반차" 식.
function usageLabel(r: LeaveRequest): string {
  if (r.unit === "전일") return r.category;
  return r.am_pm ? `${r.am_pm} ${r.unit}` : r.unit;
}

export default function LeavePage() {
  const { authedFetch } = useAuth();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [dialogOpen, setDialogOpen] = useState(false);
  // 변경 모드 = 원건 신청 id (set 되면 변경 폼 다이얼로그 오픈).
  const [changeTargetId, setChangeTargetId] = useState<string | null>(null);
  const [actingId, setActingId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // setState 는 await 이후(비동기 연속)라 effect 내 동기 setState 규칙에 안 걸림.
  const fetchMe = useCallback(async () => {
    try {
      const data = await authedFetch<LeaveSelf>("/leave/me");
      setState({ kind: "ok", data });
    } catch (err) {
      setState({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "연차 정보를 불러오지 못했습니다",
      });
    }
  }, [authedFetch]);

  const reload = useCallback(() => {
    setState({ kind: "loading" });
    fetchMe();
  }, [fetchMe]);

  // 마운트 시 fetch — 토큰이 클라(sessionStorage)에만 있어 서버 fetch 불가, effect-fetch 불가피.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchMe();
  }, [fetchMe]);

  // 토스트 자동 소거
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  // 취소 — 신청됨=자유 취소(즉시 취소됨) / 승인됨=취소 요청(취소요청됨, HR 승인 대기). 둘 다 확인 후.
  async function onCancel(r: LeaveRequest) {
    const isApproved = r.status === "승인됨";
    const ok = window.confirm(
      isApproved
        ? `${r.use_date} ${r.category} 신청의 취소를 요청할까요? HR 승인 후 잔여가 복원됩니다.`
        : `${r.use_date} ${r.category} 신청을 취소할까요?`,
    );
    if (!ok) return;
    setActingId(r.id);
    try {
      await authedFetch<LeaveRequest>(`/leave/requests/${r.id}/cancel`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      setToast(isApproved ? "취소를 요청했습니다 (HR 승인 대기)" : "신청을 취소했습니다");
      await fetchMe();
    } catch (err) {
      setToast(cancelErrorMessage(err));
      await fetchMe();
    } finally {
      setActingId(null);
    }
  }

  return (
    <>
      <AppHeader
        title="연차조회"
        actions={
          <>
            <span className="hidden items-center gap-1 rounded-full border border-mgray-200 bg-card px-2.5 py-1 text-[11px] font-medium text-mgray-500 sm:flex">
              <Smartphone className="size-3.5" />
              모바일 신규는 Slack 워크플로우
            </span>
            <Button onClick={() => setDialogOpen(true)}>
              <Plus />
              연차 신청
            </Button>
          </>
        }
      />

      <div className="w-full space-y-5 px-7 py-6">
        {state.kind === "loading" ? (
          <div className="flex items-center justify-center py-20 text-mgray-400">
            <Loader2 className="size-5 animate-spin" />
          </div>
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
          <>
            {/* 종류별 잔여 — 전체 + 연차/Off Day/보상/포상 (교환 불가) */}
            <Card>
              <CardContent className="flex items-center gap-5 px-5 py-3.5">
                <div className="flex items-baseline gap-1.5 border-r border-mgray-100 pr-5">
                  <span className="text-[12px] font-medium text-mgray-500">
                    전체
                  </span>
                  <span className="font-mono text-3xl font-semibold text-brand-500">
                    {state.data.total}
                  </span>
                  <span className="text-[12px] text-mgray-400">일</span>
                </div>
                <div className="flex flex-1 items-center justify-around text-center">
                  {BALANCE_ORDER.map((b) => (
                    <div key={b.key}>
                      <div className="text-[12px] text-mgray-500">
                        {b.label}
                        {b.sub ? (
                          <span className="text-mgray-400"> ({b.sub})</span>
                        ) : null}
                      </div>
                      <div className="font-mono text-xl font-semibold text-mgray-800">
                        {state.data.balances[b.key]}
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* 보상·포상 만료 안내 */}
            <Card>
              <CardContent className="px-5 py-4">
                <div className="mb-2.5 flex items-center gap-2">
                  <Clock className="size-[18px] text-mgray-500" />
                  <span className="text-sm font-semibold text-mgray-800">
                    보상·포상 만료 안내
                  </span>
                  <span className="text-[11px] text-mgray-500">
                    만료일이 지나면 소멸됩니다
                  </span>
                </div>
                {state.data.expiring.length === 0 ? (
                  <p className="rounded-md bg-mgray-50 px-3 py-2.5 text-[13px] text-mgray-400">
                    만료 예정인 보상·포상 연차가 없습니다.
                  </p>
                ) : (
                  <ul className="space-y-2 text-[13px]">
                    {state.data.expiring.map((lot, i) => (
                      <li
                        key={`${lot.category}-${lot.expiry_date}-${i}`}
                        className="flex items-center justify-between rounded-md bg-mgray-50 px-3 py-2"
                      >
                        <span className="text-mgray-700">
                          {LOT_LABEL[lot.category] ?? lot.category}
                          {LOT_SUB[lot.category] ? (
                            <span className="text-[11px] text-mgray-400">
                              {" "}
                              ({LOT_SUB[lot.category]})
                            </span>
                          ) : null}
                        </span>
                        <span className="flex items-center gap-2">
                          <span className="font-medium text-mgray-800">
                            {lot.remaining}일
                          </span>
                          <Badge variant="neutral">
                            {lot.expiry_date} 만료
                          </Badge>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>

            {/* 내 신청 · 사용 이력 */}
            <Card>
              <CardContent className="px-0 py-0">
                <div className="flex items-center justify-between border-b border-mgray-100 px-5 py-3.5">
                  <div className="flex items-center gap-2">
                    <History className="size-[18px] text-brand-500" />
                    <span className="text-sm font-semibold text-mgray-800">
                      내 신청 · 사용 이력
                    </span>
                  </div>
                  <span className="text-[11px] text-mgray-500">
                    신청형 = 연차 / 반차·반반차·Off Day (오전·오후)
                  </span>
                </div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>날짜</TableHead>
                      <TableHead>사용</TableHead>
                      <TableHead className="text-right">단위</TableHead>
                      <TableHead>종류</TableHead>
                      <TableHead>상태</TableHead>
                      <TableHead className="text-right">변경/취소</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {state.data.history.map((r) => {
                      const cancelled = r.status === "취소됨";
                      const busy = actingId === r.id;
                      return (
                        <TableRow
                          key={r.id}
                          className={cancelled ? "text-mgray-400" : undefined}
                        >
                          <TableCell
                            className={
                              cancelled
                                ? "font-mono line-through"
                                : "font-mono text-mgray-700"
                            }
                          >
                            {r.use_date}
                          </TableCell>
                          <TableCell>
                            <Badge variant="neutral">{usageLabel(r)}</Badge>
                          </TableCell>
                          <TableCell
                            className={
                              cancelled
                                ? "text-right font-mono line-through"
                                : "text-right font-mono text-mgray-700"
                            }
                          >
                            {r.amount}
                          </TableCell>
                          <TableCell className="text-[12px] text-mgray-500">
                            {r.category}
                          </TableCell>
                          <TableCell>
                            <Badge variant={STATUS_VARIANT[r.status]}>
                              {STATUS_LABEL[r.status] ?? r.status}
                            </Badge>
                            {r.status === "취소요청됨" ? (
                              <span className="ml-1 text-[11px] text-mgray-400">
                                · HR 승인
                              </span>
                            ) : null}
                          </TableCell>
                          <TableCell className="text-right">
                            {r.status === "신청됨" || r.status === "승인됨" ? (
                              <span className="inline-flex items-center justify-end gap-1.5">
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => setChangeTargetId(r.id)}
                                  disabled={busy}
                                >
                                  <Pencil />
                                  변경
                                </Button>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => onCancel(r)}
                                  disabled={busy}
                                  className="text-mred-500"
                                >
                                  {busy ? (
                                    <Loader2 className="animate-spin" />
                                  ) : (
                                    <X />
                                  )}
                                  취소
                                </Button>
                              </span>
                            ) : r.status === "취소요청됨" ? (
                              <span className="text-[11px] text-mgray-400">
                                결과 대기
                              </span>
                            ) : (
                              <span className="text-[11px] text-mgray-300">
                                —
                              </span>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                    {state.data.history.length === 0 ? (
                      <TableRow>
                        <TableCell
                          colSpan={6}
                          className="py-10 text-center text-mgray-400"
                        >
                          신청·사용 이력이 없습니다.
                        </TableCell>
                      </TableRow>
                    ) : null}
                  </TableBody>
                </Table>
                <p className="border-t border-mgray-100 px-5 py-2.5 text-[11px] leading-relaxed text-mgray-400">
                  변경하면 기존 신청을 취소하고 새 날짜로 다시 신청합니다. 대기
                  중 신청은 바로 취소되고, 승인된 신청은 HR이 취소를 승인하면
                  사용한 연차가 잔여로 돌아옵니다.
                </p>
              </CardContent>
            </Card>

            <p className="px-1 text-[11px] leading-relaxed text-mgray-400">
              본인 연차만 표시됩니다 — 타인 기록은 이 페이지에서 보이지 않습니다
              (관리 권한은 연차관리).
            </p>
          </>
        ) : null}
      </div>

      <LeaveRequestDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onSubmitted={reload}
      />

      {/* 변경 폼 — 신규 신청 폼 재사용(변경 모드). 원건 id 를 타깃으로 /change 제출. */}
      <LeaveRequestDialog
        open={changeTargetId !== null}
        onOpenChange={(next) => {
          if (!next) setChangeTargetId(null);
        }}
        onSubmitted={() => {
          setChangeTargetId(null);
          setToast("변경을 요청했습니다 (HR 승인 대기)");
          reload();
        }}
        changeTargetId={changeTargetId ?? undefined}
      />

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

function cancelErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return "이미 변경·취소 중이거나 처리된 신청입니다";
    if (err.status === 403) return "본인 신청만 취소할 수 있습니다";
    if (err.status === 404) return "신청을 찾을 수 없습니다 (이미 처리됨)";
    return err.message;
  }
  return "취소에 실패했습니다. 잠시 후 다시 시도해주세요";
}
