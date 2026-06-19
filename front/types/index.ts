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
// 기본 3(SPEC-003) + 취소 2(SPEC-005). 취소요청됨/취소됨 은 GET /leave/me 이력에 섞여 내려온다.
export type RequestStatus =
  | "신청됨"
  | "승인됨"
  | "반려됨"
  | "취소요청됨"
  | "취소됨";
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

// GET /me (MeOut) — auth 컨텍스트 self 해석 단일 소스. is_hr 은 BE 가 계산(department=="hr").
export interface Me {
  id: string;
  email: string;
  name: string;
  role: Role | null;
  department: string | null;
  is_hr: boolean;
}

// HR 신청 큐 1건 (PendingRequestOut) — 신청 내용 + 신청자 식별. `신청됨`만.
export interface PendingRequest {
  id: string;
  employee_id: string;
  employee_name: string;
  employee_email: string;
  category: LeaveCategory;
  unit: LeaveUnit;
  amount: string;
  am_pm: AmPm | null;
  use_date: string;
  note: string | null;
  status: RequestStatus;
  channel: RequestChannel;
  created_at: string;
}

// POST /leave/admin/requests/{id}/approve 응답 (ApprovalOut). warning=차감 후 잔여 음수.
export interface ApprovalResult {
  request: LeaveRequest;
  balance: string; // Decimal 문자열(음수 가능) — 표시 전용
  warning: boolean;
}

// ---- 변경 = 취소 + 재신청 묶음 (WP-004 Phase 2) — back/app/schemas/leave_request.py 와 정렬 ----
// 변경은 직원·HR 에게 "변경" 단일 항목으로 보인다("오전 반차 06-20 → 연차 06-22" = original → reapplication).

// 변경 묶음 한 쪽(원건/재신청)의 신청 내용 (ChangeSideOut). 신청자 식별은 묶음 상위에 1번만.
export interface ChangeSide {
  id: string;
  category: LeaveCategory;
  unit: LeaveUnit;
  amount: string;
  am_pm: AmPm | null;
  use_date: string;
  note: string | null;
  status: RequestStatus;
  channel: RequestChannel;
  created_at: string;
}

// HR 변경 큐 1건 (ChangeRequestOut) — change_group_id 가 식별자(승인/반려 경로 키).
export interface ChangeRequest {
  change_group_id: string;
  employee_id: string;
  employee_name: string;
  employee_email: string;
  original: ChangeSide; // 취소 대상 원건(승인됨/신청됨 → 승인 시 취소됨)
  reapplication: ChangeSide; // ERP 폼 재신청(신청됨 → 승인 시 승인됨)
}

// ---- HR 운영 (WP-005) — back/app/schemas/{leave_grant,leave_adjustment,leave_admin}.py 와 정렬 --
// Decimal 은 전부 문자열로 옴/감(표시·전송 전용 — FE 산술 금지, 미리보기 외).

// 벌크 부여 대상 종류 — `연차` 제외(HR 부여형만). LeaveCategory 의 부분집합.
export type GrantCategory = "보상" | "포상" | "Off Day";

// POST /leave/admin/grants body (BulkGrantIn). amount/expiry 는 Off Day default 위임 시 생략.
export interface BulkGrantBody {
  employee_ids: string[];
  category: GrantCategory;
  amount?: string; // Decimal 문자열(>0). Off Day 미지정 시 BE default 0.5
  expiry_date?: string; // YYYY-MM-DD. 보상/포상 필수 · Off Day 미지정 시 BE default(그달 말일)
  reason?: string;
}

// POST /leave/admin/grants 응답 (BulkGrantOut) — 결과 요약(toast 표시용).
export interface BulkGrantResult {
  target_count: number;
  category: GrantCategory;
  amount: string;
  expiry_date: string;
  reason: string | null;
  source: string; // "HR부여"
  granted_by: string;
  granted_at: string;
  lot_count: number;
}

// 연차수 조정 1건 (AdjustmentItemIn). delta 는 ± 문자열(≠0, FE 가드 + BE 422).
export interface AdjustmentItem {
  category: LeaveCategory; // 4 종류 전부(연차 포함)
  delta: string; // Decimal 문자열(±, ≠0)
  reason?: string;
}

// POST /leave/admin/adjustments body (LeaveAdjustmentIn).
export interface AdjustmentBody {
  employee_id: string;
  items: AdjustmentItem[];
}

// 조정 결과 1건 (AdjustmentResultItem).
export interface AdjustmentResultItem {
  category: LeaveCategory;
  delta: string;
  reason: string | null;
}

// POST /leave/admin/adjustments 응답 (LeaveAdjustmentOut). balances = 조정된 종류만(조정 후 잔여).
export interface AdjustmentResult {
  employee_id: string;
  adjusted_by: string;
  adjusted_at: string;
  items: AdjustmentResultItem[];
  balances: Partial<Record<LeaveCategory, string>>;
}

// 연차관리기록 1건 (LedgerEntryOut) — ledger derived view 행. occurred_at ASC.
export interface LedgerEntry {
  entry_type: string; // 발생/HR부여/이월/신청/사용/조정
  occurred_at: string;
  category: string; // 연차/Off Day/보상/포상 (Text)
  amount: string; // 부호 그대로(음수 가능)
  detail: string | null; // 신청=상태, 사용=null
  ref_id: string;
}

// 상세 조회 대상 직원 식별 (EmployeeIdentityOut).
export interface EmployeeIdentity {
  id: string;
  name: string;
  email: string;
  department: string | null;
}

// GET /leave/admin/employees/{id} 응답 (EmployeeLeaveDetailOut). balances 4종 키 항상 존재(BE 보장).
export interface EmployeeLeaveDetail {
  employee: EmployeeIdentity;
  balances: Record<LeaveCategory, string>; // 음수 가능
  total: string; // 음수 가능
  ledger: LedgerEntry[];
}
