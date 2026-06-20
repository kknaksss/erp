"use client";

// 공유 모달 (SPEC-006 §2 U-5, v1 범위) — 스페이스 멤버십 범위 안내 수준.
// 현재 스페이스 멤버십으로 접근 가능함을 안내. 개별(특정 직원·부서) 공유 부여 UI 는 v1.1(§향후) — 비노출.
// 시안: document-management.html 편집기 헤더 "공유".
import { Share2, Users } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function DocumentShareDialog({
  open,
  onOpenChange,
  documentName,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  documentName: string;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[420px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Share2 className="size-[18px] text-brand-500" />공유
          </DialogTitle>
          <DialogDescription>{documentName}</DialogDescription>
        </DialogHeader>

        <div className="flex items-start gap-3 rounded-md border border-mgray-200 bg-mgray-50 px-3.5 py-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-brand-50 text-brand-500">
            <Users className="size-[18px]" />
          </span>
          <div className="leading-relaxed">
            <p className="text-[13px] font-medium text-mgray-800">
              스페이스 멤버십 범위로 공유됩니다
            </p>
            <p className="mt-0.5 text-[12px] text-mgray-500">
              이 문서는 해당 스페이스의 멤버(부서스페이스는 부서원, 개인스페이스는
              본인)에게 접근이 허용됩니다. 특정 직원·부서 단위의 개별 공유는 다음
              버전에서 제공됩니다.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            확인
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
