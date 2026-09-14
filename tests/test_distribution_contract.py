from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from localai import __version__

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATH = ROOT / "installer" / "version.json"


def load_version() -> dict[str, object]:
    return json.loads(VERSION_PATH.read_text(encoding="utf-8"))


def test_release_identity_is_canonical() -> None:
    metadata = load_version()

    assert metadata == {
        "schema_version": 1,
        "product_name": "AFK LocalAI",
        "executable_name": "AFKLocalAI.exe",
        "installer_name": "AFKLocalAISetup-0.2.0-rc1-x64.exe",
        "display_version": "0.2.0-rc1",
        "python_version": "0.2.0rc1",
        "file_version": "0.2.0.0",
        "channel": "prerelease",
        "architecture": "x64",
        "app_id": "{8A8A2D4D-CE75-4A2D-A39B-56B4206F93D0}",
        "publisher": "AFK",
        "repository": "https://github.com/allusionsafk/localai-windows-starter",
        "support_url": (
            "https://github.com/allusionsafk/localai-windows-starter/issues/new/choose"
        ),
    }


def test_python_metadata_matches_release_contract() -> None:
    metadata = load_version()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == metadata["python_version"]
    assert __version__ == metadata["python_version"]
    assert "public AFK LocalAI Windows distribution" in project["project"][
        "description"
    ]


def test_version_shapes_match_their_consumers() -> None:
    metadata = load_version()

    assert re.fullmatch(
        r"\d+\.\d+\.\d+-(?:rc|beta)\d+", str(metadata["display_version"])
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", str(metadata["file_version"]))
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:rc|b)\d+", str(metadata["python_version"]))
    assert metadata["installer_name"] == (
        f"AFKLocalAISetup-{metadata['display_version']}-{metadata['architecture']}.exe"
    )


def test_public_distribution_does_not_track_unrelated_release_workflows() -> None:
    workflow_names = {
        path.name.lower() for path in (ROOT / ".github" / "workflows").glob("*")
    }

    assert not any("adaptive" in name or "valclip" in name for name in workflow_names)
