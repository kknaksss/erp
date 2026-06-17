import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// shadcn/ui 표준 className 병합 헬퍼.
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
