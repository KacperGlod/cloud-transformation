"""Keep the uploaded original separate from a normalized processing file."""
from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("clouds", sa.Column("processing_file", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("clouds", "processing_file")
