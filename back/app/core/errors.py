"""앱 표준 에러 — main.py 핸들러가 JSON 응답으로 변환."""


class AppError(Exception):
    """모든 앱 예외의 base."""

    error_code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "서버 오류가 발생했습니다"

    def __init__(self, message: str | None = None, detail: dict | None = None):
        super().__init__(message or self.message)
        self.message = message or self.message
        self.detail = detail


class NotFoundError(AppError):
    error_code = "NOT_FOUND"
    status_code = 404
    message = "대상을 찾을 수 없습니다"


class InvalidCredentialsError(AppError):
    error_code = "INVALID_CREDENTIALS"
    status_code = 401
    message = "ID 또는 비밀번호가 올바르지 않습니다"


class InvalidTokenError(AppError):
    error_code = "INVALID_TOKEN"
    status_code = 401
    message = "유효하지 않은 토큰입니다"


class ForbiddenError(AppError):
    error_code = "FORBIDDEN"
    status_code = 403
    message = "권한이 없습니다"


class ConflictError(AppError):
    error_code = "CONFLICT"
    status_code = 409
    message = "요청이 현재 상태와 충돌합니다"
