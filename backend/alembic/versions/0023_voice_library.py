"""Voice library tables and dub voice binding snapshots."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0023_voice_library"
down_revision = "0022_remove_translation_review"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return name in sa.inspect(bind).get_table_names()


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {item["name"] for item in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if not _has_table("voice_characters"):
        op.create_table(
            "voice_characters",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("media_item_id", sa.Integer(), sa.ForeignKey("media_items.id"), nullable=False),
            sa.Column("target_language", sa.String(length=32), nullable=False),
            sa.Column("character_key", sa.String(length=128), nullable=False),
            sa.Column("display_name", sa.String(length=128), nullable=False),
            sa.Column("approval_status", sa.String(length=32), nullable=False, server_default="draft"),
            sa.Column("approved_voice_model", sa.String(length=256), nullable=True),
            sa.Column("synthesis_params_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("pronunciation_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("embedding_centroid_json", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("similarity_threshold", sa.Float(), nullable=False, server_default="0.72"),
            sa.Column("cloud_voice_id", sa.String(length=128), nullable=True),
            sa.Column("cloud_rights_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint(
                "media_item_id",
                "target_language",
                "character_key",
                name="uq_voice_characters_owner_language_key",
            ),
        )
        op.create_index("ix_voice_characters_media_item_id", "voice_characters", ["media_item_id"])
        op.create_index("ix_voice_characters_target_language", "voice_characters", ["target_language"])
        op.create_index("ix_voice_characters_approval_status", "voice_characters", ["approval_status"])

    if not _has_table("voice_references"):
        op.create_table(
            "voice_references",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("character_id", sa.Integer(), sa.ForeignKey("voice_characters.id"), nullable=False),
            sa.Column("variant", sa.String(length=32), nullable=False, server_default="neutral"),
            sa.Column("relative_path", sa.String(length=512), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("duration_s", sa.Float(), nullable=True),
            sa.Column("source_cue_indices", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_canonical", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("embedding_json", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_voice_references_character_id", "voice_references", ["character_id"])
        op.create_index("ix_voice_references_sha256", "voice_references", ["sha256"])

    if not _has_table("episode_voice_casts"):
        op.create_table(
            "episode_voice_casts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("media_item_id", sa.Integer(), sa.ForeignKey("media_items.id"), nullable=False),
            sa.Column("target_language", sa.String(length=32), nullable=False),
            sa.Column("cue_index", sa.Integer(), nullable=False),
            sa.Column("character_id", sa.Integer(), sa.ForeignKey("voice_characters.id"), nullable=True),
            sa.Column("speaker_label", sa.String(length=128), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="unresolved"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint(
                "media_item_id",
                "target_language",
                "cue_index",
                name="uq_episode_voice_casts_media_language_cue",
            ),
        )
        op.create_index("ix_episode_voice_casts_media_item_id", "episode_voice_casts", ["media_item_id"])
        op.create_index("ix_episode_voice_casts_target_language", "episode_voice_casts", ["target_language"])
        op.create_index("ix_episode_voice_casts_character_id", "episode_voice_casts", ["character_id"])
        op.create_index("ix_episode_voice_casts_status", "episode_voice_casts", ["status"])

    if _has_table("jobs") and not _has_column("jobs", "dub_voice_bindings"):
        with op.batch_alter_table("jobs") as batch:
            batch.add_column(sa.Column("dub_voice_bindings", sa.JSON(), nullable=False, server_default="{}"))

    if _has_table("settings"):
        with op.batch_alter_table("settings") as batch:
            if not _has_column("settings", "dub_cloud_fallback_enabled"):
                batch.add_column(
                    sa.Column(
                        "dub_cloud_fallback_enabled",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.false(),
                    )
                )
            if not _has_column("settings", "elevenlabs_api_key_encrypted"):
                batch.add_column(sa.Column("elevenlabs_api_key_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_table("settings"):
        with op.batch_alter_table("settings") as batch:
            if _has_column("settings", "elevenlabs_api_key_encrypted"):
                batch.drop_column("elevenlabs_api_key_encrypted")
            if _has_column("settings", "dub_cloud_fallback_enabled"):
                batch.drop_column("dub_cloud_fallback_enabled")
    if _has_table("jobs") and _has_column("jobs", "dub_voice_bindings"):
        with op.batch_alter_table("jobs") as batch:
            batch.drop_column("dub_voice_bindings")
    if _has_table("episode_voice_casts"):
        op.drop_table("episode_voice_casts")
    if _has_table("voice_references"):
        op.drop_table("voice_references")
    if _has_table("voice_characters"):
        op.drop_table("voice_characters")
