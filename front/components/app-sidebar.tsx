"use client";

// 셸 사이드바 — 워크스페이스/관리자(HR) 2섹션 네비.
// 로고·유저·로그아웃은 글로벌 모듈 헤더(app-module-header)로 이관 — 시안 따름.
// role 게이트: 관리자(HR) 섹션은 isHr(department=hr) 에게만 노출(SPEC-002 §U-1).
import { CalendarCheck, CalendarDays, LayoutGrid, UsersRound } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type NavItem = {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  href?: string; // 없으면 placeholder(아직 미구현 도메인)
  badge?: string;
};

const workspaceNav: NavItem[] = [
  { label: "대시보드", icon: LayoutGrid, href: "/" },
  { label: "연차조회", icon: CalendarCheck, href: "/leave" }, // WP-003 P3 — 전 직원
];

const adminNav: NavItem[] = [
  { label: "연차관리", icon: CalendarDays, href: "/leave/admin", badge: "HR" }, // WP-003 P4 — HR 전용
  { label: "직원 관리", icon: UsersRound, href: "/directory", badge: "HR" }, // WP-007 P4 — HR origin CRUD(SPEC-002)
];

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  const className = cn(
    "flex items-center gap-3 rounded-md px-3 py-2 text-sm",
    active
      ? "bg-sidebar-accent font-semibold text-sidebar-accent-foreground"
      : "text-sidebar-foreground hover:bg-mgray-50",
    item.href ? "" : "cursor-default opacity-60",
  );
  const inner = (
    <>
      <Icon className="size-[18px] shrink-0" />
      <span className="flex-1 truncate">{item.label}</span>
      {item.badge ? (
        <Badge variant="neutral" className="px-1.5 py-0.5 text-[10px]">
          {item.badge}
        </Badge>
      ) : null}
    </>
  );
  return item.href ? (
    <Link href={item.href} className={className}>
      {inner}
    </Link>
  ) : (
    <span className={className}>{inner}</span>
  );
}

export function AppSidebar() {
  const { isHr } = useAuth();
  const pathname = usePathname();

  // 문서관리 모듈은 자체 디렉토리 트리를 사이드바로 쓴다(시안 document-management.html).
  // HR 네비 사이드바는 그 화면에서 숨긴다 — 시안의 단일 트리 사이드바와 정합.
  if (pathname.startsWith("/documents")) return null;

  return (
    <aside className="flex w-[220px] shrink-0 flex-col border-r border-sidebar-border bg-sidebar">
      <div className="px-4 pb-1 pt-4 text-[11px] font-semibold uppercase tracking-wide text-mgray-500">
        워크스페이스
      </div>
      <div className="flex flex-col gap-1 px-2">
        {workspaceNav.map((item) => (
          <NavLink
            key={item.label}
            item={item}
            active={item.href === pathname}
          />
        ))}
      </div>

      {isHr ? (
        <>
          <div className="px-4 pb-1 pt-5 text-[11px] font-semibold uppercase tracking-wide text-mgray-500">
            관리자 (HR)
          </div>
          <div className="flex flex-col gap-1 px-2">
            {adminNav.map((item) => (
              <NavLink
                key={item.label}
                item={item}
                active={item.href === pathname}
              />
            ))}
          </div>
        </>
      ) : null}

      <div className="flex-1" />
    </aside>
  );
}
