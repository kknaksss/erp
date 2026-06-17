import { Plus } from "lucide-react";

import { AppHeader } from "@/components/app-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

// P1 진입 placeholder — 셸·shadcn 토큰 파이프라인 확인용. 합성 데이터.
// 실제 대시보드/연차 도메인 화면은 후속 phase.
const summary = [
  { label: "전체 잔여", value: "16.5", unit: "일" },
  { label: "연차", value: "12.5", unit: "일" },
  { label: "보상", value: "2.0", unit: "일" },
  { label: "포상", value: "1.0", unit: "일" },
];

const recent = [
  { date: "2026-06-12", kind: "연차", unit: "전일", status: "승인" as const },
  { date: "2026-06-05", kind: "보상", unit: "반차", status: "신청됨" as const },
  { date: "2026-05-28", kind: "Off Day", unit: "전일", status: "반려" as const },
];

const statusVariant = {
  승인: "success",
  신청됨: "default",
  반려: "destructive",
} as const;

export default function Home() {
  return (
    <>
      <AppHeader
        title="대시보드"
        description="ERP 골격 (WP-001 Phase 1) — shadcn/ui 토큰 시안 적용 확인용 placeholder"
        actions={
          <Button>
            <Plus />
            연차 신청
          </Button>
        }
      />

      <div className="w-full space-y-5 px-7 py-6">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {summary.map((item) => (
            <Card key={item.label}>
              <CardHeader>
                <CardDescription>{item.label}</CardDescription>
                <div className="flex items-baseline gap-1">
                  <span className="font-mono text-3xl font-semibold text-brand-500">
                    {item.value}
                  </span>
                  <span className="text-[12px] text-mgray-400">
                    {item.unit}
                  </span>
                </div>
              </CardHeader>
            </Card>
          ))}
        </div>

        <Card>
          <CardHeader>
            <CardTitle>최근 신청·사용 이력</CardTitle>
            <CardDescription>
              합성 데이터 — 실제 BE 연동은 후속 phase
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>사용날짜</TableHead>
                  <TableHead>종류</TableHead>
                  <TableHead>단위</TableHead>
                  <TableHead>상태</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {recent.map((row) => (
                  <TableRow key={row.date}>
                    <TableCell className="font-mono">{row.date}</TableCell>
                    <TableCell>{row.kind}</TableCell>
                    <TableCell>{row.unit}</TableCell>
                    <TableCell>
                      <Badge variant={statusVariant[row.status]}>
                        {row.status}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
