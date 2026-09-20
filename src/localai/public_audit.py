"""Public-readiness audit ported from ai-public-audit.ps1."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from localai.ops import run_command
from localai.paths import REPO_ROOT


@dataclass(frozen=True)
class AuditPattern:
    name: str
    pattern: str


@dataclass(frozen=True)
class Finding:
    kind: str
    file: str
    line: int
    text: str


def collect_public_audit_report(
    *,
    strict: bool = False,
    context: int = 0,
    extra_patterns: tuple[str, ...] = (),
    now: datetime | None = None,
) -> tuple[int, list[str]]:
    """Scan tracked files for private markers before extracting a public repo."""
    del context  # Kept for CLI parity; legacy output does not print context lines.

    git = run_command(["git", "--version"], cwd=REPO_ROOT, timeout_sec=10)
    if git.code != 0:
        return 2, [
            format_status_line(
                "FAIL", "git", "git.exe is required for tracked-file audit"
            )
        ]

    tracked_result = run_command(["git", "ls-files"], cwd=REPO_ROOT, timeout_sec=20)
    tracked = [line for line in tracked_result.stdout.splitlines() if line]
    if tracked_result.code != 0 or not tracked:
        return 2, [
            format_status_line(
                "FAIL", "tracked files", "git ls-files returned no files"
            )
        ]

    origin = get_github_origin_repo()
    patterns, unavailable = build_patterns(
        extra_patterns, owner=origin[0] if origin else None
    )
    findings = scan_files(tracked, patterns)
    findings, allowed_self_refs = partition_self_references(findings, origin)

    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"==== localai public-readiness audit ====  {stamp}"]
    lines.append(
        format_status_line("OK", "tracked files", f"{len(tracked)} files scanned")
    )
    # Say what could NOT be checked. A clean result from a run that silently
    # skipped half its patterns is worse than no result at all.
    for skipped in unavailable:
        lines.append(format_status_line("SKIP", "pattern unavailable", skipped))
    if allowed_self_refs:
        lines.append(
            format_status_line(
                "OK",
                "origin self-refs",
                f"{allowed_self_refs} allowed reference(s) to the origin repo",
            )
        )

    if not findings:
        lines.append(
            format_status_line(
                "OK", "private markers", "no built-in marker patterns found"
            )
        )
        return 0, lines

    for kind, count in grouped_counts(findings):
        lines.append(format_status_line("WARN", kind, f"{count} hit(s)"))

    lines.append("")
    for finding in sorted(
        findings, key=lambda item: (item.kind.lower(), item.file.lower(), item.line)
    ):
        lines.append(
            f"{finding.file}:{finding.line}: [{finding.kind}] {finding.text}"
        )

    lines.append("")
    if strict:
        lines.append(
            format_status_line(
                "FAIL",
                "public readiness",
                "private/laptop-specific markers found",
            )
        )
        return 1, lines

    lines.append(
        format_status_line(
            "WARN",
            "public readiness",
            "markers found; expected in this private repo, blocking for a public "
            "template only with -Strict",
        )
    )
    return 0, lines


def format_status_line(level: str, name: str, detail: str) -> str:
    return f"[{level}] {name:<22} {detail}"


# Home-directory names that are generic examples or Windows' own accounts, not a
# leaked identity. Kept deliberately short: any other real home directory in a
# public repository belongs to somebody's actual machine.
PLACEHOLDER_USER_NAMES = frozenset(
    {
        "example",
        "user",
        "username",
        "you",
        "youruser",
        "yourname",
        "public",
        "default",
        "defaultuser",
        "all users",
    }
)

# GitHub preserves redirects after a repository rename. These are public product
# identities that were canonical before the rename, not arbitrary repositories
# owned by the same account. Keep the exemption keyed by the CURRENT repository
# name so unrelated sibling repositories remain findings.
HISTORICAL_PUBLIC_REPO_ALIASES: dict[str, tuple[str, ...]] = {
    "afk-ai": ("localai-windows-starter",),
}


def build_patterns(
    extra_patterns: tuple[str, ...] = (),
    *,
    owner: str | None = None,
) -> tuple[list[AuditPattern], list[str]]:
    """Build the marker patterns, plus the names of any that cannot be checked.

    Identity-derived patterns come from the environment, and those variables do
    not exist off Windows. Interpolating an empty value used to produce
    match-everything regexes -- ``\\b\\b`` for the computer name -- so on Linux
    or macOS the audit flagged essentially every line of the repository and
    ``--strict`` could never pass. Missing identity now DISABLES its pattern and
    is reported, rather than silently becoming either a match-all or a
    match-nothing.
    """
    username = os.environ.get("USERNAME", "").strip()
    computer_name = os.environ.get("COMPUTERNAME", "").strip()
    # Use \s+ (not a literal space) so this definition line does not match
    # itself when the audit scans its own source. Still catches the reference
    # GPU phrasing in user-facing docs.
    hardware_pattern = r"RTX\s*4080|laptop\s+GPU"

    patterns = [
        # Username-independent, so it still works in a portable CI environment:
        # a public repository should not carry ANY real Windows home directory,
        # not merely the one belonging to whoever runs the audit. The captured
        # name excludes whitespace so that prose mentioning the path shape does
        # not read as a hit (and so this file does not match itself).
        AuditPattern(
            "Windows user home path",
            r"[Cc]:[\\/]Users[\\/](?P<home>[^\\/\s\"'`,;:)\]}]+)",
        ),
        AuditPattern("Tailnet URL", r"\b[a-z0-9-]+\.tail[0-9a-f]+\.ts\.net\b"),
        AuditPattern(
            "Tailscale IPv4",
            r"\b100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\."
            r"[0-9]{1,3}\.[0-9]{1,3}\b",
        ),
        AuditPattern(
            "Tailscale IPv6",
            r"\bfd[0-9a-f]{2}:[0-9a-f]{1,4}:[0-9a-f]{1,4}\b",
        ),
        AuditPattern("Laptop hardware", hardware_pattern),
    ]

    unavailable: list[str] = []
    if username:
        patterns.append(
            AuditPattern("Windows user name", rf"\b{re.escape(username)}\b")
        )
    else:
        unavailable.append("Windows user name (no USERNAME in this environment)")

    if computer_name:
        patterns.append(
            AuditPattern("Computer name", rf"\b{re.escape(computer_name)}\b")
        )
    else:
        unavailable.append("Computer name (no COMPUTERNAME in this environment)")

    if owner:
        patterns.append(AuditPattern("Origin GitHub owner", rf"\b{re.escape(owner)}\b"))
    else:
        unavailable.append("Origin GitHub owner (no git origin resolved)")

    for pattern in extra_patterns:
        if pattern:
            patterns.append(AuditPattern("Extra pattern", pattern))
    return patterns, unavailable


def is_placeholder_home(text: str) -> bool:
    """True when a C:\\Users\\<name> hit is a documentation placeholder."""
    name = text.strip().strip("<>%").strip()
    return name.lower() in PLACEHOLDER_USER_NAMES


def get_github_origin_repo() -> tuple[str, str] | None:
    result = run_command(["git", "remote", "get-url", "origin"], cwd=REPO_ROOT)
    if result.code != 0 or not result.stdout.strip():
        return None

    match = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", result.stdout.strip())
    if match is None:
        return None
    return match.group(1), match.group(2)


def partition_self_references(
    findings: list[Finding],
    origin: tuple[str, str] | None,
) -> tuple[list[Finding], int]:
    """Drop owner hits that are approved public project self-references.

    A public repo legitimately names its own origin (release URLs, bootstrap
    source, LICENSE copyright holder) and its dedicated public website repo.
    Only those exact project surfaces are exempt; arbitrary repositories owned by
    the same account still count as leaks.

    The project's own website hostname counts as a self-reference too. It is
    deployed to Cloudflare Workers, whose hostnames embed the account name as a
    subdomain (``<project>.<owner>.workers.dev``) rather than as an ``owner/repo``
    path.
    """
    if origin is None:
        return findings, 0
    owner, repo = origin
    approved_repos = {repo, f"{repo}-site"}
    for alias in HISTORICAL_PUBLIC_REPO_ALIASES.get(repo, ()):
        approved_repos.add(alias)
        approved_repos.add(f"{alias}-site")
    repo_choices = "|".join(
        re.escape(name) for name in sorted(approved_repos, key=len, reverse=True)
    )
    approved_repo_ref = re.compile(
        rf"\b{re.escape(owner)}/(?:{repo_choices})(?![\w-])"
    )
    # Deliberately anchored to workers.dev: this allows the project's own
    # deployment host, not any string that happens to contain the owner name.
    self_site = re.compile(rf"\b[\w-]+\.{re.escape(owner)}\.workers\.dev\b")
    copyright_line = re.compile(r"^Copyright\b")
    kept: list[Finding] = []
    allowed = 0
    for finding in findings:
        if finding.kind == "Origin GitHub owner":
            is_approved_repo = bool(approved_repo_ref.search(finding.text))
            is_self_site = bool(self_site.search(finding.text))
            is_license_copyright = finding.file == "LICENSE" and bool(
                copyright_line.match(finding.text)
            )
            if (
                is_approved_repo
                or is_self_site
                or is_license_copyright
            ):
                allowed += 1
                continue
        kept.append(finding)
    return kept, allowed


def scan_files(
    tracked: list[str],
    patterns: list[AuditPattern],
    *,
    root: Path = REPO_ROOT,
) -> list[Finding]:
    compiled = [(entry.name, re.compile(entry.pattern)) for entry in patterns]
    findings: list[Finding] = []

    for entry in tracked:
        path = root / entry
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        display = relative_display_path(path, root)
        findings.extend(scan_text(display, text, compiled))
    return findings


def scan_text(
    display_path: str,
    text: str,
    compiled_patterns: list[tuple[str, re.Pattern[str]]],
) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        normalized = re.sub(r"\s+", " ", line.strip())
        for name, pattern in compiled_patterns:
            match = pattern.search(line)
            if match is None:
                continue
            # C:\Users\example\... in documentation is a placeholder, not a
            # leaked identity; only a real home directory name is a finding.
            home = match.groupdict().get("home") if match.groupdict() else None
            if home is not None and is_placeholder_home(home):
                continue
            findings.append(Finding(name, display_path, line_number, normalized))
    return findings


def relative_display_path(path: Path, root: Path = REPO_ROOT) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return str(path)
    return str(relative)


def grouped_counts(findings: list[Finding]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
    return sorted(counts.items())
