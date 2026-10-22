"""Add optional, private cloud collections and many-to-many membership."""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cloud_collections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_collection_owner_created", "cloud_collections", ["owner_id", "created_at"])
    op.create_table(
        "collection_clouds",
        sa.Column("collection_id", sa.Uuid(), sa.ForeignKey("cloud_collections.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("cloud_id", sa.Uuid(), sa.ForeignKey("clouds.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_collection_clouds_cloud_id", "collection_clouds", ["cloud_id"])


def downgrade():
    op.drop_index("ix_collection_clouds_cloud_id", table_name="collection_clouds")
    op.drop_table("collection_clouds")
    op.drop_index("ix_collection_owner_created", table_name="cloud_collections")
    op.drop_table("cloud_collections")
