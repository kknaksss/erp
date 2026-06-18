"use client";

// 인증 컨텍스트 — 토큰 보관 · 로그인/로그아웃/리프레시 · 401 인터셉터(authedFetch) · self 해석(role·department·isHr).
//
// self 해석(WP-003 P4): GET /me(MeOut {role, department, is_hr}) 단일 소스로 전환.
//   - 구 `/admin/employees` self-row hack 폐기 — member-role HR 직원이 거기서 403 이라 자기 department 를 못 보던 문제 해소.
//   - /me 는 게이트 없음(유효 토큰만) → 모든 로그인 직원이 본인 role·department·is_hr 확보.
//   - isHr = /me.is_hr(BE 가 department=="hr" 계산) 그대로 — FE 는 department 문자열 비교 안 함.
//   - isAdmin(role==admin) 은 유지(직원 디렉토리 동기 버튼 게이트가 사용, HR 축과 별개).
//
// 토큰 보관 결정(WP Open Issue, 워커 결정):
//   = 메모리(ref) + sessionStorage 미러.
//   - httpOnly 쿠키가 이상적이나 BE 가 토큰을 JSON body 로 반환(mediness passthrough)하고
//     BE 수정은 범위 밖이라 FE 단독으로 쿠키 세션 불가 → SPA 표준인 storage 채택.
//   - localStorage(영속) 대신 sessionStorage: 탭 종료 시 소거 + 새로고침엔 생존(가드 재실행 대비).
//   - 트레이드오프: JS 접근 가능 → XSS 시 토큰 탈취 위험. 완화는 향후 BE httpOnly 쿠키 세션 권장(리포트).

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  ApiError,
  apiFetch,
  rawAuthedFetch,
  toApiError,
  parseResponse,
} from "@/lib/api";
import type { AuthUser, LoginData, Me, Role, TokenPair } from "@/types";

type Status = "loading" | "authenticated" | "unauthenticated";

// /me 파생 self 정보 — 토큰/유저와 별개로 보관·미러.
interface SelfInfo {
  role: Role | null; // null = 미해석 또는 member 취급(권한 기능 비노출)
  department: string | null;
  isHr: boolean;
}

const NO_SELF: SelfInfo = { role: null, department: null, isHr: false };

interface AuthState {
  status: Status;
  user: AuthUser | null;
  role: Role | null;
  department: string | null;
  isHr: boolean;
}

interface AuthContextValue extends AuthState {
  isAdmin: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  authedFetch: <T>(path: string, init?: RequestInit) => Promise<T>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const TOKENS_KEY = "erp.tokens";
const USER_KEY = "erp.user";
const SELF_KEY = "erp.self";

function readStorage<T>(key: string): T | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({
    status: "loading",
    user: null,
    role: null,
    department: null,
    isHr: false,
  });
  // authedFetch 가 항상 최신 토큰을 보도록 ref 로 동기 보관(상태 클로저 회피).
  const tokensRef = useRef<TokenPair | null>(null);

  const persist = useCallback(
    (tokens: TokenPair | null, user: AuthUser | null, self: SelfInfo | null) => {
      tokensRef.current = tokens;
      if (typeof window === "undefined") return;
      const ss = window.sessionStorage;
      if (tokens) ss.setItem(TOKENS_KEY, JSON.stringify(tokens));
      else ss.removeItem(TOKENS_KEY);
      if (user) ss.setItem(USER_KEY, JSON.stringify(user));
      else ss.removeItem(USER_KEY);
      if (self) ss.setItem(SELF_KEY, JSON.stringify(self));
      else ss.removeItem(SELF_KEY);
    },
    [],
  );

  const clear = useCallback(() => {
    persist(null, null, null);
    setState({
      status: "unauthenticated",
      user: null,
      role: null,
      department: null,
      isHr: false,
    });
  }, [persist]);

  // 401 인터셉터: access 주입 → 401 이면 refresh 1회 → 재시도. refresh 실패 시 로그아웃.
  // refresh 자체는 공용 apiFetch(비인증 엔드포인트)라 인터셉터 재진입 없음.
  const authedFetch = useCallback(
    async <T,>(path: string, init?: RequestInit): Promise<T> => {
      const tokens = tokensRef.current;
      if (!tokens) throw new ApiError(401, "NO_TOKEN", "로그인이 필요합니다");

      let res = await rawAuthedFetch(path, tokens.access_token, init);
      if (res.status === 401) {
        // refresh 회전 시도
        let refreshed: TokenPair;
        try {
          const data = await apiFetch<{ data: LoginData }>("/auth/refresh", {
            method: "POST",
            body: JSON.stringify({ refresh_token: tokens.refresh_token }),
          });
          refreshed = data.data;
        } catch {
          clear(); // 재사용/만료 → 재로그인
          throw new ApiError(401, "REFRESH_FAILED", "세션이 만료되었습니다");
        }
        // 새 토큰쌍 교체(user/self 유지) 후 재시도
        const user = readStorage<AuthUser>(USER_KEY);
        tokensRef.current = refreshed;
        persist(refreshed, user, readStorage<SelfInfo>(SELF_KEY));
        res = await rawAuthedFetch(path, refreshed.access_token, init);
      }
      if (!res.ok) throw await toApiError(res);
      return parseResponse<T>(res);
    },
    [clear, persist],
  );

  // self 해석: GET /me(role·department·is_hr). 게이트 없음 — 401 은 authedFetch 가 refresh 처리.
  //  - 200 → MeOut 매핑
  //  - 502/503/네트워크/404(미러 없음) → NO_SELF(권한 기능 비노출, 안전측)
  const resolveSelf = useCallback(async (): Promise<SelfInfo> => {
    try {
      const me = await authedFetch<Me>("/me");
      return { role: me.role, department: me.department, isHr: me.is_hr };
    } catch {
      return NO_SELF;
    }
  }, [authedFetch]);

  const login = useCallback(
    async (email: string, password: string) => {
      const data = await apiFetch<{ data: LoginData }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      const { user, ...tokens } = data.data; // ⚠ data 엔벨로프(mediness passthrough)
      persist(tokens, user, null);
      setState({
        status: "authenticated",
        user,
        role: null,
        department: null,
        isHr: false,
      });
      // self 는 후속 해석(로그인 차단하지 않음)
      const self = await resolveSelf();
      persist(tokens, user, self);
      setState({ status: "authenticated", user, ...self });
    },
    [persist, resolveSelf],
  );

  const logout = useCallback(async () => {
    try {
      if (tokensRef.current) {
        await rawAuthedFetch("/auth/logout", tokensRef.current.access_token, {
          method: "POST",
        });
      }
    } catch {
      // 폐기 실패해도 로컬 세션은 비운다(베스트에포트)
    }
    clear();
  }, [clear]);

  // 마운트 시 sessionStorage 재수화(새로고침 생존).
  useEffect(() => {
    const tokens = readStorage<TokenPair>(TOKENS_KEY);
    const user = readStorage<AuthUser>(USER_KEY);
    const self = readStorage<SelfInfo>(SELF_KEY);
    if (tokens && user) {
      tokensRef.current = tokens;
      // 마운트 시 sessionStorage(외부 스토어) 재수화 — 클라 전용·하이드레이션 후 1회 전이라
      // 동기 setState 가 불가피(lazy init 은 SSR↔클라 불일치 유발). 캐스케이드 1회 허용.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setState({
        status: "authenticated",
        user,
        role: self?.role ?? null,
        department: self?.department ?? null,
        isHr: self?.isHr ?? false,
      });
      // 캐시된 self 를 백그라운드 재검증(만료/권한 변동 반영) — await 이후라 규칙 무관
      resolveSelf().then((s) => {
        persist(tokensRef.current, user, s);
        setState((prev) =>
          prev.status === "authenticated" ? { ...prev, ...s } : prev,
        );
      });
    } else {
      setState({
        status: "unauthenticated",
        user: null,
        role: null,
        department: null,
        isHr: false,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value: AuthContextValue = {
    ...state,
    isAdmin: state.role === "admin",
    login,
    logout,
    authedFetch,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth 는 AuthProvider 안에서만 사용");
  return ctx;
}
