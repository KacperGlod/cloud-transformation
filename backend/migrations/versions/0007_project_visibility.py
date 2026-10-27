"""Add private/public visibility to saved viewer projects."""
from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("visibility", sa.String(16), nullable=False, server_default="private"))
        batch.create_check_constraint("ck_project_visibility", "visibility IN ('private', 'public')")


def downgrade():
    with op.batch_alter_table("projects") as batch:
        batch.drop_constraint("ck_project_visibility", type_="check")
        batch.drop_column("visibility")
