"use client";

// 보호 라우트 셸 — 인증 가드 + 사이드바 + main 프레임.
// 미인증 → /login 리다이렉트. 재수화(loading) 동안엔 스피너.
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

import { AppModuleHeader } from "@/components/app-module-header";
import { AppSidebar } from "@/components/app-sidebar";
import { useAuth } from "@/lib/auth";

export default function AppLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "unauthenticated") router.replace("/login");
  }, [status, router]);

  if (status !== "authenticated") {
    return (
      <div className="flex min-h-screen items-center justify-center text-mgray-400">
        <Loader2 className="size-5 animate-spin" />
      </div>
    );
  }

  // 시안(leave-inquiry-my.html): 전폭 세로 스택 — 글로벌 모듈 헤더 → (사이드바 + 메인).
  return (
    <div className="flex min-h-screen w-full flex-col bg-white">
      <AppModuleHeader />
      <div className="flex min-h-0 flex-1">
        <AppSidebar />
        <main className="flex min-w-0 flex-1 flex-col bg-mgray-50">
          {children}
        </main>
      </div>
    </div>
  );
}
