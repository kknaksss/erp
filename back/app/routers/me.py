"""내정보(self profile) 라우터 — 로그인 본인 식별 + HR 여부.

GET /me — 로그인 누구나(admin/member 무관, 유효 토큰만). 본인 `employee` 를 토큰 `sub` 로 조회해
role·department·is_hr 노출. FE auth 컨텍스트가 role·department·isHr 를 해석하는 단일 소스 —
구 `/admin/employees` self-row hack(member-role HR 은 403 이라 자기 department 못 봄)을 대체한다.

admin↔사용자 2026-06-18 결정 2. 승인/반려 비즈 로직(WP-003 P2)은 손대지 않음.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import HR_DEPARTMENT, get_current_employee
from app.models.employee import Employee
from app.schemas.employee import MeOut

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeOut)
async def my_profile(
    employee: Annotated[Employee, Depends(get_current_employee)],
) -> MeOut:
    """본인 프로필 — `{id, email, name, role, department, is_hr}`. 토큰 없음/미러 없음 401.

    권한 게이트 없음(require access token 만) — member-role HR 직원도 본인 정보를 조회한다.
    `is_hr` = `department == "hr"`(deps.HR_DEPARTMENT 단일 소스).
    """
    return MeOut(
        id=employee.id,
        email=employee.email,
        name=employee.name,
        role=employee.role,
        department=employee.department,
        is_hr=employee.department == HR_DEPARTMENT,
    )
