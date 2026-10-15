"""Users, sessions, clouds and durable processing jobs."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("login_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"])
    op.create_table("clouds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("visibility", sa.String(7), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("storage_key", sa.String(255), nullable=False, unique=True),
        sa.Column("source_file", sa.String(255)),
        sa.Column("entry_file", sa.String(40)),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("clouds.id", ondelete="SET NULL")),
        sa.Column("operation", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("visibility IN ('public', 'private')", name="ck_cloud_visibility"))
    op.create_index("ix_cloud_owner_created", "clouds", ["owner_id", "created_at"])
    op.create_table("processing_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cloud_id", sa.Uuid(), sa.ForeignKey("clouds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source_cloud_id", sa.Uuid(), sa.ForeignKey("clouds.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("upload_progress", sa.Integer()),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("log", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    for column in ("cloud_id", "owner_id"):
        op.create_index(f"ix_processing_jobs_{column}", "processing_jobs", [column])
    op.create_index("ix_job_status_created", "processing_jobs", ["status", "created_at"])


def downgrade():
    for table in ("processing_jobs", "login_sessions", "clouds", "users"):
        op.drop_table(table)
