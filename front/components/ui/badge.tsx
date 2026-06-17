import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

// 분류·상태·권한 배지. 색 남용 금지 — 시안 토큰 한정 variant.
const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold transition-colors",
  {
    variants: {
      variant: {
        default: "bg-brand-50 text-brand-700",
        neutral: "bg-mgray-100 text-mgray-600",
        success: "bg-mgreen-50 text-mgreen-500",
        warning: "bg-mamber-50 text-mamber-500",
        destructive: "bg-mred-50 text-mred-500",
        outline: "border border-mgray-200 text-mgray-600",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

function Badge({
  className,
  variant,
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & {
    asChild?: boolean;
  }) {
  const Comp = asChild ? Slot : "span";
  return (
    <Comp
      data-slot="badge"
      className={cn(badgeVariants({ variant, className }))}
      {...props}
    />
  );
}

export { Badge, badgeVariants };
