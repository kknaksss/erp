"use client";

// ERP 로그인 — 단독 페이지(셸 없음). UX 기준 = mediness 로그인 화면(SPEC-001 §2).
// 이메일·비밀번호 → POST /auth/login. 실패 시 폼 에러(입력 유지).
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

// SPEC-001 §U-1 문구: status → 사용자 메시지.
function messageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 422) return "이메일 형식이 올바르지 않습니다";
    if (err.status === 401) return "이메일 또는 비밀번호를 확인해주세요";
    if (err.status === 502 || err.status === 503)
      return "로그인 서버에 연결할 수 없습니다. 잠시 후 다시 시도해주세요";
  }
  return "로그인에 실패했습니다. 잠시 후 다시 시도해주세요";
}

export default function LoginPage() {
  const { status, login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 이미 로그인 상태면 진입(대시보드)으로.
  useEffect(() => {
    if (status === "authenticated") router.replace("/");
  }, [status, router]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(email, password);
      router.replace("/");
    } catch (err) {
      setError(messageFor(err)); // 입력 유지
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit = email.length > 0 && password.length > 0 && !submitting;

  return (
    <div className="flex min-h-screen items-center justify-center bg-mgray-50 px-4">
      <div className="w-full max-w-[380px]">
        <div className="mb-6 flex flex-col items-center gap-3">
          <div className="flex size-11 items-center justify-center rounded-xl bg-brand-500 text-lg font-semibold text-white">
            E
          </div>
          <div className="text-center">
            <h1 className="text-lg font-semibold text-mgray-800">
              mediness ERP
            </h1>
            <p className="mt-0.5 text-[13px] text-mgray-500">
              mediness 계정으로 로그인하세요
            </p>
          </div>
        </div>

        <form
          onSubmit={onSubmit}
          className="rounded-xl border border-mgray-200 bg-card p-6 shadow-sm"
        >
          <div className="space-y-4">
            <div className="space-y-1.5">
              <label
                htmlFor="email"
                className="text-[13px] font-medium text-mgray-700"
              >
                이메일
              </label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={submitting}
                placeholder="name@medisolveai.com"
              />
            </div>
            <div className="space-y-1.5">
              <label
                htmlFor="password"
                className="text-[13px] font-medium text-mgray-700"
              >
                비밀번호
              </label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={submitting}
              />
            </div>

            {error ? (
              <p
                role="alert"
                className="rounded-md bg-mred-50 px-3 py-2 text-[13px] text-mred-500"
              >
                {error}
              </p>
            ) : null}

            <Button type="submit" disabled={!canSubmit} className="w-full">
              {submitting ? <Loader2 className="animate-spin" /> : null}
              로그인
            </Button>
          </div>
        </form>

        <p className="mt-4 text-center text-[12px] text-mgray-400">
          비밀번호 변경·초기화는 mediness 에서 진행됩니다
        </p>
      </div>
    </div>
  );
}
