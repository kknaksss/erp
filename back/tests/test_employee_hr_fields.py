"""employee HR 필드 6종 + employment_type enum 테스트. PLAN-002-T-015.

- 모델: 6 컬럼 round-trip(insert→read) + EmploymentType enum 값(영문 코드).
- 보존: upsert_mirror(미러 동기)가 신규 6 필드를 덮어쓰지 않음(position/department 와 동일 취급).

모델+migration 만(시드·연차 로직 없음). 실제 erp DB·트랜잭션-롤백(d5da444dcd8f 적용 전제).
"""

import uuid
from datetime import date

import pytest

from app.models.employee import Employee
from app.models.enums import EmploymentType
from app.repositories import employee as employee_repo


def _row(eid: str, *, name: str = "홍길동", role: str = "member", active: bool = True,
         email: str | None = None, **extra) -> dict:
    return {"id": eid, "email": email or f"{eid[:8]}@x.com", "name": name,
            "role": role, "active": active, **extra}


# ---- enum 값 (영문 코드) --------------------------------------------------


def test_employment_type_values() -> None:
    assert [e.value for e in EmploymentType] == ["fulltime", "contract", "parttime"]


# ---- 모델 6 컬럼 round-trip (실제 DB · 롤백) ------------------------------


@pytest.mark.asyncio
async def test_employee_hr_fields_persist(db_session) -> None:
    eid = uuid.uuid4()
    emp = Employee(
        id=eid, email=f"{eid.hex[:8]}@x.com", name="emp", role="member", active=True,
        hire_date=date(2024, 3, 2), resigned_at=None,
        employment_type=EmploymentType.CONTRACT, phone="010-1234-5678",
        birth_date=date(1995, 7, 15), corporate_card_no="1234-5678",
    )
    db_session.add(emp)
    await db_session.flush()
    db_session.expire(emp)  # DB 에서 다시 로드

    got = await employee_repo.get_by_id(db_session, eid)
    assert got.hire_date == date(2024, 3, 2)
    assert got.resigned_at is None
    assert got.employment_type == EmploymentType.CONTRACT
    assert got.phone == "010-1234-5678"
    assert got.birth_date == date(1995, 7, 15)
    assert got.corporate_card_no == "1234-5678"


@pytest.mark.asyncio
async def test_employee_hr_fields_default_null(db_session) -> None:
    """동기 신규(미러 4필드만)는 HR 6필드 NULL 로 존재 가능(전부 nullable)."""
    eid = str(uuid.uuid4())
    await employee_repo.upsert_mirror(db_session, [_row(eid)])
    emp = await employee_repo.get_by_id(db_session, uuid.UUID(eid))
    assert emp.hire_date is None and emp.employment_type is None
    assert emp.phone is None and emp.birth_date is None
    assert emp.resigned_at is None and emp.corporate_card_no is None


# ---- upsert_mirror 보존 (미러 동기에서 HR 필드 안 날아감) ------------------


@pytest.mark.asyncio
async def test_hr_fields_preserved_on_resync(db_session) -> None:
    eid = str(uuid.uuid4())
    await employee_repo.upsert_mirror(db_session, [_row(eid)])
    # ERP 가 HR 필드 입력(향후 HR 화면 — 여기선 직접 set)
    emp = await employee_repo.get_by_id(db_session, uuid.UUID(eid))
    emp.hire_date = date(2024, 1, 10)
    emp.employment_type = EmploymentType.FULLTIME
    emp.phone = "010-0000-1111"
    emp.corporate_card_no = "9999"
    await db_session.flush()

    # 재동기 — 미러 4필드만 갱신, HR 필드는 position/department 처럼 보존
    await employee_repo.upsert_mirror(db_session, [_row(eid, name="새이름", role="admin")])
    emp = await employee_repo.get_by_id(db_session, uuid.UUID(eid))
    assert emp.name == "새이름" and emp.role == "admin"        # 미러 갱신
    assert emp.hire_date == date(2024, 1, 10)                  # HR 필드 보존
    assert emp.employment_type == EmploymentType.FULLTIME
    assert emp.phone == "010-0000-1111"
    assert emp.corporate_card_no == "9999"
