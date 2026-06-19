import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// shadcn/ui 표준 className 병합 헬퍼.
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// 연차 일수 표시 — Decimal 문자열/숫자를 소수 최대 2자리(끝 0 제거)로. 표시 전용(산술 X).
//  ⚠ toFixed(1) 금지: 반반차 = 0.25 단위라 0.25→"0.2", 11.75→"11.8" 로 오표시(SPEC-003 §AC).
//  예) "0.25"→"0.25" · "0.5"→"0.5" · "12.00"→"12" · "-2.50"→"-2.5" · "11.75"→"11.75".
export function formatDays(value: string | number): string {
  const n = typeof value === "number" ? value : parseFloat(value);
  if (Number.isNaN(n)) return String(value);
  // 2자리 반올림으로 부동소수 노이즈(11.749999) 제거 후 끝 0 trim.
  return (Math.round(n * 100) / 100).toString();
}
