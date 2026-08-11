"""Discover media roots from container volume mounts."""

from __future__ import annotations

from pathlib import Path

# Bind/volume targets we treat as media libraries (Bazarr / *arr convention).
_MEDIA_PREFIXES = ("/data", "/media")

# Never treat these as media roots even if they appear in mountinfo.
_EXCLUDED_EXACT = frozenset(
    {
        "/",
        "/config",
        "/app",
        "/tmp",
        "/run",
        "/proc",
        "/sys",
        "/dev",
        "/etc",
        "/boot",
        "/usr",
        "/var",
        "/lib",
        "/lib64",
        "/bin",
        "/sbin",
        "/opt",
        "/home",
        "/root",
    }
)

_CONVENTION_CANDIDATES = (
    "/data/movies",
    "/data/tv",
    "/data/music",
    "/media/movies",
    "/media/tv",
    "/media",
    "/data",
)


def _unescape_mount_field(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def read_mount_points(mountinfo_path: Path | str = "/proc/self/mountinfo") -> list[str]:
    """Return mount destinations from Linux ``/proc/self/mountinfo``."""
    path = Path(mountinfo_path)
    if not path.is_file():
        return []
    points: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        # Format: ... mount-id parent major:minor root mount-point options ...
        # Fields before '-' may include optional tags; mount point is field 5.
        parts = line.split()
        if len(parts) < 5:
            continue
        mount_point = _unescape_mount_field(parts[4])
        if mount_point:
            points.append(mount_point)
    return points


def is_media_mount_candidate(mount_point: str, *, config_dir: Path | None = None) -> bool:
    normalized = mount_point.rstrip("/") or "/"
    if normalized in _EXCLUDED_EXACT:
        return False
    if config_dir is not None:
        try:
            if Path(normalized).resolve() == config_dir.resolve():
                return False
        except OSError:
            pass
    for prefix in _MEDIA_PREFIXES:
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return True
    return False


def prefer_leaf_roots(roots: list[str]) -> list[str]:
    """Drop parent roots when a deeper child root is also present."""
    normalized = sorted({(root.rstrip("/") or "/") for root in roots})
    leaves: list[str] = []
    for root in normalized:
        if any(other != root and other.startswith(root + "/") for other in normalized):
            continue
        leaves.append(root)
    return leaves


def discover_convention_dirs() -> list[str]:
    """Fallback when mountinfo is unavailable (local macOS/dev)."""
    found: list[str] = []
    for candidate in _CONVENTION_CANDIDATES:
        path = Path(candidate)
        if path.is_dir():
            found.append(str(path))
    return prefer_leaf_roots(found)


def discover_media_roots(
    *,
    config_dir: Path | None = None,
    mountinfo_path: Path | str = "/proc/self/mountinfo",
) -> list[str]:
    """
    Resolve allowed media roots from the running container.

    Priority:
    1. Bind/volume mounts under ``/data`` or ``/media`` (from Docker compose volumes)
    2. Existing conventional directories if present
    3. ``/media`` as a last-resort default for local development
    """
    mounts = [
        point
        for point in read_mount_points(mountinfo_path)
        if is_media_mount_candidate(point, config_dir=config_dir)
    ]
    roots = prefer_leaf_roots(mounts)
    if roots:
        return sorted(roots)

    convention = discover_convention_dirs()
    if convention:
        return sorted(convention)

    return ["/media"]
