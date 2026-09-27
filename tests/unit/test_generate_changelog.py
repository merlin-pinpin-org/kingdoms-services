"""Unit tests for the conventional changelog generator (kingdoms-services#28).

The parser and renderer are pure functions over commit messages and entry
lists; the git plumbing (rev-parse, log) is exercised through the public
helpers against a real temporary git repository, since every consumer of
the script runs against git anyway (release notes, tag-time changelog).
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from scripts.generate_changelog import (
    CommitEntry,
    bump_level,
    collect_entries,
    entry_bullet,
    group_by_type,
    next_version,
    parse_commit,
    parse_tag,
    render_changelog_section,
    render_notes,
    write_changelog,
)


def entry(
    type_: str, subject: str, pr: int | None = None, scope: str | None = None, breaking: bool = False
) -> CommitEntry:
    """Build a CommitEntry shorthand for renderer tests."""
    return CommitEntry(sha="abcdef1234567890", type=type_, scope=scope, breaking=breaking, subject=subject, pr=pr)


class TestVersionBump:
    """The conventional commits define the increments — never hand-picked."""

    def test_parse_tag_final_and_prerelease(self) -> None:
        assert parse_tag("v0.2.2") == (0, 2, 2, 0)
        assert parse_tag("v0.3.0-rc1") == (0, 3, 0, 1)
        assert parse_tag("v0.3.0-rc12") == (0, 3, 0, 12)
        assert parse_tag("v1.2.3.4") is None

    def test_bump_level_breaking_feat_fix(self) -> None:
        assert bump_level([entry("feat", "x"), entry("fix", "y")]) == "minor"
        assert bump_level([entry("fix", "y"), entry("docs", "z")]) == "patch"
        assert bump_level([entry("fix", "y", breaking=True)]) == "major"
        assert bump_level([entry("chore", "m")]) == "patch"

    def test_feat_bumps_minor_from_previous_final(self) -> None:
        tag = next_version("v0.2.2", [entry("feat", "new thing")], prerelease=False)
        assert tag == "v0.3.0"

    def test_fix_bumps_patch(self) -> None:
        tag = next_version("v0.2.2", [entry("fix", "a bug")], prerelease=False)
        assert tag == "v0.2.3"

    def test_prerelease_appends_first_free_rc(self) -> None:
        tag = next_version("v0.2.2", [entry("feat", "x")], prerelease=True)
        assert tag == "v0.3.0-rc1"

    def test_prerelease_first_release(self) -> None:
        assert next_version(None, [entry("feat", "x")], prerelease=True) == "v0.1.0-rc1"
        assert next_version(None, [entry("fix", "y")], prerelease=False) == "v0.0.1"

    def test_prerelease_cycle_keeps_the_base(self) -> None:
        # The rc cycle validated v0.3.0: cutting the final drops the classifier.
        tag = next_version("v0.3.0-rc2", [entry("fix", "rc feedback")], prerelease=False)
        assert tag == "v0.3.0"

    def test_breaking_bumps_major(self) -> None:
        tag = next_version("v0.2.2", [entry("feat", "x", breaking=True)], prerelease=False)
        assert tag == "v1.0.0"


class TestParseCommit:
    def test_conventional_with_scope_and_pr(self) -> None:
        parsed = parse_commit("abc123", "feat(i18n): localize slash commands (#105)")
        assert parsed is not None
        assert parsed.type == "feat"
        assert parsed.scope == "i18n"
        assert parsed.subject == "localize slash commands"
        assert parsed.pr == 105
        assert not parsed.breaking

    def test_breaking_bang(self) -> None:
        parsed = parse_commit("abc", "feat!: drop the legacy enrollment flow")
        assert parsed is not None
        assert parsed.breaking

    def test_breaking_trailer(self) -> None:
        parsed = parse_commit("abc", "refactor(core): rework the API\n\nBREAKING CHANGE: config keys renamed")
        assert parsed is not None
        assert parsed.breaking

    def test_non_conventional_returns_none(self) -> None:
        assert parse_commit("abc", "updated stuff") is None
        assert parse_commit("abc", "") is None


class TestGroupByType:
    def test_known_types_in_canonical_order(self) -> None:
        entries = [entry("chore", "x"), entry("docs", "y"), entry("fix", "z"), entry("feat", "w")]
        groups = group_by_type(entries)
        assert [t for t, _ in groups] == ["feat", "fix", "docs", "chore"]

    def test_unknown_type_last(self) -> None:
        groups = group_by_type([entry("zzz", "x"), entry("feat", "y")])
        assert [t for t, _ in groups] == ["feat", "zzz"]


class TestRender:
    def test_bullet_with_pr_link(self) -> None:
        bullet = entry_bullet(entry("feat", "add the panel", pr=12))
        assert bullet == "- add the panel ([#12](https://github.com/merlin-pinpin-org/kingdoms-services/pull/12))"

    def test_bullet_with_scope_and_sha(self) -> None:
        bullet = entry_bullet(entry("fix", "crash", scope="logs"))
        assert bullet == "- logs: crash (abcdef1)"

    def test_section_groups_and_labels(self) -> None:
        entries = [entry("feat", "a", pr=1), entry("fix", "b", pr=2)]
        text = render_changelog_section("v0.3.0", None, entries, date=datetime(2026, 9, 27))
        assert "## v0.3.0 — 2026-09-27" in text
        assert "### Features" in text
        assert "### Bug fixes" in text
        assert "- a ([#1]" in text
        assert "- b ([#2]" in text

    def test_notes_image_section(self) -> None:
        notes = render_notes("v0.3.0", None, [entry("feat", "a", pr=1)])
        assert notes.startswith("## What's changed")
        assert "## Image" in notes
        assert "ghcr.io/merlin-pinpin-org/kingdoms-services:v0.3.0" in notes

    def test_notes_since_previous(self) -> None:
        notes = render_notes("v0.4.0", "v0.3.0", [])
        assert "_Since v0.3.0_" in notes
        assert "_No changes._" in notes


class TestChangelogFile:
    def test_write_then_replace_section(self, tmp_path: Path) -> None:
        path = tmp_path / "CHANGELOG.md"
        section_v3 = render_changelog_section("v0.3.0", None, [entry("feat", "a", pr=1)], date=datetime(2026, 9, 27))
        write_changelog(str(path), "v0.3.0", section_v3)
        assert "## v0.3.0" in path.read_text()
        section_v4 = render_changelog_section("v0.4.0", "v0.3.0", [entry("fix", "b", pr=2)], date=datetime(2026, 10, 1))
        write_changelog(str(path), "v0.4.0", section_v4)
        content = path.read_text()
        assert content.index("## v0.4.0") < content.index("## v0.3.0")

    def test_rejects_unexpected_content(self, tmp_path: Path) -> None:
        path = tmp_path / "CHANGELOG.md"
        path.write_text("hand-written junk\n")
        with pytest.raises(SystemExit):
            write_changelog(str(path), "v0.3.0", "## v0.3.0 — 2026-09-27\n")


GIT = "/usr/bin/git" if Path("/usr/bin/git").exists() else "git"


def run_git(repo: Path, *args: str) -> None:
    """Run a git command in the temporary repository."""
    subprocess.run([GIT, *args], cwd=repo, check=True)


def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with a conventional history."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q")
    run_git(repo, "config", "user.email", "t@example.com")
    run_git(repo, "config", "user.name", "T")
    subjects = [
        "feat(bot): first feature (#10)",
        "fix(logs): a bug (#11)",
        "random non-conventional commit",
    ]
    for i, subject in enumerate(subjects):
        (repo / f"f{i}.txt").write_text(str(i))
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", subject)
    run_git(repo, "tag", "v0.1.0")
    (repo / "after.txt").write_text("after")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-q", "-m", "feat(core): post-tag feature (#12)")
    return repo


class TestCollectEntries:
    def test_collects_and_filters_since_previous_tag(self, tmp_path: Path) -> None:
        repo = git_repo(tmp_path)
        entries = collect_entries("v0.1.0", cwd=str(repo))
        subjects = [e.subject for e in entries]
        assert subjects == ["post-tag feature"]

    def test_collects_everything_without_previous(self, tmp_path: Path) -> None:
        repo = git_repo(tmp_path)
        entries = collect_entries(None, cwd=str(repo))
        subjects = [e.subject for e in entries]
        assert "first feature" in subjects
        assert "a bug" in subjects
        assert "post-tag feature" in subjects
        assert all("non-conventional" not in s for s in subjects)
