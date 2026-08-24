"""Manual smoke test for isolated audio separation.

Examples::

    python -m app.localization.audio --input /media/movie.mkv --duration 45
    python -m app.localization.audio --input /media/movie.mkv --output-dir /config/audio-separation/smoke
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.core.config import get_app_config
from app.core.logging import setup_logging
from app.localization.audio.models import AudioSeparationError
from app.localization.audio.separation import AudioSeparationService


def _format_bytes(size: int | None) -> str:
    if not size:
        return "0 B"
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"


def _print_stem(title: str, artifact) -> None:
    path = Path(artifact.path or "")
    size = artifact.metadata.get("size")
    if size is None and path.is_file():
        size = path.stat().st_size
    print(f"{title}:")
    print(f"  path      {artifact.path}")
    print(f"  duration  {artifact.duration:.3f}s" if artifact.duration is not None else "  duration  unknown")
    print(f"  size      {_format_bytes(size)}")
    print(f"  rate      {artifact.sample_rate} Hz  channels={artifact.channels}")


async def _run(args: argparse.Namespace) -> int:
    setup_logging(get_app_config().log_level)
    service = AudioSeparationService()
    try:
        result = await service.separate(
            Path(args.input),
            output_dir=Path(args.output_dir) if args.output_dir else None,
            task_id=args.task_id,
            preferred_languages=args.language.split(",") if args.language else [],
            stream_index=args.stream,
            start=args.start,
            duration=args.duration,
            debug=True if args.debug else None,
        )
    except AudioSeparationError as exc:
        print(f"FAILED  {exc.code}  stage={exc.stage}  {exc}", file=sys.stderr)
        return 1
    _print_stem("Dialogue", result.dialogue)
    print()
    _print_stem("Background", result.background)
    print()
    print("Debug trace:")
    print(f"  {result.debug_trace_path or 'disabled'}")
    print()
    print(f"provider={result.provider}  model={result.model}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Separate dialogue and background audio stems")
    parser.add_argument("--input", required=True, help="Audio file or media container")
    parser.add_argument("--output-dir", help="Directory for dialogue.wav and background.wav")
    parser.add_argument("--task-id", help="Optional task id used in output and debug paths")
    parser.add_argument("--language", help="Preferred audio language code (optional)")
    parser.add_argument("--stream", type=int, help="Explicit ffmpeg audio stream index")
    parser.add_argument("--start", type=float, help="Optional start offset in seconds")
    parser.add_argument("--duration", type=float, help="Optional window length in seconds")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Write a debug trace for this run (overrides SUBTITLE_AI_DEBUG_TRACE=false)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
