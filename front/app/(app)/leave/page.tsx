"use client";

// 연차조회 /leave (전 직원 기본 진입, WP-003 Phase 3) — GET /leave/me 본인 스코프.
//  - 종류별 잔여(전체 + 연차/Off Day/보상/포상, 교환 불가)
//  - 보상·포상 만료 안내(만료일 lot)
//  - 본인 신청/사용 이력(날짜·사용·단위·종류·상태)
//  - 연차 신청 폼 → POST /leave/intake (모달, 성공 시 재조회)
// 시안: 21-html/leave-inquiry-my.html. 변경/취소(SPEC-005)·만료 임박 임계는 범위 밖(리포트 참조).
import { useCallback, useEffect, useState } from "react";
import { Clock, History, Loader2, Plus, Smartphone } from "lucide-react";

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
  "default" | "success" | "destructive"
> = {
  신청됨: "default",
  승인됨: "success",
  반려됨: "destructive",
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
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {state.data.history.map((r) => (
                      <TableRow key={r.id}>
                        <TableCell className="font-mono text-mgray-700">
                          {r.use_date}
                        </TableCell>
                        <TableCell>
                          <Badge variant="neutral">{usageLabel(r)}</Badge>
                        </TableCell>
                        <TableCell className="text-right font-mono text-mgray-700">
                          {r.amount}
                        </TableCell>
                        <TableCell className="text-[12px] text-mgray-500">
                          {r.category}
                        </TableCell>
                        <TableCell>
                          <Badge variant={STATUS_VARIANT[r.status]}>
                            {r.status}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                    {state.data.history.length === 0 ? (
                      <TableRow>
                        <TableCell
                          colSpan={5}
                          className="py-10 text-center text-mgray-400"
                        >
                          신청·사용 이력이 없습니다.
                        </TableCell>
                      </TableRow>
                    ) : null}
                  </TableBody>
                </Table>
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
    </>
  );
}
