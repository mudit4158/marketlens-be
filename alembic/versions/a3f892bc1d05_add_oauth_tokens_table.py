"""add oauth_tokens table

Revision ID: a3f892bc1d05
Revises: 6aeb74959e11
Create Date: 2026-07-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a3f892bc1d05'
down_revision: Union[str, None] = '6aeb74959e11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'oauth_tokens',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('access_token', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider'),
    )
    op.create_index('ix_oauth_tokens_provider', 'oauth_tokens', ['provider'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_oauth_tokens_provider', table_name='oauth_tokens')
    op.drop_table('oauth_tokens')
