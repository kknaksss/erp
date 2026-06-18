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

// ---- 연차 (WP-003) — back/app/schemas/leave_request.py 와 정렬 ----------------
// enum 은 한글 value 로 직렬화/파싱(BE 계약). Decimal 은 문자열로 옴(표시 전용 — 산술 X).

export type LeaveCategory = "연차" | "보상" | "포상" | "Off Day";
export type LeaveUnit = "전일" | "반차" | "반반차";
export type AmPm = "오전" | "오후";
export type RequestStatus = "신청됨" | "승인됨" | "반려됨";
export type RequestChannel = "slack" | "erp";

// 신청/이력 1건 (LeaveRequestOut). amount 는 서버 derive(문자열) — 입력엔 안 보냄.
export interface LeaveRequest {
  id: string;
  category: LeaveCategory;
  unit: LeaveUnit;
  amount: string; // Decimal 문자열 ("1.0"/"0.5"/"0.25")
  am_pm: AmPm | null;
  use_date: string; // YYYY-MM-DD
  note: string | null;
  status: RequestStatus;
  channel: RequestChannel;
  created_at: string;
}

// 보상/포상 만료 안내 1건 (ExpiringLotOut).
export interface ExpiringLot {
  category: LeaveCategory;
  remaining: string; // Decimal 문자열
  expiry_date: string; // YYYY-MM-DD
}

// GET /leave/me (LeaveSelfOut). balances 4종류 키 항상 존재(BE 보장).
export interface LeaveSelf {
  balances: Record<LeaveCategory, string>;
  total: string;
  expiring: ExpiringLot[];
  history: LeaveRequest[];
}

// POST /leave/intake body (ErpIntakeIn). am_pm 은 반차/반반차만, 전일은 생략. amount 안 보냄.
export interface ErpIntakeBody {
  category: LeaveCategory;
  unit: LeaveUnit;
  am_pm?: AmPm;
  use_date: string;
  note?: string;
}
