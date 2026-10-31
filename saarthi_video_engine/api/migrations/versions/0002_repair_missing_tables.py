"""Create any 0001 table or index that is missing. Never drops, alters, or touches rows.

A database stamped at 0001_initial without all of its tables (for example the
production database where the worker found no "workers" table) is brought to the
full schema. On a complete database this does nothing.

Revision ID: 0002_repair_missing_tables
Revises: 0001_initial
Create Date: 2026-10-31
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0002_repair_missing_tables"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _initial():
    """migrations/initial_schema.py: the 0001 tables one at a time. 0001_initial.py itself is untouched."""
    spec = importlib.util.spec_from_file_location("studio_initial_schema", Path(__file__).resolve().parents[1] / "initial_schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    initial = _initial()
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    for name in initial.CREATORS:
        if name not in existing:
            initial.create_table_with_indexes(name)
            continue
        present = {index["name"] for index in inspector.get_indexes(name)}
        for index_name, columns in initial.INDEXES.get(name, []):
            if index_name not in present:
                op.create_index(index_name, name, columns)


def downgrade() -> None:
    """Nothing to undo: tables created here belong to 0001_initial and are dropped by its downgrade."""
