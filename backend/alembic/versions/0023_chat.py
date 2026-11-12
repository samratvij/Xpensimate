"""chat assistant: threads, messages and per-person memories

Revision ID: 0023
Revises: 0022
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _id() -> sa.Column:
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "chat_threads",
        _id(),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        _created_at(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_chat_threads_owner", "chat_threads", ["owner", "updated_at"])
    op.create_table(
        "chat_messages",
        _id(),
        sa.Column("thread_id", UUID(as_uuid=True), sa.ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("text", sa.Text, nullable=True),
        sa.Column("actions", JSONB, nullable=True),
        sa.Column("sources", JSONB, nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        _created_at(),
        sa.UniqueConstraint("thread_id", "seq", name="uq_chat_messages_seq"),
    )
    op.create_index("idx_chat_messages_questions", "chat_messages", ["kind", "created_at"])
    op.create_table(
        "chat_memories",
        _id(),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("fact", sa.Text, nullable=False),
        _created_at(),
    )
    op.create_index("ix_chat_memories_owner", "chat_memories", ["owner"])


def downgrade() -> None:
    op.drop_table("chat_memories")
    op.drop_table("chat_messages")
    op.drop_table("chat_threads")
