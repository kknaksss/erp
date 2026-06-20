"""연차 도메인 enum 7종 — domains 정의(한글 값) 그대로.

PG native enum 사용. **Python 멤버명 = 영문 / value = domains 한글값**.
- SQLAlchemy `Enum(values_callable=...)` 로 PG 에 저장되는 값을 멤버명(영문)이 아닌
  value(한글) 로 강제한다(기본은 name 저장 → 한글 손실).
- `create_type=False` + 4 테이블 공유 단일 인스턴스 → migration 이 CREATE/DROP TYPE 를
  명시 제어(중복 CREATE TYPE 방지). 정본 = 40-architecture/domains/leave_*.md §Enum.
"""

import enum

from sqlalchemy import Enum as SAEnum


class LeaveCategory(str, enum.Enum):
    """연차 종류 — 4 독립·교환 불가(leave_grant §Enum)."""

    ANNUAL = "연차"
    COMP = "보상"
    REWARD = "포상"
    OFF_DAY = "Off Day"


class LeaveUnit(str, enum.Enum):
    """사용 단위 — amount 매핑(전일 1.0 / 반차 0.5 / 반반차 0.25)."""

    FULL = "전일"
    HALF = "반차"
    QUARTER = "반반차"


class AmPm(str, enum.Enum):
    """반차·반반차 오전/오후."""

    AM = "오전"
    PM = "오후"


class GrantSource(str, enum.Enum):
    """부여 출처(leave_grant §Enum)."""

    ACCRUAL = "발생"
    HR_GRANT = "HR부여"
    CARRYOVER = "이월"


class GrantStatus(str, enum.Enum):
    """lot 상태 — expired 는 잔여 합산 제외."""

    ACTIVE = "active"
    EXPIRED = "expired"


class RequestStatus(str, enum.Enum):
    """신청 상태(기본 3 = SPEC-003 / 취소 2 = SPEC-005)."""

    REQUESTED = "신청됨"
    APPROVED = "승인됨"
    REJECTED = "반려됨"
    CANCEL_REQUESTED = "취소요청됨"
    CANCELLED = "취소됨"


class RequestChannel(str, enum.Enum):
    """신청 채널."""

    SLACK = "slack"
    ERP = "erp"


class EmploymentType(str, enum.Enum):
    """채용구분(employee) — **값은 영문 코드**(department 'hr' 영문화 선례 정합, 표시 라벨은 FE)."""

    FULLTIME = "fulltime"  # 정규직
    CONTRACT = "contract"  # 계약직
    PARTTIME = "parttime"  # 알바


class SpaceType(str, enum.Enum):
    """문서관리 스페이스 타입(SPEC-006 §Lifecycle, 2값) — **값은 영문**(외부 계약 그대로).

    부서스페이스(부서별)·개인스페이스(직원별). 멤버십 판정 단위.
    """

    DEPARTMENT = "department"
    PERSONAL = "personal"


class DocumentType(str, enum.Enum):
    """문서 타입(SPEC-006 §Lifecycle, 2값) — **값은 영문**. word=.docx / excel=.xlsx."""

    WORD = "word"
    EXCEL = "excel"


def _pg_enum(py_enum: type[enum.Enum], name: str) -> SAEnum:
    """PG native enum 컬럼 타입 — value(한글) 저장, 타입 생성은 migration 위임."""
    return SAEnum(
        py_enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        native_enum=True,
        create_type=False,
    )


# 4 테이블 공유 단일 인스턴스 (migration 이 명시 CREATE/DROP).
leave_category_enum = _pg_enum(LeaveCategory, "leave_category")
leave_unit_enum = _pg_enum(LeaveUnit, "leave_unit")
am_pm_enum = _pg_enum(AmPm, "am_pm")
grant_source_enum = _pg_enum(GrantSource, "grant_source")
grant_status_enum = _pg_enum(GrantStatus, "grant_status")
request_status_enum = _pg_enum(RequestStatus, "request_status")
request_channel_enum = _pg_enum(RequestChannel, "request_channel")
employment_type_enum = _pg_enum(EmploymentType, "employment_type")

# 문서관리 도메인 enum (WP-006 Phase 1) — 값 영문. migration 이 명시 CREATE/DROP.
space_type_enum = _pg_enum(SpaceType, "space_type")
document_type_enum = _pg_enum(DocumentType, "document_type")


# migration 의 CREATE/DROP TYPE 순회용 (이름, 값목록).
ALL_PG_ENUMS: list[tuple[str, list[str]]] = [
    ("leave_category", [m.value for m in LeaveCategory]),
    ("leave_unit", [m.value for m in LeaveUnit]),
    ("am_pm", [m.value for m in AmPm]),
    ("grant_source", [m.value for m in GrantSource]),
    ("grant_status", [m.value for m in GrantStatus]),
    ("request_status", [m.value for m in RequestStatus]),
    ("request_channel", [m.value for m in RequestChannel]),
    ("employment_type", [m.value for m in EmploymentType]),
]
