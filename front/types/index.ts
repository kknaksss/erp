// BE 계약 타입(DTO) — back/app/schemas/ 와 정렬.

// SPEC-002 §3: role enum = admin | member (mediness 미러).
export type Role = "admin" | "member";

// 로그인/리프레시 user 객체 (mediness passthrough — SPEC-001 §S-1).
// ⚠ role 없음 — 권한은 employee roster(self-row)에서 읽는다(P4 결정).
export interface AuthUser {
  id: string; // mediness users.id (UUID) = employee.id 연결키
  email: string;
  name: string;
  first_login?: boolean;
}

// 토큰쌍 — SPEC-001 §3. ⚠ 로그인/리프레시 응답은 { data: {...} } 엔벨로프.
export interface TokenPair {
  access_token: string;
  refresh_token: string;
  access_expires_at: string;
  refresh_expires_at: string;
}

export interface LoginData extends TokenPair {
  user: AuthUser;
}

// 직원 명부 행 — back EmployeeOut (raw, 엔벨로프 없음).
export interface Employee {
  id: string;
  email: string;
  name: string;
  role: Role | null;
  active: boolean;
  position: string | null; // ERP 소유 — P5 입력 전까진 null
  department: string | null;
  created_at: string;
  updated_at: string;
}

// POST /admin/employees/sync 응답 (raw).
export interface SyncResult {
  updated: number;
  new: number;
}
