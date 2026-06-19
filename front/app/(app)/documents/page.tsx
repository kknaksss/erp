import { FileText } from "lucide-react";

import { AppHeader } from "@/components/app-header";

// 문서관리 모듈 — placeholder(사용자 결정). 글로벌 헤더의 "문서관리" 탭 라우팅 대상.
// 시안 link(document-management.html) 는 미구현 영역 → 라우트만 두고 "준비 중" 안내.
export default function DocumentsPage() {
  return (
    <>
      <AppHeader
        title="문서관리"
        description="준비 중인 모듈입니다"
      />

      <div className="flex flex-1 items-center justify-center px-7 py-6">
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="flex size-12 items-center justify-center rounded-xl bg-mgray-100 text-mgray-400">
            <FileText className="size-6" />
          </div>
          <div className="text-sm font-semibold text-mgray-700">
            문서관리 준비 중
          </div>
          <p className="max-w-sm text-[13px] leading-relaxed text-mgray-500">
            문서관리 모듈은 아직 준비 중입니다. 현재는 HR관리(연차) 기능을
            이용해 주세요.
          </p>
        </div>
      </div>
    </>
  );
}
