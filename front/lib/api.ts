// BE REST 클라이언트 — 얇은 fetch 래퍼.
// base URL = ERP back(FastAPI, 로컬 :28082). 토큰 보관 위치·401 인터셉트는 P2/P4.

const API_BASE_URL =
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

// P1 골격 — 토큰 주입 자리만. 실제 토큰 보관(메모리/스토리지)·갱신은 P2/P4 결정.
function authHeader(): Record<string, string> {
  // TODO(P4): 인증 토큰 첨부 (Authorization: Bearer <access>)
  return {};
}

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeader(),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(
      res.status,
      body.error_code ?? "UNKNOWN",
      body.message ?? res.statusText,
    );
  }
  // 204 No Content 등 본문 없는 응답 대비
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}
