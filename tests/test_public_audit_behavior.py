"""Behavior tests for the public-audit origin self-reference exemption."""

import re

from localai.public_audit import (
    AuditPattern,
    Finding,
    build_patterns,
    partition_self_references,
    scan_text,
)

# Built at runtime so public-audit -Strict does not flag its own fixtures:
# the audit scans tracked source lines for the literal owner marker.
OWNER = "allusion" + "safk"
ORIGIN = (OWNER, "afk-ai")


def make_finding(kind: str, file: str, text: str, line: int = 1) -> Finding:
    return Finding(kind, file, line, text)


def test_origin_url_self_reference_is_allowed() -> None:
    findings = [
        make_finding(
            "Origin GitHub owner",
            "README.md",
            "https://github.com/allusionsafk/afk-ai/releases/latest",
        )
    ]
    kept, allowed = partition_self_references(findings, ORIGIN)
    assert kept == []
    assert allowed == 1


def test_public_companion_site_repo_is_allowed() -> None:
    findings = [
        make_finding(
            "Origin GitHub owner",
            ".github/ISSUE_TEMPLATE/config.yml",
            f"https://github.com/{OWNER}/afk-ai-site/issues/new/choose",
        )
    ]
    kept, allowed = partition_self_references(findings, ORIGIN)
    assert kept == []
    assert allowed == 1


def test_license_copyright_is_allowed() -> None:
    findings = [
        make_finding(
            "Origin GitHub owner", "LICENSE", f"Copyright (c) 2026 {OWNER}"
        )
    ]
    kept, allowed = partition_self_references(findings, ORIGIN)
    assert kept == []
    assert allowed == 1


def test_copyright_outside_license_file_is_still_flagged() -> None:
    findings = [
        make_finding(
            "Origin GitHub owner", "docs/notes.md", f"Copyright (c) 2026 {OWNER}"
        )
    ]
    kept, allowed = partition_self_references(findings, ORIGIN)
    assert len(kept) == 1
    assert allowed == 0


def test_bare_owner_mention_is_still_flagged() -> None:
    findings = [
        make_finding(
            "Origin GitHub owner", "docs/notes.md", f"ask {OWNER} about this"
        )
    ]
    kept, allowed = partition_self_references(findings, ORIGIN)
    assert len(kept) == 1
    assert allowed == 0


def test_other_repo_of_same_owner_is_not_a_self_reference() -> None:
    findings = [
        make_finding(
            "Origin GitHub owner",
            "docs/notes.md",
            f"see github.com/{OWNER}/localai for the private stack",
        )
    ]
    kept, allowed = partition_self_references(findings, ORIGIN)
    assert len(kept) == 1
    assert allowed == 0


def test_similar_sibling_repo_name_is_not_allowed() -> None:
    findings = [
        make_finding(
            "Origin GitHub owner",
            "docs/notes.md",
            f"see github.com/{OWNER}/afk-ai-site-backup",
        )
    ]
    kept, allowed = partition_self_references(findings, ORIGIN)
    assert len(kept) == 1
    assert allowed == 0


def test_longer_repo_name_does_not_match_shorter_origin() -> None:
    # With origin owner/localai, owner/afk-ai is a DIFFERENT
    # repo; the trailing (?![\w-]) lookahead must reject the prefix match.
    findings = [
        make_finding(
            "Origin GitHub owner",
            "docs/notes.md",
            "https://github.com/allusionsafk/afk-ai/releases",
        )
    ]
    kept, allowed = partition_self_references(findings, (OWNER, "localai"))
    assert len(kept) == 1
    assert allowed == 0


def test_non_owner_kinds_pass_through_untouched() -> None:
    findings = [
        make_finding("Tailnet URL", "docs/notes.md", "box.tail0123" + ".ts.net"),
    ]
    kept, allowed = partition_self_references(findings, ORIGIN)
    assert kept == findings
    assert allowed == 0


def test_no_origin_means_no_exemption() -> None:
    findings = [
        make_finding("Origin GitHub owner", "README.md", "github.com/x/y")
    ]
    kept, allowed = partition_self_references(findings, None)
    assert kept == findings
    assert allowed == 0


# The project's website is deployed to Cloudflare Workers, whose hostnames embed
# the account name as a SUBDOMAIN rather than an owner/repo path. README and
# SUPPORT.md have to print that URL to send a customer to the download page, so
# it must not be reported as a private marker.
SITE_HOST = "localai-windows-starter-site." + OWNER + ".workers.dev"


def test_project_workers_dev_site_is_allowed() -> None:
    findings = [
        make_finding(
            "Origin GitHub owner",
            "README.md",
            f"- **Website:** https://{SITE_HOST}/",
        )
    ]
    kept, allowed = partition_self_references(findings, ORIGIN)
    assert kept == []
    assert allowed == 1


def test_workers_dev_exemption_requires_the_owner_subdomain() -> None:
    # Someone else's workers.dev deployment is not this project's site.
    findings = [
        make_finding(
            "Origin GitHub owner",
            "docs/notes.md",
            f"{OWNER} tried https://someone-else.other-account.workers.dev/",
        )
    ]
    kept, allowed = partition_self_references(findings, ORIGIN)
    assert len(kept) == 1
    assert allowed == 0


def test_workers_dev_exemption_does_not_allow_bare_owner_mentions() -> None:
    # The exemption is anchored to the deployment host; a bare owner mention in
    # prose still leaks and must still be reported.
    findings = [
        make_finding("Origin GitHub owner", "docs/notes.md", f"ask {OWNER} about it")
    ]
    kept, allowed = partition_self_references(findings, ORIGIN)
    assert len(kept) == 1
    assert allowed == 0


# --- identity-derived patterns must not degrade into match-everything ---------
#
# USERNAME/COMPUTERNAME do not exist off Windows. Interpolating an empty value
# built `\b\b` for the computer name, which matches at every word boundary, so
# on Linux the audit flagged essentially every line in the repository and
# --strict could never pass. CI caught this on its first run.

WINDOWS_ENV = {"USERNAME": "jdoe", "COMPUTERNAME": "DESKTOP-7QK2Z9"}


def compile_named(patterns: list[AuditPattern]) -> list[tuple[str, re.Pattern[str]]]:
    return [(p.name, re.compile(p.pattern)) for p in patterns]


def test_missing_identity_env_disables_those_patterns(monkeypatch) -> None:
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("COMPUTERNAME", raising=False)
    patterns, unavailable = build_patterns(owner=None)

    names = {p.name for p in patterns}
    assert "Computer name" not in names
    assert "Windows user name" not in names
    assert any("COMPUTERNAME" in note for note in unavailable)
    assert any("USERNAME" in note for note in unavailable)


def test_missing_identity_env_never_matches_ordinary_source(monkeypatch) -> None:
    # The exact regression: a line of ordinary PowerShell must produce no hits.
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("COMPUTERNAME", raising=False)
    patterns, _ = build_patterns(owner=None)

    line = "$psi.RedirectStandardError = $true"
    assert scan_text("ai-common.ps1", line, compile_named(patterns)) == []


def test_blank_identity_env_is_treated_as_missing(monkeypatch) -> None:
    # A set-but-empty variable is the same hazard as an absent one.
    monkeypatch.setenv("USERNAME", "   ")
    monkeypatch.setenv("COMPUTERNAME", "")
    patterns, unavailable = build_patterns(owner=None)

    names = {p.name for p in patterns}
    assert "Computer name" not in names
    assert "Windows user name" not in names
    assert len(unavailable) >= 2


def test_identity_patterns_are_built_when_the_env_provides_them(monkeypatch) -> None:
    for key, value in WINDOWS_ENV.items():
        monkeypatch.setenv(key, value)
    patterns, unavailable = build_patterns(owner=None)

    names = {p.name for p in patterns}
    assert "Computer name" in names
    assert "Windows user name" in names
    assert unavailable == ["Origin GitHub owner (no git origin resolved)"]

    compiled = compile_named(patterns)
    hits = scan_text("notes.md", f"ran it on {WINDOWS_ENV['COMPUTERNAME']}", compiled)
    assert [f.kind for f in hits] == ["Computer name"]


# --- the username-independent home-path pattern ------------------------------
#
# The portable CI job has no USERNAME to interpolate, so the leak that matters
# most must be detectable without one: a public repo should carry no real
# Windows home directory, whoever it belongs to.

# Built at runtime for the same reason as OWNER above: a literal home path in
# this file is itself a finding, and the audit scans its own tests.
HOME_PREFIX = "C:" + chr(92) + "Users" + chr(92)
FWD_PREFIX = "C:" + "/" + "Users" + "/"


def home_path_hits(text: str, monkeypatch) -> list[Finding]:
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("COMPUTERNAME", raising=False)
    patterns, _ = build_patterns(owner=None)
    return scan_text("notes.md", text, compile_named(patterns))


def test_real_home_directory_is_reported_without_a_username(monkeypatch) -> None:
    path = f"{HOME_PREFIX}alice{chr(92)}localai"
    hits = home_path_hits(f"see {path}", monkeypatch)
    assert [f.kind for f in hits] == ["Windows user home path"]


def test_forward_slash_home_directory_is_reported(monkeypatch) -> None:
    hits = home_path_hits(f"see {FWD_PREFIX}alice/localai/logs", monkeypatch)
    assert [f.kind for f in hits] == ["Windows user home path"]


def test_documentation_placeholders_are_not_reported(monkeypatch) -> None:
    for placeholder in ("example", "user", "you", "Public", "Default"):
        text = f"copy it to {HOME_PREFIX}{placeholder}{chr(92)}localai"
        assert home_path_hits(text, monkeypatch) == [], placeholder


def test_prose_about_the_path_shape_is_not_reported(monkeypatch) -> None:
    # The audit scans its own source, so a comment describing the pattern must
    # not read as a hit; the captured name excludes whitespace for this reason.
    prose = f"anything under {HOME_PREFIX} in a public repo"
    assert home_path_hits(prose, monkeypatch) == []
