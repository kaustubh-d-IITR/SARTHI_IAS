"""Initial Studio schema: users, auth sessions, uploads, studio sessions, jobs, plan versions, renders, audit, workers.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-31
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("password_hash", sa.String(400), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_login_at", TS),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("user_agent", sa.String(300), nullable=False, server_default=""),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("ip", sa.String(64), nullable=False, server_default=""),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_login_attempts_email", "login_attempts", ["email"])
    op.create_index("ix_login_attempts_created_at", "login_attempts", ["created_at"])
    op.create_table(
        "uploads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("object_key", sa.String(300), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("declared_size", sa.Integer),
        sa.Column("size_bytes", sa.Integer),
        sa.Column("sha256", sa.String(64)),
        sa.Column("meta", sa.JSON, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_uploads_owner_id", "uploads", ["owner_id"])
    op.create_table(
        "studio_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("plan_version_id", sa.String(40)),
        sa.Column("record", sa.JSON, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_studio_sessions_owner_id", "studio_sessions", ["owner_id"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("studio_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("active_slot", sa.String(16)),
        sa.Column("idempotency_key", sa.String(100), unique=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="1"),
        sa.Column("lease_owner", sa.String(100)),
        sa.Column("lease_expires_at", TS),
        sa.Column("heartbeat_at", TS),
        sa.Column("last_error", sa.Text, nullable=False, server_default=""),
        sa.Column("failure_category", sa.String(40), nullable=False, server_default=""),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.UniqueConstraint("session_id", "active_slot", name="uq_jobs_one_active_per_slot"),
    )
    op.create_index("ix_jobs_session_id", "jobs", ["session_id"])
    op.create_index("ix_jobs_status_created", "jobs", ["status", "created_at"])
    op.create_table(
        "plan_versions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("studio_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("parent_version_id", sa.String(40)),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("validation_ok", sa.Boolean, nullable=False),
        sa.Column("title", sa.JSON),
        sa.Column("object_key", sa.String(300), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("session_id", "number", name="uq_plan_version_number"),
    )
    op.create_index("ix_plan_versions_session_id", "plan_versions", ["session_id"])
    op.create_table(
        "renders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("studio_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(36)),
        sa.Column("plan_version_id", sa.String(40)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("object_prefix", sa.String(300), nullable=False),
        sa.Column("details", sa.JSON, nullable=False),
        sa.Column("error", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("completed_at", TS),
    )
    op.create_index("ix_renders_session_id", "renders", ["session_id"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64)),
        sa.Column("session_id", sa.String(36)),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("detail", sa.JSON, nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_table(
        "workers",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("hostname", sa.String(200), nullable=False, server_default=""),
        sa.Column("version", sa.String(40), nullable=False, server_default=""),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("current_job_id", sa.String(36)),
    )


def downgrade() -> None:
    for name in ("workers", "audit_events", "renders", "plan_versions", "jobs", "studio_sessions", "uploads", "login_attempts", "auth_sessions", "users"):
        op.drop_table(name)
