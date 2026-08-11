"""Tests for container media-root discovery."""

from __future__ import annotations

from pathlib import Path

from app.core.config import AppConfig, get_app_config
from app.core.media_roots import (
    discover_media_roots,
    prefer_leaf_roots,
    read_mount_points,
)


SAMPLE_MOUNTINFO = """\
24 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw
25 24 0:21 / /proc rw,nosuid,nodev,noexec - proc proc rw
26 24 0:22 / /sys rw,nosuid,nodev,noexec - sysfs sysfs rw
27 24 0:5 / /dev rw,nosuid - devtmpfs udev rw
28 24 0:48 / /config rw,relatime - ext4 /dev/sdb1 rw
29 24 0:49 / /data/tv rw,relatime - ext4 /dev/sdc1 rw
30 24 0:50 / /data/movies rw,relatime - ext4 /dev/sdd1 rw
31 24 0:51 / /app rw,relatime - overlay overlay rw
"""


def test_read_mount_points(tmp_path: Path) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(SAMPLE_MOUNTINFO, encoding="utf-8")
    points = read_mount_points(mountinfo)
    assert "/data/tv" in points
    assert "/data/movies" in points
    assert "/config" in points


def test_discover_from_mountinfo(tmp_path: Path) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(SAMPLE_MOUNTINFO, encoding="utf-8")
    roots = discover_media_roots(config_dir=Path("/config"), mountinfo_path=mountinfo)
    assert roots == ["/data/movies", "/data/tv"]


def test_prefer_leaf_roots_drops_parent() -> None:
    assert prefer_leaf_roots(["/data", "/data/tv", "/data/movies"]) == [
        "/data/movies",
        "/data/tv",
    ]


def test_media_roots_env_override(monkeypatch) -> None:
    monkeypatch.setenv("SUBTITLE_AI_MEDIA_ROOTS", "/custom/a,/custom/b")
    get_app_config.cache_clear()
    config = AppConfig()
    assert config.media_roots == ["/custom/a", "/custom/b"]
    assert config.resolved_media_roots == ["/custom/a", "/custom/b"]
    get_app_config.cache_clear()


def test_media_roots_auto_when_env_unset(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SUBTITLE_AI_MEDIA_ROOTS", raising=False)
    get_app_config.cache_clear()
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(SAMPLE_MOUNTINFO, encoding="utf-8")
    roots = discover_media_roots(config_dir=Path("/config"), mountinfo_path=mountinfo)
    assert roots == ["/data/movies", "/data/tv"]
    get_app_config.cache_clear()
