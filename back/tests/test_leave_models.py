"""연차 도메인 모델·enum 검증 — WP-002 Phase 1.

스키마만(repo/service P2/P3). 검증 범위 = 모델 메타데이터 로드 + enum 멤버 값이 domains
정의와 일치(LeaveCategory 4·LeaveUnit 3·AmPm 2·GrantSource 3·GrantStatus 2·RequestStatus 5·
RequestChannel 2). DB 통합(왕복·pg_enum 조회)은 리포트 alembic 실행 결과 참조.
"""

from app.models import (
    LeaveAdjustment,
    LeaveAllocation,
    LeaveGrant,
    LeaveRequest,
)
from app.models.base import Base
from app.models.enums import (
    AmPm,
    GrantSource,
    GrantStatus,
    LeaveCategory,
    LeaveUnit,
    RequestChannel,
    RequestStatus,
)


def test_four_tables_registered() -> None:
    for model in (LeaveGrant, LeaveRequest, LeaveAllocation, LeaveAdjustment):
        assert model.__tablename__ in Base.metadata.tables


def test_enum_values_match_domains() -> None:
    """멤버명=영문 / value=domains 한글값 — 개수·값 정확 일치."""
    assert [m.value for m in LeaveCategory] == ["연차", "보상", "포상", "Off Day"]
    assert [m.value for m in LeaveUnit] == ["전일", "반차", "반반차"]
    assert [m.value for m in AmPm] == ["오전", "오후"]
    assert [m.value for m in GrantSource] == ["발생", "HR부여", "이월"]
    assert [m.value for m in GrantStatus] == ["active", "expired"]
    assert [m.value for m in RequestStatus] == ["신청됨", "승인됨", "반려됨", "취소요청됨", "취소됨"]
    assert [m.value for m in RequestChannel] == ["slack", "erp"]


def test_append_only_tables_have_no_updated_at() -> None:
    """leave_allocation·leave_adjustment = append-only → created_at 만(updated_at 없음)."""
    for model in (LeaveAllocation, LeaveAdjustment):
        cols = set(model.__table__.columns.keys())
        assert "created_at" in cols
        assert "updated_at" not in cols


def test_timestamped_tables_have_both() -> None:
    for model in (LeaveGrant, LeaveRequest):
        cols = set(model.__table__.columns.keys())
        assert {"created_at", "updated_at"} <= cols


def test_allocation_expiry_skip_column() -> None:
    """만료소멸 표시 컬럼 결정 = expired_at (restored_at 와 대칭)."""
    cols = LeaveAllocation.__table__.columns.keys()
    assert "expired_at" in cols
    assert "restored_at" in cols


def test_key_nullability() -> None:
    """domains §Schema nullable 정합 핵심 — 조건부 NN(am_pm 등)은 P2/P3 invariant 라 컬럼은 nullable."""
    g = LeaveGrant.__table__.columns
    assert g["expiry_date"].nullable and g["granted_by"].nullable
    assert not g["employee_id"].nullable and not g["amount"].nullable
    r = LeaveRequest.__table__.columns
    assert r["am_pm"].nullable and r["deleted_at"].nullable and r["change_group_id"].nullable
    assert not r["use_date"].nullable and not r["status"].nullable
    adj = LeaveAdjustment.__table__.columns
    assert not adj["adjusted_by"].nullable and not adj["delta"].nullable
