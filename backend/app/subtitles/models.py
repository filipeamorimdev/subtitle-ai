"""Internal subtitle models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SubtitleBlock:
    index: int
    start: str
    end: str
    text: str
    original_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubtitleDocument:
    format: str
    encoding: str
    blocks: list[SubtitleBlock]

    def clone_structure(self) -> SubtitleDocument:
        return SubtitleDocument(
            format=self.format,
            encoding=self.encoding,
            blocks=[
                SubtitleBlock(
                    index=b.index,
                    start=b.start,
                    end=b.end,
                    text=b.text,
                    original_text=b.original_text,
                    metadata=dict(b.metadata),
                )
                for b in self.blocks
            ],
        )
