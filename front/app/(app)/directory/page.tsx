"use client";

// 직원 관리 (HR origin CRUD) — SPEC-002 / ERP-WP-007 P4. 시안: 21-html/employee-admin.html.
//  ERP 가 직원 정보를 완전 소유(origin). 생성 시 mediness 로그인 계정만 1회 발급, 이후 독립.
//  - 목록:   GET /admin/employees (department=="hr" 게이트)
//  - 생성:   POST /admin/employees → mediness provisioning(email+임시비번) + id 발급 채택
//  - 수정:   PATCH /admin/employees/{id} (ERP-local, mediness push 없음)
//  - 비활성: DELETE /admin/employees/{id} (soft delete active=false, 행 보존 + mediness 로그인 차단 push)
//  "동기화" 버튼 없음(미러 pull 제거 — origin 계약). adminNav 위치(app-sidebar).
//  권한 게이트 = ERP 자체 department=="hr". 페이지 가드 = 403-degrade(leave/admin 패턴): 비-HR → forbidden 화면.
//  ⚠ contract-first: BE CRUD(P2)·provisioning(P3) 병렬 진행 — 경로/스키마는 SPEC-002 §3 계약 기준 가정(리포트 참조).
import { useCallback, useEffect, useState } from "react";
import { Loader2, ShieldAlert, UserPlus } from "lucide-react";

import { AppHeader } from "@/components/app-header";
import { EmployeeDeactivateDialog } from "@/components/employee-deactivate-dialog";
import { EmployeeFormDialog } from "@/components/employee-form-dialog";
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
import { DEPARTMENT_LABELS, type Department, type Employee } from "@/types";

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; rows: Employee[] }
  | { kind: "forbidden" }
  | { kind: "error"; message: string };

// 폼 모달 상태 — 생성/수정 한 컴포넌트(mode). 수정은 대상 직원 보관.
type FormState =
  | { open: false }
  | { open: true; mode: { kind: "create" } }
  | { open: true; mode: { kind: "edit"; employee: Employee } };

export default function DirectoryPage() {
  const { authedFetch } = useAuth();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [form, setForm] = useState<FormState>({ open: false });
  const [deactivating, setDeactivating] = useState<Employee | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // setState 는 모두 await 이후(비동기 연속)라 effect 내 동기 setState 규칙에 걸리지 않음.
  // 페이지 가드 = leave/admin 패턴: 비-HR 은 BE 가 403 → forbidden degrade(nav 숨김과 이중).
  const fetchRows = useCallback(async () => {
    try {
      const rows = await authedFetch<Employee[]>("/admin/employees");
      // BE 는 ORDER BY name(en_US collation)이라 한글 가나다순이 아님 → FE 에서 한글 사전순 재정렬.
      const sorted = [...rows].sort((a, b) => a.name.localeCompare(b.name, "ko"));
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

  const reload = useCallback(() => {
    setState({ kind: "loading" });
    fetchRows();
  }, [fetchRows]);

  // 마운트 시 직원 목록 fetch — 토큰이 클라(sessionStorage)에만 있어 서버 fetch 불가.
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

  // 생성/수정 성공 → toast + 명부 재조회(목록 갱신).
  function onSaved(result: { kind: "create" | "edit"; summary: string }) {
    setToast(result.summary);
    fetchRows();
  }
  function onDeactivated(summary: string) {
    setToast(summary);
    fetchRows();
  }

  return (
    <>
      <AppHeader
        title="직원 관리 (HR)"
        description="ERP 가 직원 정보를 소유(origin) — 생성 시 mediness 로그인 계정만 1회 발급, 이후 독립"
        actions={
          state.kind === "ok" ? (
            <Button onClick={() => setForm({ open: true, mode: { kind: "create" } })}>
              <UserPlus />
              직원 생성
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
              <div className="text-sm font-medium text-mgray-700">관리자 전용</div>
              <p className="max-w-[360px] text-[13px] text-mgray-500">
                직원 관리는 <span className="font-mono">department == &quot;hr&quot;</span>{" "}
                직원만 사용할 수 있습니다. CRUD 액션은 노출되지 않으며, 직접 호출
                시 백엔드가 방어적으로 403 을 반환합니다.
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
                    <TableHead>부서</TableHead>
                    <TableHead>직급</TableHead>
                    <TableHead>role</TableHead>
                    <TableHead>재직</TableHead>
                    <TableHead className="text-right">관리</TableHead>
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
                        {DEPARTMENT_LABELS[e.department as Department] ??
                          e.department ??
                          "—"}
                      </TableCell>
                      <TableCell>{e.position ?? "—"}</TableCell>
                      <TableCell>
                        <Badge variant={e.role === "admin" ? "default" : "neutral"}>
                          {e.role ?? "—"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <span
                          className={
                            e.active
                              ? "inline-flex items-center gap-1 text-[12px] text-mgreen-500"
                              : "inline-flex items-center gap-1 text-[12px] text-mred-500"
                          }
                        >
                          <span
                            className={
                              e.active
                                ? "size-2 rounded-full bg-mgreen-500"
                                : "size-2 rounded-full bg-mred-500"
                            }
                          />
                          {e.active ? "재직" : "비활성"}
                        </span>
                      </TableCell>
                      <TableCell>
                        <div className="flex justify-end gap-1.5">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              setForm({
                                open: true,
                                mode: { kind: "edit", employee: e },
                              })
                            }
                          >
                            수정
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            className="text-mred-500"
                            disabled={!e.active}
                            onClick={() => setDeactivating(e)}
                          >
                            삭제
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                  {state.rows.length === 0 ? (
                    <TableRow>
                      <TableCell
                        colSpan={7}
                        className="py-10 text-center text-mgray-400"
                      >
                        직원이 없습니다. &quot;직원 생성&quot;으로 추가하세요.
                      </TableCell>
                    </TableRow>
                  ) : null}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ) : null}
      </div>

      {/* 생성/수정 모달 — mode 로 분기(한 컴포넌트) */}
      {form.open ? (
        <EmployeeFormDialog
          open={form.open}
          onOpenChange={(next) => {
            if (!next) setForm({ open: false });
          }}
          mode={form.mode}
          onSaved={onSaved}
        />
      ) : null}

      {/* 비활성(soft delete) 확인 모달 */}
      <EmployeeDeactivateDialog
        open={deactivating !== null}
        onOpenChange={(next) => {
          if (!next) setDeactivating(null);
        }}
        employee={deactivating}
        onDeactivated={onDeactivated}
      />

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
