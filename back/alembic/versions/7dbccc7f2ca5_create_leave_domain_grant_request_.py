"""create leave domain (grant/request/allocation/adjustment) + enums

Revision ID: 7dbccc7f2ca5
Revises: fa8812f3562b
Create Date: 2026-06-18 09:48:50.309047

WP-002 Phase 1. autogenerate scaffold 를 수동 보정:
- enum 7종을 4 테이블이 공유(leave_category=3 테이블 등) → 인라인 sa.Enum 은 중복 CREATE TYPE 로
  실패. PG ENUM 을 upgrade 시작에 한 번씩 명시 생성(create_type=False), downgrade 끝에 명시 DROP →
  테이블·enum 클린 왕복. 한글 enum 값·partial index 는 autogenerate 가 정확히 반영함(검수 완료).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7dbccc7f2ca5'
down_revision: Union[str, None] = 'fa8812f3562b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# PG native enum 타입 — create_type=False 라 create_table 이 재생성 안 함(명시 제어).
leave_category = postgresql.ENUM('연차', '보상', '포상', 'Off Day', name='leave_category', create_type=False)
leave_unit = postgresql.ENUM('전일', '반차', '반반차', name='leave_unit', create_type=False)
am_pm = postgresql.ENUM('오전', '오후', name='am_pm', create_type=False)
grant_source = postgresql.ENUM('발생', 'HR부여', '이월', name='grant_source', create_type=False)
grant_status = postgresql.ENUM('active', 'expired', name='grant_status', create_type=False)
request_status = postgresql.ENUM('신청됨', '승인됨', '반려됨', '취소요청됨', '취소됨', name='request_status', create_type=False)
request_channel = postgresql.ENUM('slack', 'erp', name='request_channel', create_type=False)

_ALL_ENUMS = [leave_category, leave_unit, am_pm, grant_source, grant_status, request_status, request_channel]


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in _ALL_ENUMS:
        enum_type.create(bind, checkfirst=False)

    op.create_table('leave_adjustment',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('category', leave_category, nullable=False),
    sa.Column('delta', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('adjusted_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['adjusted_by'], ['employee.id'], ),
    sa.ForeignKeyConstraint(['employee_id'], ['employee.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_leave_adjustment_emp_cat', 'leave_adjustment', ['employee_id', 'category'], unique=False)
    op.create_table('leave_grant',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('category', leave_category, nullable=False),
    sa.Column('amount', sa.Numeric(precision=4, scale=2), nullable=False),
    sa.Column('remaining', sa.Numeric(precision=4, scale=2), nullable=False),
    sa.Column('expiry_date', sa.Date(), nullable=True),
    sa.Column('source', grant_source, nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('granted_by', sa.UUID(), nullable=True),
    sa.Column('granted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', grant_status, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employee.id'], ),
    sa.ForeignKeyConstraint(['granted_by'], ['employee.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_leave_grant_active_remaining', 'leave_grant', ['employee_id', 'category'], unique=False, postgresql_where=sa.text("status = 'active' AND remaining > 0"))
    op.create_index('ix_leave_grant_emp_cat_expiry', 'leave_grant', ['employee_id', 'category', 'expiry_date'], unique=False)
    op.create_index('ix_leave_grant_emp_cat_status', 'leave_grant', ['employee_id', 'category', 'status'], unique=False)
    op.create_table('leave_request',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('category', leave_category, nullable=False),
    sa.Column('unit', leave_unit, nullable=False),
    sa.Column('amount', sa.Numeric(precision=3, scale=2), nullable=False),
    sa.Column('am_pm', am_pm, nullable=True),
    sa.Column('use_date', sa.Date(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('status', request_status, nullable=False),
    sa.Column('channel', request_channel, nullable=False),
    sa.Column('approved_by', sa.UUID(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reject_reason', sa.Text(), nullable=True),
    sa.Column('cancel_reason', sa.Text(), nullable=True),
    sa.Column('change_group_id', sa.UUID(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['approved_by'], ['employee.id'], ),
    sa.ForeignKeyConstraint(['employee_id'], ['employee.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_leave_request_emp_status', 'leave_request', ['employee_id', 'status'], unique=False)
    op.create_index('ix_leave_request_emp_use_date', 'leave_request', ['employee_id', 'use_date'], unique=False)
    op.create_index('ix_leave_request_pending', 'leave_request', ['status'], unique=False, postgresql_where=sa.text("status IN ('신청됨', '취소요청됨')"))
    op.create_table('leave_allocation',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('request_id', sa.UUID(), nullable=False),
    sa.Column('grant_id', sa.UUID(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=3, scale=2), nullable=False),
    sa.Column('restored_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['grant_id'], ['leave_grant.id'], ),
    sa.ForeignKeyConstraint(['request_id'], ['leave_request.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_leave_allocation_grant', 'leave_allocation', ['grant_id'], unique=False)
    op.create_index('ix_leave_allocation_request', 'leave_allocation', ['request_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_leave_allocation_request', table_name='leave_allocation')
    op.drop_index('ix_leave_allocation_grant', table_name='leave_allocation')
    op.drop_table('leave_allocation')
    op.drop_index('ix_leave_request_pending', table_name='leave_request', postgresql_where=sa.text("status IN ('신청됨', '취소요청됨')"))
    op.drop_index('ix_leave_request_emp_use_date', table_name='leave_request')
    op.drop_index('ix_leave_request_emp_status', table_name='leave_request')
    op.drop_table('leave_request')
    op.drop_index('ix_leave_grant_emp_cat_status', table_name='leave_grant')
    op.drop_index('ix_leave_grant_emp_cat_expiry', table_name='leave_grant')
    op.drop_index('ix_leave_grant_active_remaining', table_name='leave_grant', postgresql_where=sa.text("status = 'active' AND remaining > 0"))
    op.drop_table('leave_grant')
    op.drop_index('ix_leave_adjustment_emp_cat', table_name='leave_adjustment')
    op.drop_table('leave_adjustment')

    bind = op.get_bind()
    for enum_type in reversed(_ALL_ENUMS):
        enum_type.drop(bind, checkfirst=False)
