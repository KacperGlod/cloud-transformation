"""Add optional description and notes to cloud collections."""
from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cloud_collections", sa.Column("description", sa.Text(), nullable=False, server_default=""))
    op.add_column("cloud_collections", sa.Column("notes", sa.Text(), nullable=False, server_default=""))


def downgrade():
    op.drop_column("cloud_collections", "notes")
    op.drop_column("cloud_collections", "description")
