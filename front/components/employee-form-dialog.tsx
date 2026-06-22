"use client";

// 직원 생성/수정 모달 (SPEC-002 §U-1·U-2, WP-007 P4) — origin CRUD.
//  mode="create": 이름·이메일·부서·직급·role 5필드 → POST /admin/employees.
//    저장 시 ERP employee 생성 + mediness 로그인 계정 1회 provisioning(email+임시비번, id=mediness 발급).
//    실패 케이스(SPEC-002 §케이스 매트릭스): email 충돌 → "이미 계정이 존재합니다",
//    provisioning 일시 실패 → "직원 생성에 실패했습니다. 잠시 후 다시 시도해주세요".
//  mode="edit": 이름·부서·직급·role 4필드(이메일 제외) → PATCH /admin/employees/{id} (ERP-local, mediness push 없음).
//  시안: 21-html/employee-admin.html #createModal/#editModal. "동기화" 없음(origin — 미러 pull 제거).
//  ⚠ contract-first: BE P2(CRUD)·P3(provisioning) 병렬 진행 — 경로/스키마는 SPEC-002 §3 계약 기준 가정(리포트 참조).
import { useState } from "react";
import { Loader2, UserPlus, UserCog } from "lucide-react";

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
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type {
  Employee,
  EmployeeCreateBody,
  EmployeeUpdateBody,
  Position,
  Role,
} from "@/types";

// 직급 8값 (SPEC-002 §position). 시안 select 순서 그대로.
const POSITIONS: Position[] = [
  "ceo",
  "coo",
  "cmo",
  "cto",
  "po",
  "manager",
  "leader",
  "staff",
];
// role 2값 (SPEC-002 §role).
const ROLES: Role[] = ["admin", "member"];

type Mode =
  | { kind: "create" }
  | { kind: "edit"; employee: Employee };

export function EmployeeFormDialog({
  open,
  onOpenChange,
  mode,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: Mode;
  // 성공 요약(부모가 toast + 명부 재조회). kind 로 toast 문구 분기.
  onSaved: (result: { kind: "create" | "edit"; summary: string }) => void;
}) {
  const { authedFetch } = useAuth();
  const isEdit = mode.kind === "edit";

  // 모드별 프리필 — edit 는 대상 직원 값, create 는 시안 디폴트(staff/member).
  // 부모(page)가 열 때마다 fresh 마운트하므로 lazy initializer 로 충분(effect 불필요).
  const initial = mode.kind === "edit" ? mode.employee : null;
  const [name, setName] = useState(initial?.name ?? "");
  const [email, setEmail] = useState(initial?.email ?? "");
  const [department, setDepartment] = useState(initial?.department ?? "");
  const [position, setPosition] = useState<Position>(
    (initial?.position as Position) ?? "staff",
  );
  const [role, setRole] = useState<Role>(initial?.role ?? "member");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleOpenChange(next: boolean) {
    if (!next) setError(null);
    onOpenChange(next);
  }

  async function onSubmit() {
    const trimmedName = name.trim();
    const trimmedEmail = email.trim();
    const trimmedDept = department.trim();
    if (!trimmedName) {
      setError("이름을 입력해주세요");
      return;
    }
    if (!isEdit && !trimmedEmail) {
      setError("이메일(로그인 아이디)을 입력해주세요");
      return;
    }
    if (!trimmedDept) {
      setError("부서(영문 코드)를 입력해주세요");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      if (mode.kind === "edit") {
        const body: EmployeeUpdateBody = {
          name: trimmedName,
          department: trimmedDept,
          position,
          role,
        };
        await authedFetch<Employee>(`/admin/employees/${mode.employee.id}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        });
        onOpenChange(false);
        onSaved({ kind: "edit", summary: "직원 정보가 수정되었습니다" });
      } else {
        const body: EmployeeCreateBody = {
          name: trimmedName,
          email: trimmedEmail,
          department: trimmedDept,
          position,
          role,
        };
        await authedFetch<Employee>("/admin/employees", {
          method: "POST",
          body: JSON.stringify(body),
        });
        onOpenChange(false);
        onSaved({
          kind: "create",
          summary: "직원이 생성되고 로그인 계정이 발급되었습니다",
        });
      }
    } catch (err) {
      setError(submitErrorMessage(err, isEdit));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-[460px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {isEdit ? (
              <UserCog className="size-[18px] text-brand-500" />
            ) : (
              <UserPlus className="size-[18px] text-brand-500" />
            )}
            {isEdit ? "직원 수정" : "직원 생성"}
            {isEdit ? (
              <span className="text-[12px] font-normal text-mgray-400">
                · {mode.employee.name}
              </span>
            ) : null}
          </DialogTitle>
          <DialogDescription>
            {isEdit
              ? "이름·부서·직급·role 을 ERP-local 로 편집합니다 (mediness 로 push 하지 않습니다)."
              : "ERP 직원 레코드를 만들고 mediness 로그인 계정을 1회 발급합니다."}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* 이름 */}
          <div className="space-y-1.5">
            <label
              htmlFor="emp-name"
              className="block text-[12px] font-medium text-mgray-700"
            >
              이름
            </label>
            <Input
              id="emp-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="예: 박신입"
            />
          </div>

          {/* 이메일 — 생성만(수정은 로그인 아이디 고정) */}
          {!isEdit ? (
            <div className="space-y-1.5">
              <label
                htmlFor="emp-email"
                className="block text-[12px] font-medium text-mgray-700"
              >
                이메일{" "}
                <span className="font-normal text-mgray-400">
                  (로그인 아이디)
                </span>
              </label>
              <Input
                id="emp-email"
                type="email"
                className="font-mono"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="park.newbie@mediness.co"
              />
            </div>
          ) : null}

          {/* 부서 / 직급 */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label
                htmlFor="emp-department"
                className="block text-[12px] font-medium text-mgray-700"
              >
                부서{" "}
                <span className="font-normal text-mgray-400">(영문 코드)</span>
              </label>
              <Input
                id="emp-department"
                className="font-mono"
                value={department}
                onChange={(e) => setDepartment(e.target.value)}
                placeholder="sales"
              />
            </div>
            <div className="space-y-1.5">
              <label className="block text-[12px] font-medium text-mgray-700">
                직급
              </label>
              <Select
                value={position}
                onValueChange={(v) => setPosition(v as Position)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {POSITIONS.map((p) => (
                    <SelectItem key={p} value={p}>
                      {p}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* role */}
          <div className="space-y-1.5">
            <label className="block text-[12px] font-medium text-mgray-700">
              role
            </label>
            <Select value={role} onValueChange={(v) => setRole(v as Role)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLES.map((r) => (
                  <SelectItem key={r} value={r}>
                    {r}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* 모드별 안내 */}
          {isEdit ? (
            <p className="text-[11px] leading-relaxed text-mgray-400">
              이메일(로그인 아이디)은 수정하지 않습니다. 수정은 ERP-local
              반영이며 mediness 로 push 하지 않습니다(디커플).
            </p>
          ) : (
            <div className="rounded-md bg-brand-50 px-3 py-2 text-[12px] leading-relaxed text-mgray-600">
              저장 시 ERP <span className="font-mono">employee</span> 생성 +
              mediness 로그인 계정 provisioning(email + 임시비밀번호 발급). 계정
              id 는 mediness 가 발급해 <span className="font-mono">employee.id</span>{" "}
              로 채택됩니다.{" "}
              <span className="text-mgray-500">
                provisioning 실패 시 직원이 생성되지 않습니다.
              </span>
            </div>
          )}

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
          <Button onClick={onSubmit} disabled={submitting}>
            {submitting ? (
              <Loader2 className="animate-spin" />
            ) : isEdit ? (
              <UserCog />
            ) : (
              <UserPlus />
            )}
            {isEdit ? "수정" : "직원 생성"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// 생성/수정 에러 → 사용자 문구(SPEC-002 §케이스 매트릭스 verbatim).
//  email 충돌(409): "이미 계정이 존재합니다" — 멱등키=email, 중복·부분 생성 금지(OQ-4).
//  provisioning 일시 실패(502/503): "직원 생성에 실패했습니다. 잠시 후 다시 시도해주세요"(수동 재시도).
function submitErrorMessage(err: unknown, isEdit: boolean): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return "HR 권한이 필요합니다";
    if (err.status === 422) return "입력값을 확인해주세요 (이름·이메일·부서·직급·role)";
    if (!isEdit) {
      if (err.status === 409) return "이미 계정이 존재합니다";
      if (err.status === 502 || err.status === 503)
        return "직원 생성에 실패했습니다. 잠시 후 다시 시도해주세요";
    }
    return err.message;
  }
  return isEdit
    ? "직원 정보 수정에 실패했습니다. 잠시 후 다시 시도해주세요"
    : "직원 생성에 실패했습니다. 잠시 후 다시 시도해주세요";
}
