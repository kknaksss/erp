// BE REST 클라이언트 — 얇은 fetch 래퍼.
// base URL = ERP back(FastAPI, 로컬 :28082).
// 토큰 주입·401 리프레시 인터셉트는 lib/auth.tsx(AuthProvider)가 authedFetch 로 소유.
// 여기는 공용(비인증) 호출 + 에러 정규화만 담당.

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:28082";

export class ApiError extends Error {
  constructor(
    public status: number,
    public errorCode: string,
    message: string,
  ) {
    super(message);
  }
}

// 에러 body 2종 정규화(SPEC-001 §3 환류):
//   ERP-native      → { error_code, message, detail }   (422·로컬401·502·503)
//   mediness passthrough → { error: { code, message } }  (401 인증실패·chain revoke)
export async function toApiError(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => ({}) as Record<string, unknown>);
  // mediness 형식 우선 탐지
  const med = (body as { error?: { code?: string; message?: string } }).error;
  if (med && typeof med === "object") {
    return new ApiError(
      res.status,
      med.code ?? "UNKNOWN",
      med.message ?? res.statusText,
    );
  }
  const erp = body as { error_code?: string; message?: string };
  return new ApiError(
    res.status,
    erp.error_code ?? "UNKNOWN",
    erp.message ?? res.statusText,
  );
}

async function parse<T>(res: Response): Promise<T> {
  if (res.status === 204) return undefined as T; // No Content (logout 등)
  return res.json() as Promise<T>;
}

// 비인증 호출(login/refresh). 토큰을 붙이지 않는다.
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) throw await toApiError(res);
  return parse<T>(res);
}

// 인증 호출의 저수준 1회 시도(토큰은 호출자가 헤더로 주입). 401 재시도는 AuthProvider 소관.
export async function rawAuthedFetch(
  path: string,
  token: string,
  init?: RequestInit,
): Promise<Response> {
  return fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...init?.headers,
    },
  });
}

export { parse as parseResponse };
