from pathlib import Path

import pytest

from scan.core.types import Tier
from scan.ingest import CaptureFormatError, detect_capture


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_stray_export_is_lidar(stray_capture):
    d = detect_capture(stray_capture)
    assert d.tier is Tier.LIDAR
    assert d.source_format == "stray_scanner"
    assert d.capture_id == "abc123"


def test_wrapper_folder_is_descended(stray_capture):
    d = detect_capture(stray_capture.parent)  # .../wrapper -> .../wrapper/abc123
    assert d.root == stray_capture.resolve()
    assert d.tier is Tier.LIDAR


def test_single_video_is_video_tier(tmp_path):
    video = touch(tmp_path / "walk" / "IMG_0001.MOV")
    d = detect_capture(tmp_path / "walk")
    assert d.tier is Tier.VIDEO
    assert d.video_path == video.resolve()


def test_video_file_path_directly(tmp_path):
    video = touch(tmp_path / "walk.mp4")
    d = detect_capture(video)
    assert d.tier is Tier.VIDEO
    assert d.capture_id == "walk"


def test_two_videos_is_an_error(tmp_path):
    touch(tmp_path / "c" / "a.mov")
    touch(tmp_path / "c" / "b.mov")
    with pytest.raises(CaptureFormatError, match="one capture must be one video"):
        detect_capture(tmp_path / "c")


def test_room_folders_are_photo_tier(tmp_path):
    for room in ("kitchen", "bedroom"):
        for k in range(3):
            touch(tmp_path / "flat" / room / f"IMG_{k}.HEIC")
    d = detect_capture(tmp_path / "flat")
    assert d.tier is Tier.PHOTO
    assert set(d.room_dirs) == {"kitchen", "bedroom"}
    assert d.warnings == []


def test_room_with_one_photo_warns(tmp_path):
    touch(tmp_path / "flat" / "kitchen" / "a.jpg")
    touch(tmp_path / "flat" / "kitchen" / "b.jpg")
    touch(tmp_path / "flat" / "hall" / "a.jpg")
    d = detect_capture(tmp_path / "flat")
    assert any("hall" in w and "at least 2" in w for w in d.warnings)


def test_flat_photos_are_single_room_with_warning(tmp_path):
    for k in range(4):
        touch(tmp_path / "pics" / f"{k}.jpg")
    d = detect_capture(tmp_path / "pics")
    assert d.tier is Tier.PHOTO
    assert list(d.room_dirs) == ["pics"]
    assert any("single room" in w for w in d.warnings)


def test_hidden_files_are_ignored(tmp_path):
    touch(tmp_path / "flat" / ".DS_Store")
    touch(tmp_path / "flat" / "kitchen" / "a.jpg")
    touch(tmp_path / "flat" / "kitchen" / "b.jpg")
    assert detect_capture(tmp_path / "flat").tier is Tier.PHOTO


def test_unrecognised_folder_is_an_error(tmp_path):
    touch(tmp_path / "junk" / "notes.txt")
    with pytest.raises(CaptureFormatError, match="unrecognised capture"):
        detect_capture(tmp_path / "junk")


def test_missing_folder_is_an_error(tmp_path):
    with pytest.raises(CaptureFormatError, match="not found"):
        detect_capture(tmp_path / "nope")
