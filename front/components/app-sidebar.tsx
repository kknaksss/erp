import {
  CalendarCheck,
  CalendarDays,
  LayoutGrid,
  PanelLeftClose,
  Users,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// P1 셸 골격 — 합성 데이터. 실제 인증/현재 직원/HR 게이트는 P4.
type NavItem = {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  active?: boolean;
  badge?: string;
};

const workspaceNav: NavItem[] = [
  { label: "대시보드", icon: LayoutGrid, active: true },
  { label: "연차조회", icon: CalendarCheck },
  { label: "직원 디렉토리", icon: Users },
];

const adminNav: NavItem[] = [
  { label: "연차관리", icon: CalendarDays, badge: "HR" },
];

function NavLink({ item }: { item: NavItem }) {
  const Icon = item.icon;
  return (
    <span
      className={cn(
        "flex cursor-default items-center gap-3 rounded-md px-3 py-2 text-sm",
        item.active
          ? "bg-sidebar-accent font-semibold text-sidebar-accent-foreground"
          : "text-sidebar-foreground hover:bg-mgray-50",
      )}
    >
      <Icon className="size-[18px] shrink-0" />
      <span className="flex-1 truncate">{item.label}</span>
      {item.badge ? (
        <Badge variant="neutral" className="px-1.5 py-0.5 text-[10px]">
          {item.badge}
        </Badge>
      ) : null}
    </span>
  );
}

export function AppSidebar() {
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
        <PanelLeftClose className="size-[18px] text-mgray-500" />
      </div>

      <div className="px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-mgray-500">
        워크스페이스
      </div>
      <div className="flex flex-col gap-1 px-2">
        {workspaceNav.map((item) => (
          <NavLink key={item.label} item={item} />
        ))}
      </div>

      <div className="px-4 pb-1 pt-5 text-[11px] font-semibold uppercase tracking-wide text-mgray-500">
        관리자 (HR)
      </div>
      <div className="flex flex-col gap-1 px-2">
        {adminNav.map((item) => (
          <NavLink key={item.label} item={item} />
        ))}
      </div>
      <div className="px-4 pt-1.5 text-[10px] leading-relaxed text-mgray-400">
        department=인사 직원만 연차관리 노출 (P4)
      </div>

      <div className="flex-1" />

      <div className="mx-3 mb-3 flex items-center gap-3 rounded-md bg-sidebar-accent px-2 py-1.5">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-mgray-100 text-sm font-medium text-mgray-700">
          하
        </div>
        <div className="min-w-0 flex-1 leading-tight">
          <div className="flex items-center gap-1.5 text-sm font-medium text-mgray-800">
            김하늘
            <Badge variant="neutral" className="px-1.5 py-0.5 text-[10px]">
              member
            </Badge>
          </div>
          <div className="truncate text-[11px] text-mgray-500">개발 · staff</div>
        </div>
      </div>
    </aside>
  );
}
