"""employee role/position NOT NULL + server_default (origin)

Revision ID: 3f7a1c9e2b04
Revises: 05df3bab4b2e
Create Date: 2026-06-22 10:00:00.000000

ERP-WP-007 P1. employee.role·position 을 origin 소유 제약으로 정정 — nullable(미러 잔재) →
NOT NULL + server_default(role='member', position='staff'). 기존 NULL 행은 flip 전 backfill
(server_default 는 기존 행을 채우지 않으므로). Text 유지(enum 승격 아님). 기존 직원 행은 보존.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f7a1c9e2b04'
down_revision: Union[str, None] = '05df3bab4b2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 기존 NULL 행 backfill (server_default 는 기존 행 미적용 — flip 전 선행 필수)
    op.execute("UPDATE employee SET role = 'member' WHERE role IS NULL")
    op.execute("UPDATE employee SET position = 'staff' WHERE position IS NULL")

    op.alter_column(
        'employee', 'role',
        existing_type=sa.Text(),
        nullable=False,
        server_default='member',
    )
    op.alter_column(
        'employee', 'position',
        existing_type=sa.Text(),
        nullable=False,
        server_default='staff',
    )


def downgrade() -> None:
    op.alter_column(
        'employee', 'position',
        existing_type=sa.Text(),
        nullable=True,
        server_default=None,
    )
    op.alter_column(
        'employee', 'role',
        existing_type=sa.Text(),
        nullable=True,
        server_default=None,
    )
