"""인증 프록시 요청 스키마 — ERP 자체 입력 검증(422).

응답은 mediness 응답을 verbatim passthrough 하므로 response 스키마는 두지 않는다
(순수 프록시 — SPEC-001 §3 "mediness 응답을 그대로 전달"). 검증 규칙은 SPEC-001 §Validation.
"""

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr  # 비어있지 않음 · 이메일 형식
    password: str = Field(min_length=1)  # 비어있지 않음(최소 1자)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)  # 비어있지 않음
