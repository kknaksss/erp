"""add employee hr fields + employment_type enum

Revision ID: d5da444dcd8f
Revises: 7dbccc7f2ca5
Create Date: 2026-06-18 16:55:20.064793

T-015. employee 에 ERP 소유 HR 필드 6종 추가(연차 대장 마이그레이션 준비). enum
`employment_type`(값 영문 코드)는 leave enum 패턴대로 명시 CREATE/DROP(create_type=False) —
ADD COLUMN 이 타입을 재생성하지 않게. 전부 nullable(기존 행·동기 신규가 값 없이 존재). 컬럼만 추가.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd5da444dcd8f'
down_revision: Union[str, None] = '7dbccc7f2ca5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# PG native enum — create_type=False 라 add_column 이 재생성 안 함(명시 제어, leave enum 선례).
employment_type = postgresql.ENUM(
    'fulltime', 'contract', 'parttime', name='employment_type', create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    employment_type.create(bind, checkfirst=False)

    op.add_column('employee', sa.Column('hire_date', sa.Date(), nullable=True))
    op.add_column('employee', sa.Column('resigned_at', sa.Date(), nullable=True))
    op.add_column('employee', sa.Column('employment_type', employment_type, nullable=True))
    op.add_column('employee', sa.Column('phone', sa.Text(), nullable=True))
    op.add_column('employee', sa.Column('birth_date', sa.Date(), nullable=True))
    op.add_column('employee', sa.Column('corporate_card_no', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('employee', 'corporate_card_no')
    op.drop_column('employee', 'birth_date')
    op.drop_column('employee', 'employment_type')
    op.drop_column('employee', 'phone')
    op.drop_column('employee', 'resigned_at')
    op.drop_column('employee', 'hire_date')

    bind = op.get_bind()
    employment_type.drop(bind, checkfirst=False)
