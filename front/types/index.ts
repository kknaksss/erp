// BE 계약 타입(DTO) — back/app/schemas/ 와 정렬.

// SPEC-002 §3: role enum = admin | member (ERP 소유 — origin).
export type Role = "admin" | "member";

// SPEC-002 §position: 직급 8값 (ERP 소유 라벨, mediness 와 독립).
export type Position =
  | "ceo"
  | "coo"
  | "cmo"
  | "cto"
  | "po"
  | "manager"
  | "leader"
  | "staff";

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
// SPEC-002(origin): 전 필드 ERP 소유. role·position 은 origin 에서 notnull(server_default)이나,
// 마이그레이션 전 legacy 행 대비 표시 측은 null 을 방어적으로 허용("—" fallback) 한다.
export interface Employee {
  id: string;
  email: string;
  name: string;
  role: Role | null;
  active: boolean;
  position: string | null;
  department: string | null;
  created_at: string;
  updated_at: string;
}

// 직원 생성 body (ERP origin CRUD — POST /admin/employees, BE P2 contract-first 가정).
// 저장 시 mediness 로그인 계정 1회 provisioning(email + 임시비번), id=mediness 발급 채택(SPEC-002 §3).
export interface EmployeeCreateBody {
  name: string;
  email: string; // 로그인 아이디 = provisioning push 대상
  department: string; // 영문 부서 코드
  position: Position;
  role: Role;
}

// 직원 수정 body (ERP-local — PATCH /admin/employees/{id}). 이메일 제외(생성 시 확정).
// mediness 로 push 하지 않는다(디커플 — SPEC-002 §U-2).
export interface EmployeeUpdateBody {
  name: string;
  department: string;
  position: Position;
  role: Role;
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

// ---- 문서관리 (WP-006) — back/app/schemas/document.py 와 정렬 (FE Phase 4) ----------
// enum 값은 영문(외부 계약 그대로). 트리는 SpaceNode[] (엔벨로프 없음, raw 배열).

export type SpaceType = "department" | "personal";
export type DocumentType = "word" | "excel"; // word=.docx / excel=.xlsx

// 폴더 (FolderOut). 자기참조 트리 — parent_id=null 이면 space 직속(루트).
export interface DocFolder {
  id: string;
  space_id: string;
  parent_id: string | null;
  name: string;
}

// 문서 잎 (DocumentOut). 선택 식별자 = id (P5 editor-config 핸드오프 키).
export interface DocDocument {
  id: string;
  space_id: string;
  folder_id: string | null;
  name: string;
  type: DocumentType;
}

// 버전 1건 (VersionOut) — P5(버전 이력 UI)용. P4 는 타입만 정의.
export interface DocVersion {
  id: string;
  document_id: string;
  version_no: number;
  ext: string;
  size_bytes: number;
  created_at: string;
}

// 스페이스 (SpaceOut) — 멤버십 판정 단위. 부서스페이스(department)·개인스페이스(personal).
export interface DocSpace {
  id: string;
  type: SpaceType;
  name: string;
  department: string | null;
  owner_id: string | null;
}

// 트리 노드 — 폴더 + 자기참조 하위(폴더/문서). 재귀 (FolderNode).
export interface DocFolderNode {
  folder: DocFolder;
  folders: DocFolderNode[];
  documents: DocDocument[];
}

// 트리 최상위 — 스페이스 + 루트 직속 폴더/문서 (SpaceNode). GET /documents/tree 응답 요소.
export interface DocSpaceNode {
  space: DocSpace;
  folders: DocFolderNode[];
  documents: DocDocument[];
}

// POST /documents/folders body (FolderCreateIn). parent_id 생략 = space 직속.
export interface FolderCreateBody {
  space_id: string;
  parent_id?: string | null;
  name: string;
}

// POST /documents/files body (DocumentCreateIn). 빈 .docx/.xlsx 생성.
export interface DocumentCreateBody {
  space_id: string;
  folder_id?: string | null;
  name: string;
  type: DocumentType;
}

// FE 헬퍼 — "새로 만들기/업로드" 대상 위치. 트리 노드(스페이스 루트/폴더)에서 해석.
//   space 루트 → { spaceId, parentId:null }, 폴더 → { spaceId:folder.space_id, parentId:folder.id }.
export interface DocCreateTarget {
  spaceId: string;
  parentId: string | null; // 폴더 id 또는 null(스페이스 루트)
  label: string; // 위치 표시(스페이스명 / 폴더명)
}
