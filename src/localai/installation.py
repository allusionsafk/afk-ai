"""Is this installation the one that was shipped?

Setup stages every program file into ``payload-manifest.json`` with its SHA-256.
This module checks the installed program folder against that record so a
partially written update, a half-removed uninstall, or an out-of-band edit is
reported as a corrupt installation instead of being executed.

Scope is deliberate: ``unexpected`` files are only looked for inside the
directories the product owns outright (its Python package, its runtime, its
installer scripts). A stale module left behind by an older version would still be
importable there, so its presence is itself a defect. Anything else in the
program folder (Setup's own uninstaller files, for example) is not our business.

Threat model, stated honestly: the manifest lives beside the files it describes
in a per-user folder. This detects corruption and mismatched or partial
installs. It is not a defence against malware already running as the user.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = "payload-manifest.json"
RUNTIME_IDENTITY = "runtime/afk-runtime.json"

# Directories whose every file must be accounted for by the manifest.
OWNED_TREES = ("src/localai", "runtime", "installer")
# Files that exist only in development checkouts and are never shipped.
_IGNORED_PARTS = {"__pycache__"}


@dataclass
class IntegrityReport:
    manifest_present: bool = False
    source_commit: str | None = None
    display_version: str | None = None
    checked: int = 0
    missing: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    @property
    def intact(self) -> bool:
        return (
            self.manifest_present
            and self.checked > 0
            and not self.missing
            and not self.mismatched
            and not self.unexpected
        )

    def to_dict(self, *, limit: int = 10) -> dict[str, object]:
        return {
            "manifest_present": self.manifest_present,
            "intact": self.intact,
            "source_commit": self.source_commit,
            "display_version": self.display_version,
            "checked": self.checked,
            "missing_count": len(self.missing),
            "mismatched_count": len(self.mismatched),
            "unexpected_count": len(self.unexpected),
            "missing": self.missing[:limit],
            "mismatched": self.mismatched[:limit],
            "unexpected": self.unexpected[:limit],
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _safe_relative(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("\\", "/")
    parts = text.split("/")
    if text.startswith("/") or ":" in parts[0] or ".." in parts or "" in parts:
        return None
    return text


def verify_installation(
    program_root: Path, *, trees: tuple[str, ...] = OWNED_TREES
) -> IntegrityReport:
    report = IntegrityReport()
    try:
        manifest = json.loads(
            (program_root / MANIFEST_NAME).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValueError):
        return report
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        return report
    report.manifest_present = True
    commit = manifest.get("source_commit")
    version = manifest.get("display_version")
    report.source_commit = commit if isinstance(commit, str) else None
    report.display_version = version if isinstance(version, str) else None

    expected: dict[str, str] = {}
    for entry in files:
        if not isinstance(entry, dict):
            continue
        relative = _safe_relative(entry.get("path"))
        digest = entry.get("sha256")
        if relative is None or not isinstance(digest, str):
            continue
        expected[relative] = digest.upper()

    for relative, digest in sorted(expected.items()):
        path = program_root / relative
        report.checked += 1
        if not path.is_file():
            report.missing.append(relative)
            continue
        try:
            if _sha256(path) != digest:
                report.mismatched.append(relative)
        except OSError:
            report.mismatched.append(relative)

    lowered = {key.lower() for key in expected}
    for tree in trees:
        base = program_root / tree
        if not base.is_dir():
            continue
        for current, directories, names in os.walk(base):
            directories[:] = [d for d in directories if d not in _IGNORED_PARTS]
            for name in names:
                relative = Path(current, name).relative_to(program_root).as_posix()
                if relative.lower() not in lowered:
                    report.unexpected.append(relative)
    report.unexpected.sort()
    return report


@dataclass(frozen=True)
class RuntimeIdentity:
    implementation: str
    version: str
    sha256: str
    source_url: str


def read_runtime_identity(program_root: Path) -> RuntimeIdentity | None:
    """The identity file Setup staged beside the owned interpreter."""
    try:
        payload = json.loads(
            (program_root / RUNTIME_IDENTITY).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    values = {
        key: payload.get(key)
        for key in ("implementation", "version", "sha256", "source_url")
    }
    if not all(isinstance(v, str) and v for v in values.values()):
        return None
    return RuntimeIdentity(
        implementation=str(values["implementation"]),
        version=str(values["version"]),
        sha256=str(values["sha256"]),
        source_url=str(values["source_url"]),
    )
