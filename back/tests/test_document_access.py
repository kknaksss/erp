"""스페이스 멤버십 게이트 검증 — WP-006 Phase 2 (SPEC-006 §Functional Rule). 순수 함수.

부서스페이스 = 부서원만(admin cross-department 특권 없음, PLAN-003-T-005) /
개인스페이스 = 본인(admin override 없음). NULL 부서는 NULL 스페이스에 매칭 금지.
"""

import uuid

from app.models.document import Space
from app.models.employee import Employee
from app.models.enums import SpaceType
from app.services.document_access import is_space_member


def _emp(*, department=None, role="member") -> Employee:
    return Employee(id=uuid.uuid4(), email="e@x.com", name="e", role=role, active=True,
                    department=department)


def _dept_space(department: str) -> Space:
    return Space(id=uuid.uuid4(), type=SpaceType.DEPARTMENT, name=department, department=department)


def _personal_space(owner_id) -> Space:
    return Space(id=uuid.uuid4(), type=SpaceType.PERSONAL, name="me", owner_id=owner_id)


def test_department_member_can_access_own_department_space() -> None:
    emp = _emp(department="dev")
    assert is_space_member(emp, _dept_space("dev")) is True


def test_department_member_cannot_access_other_department_space() -> None:
    emp = _emp(department="dev")
    assert is_space_member(emp, _dept_space("hr")) is False


def test_admin_cannot_access_other_department_space() -> None:
    """admin 도 본인 부서스페이스만 — cross-department 특권 없음(PLAN-003-T-005)."""
    admin = _emp(department="dev", role="admin")
    assert is_space_member(admin, _dept_space("hr")) is False  # 타 부서 부서스페이스 차단


def test_admin_can_access_own_department_space() -> None:
    """admin 도 본인 부서 부서스페이스는 접근(일반 멤버와 동일)."""
    admin = _emp(department="dev", role="admin")
    assert is_space_member(admin, _dept_space("dev")) is True


def test_owner_can_access_own_personal_space() -> None:
    emp = _emp(department="dev")
    assert is_space_member(emp, _personal_space(emp.id)) is True


def test_admin_has_no_personal_space_override() -> None:
    """admin 도 타인 개인스페이스 접근 불가(프라이버시)."""
    admin = _emp(department="dev", role="admin")
    other = _emp(department="hr")
    assert is_space_member(admin, _personal_space(other.id)) is False


def test_member_cannot_access_other_personal_space() -> None:
    emp = _emp(department="dev")
    other = _emp(department="dev")
    assert is_space_member(emp, _personal_space(other.id)) is False


def test_null_department_does_not_match_null_space() -> None:
    """부서 미지정(NULL) 직원이 NULL department 스페이스(존재하지 않아야 하지만 방어)에 매칭 금지."""
    emp = _emp(department=None)
    null_space = Space(id=uuid.uuid4(), type=SpaceType.DEPARTMENT, name="x", department=None)
    assert is_space_member(emp, null_space) is False
