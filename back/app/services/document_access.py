"""문서관리 스페이스 멤버십 게이트 — WP-006 Phase 2 (SPEC-006 §Functional Rule).

접근 = 스페이스 멤버십. **신규 권한 모델 발명 금지** — WP-001 `employee`(department/id/role) 재사용.
`require_hr`(department=="hr") 게이트가 **아니다** — hr 부서 특권이 아니라 스페이스 멤버십 판정.
`get_current_employee` 위에 employee.department/id 만 읽는 순수 판정 헬퍼를 얹는다(role 미참조).

- 부서스페이스(`type=department`): 같은 부서원(`employee.department == space.department`).
  **admin 도 본인 부서스페이스만** — admin cross-department 특권 없음(사용자 결정 2026-06-20,
  PLAN-003-T-005). 부서스페이스 접근에서 admin 은 일반 멤버와 동일(`department` 일치만).
- 개인스페이스(`type=personal`): 본인(`employee.id == space.owner_id`).
  **admin override 없음** — 타인 개인스페이스 열람 불가(프라이버시).
- 멤버십 밖 = 403(ForbiddenError).
"""

from app.core.errors import ForbiddenError
from app.models.document import Space
from app.models.employee import Employee
from app.models.enums import SpaceType


def is_space_member(employee: Employee, space: Space) -> bool:
    """employee.department/id 만 읽어 스페이스 접근 가능 여부 판정(순수 함수).

    admin 특권 없음(role 미참조) — 부서스페이스는 부서 일치, 개인스페이스는 본인.
    """
    if space.type == SpaceType.PERSONAL:
        # 개인스페이스 = 본인만(admin override 없음)
        return space.owner_id == employee.id
    # 부서스페이스 = 같은 부서원만(admin cross-department 특권 없음). NULL 부서는 NULL 스페이스에 매칭 금지.
    return employee.department is not None and employee.department == space.department


def require_space_member(employee: Employee, space: Space) -> None:
    """멤버 아니면 403. 라우터 dep 가 아니라 service 가 space 적재 후 호출(resource-level)."""
    if not is_space_member(employee, space):
        raise ForbiddenError()
