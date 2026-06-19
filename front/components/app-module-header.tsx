"use client";

// 글로벌 모듈 헤더 (전 화면 공통) — 시안 leave-inquiry-my.html <header> 1:1.
//  - 로고(E 박스 + mediness ERP / HR workspace · v0.1)
//  - 모듈 네비: HR관리(현재 영역·활성) / 문서관리(→ /documents placeholder)
//  - 유저 프로필: auth 실데이터(이름·role badge·부서). 로그아웃 = 시안엔 없으나 auth 보존상 LogOut 아이콘 버튼으로 유지.
// ⚠ 부서(department)는 코드베이스 전역과 동일하게 raw 표시(코드→표시명 매핑 source 없음).
//   직급(position)은 /me(MeOut) 에 없어 미표시(발명 금지) — 둘 다 리포트 yes/no.
import { LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

// 모듈 네비 — HR관리(현재 워크스페이스) / 문서관리(별 모듈, placeholder).
const MODULES: { label: string; href: string; match: (p: string) => boolean }[] =
  [
    { label: "HR관리", href: "/", match: (p) => !p.startsWith("/documents") },
    {
      label: "문서관리",
      href: "/documents",
      match: (p) => p.startsWith("/documents"),
    },
  ];

export function AppModuleHeader() {
  const { user, role, department, logout } = useAuth();
  const pathname = usePathname();

  const initial = user?.name?.[0] ?? "·";

  return (
    <header className="flex items-center gap-6 border-b border-mgray-100 bg-white px-5 py-3">
      {/* 로고 */}
      <div className="flex items-center gap-2.5">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-500 text-base font-semibold text-white">
          E
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold text-mgray-800">
            mediness ERP
          </div>
          <div className="text-[11px] text-mgray-500">HR workspace · v0.1</div>
        </div>
      </div>

      {/* 모듈 네비 */}
      <nav className="flex items-center gap-1">
        {MODULES.map((m) => {
          const active = m.match(pathname);
          return (
            <Link
              key={m.label}
              href={m.href}
              className={cn(
                "rounded-md px-3.5 py-2 text-sm",
                active
                  ? "bg-brand-50 font-semibold text-brand-500"
                  : "font-medium text-mgray-500 hover:bg-mgray-50 hover:text-mgray-700",
              )}
            >
              {m.label}
            </Link>
          );
        })}
      </nav>

      <div className="flex-1" />

      {/* 유저 프로필 (auth 실데이터) + 로그아웃 */}
      <div className="flex items-center gap-2.5">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-mgray-100 text-sm font-medium text-mgray-700">
          {initial}
        </div>
        <div className="leading-tight">
          <div className="flex items-center gap-1.5 text-sm font-medium text-mgray-800">
            <span className="truncate">{user?.name ?? "직원"}</span>
            {role ? (
              <span className="rounded-full bg-mgray-200 px-1.5 py-0.5 text-[10px] font-semibold text-mgray-600">
                {role}
              </span>
            ) : null}
          </div>
          <div className="text-[11px] text-mgray-500">{department ?? "—"}</div>
        </div>
        <button
          type="button"
          onClick={() => logout()}
          aria-label="로그아웃"
          title="로그아웃"
          className="ml-1 rounded-md p-2 text-mgray-400 hover:bg-mgray-50 hover:text-mgray-700"
        >
          <LogOut className="size-4" />
        </button>
      </div>
    </header>
  );
}
