"""create document domain (space/folder/document/version) + enums

Revision ID: 05df3bab4b2e
Revises: d5da444dcd8f
Create Date: 2026-06-20 15:51:16.745962

WP-006 Phase 1. 문서관리 4 테이블(space/folder/document/version) + enum 2종(값 영문).
PG native enum 은 leave/employment_type 선례대로 명시 CREATE(upgrade 시작)/DROP(downgrade 끝)
— `create_type=False` 라 create_table 이 타입을 재생성하지 않게 명시 제어(중복 CREATE 방지 +
downgrade 가 타입을 DROP 해 up/down/up roundtrip 성립). 바이너리는 fs(version.storage_path).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '05df3bab4b2e'
down_revision: Union[str, None] = 'd5da444dcd8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# PG native enum — create_type=False 라 create_table 이 재생성 안 함(명시 제어, 값 영문).
space_type = postgresql.ENUM('department', 'personal', name='space_type', create_type=False)
document_type = postgresql.ENUM('word', 'excel', name='document_type', create_type=False)

_ALL_ENUMS = [space_type, document_type]


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in _ALL_ENUMS:
        enum_type.create(bind, checkfirst=False)

    op.create_table('space',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('type', space_type, nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('department', sa.Text(), nullable=True),
    sa.Column('owner_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['employee.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('department', name='uq_space_department'),
    sa.UniqueConstraint('owner_id', name='uq_space_owner')
    )
    op.create_table('folder',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('space_id', sa.UUID(), nullable=False),
    sa.Column('parent_id', sa.UUID(), nullable=True),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['folder.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['space_id'], ['space.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_folder_space_parent', 'folder', ['space_id', 'parent_id'], unique=False)
    op.create_table('document',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('space_id', sa.UUID(), nullable=False),
    sa.Column('folder_id', sa.UUID(), nullable=True),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('type', document_type, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['folder_id'], ['folder.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['space_id'], ['space.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_document_space_folder', 'document', ['space_id', 'folder_id'], unique=False)
    op.create_table('version',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('version_no', sa.Integer(), nullable=False),
    sa.Column('ext', sa.Text(), nullable=False),
    sa.Column('storage_path', sa.Text(), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'version_no', name='uq_version_document_no')
    )
    op.create_index('ix_version_document', 'version', ['document_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_version_document', table_name='version')
    op.drop_table('version')
    op.drop_index('ix_document_space_folder', table_name='document')
    op.drop_table('document')
    op.drop_index('ix_folder_space_parent', table_name='folder')
    op.drop_table('folder')
    op.drop_table('space')

    bind = op.get_bind()
    for enum_type in _ALL_ENUMS:
        enum_type.drop(bind, checkfirst=False)
