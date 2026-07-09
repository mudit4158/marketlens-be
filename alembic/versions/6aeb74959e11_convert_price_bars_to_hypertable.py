"""convert price_bars to hypertable

Revision ID: 6aeb74959e11
Revises: cbd15fe28e34
Create Date: 2026-06-27 19:27:47.914027

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6aeb74959e11'
down_revision: Union[str, None] = 'cbd15fe28e34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute(
        "SELECT create_hypertable('price_bars', 'ts', migrate_data => true, if_not_exists => true)"
    )


def downgrade() -> None:
    # TimescaleDB has no direct "un-hypertable" operation; dropping/recreating the
    # plain table is the supported path, which isn't worth doing for a local-dev downgrade.
    pass
