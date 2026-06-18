"use client";

// 셸 사이드바 — 현재 직원(이름·권한) + 로그아웃 + 네비.
// role 게이트: 관리자(HR) 섹션은 admin 에게만 노출(SPEC-002 §U-1).
import {
  CalendarCheck,
  CalendarDays,
  LayoutGrid,
  LogOut,
  Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
  { label: "직원 디렉토리", icon: Users, href: "/directory" },
];

const adminNav: NavItem[] = [
  { label: "연차관리", icon: CalendarDays, badge: "HR" }, // 후속 — placeholder
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
  const { user, role, isAdmin, logout } = useAuth();
  const pathname = usePathname();

  const initial = user?.name?.[0] ?? "·";

  return (
    <aside className="flex w-[220px] shrink-0 flex-col border-r border-sidebar-border bg-sidebar">
      <div className="flex items-center gap-3 px-4 py-4">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-500 text-base font-semibold text-white">
          E
        </div>
        <div className="min-w-0 flex-1 leading-tight">
          <div className="text-sm font-semibold text-mgray-800">
            mediness ERP
          </div>
          <div className="text-[11px] text-mgray-500">HR workspace · v0.1</div>
        </div>
      </div>

      <div className="px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-mgray-500">
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

      {isAdmin ? (
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

      <div className="mx-3 mb-3 rounded-md bg-sidebar-accent px-2 py-2">
        <div className="flex items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-mgray-100 text-sm font-medium text-mgray-700">
            {initial}
          </div>
          <div className="min-w-0 flex-1 leading-tight">
            <div className="flex items-center gap-1.5 text-sm font-medium text-mgray-800">
              <span className="truncate">{user?.name ?? "직원"}</span>
              {role ? (
                <Badge variant="neutral" className="px-1.5 py-0.5 text-[10px]">
                  {role}
                </Badge>
              ) : null}
            </div>
            <div className="truncate text-[11px] text-mgray-500">
              {user?.email ?? ""}
            </div>
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => logout()}
          className="mt-2 w-full justify-start text-mgray-600"
        >
          <LogOut className="size-4" />
          로그아웃
        </Button>
      </div>
    </aside>
  );
}
