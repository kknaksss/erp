"use client";

// 직원 디렉토리 — GET /admin/employees 목록 + admin "동기화"(POST /admin/employees/sync).
// ⚠ BE drift: GET /admin/employees 가 require_admin → member 는 403(SPEC-002 §151 은 "인증됨").
//   member 는 "관리자 전용" 안내로 degrade(리포트 이슈/블로커 참조).
import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, ShieldAlert } from "lucide-react";

import { AppHeader } from "@/components/app-header";
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
import type { Employee, SyncResult } from "@/types";

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; rows: Employee[] }
  | { kind: "forbidden" }
  | { kind: "error"; message: string };

export default function DirectoryPage() {
  const { authedFetch, isAdmin } = useAuth();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [syncing, setSyncing] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  // setState 는 모두 await 이후(비동기 연속)라 effect 내 동기 setState 규칙에 걸리지 않음.
  const fetchRows = useCallback(async () => {
    try {
      const rows = await authedFetch<Employee[]>("/admin/employees");
      // BE 는 ORDER BY name(en_US collation)이라 한글 가나다순이 아님 → FE 에서 한글 사전순 재정렬.
      const sorted = [...rows].sort((a, b) =>
        a.name.localeCompare(b.name, "ko"),
      );
      setState({ kind: "ok", rows: sorted });
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setState({ kind: "forbidden" });
      } else {
        setState({
          kind: "error",
          message:
            err instanceof ApiError
              ? err.message
              : "직원 목록을 불러오지 못했습니다",
        });
      }
    }
  }, [authedFetch]);

  // 버튼(이벤트 핸들러)용 — 로딩 표시 후 재조회. 핸들러 내 setState 는 허용.
  const reload = useCallback(() => {
    setState({ kind: "loading" });
    fetchRows();
  }, [fetchRows]);

  // 마운트 시 직원 목록 fetch — 토큰이 클라(sessionStorage)에만 있어 서버 fetch 불가,
  // effect-fetch 가 불가피(초기 state=loading). 캐스케이드 1회 허용.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchRows();
  }, [fetchRows]);

  // 토스트 자동 소거
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(t);
  }, [toast]);

  async function onSync() {
    setSyncing(true);
    try {
      const res = await authedFetch<SyncResult>("/admin/employees/sync", {
        method: "POST",
      });
      setToast(`${res.updated}명 갱신 · ${res.new}명 신규`);
      await fetchRows();
    } catch (err) {
      setToast(
        err instanceof ApiError
          ? "동기화에 실패했습니다. 잠시 후 다시 시도해주세요"
          : "동기화에 실패했습니다",
      );
    } finally {
      setSyncing(false);
    }
  }

  return (
    <>
      <AppHeader
        title="직원 디렉토리"
        description="ERP 직원 명부 — mediness roster 미러"
        actions={
          isAdmin && state.kind === "ok" ? (
            <Button onClick={onSync} disabled={syncing}>
              {syncing ? (
                <Loader2 className="animate-spin" />
              ) : (
                <RefreshCw />
              )}
              동기화
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
              <div className="text-sm font-medium text-mgray-700">
                관리자 전용
              </div>
              <p className="max-w-[360px] text-[13px] text-mgray-500">
                직원 목록 조회 권한이 없습니다. 관리자(HR)에게 문의하세요.
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
            <CardContent className="pt-6">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>이름</TableHead>
                    <TableHead>이메일</TableHead>
                    <TableHead>권한</TableHead>
                    <TableHead>상태</TableHead>
                    <TableHead>직급</TableHead>
                    <TableHead>부서</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {state.rows.map((e) => (
                    <TableRow key={e.id}>
                      <TableCell className="font-medium text-mgray-800">
                        {e.name}
                      </TableCell>
                      <TableCell className="font-mono text-[13px]">
                        {e.email}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={e.role === "admin" ? "default" : "neutral"}
                        >
                          {e.role ?? "—"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={e.active ? "success" : "neutral"}>
                          {e.active ? "활성" : "비활성"}
                        </Badge>
                      </TableCell>
                      <TableCell>{e.position ?? "—"}</TableCell>
                      <TableCell>{e.department ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                  {state.rows.length === 0 ? (
                    <TableRow>
                      <TableCell
                        colSpan={6}
                        className="py-10 text-center text-mgray-400"
                      >
                        직원이 없습니다. 동기화로 mediness 명단을 가져오세요.
                      </TableCell>
                    </TableRow>
                  ) : null}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ) : null}
      </div>

      {toast ? (
        <div
          role="status"
          className="fixed bottom-6 right-6 z-50 rounded-md border border-mgray-200 bg-card px-4 py-3 text-[13px] text-mgray-800 shadow-lg"
        >
          {toast}
        </div>
      ) : null}
    </>
  );
}
