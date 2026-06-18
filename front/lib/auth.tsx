"use client";

// 인증 컨텍스트 — 토큰 보관 · 로그인/로그아웃/리프레시 · 401 인터셉터(authedFetch) · role 해석.
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
import type {
  AuthUser,
  Employee,
  LoginData,
  Role,
  TokenPair,
} from "@/types";

type Status = "loading" | "authenticated" | "unauthenticated";

interface AuthState {
  status: Status;
  user: AuthUser | null;
  role: Role | null; // null = 미해석 또는 member 취급(권한 기능 비노출)
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
const ROLE_KEY = "erp.role";

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
  });
  // authedFetch 가 항상 최신 토큰을 보도록 ref 로 동기 보관(상태 클로저 회피).
  const tokensRef = useRef<TokenPair | null>(null);

  const persist = useCallback(
    (tokens: TokenPair | null, user: AuthUser | null, role: Role | null) => {
      tokensRef.current = tokens;
      if (typeof window === "undefined") return;
      const ss = window.sessionStorage;
      if (tokens) ss.setItem(TOKENS_KEY, JSON.stringify(tokens));
      else ss.removeItem(TOKENS_KEY);
      if (user) ss.setItem(USER_KEY, JSON.stringify(user));
      else ss.removeItem(USER_KEY);
      if (role) ss.setItem(ROLE_KEY, JSON.stringify(role));
      else ss.removeItem(ROLE_KEY);
    },
    [],
  );

  const clear = useCallback(() => {
    persist(null, null, null);
    setState({ status: "unauthenticated", user: null, role: null });
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
        // 새 토큰쌍 교체(user/role 유지) 후 재시도
        const user = readStorage<AuthUser>(USER_KEY);
        tokensRef.current = refreshed;
        persist(refreshed, user, readStorage<Role>(ROLE_KEY));
        res = await rawAuthedFetch(path, refreshed.access_token, init);
      }
      if (!res.ok) throw await toApiError(res);
      return parseResponse<T>(res);
    },
    [clear, persist],
  );

  // role 해석: GET /admin/employees 의 self-row(user.id 매칭) role 을 읽는다.
  //  - 200 + self 발견 → 그 role(admin|member)
  //  - 403(member, 디렉토리 admin 게이트) → member
  //  - 그 외(502/503/네트워크) → null(권한 기능 비노출, 안전측)
  // 401→refresh 는 authedFetch 가 처리하므로 401 을 member 로 오판하지 않음.
  const resolveRole = useCallback(
    async (user: AuthUser): Promise<Role | null> => {
      try {
        const rows = await authedFetch<Employee[]>("/admin/employees");
        const self = rows.find((e) => e.id === user.id);
        return self?.role ?? "member";
      } catch (err) {
        if (err instanceof ApiError && err.status === 403) return "member";
        return null;
      }
    },
    [authedFetch],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      const data = await apiFetch<{ data: LoginData }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      const { user, ...tokens } = data.data; // ⚠ data 엔벨로프(mediness passthrough)
      persist(tokens, user, null);
      setState({ status: "authenticated", user, role: null });
      // role 은 후속 해석(로그인 차단하지 않음)
      const role = await resolveRole(user);
      persist(tokens, user, role);
      setState({ status: "authenticated", user, role });
    },
    [persist, resolveRole],
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
    const role = readStorage<Role>(ROLE_KEY);
    if (tokens && user) {
      tokensRef.current = tokens;
      // 마운트 시 sessionStorage(외부 스토어) 재수화 — 클라 전용·하이드레이션 후 1회 전이라
      // 동기 setState 가 불가피(lazy init 은 SSR↔클라 불일치 유발). 캐스케이드 1회 허용.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setState({ status: "authenticated", user, role });
      // 캐시된 role 을 백그라운드 재검증(만료/권한 변동 반영) — await 이후라 규칙 무관
      resolveRole(user).then((r) => {
        persist(tokensRef.current, user, r);
        setState((s) =>
          s.status === "authenticated" ? { ...s, role: r } : s,
        );
      });
    } else {
      setState({ status: "unauthenticated", user: null, role: null });
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
