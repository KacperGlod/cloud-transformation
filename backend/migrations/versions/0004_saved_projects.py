"""Store private, single-cloud viewer projects."""
from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("cloud_id", sa.Uuid(), sa.ForeignKey("clouds.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("scene_state", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_project_revision"),
    )
    op.create_index("ix_project_owner_updated", "projects", ["owner_id", "updated_at"])
    op.create_index("ix_projects_cloud_id", "projects", ["cloud_id"])


def downgrade():
    op.drop_index("ix_projects_cloud_id", table_name="projects")
    op.drop_index("ix_project_owner_updated", table_name="projects")
    op.drop_table("projects")
