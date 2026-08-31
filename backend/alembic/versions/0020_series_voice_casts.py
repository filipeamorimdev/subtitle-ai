"""Move existing episode voice-cast drafts to their series parent."""

from __future__ import annotations

from alembic import op


revision = "0020_series_voice_casts"
down_revision = "0019_jellyfin_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve a pre-existing series draft if both it and an episode draft use
    # the same language.  The latter remains harmless historical data; all
    # application lookups resolve through the series from now on.
    op.execute(
        """
        UPDATE voice_cast_drafts AS draft
        SET media_item_id = (
            SELECT episode.parent_media_id
            FROM media_items AS episode
            WHERE episode.id = draft.media_item_id
        )
        WHERE EXISTS (
            SELECT 1
            FROM media_items AS episode
            WHERE episode.id = draft.media_item_id
              AND episode.media_type = 'episode'
              AND episode.parent_media_id IS NOT NULL
        )
          AND NOT EXISTS (
            SELECT 1
            FROM voice_cast_drafts AS existing
            WHERE existing.media_item_id = (
                SELECT episode.parent_media_id
                FROM media_items AS episode
                WHERE episode.id = draft.media_item_id
            )
              AND existing.target_language = draft.target_language
        )
          AND NOT EXISTS (
            SELECT 1
            FROM voice_cast_drafts AS sibling
            JOIN media_items AS sibling_episode
              ON sibling_episode.id = sibling.media_item_id
            WHERE sibling_episode.media_type = 'episode'
              AND sibling_episode.parent_media_id = (
                  SELECT episode.parent_media_id
                  FROM media_items AS episode
                  WHERE episode.id = draft.media_item_id
              )
              AND sibling.target_language = draft.target_language
              AND sibling.id < draft.id
        )
        """
    )


def downgrade() -> None:
    # The former episode owner cannot be recovered reliably after sharing a
    # draft across a series, so this intentionally leaves the shared records.
    pass
