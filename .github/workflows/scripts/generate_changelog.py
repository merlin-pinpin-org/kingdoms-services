#!/usr/bin/env python3
"""Generate a Conventional Changelog for kingdoms-services releases.

Reads the git history between two tags (default: the full history, since
no release tag exists yet), parses Conventional Commits subjects
(`type(scope): subject`), groups them by type and renders Markdown:

- release notes for the GitHub release body (one bullet per commit, with
  the squash-merged PR link) — replaces scripts/release_notes.sh;
- CHANGELOG.md sections appended at tag time by the Docker workflow;

- version bumps (kingdoms-services#126): the next version is derived from
  the Conventional Commits since the last release — `feat` bumps the
  minor, `fix` the patch, a breaking change the major — and pre-releases
  append a `-rc<n>` classifier that increments per cycle. `make release`
  and `make pre-release` call this; the version is never hand-picked.

The output is generated, never hand-edited (kingdoms-services#28).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime

REPO = "merlin-pinpin-org/kingdoms-services"

TAG_RE = re.compile(
    r"^v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<prerelease>rc\d+))?$"
)


def pr_map(cwd: str | None = None) -> dict[str, int]:
    """Map merge commit sha -> PR number via gh; empty when gh is unavailable.

    In CI the workflow exports GH_TOKEN, so squash-merged commits get their
    PR link; without an authenticated gh the bullets fall back to the short
    sha — the changelog still groups and renders correctly.
    """
    gh = shutil.which("gh")
    if gh is None:
        return {}
    result = subprocess.run(
        [gh, "pr", "list", "--repo", REPO, "--state", "merged", "--limit", "400",
         "--json", "number,mergeCommit", "--jq", '.[] | "\\(.mergeCommit.oid) \\.number"'],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    if result.returncode != 0:
        return {}
    mapping: dict[str, int] = {}
    for line in result.stdout.splitlines():
        sha, _, number = line.partition(" ")
        if sha and number.isdigit():
            mapping[sha] = int(number)
    return mapping

TYPE_ORDER = ["feat", "fix", "docs", "refactor", "test", "chore", "ci", "perf", "build"]
TYPE_LABELS = {
    "feat": "Features",
    "fix": "Bug fixes",
    "docs": "Documentation",
    "perf": "Performance",
    "refactor": "Refactoring",
    "test": "Tests",
    "build": "Build",
    "ci": "CI",
    "chore": "Chores",
}
DEFAULT_LABEL = "Other changes"
COMMIT_RE = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?:\s+(?P<subject>.+)$")
GITHUB_URL = f"https://github.com/{REPO}"
NOTES_HEADER = "## What's changed"


@dataclass(frozen=True)
class CommitEntry:
    """One conventional commit between two refs."""

    sha: str
    type: str
    scope: str | None
    breaking: bool
    subject: str
    pr: int | None


def git(*args: str, cwd: str | None = None) -> str:
    """Run a git command (in cwd when given), returning its output."""
    result = subprocess.run(
        ["git", "-C", cwd, *args] if cwd else ["git", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def resolve_ref(ref: str | None) -> tuple[str, str]:
    """Resolve a git ref to (sha, iso-utc commit date)."""
    if ref:
        sha = git("rev-parse", ref)
    else:
        sha = git("rev-list", "--max-parents=0", "HEAD")
    date = git("show", "-s", "--format=%cI", sha).strip()
    return sha.strip(), date


def parse_commit(sha: str, message: str) -> CommitEntry | None:
    """Parse one commit message into a CommitEntry, or None if not conventional."""
    subject = message.splitlines()[0].strip() if message.strip() else ""
    match = COMMIT_RE.match(subject)
    if not match:
        return None
    subject = match["subject"].strip()
    pr_match = re.search(r"\s+\(#(\d+)\)$", subject)
    pr = int(pr_match.group(1)) if pr_match else None
    if pr_match:
        subject = subject[: pr_match.start()].rstrip()
    return CommitEntry(
        sha=sha,
        type=match["type"],
        scope=match["scope"],
        breaking=bool(match["bang"]) or "BREAKING CHANGE:" in message,
        subject=subject,
        pr=pr,
    )


def collect_entries(previous_ref: str | None, cwd: str | None = None) -> list[CommitEntry]:
    """Collect parsed commits between previous_ref (exclusive) and HEAD."""
    if previous_ref:
        rev_range = f"{previous_ref}..HEAD"
    else:
        rev_range = "HEAD"
    out = git("log", "--no-merges", "--format=%H%x00%B%x1e", rev_range, cwd=cwd)
    merged_prs = pr_map(cwd=cwd)
    entries: list[CommitEntry] = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record.strip():
            continue
        sha, _, body = record.partition("\x00")
        entry = parse_commit(sha.strip(), body)
        if entry is None:
            continue
        if entry.pr is None:
            entry = replace(entry, pr=merged_prs.get(entry.sha))
        entries.append(entry)
    return entries


def group_by_type(entries: list[CommitEntry]) -> list[tuple[str, list[CommitEntry]]]:
    """Group entries by commit type, in TYPE_ORDER order."""
    groups: dict[str, list[CommitEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.type, []).append(entry)
    ordered: list[tuple[str, list[CommitEntry]]] = []
    for type_ in TYPE_ORDER:
        if type_ in groups:
            ordered.append((type_, groups.pop(type_)))
    for type_, entries_ in sorted(groups.items()):
        ordered.append((type_, entries_))
    return ordered


def entry_bullet(entry: CommitEntry) -> str:
    """Render one commit as a Markdown bullet with its squash-merged PR link."""
    subject = entry.subject
    if entry.breaking:
        subject = f"**BREAKING**: {subject}"
    if entry.scope:
        subject = f"{entry.scope}: {subject}"
    if entry.pr:
        return f"- {subject} ([#{entry.pr}]({GITHUB_URL}/pull/{entry.pr}))"
    return f"- {subject} ({entry.sha[:7]})"


def render_section(title: str, entries: list[CommitEntry], entries_heading: str) -> str:
    """Render one release section of the changelog or notes."""
    lines = [f"## {title}", ""]
    if not entries:
        return "\n".join([*lines, "_No changes._", ""])
    lines.append(f"_{entries_heading}_")
    lines.append("")
    for type_, type_entries in group_by_type(entries):
        label = TYPE_LABELS.get(type_, DEFAULT_LABEL)
        lines.append(f"### {label}")
        lines.append("")
        lines.extend(entry_bullet(entry) for entry in type_entries)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_notes(tag: str, previous_ref: str | None, entries: list[CommitEntry]) -> str:
    """Render the GitHub release notes body for one tag."""
    if previous_ref:
        header = f"Since {previous_ref}"
    else:
        header = "All merged pull requests (first release)"
    lines = [NOTES_HEADER, "", f"_{header}_", ""]
    if not entries:
        lines.extend(["_No changes._", ""])
    else:
        for type_, type_entries in group_by_type(entries):
            label = TYPE_LABELS.get(type_, DEFAULT_LABEL)
            lines.append(f"### {label}")
            lines.append("")
            lines.extend(entry_bullet(e) for e in type_entries)
            lines.append("")
    lines.extend(
        [
            "## Image",
            "",
            f"`ghcr.io/{REPO}:{tag}` — deployed on the test environment first;",
            "promoted to production after validation.",
            "",
        ]
    )
    return "\n".join(lines)


def render_changelog_section(tag: str, previous_ref: str | None, entries: list[CommitEntry], date: datetime) -> str:
    """Render one CHANGELOG.md section (newest first, at the top)."""
    title = f"{tag} — {date.strftime('%Y-%m-%d')}"
    if previous_ref:
        header = f"Since {previous_ref}"
    else:
        header = "All merged pull requests (first release)"
    return render_section(title, entries, header)


def parse_tag(tag: str) -> tuple[int, int, int, int] | None:
    """Parse a tag into (major, minor, patch, rc); rc=0 for a final release."""
    match = TAG_RE.match(tag)
    if not match:
        return None
    return (
        int(match["major"]),
        int(match["minor"]),
        int(match["patch"]),
        int(match["prerelease"][2:]) if match["prerelease"] else 0,
    )


def bump_level(entries: list[CommitEntry]) -> str:
    """Derive the bump level from the conventional commits since the last release.

    BREAKING > feat > fix — everything else (docs, chore, refactor, ...)
    falls to the patch level: a release of pure chores still cuts a patch,
    so the image and the pin move.
    """
    if any(e.breaking for e in entries):
        return "major"
    if any(e.type == "feat" for e in entries):
        return "minor"
    return "patch"


def latest_release_tag(cwd: str | None = None) -> str | None:
    """The highest released tag: finals win over -rc classifiers, then semver."""
    tags = [t.strip() for t in git("tag", "--list", "v*", cwd=cwd).splitlines() if t.strip()]
    parsed = [(parse_tag(t), t) for t in tags]
    parsed = [(p, t) for p, t in parsed if p]
    if not parsed:
        return None
    parsed.sort(key=lambda pt: (pt[0][3] == 0, pt[0]))
    return parsed[-1][1]


def next_free_rc(base: str, cwd: str | None = None) -> str:
    """The first free -rc<n> classifier on a base version."""
    used = {
        t.strip()
        for t in git("tag", "--list", f"{base}-rc*", cwd=cwd).splitlines()
        if t.strip()
    }
    n = 1
    while f"{base}-rc{n}" in used:
        n += 1
    return f"{base}-rc{n}"


def bumped_base(major: int, minor: int, patch: int, level: str) -> str:
    """Apply a bump level to a base version."""
    if level == "major":
        major, minor, patch = major + 1, 0, 0
    elif level == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"v{major}.{minor}.{patch}"


def next_version(
    previous: str | None, entries: list[CommitEntry], prerelease: bool, cwd: str | None = None
) -> str:
    """Compute the next tag from the previous release and the bump level.

    Without a previous tag the base starts at v0.(minor).0 (feat) or
    v0.0.1 (fix). A pre-release cycle appends `-rc<n>` on the bumped
    base (first free n); an open rc cycle keeps its base — the commits
    since the last release (fixes to the rc feedback included) increment
    the classifier, and cutting the final drops the classifier on the
    base the cycle validated — never a new bump on top of it.
    """
    level = bump_level(entries)
    if previous is None:
        base = "v0.1.0" if level == "minor" else "v0.0.1"
        return f"{base}-rc1" if prerelease else base
    parsed = parse_tag(previous)
    if parsed is None:
        raise SystemExit(f"internal error: unparsable previous tag {previous}")
    major, minor, patch, rc = parsed
    if rc:
        base = f"v{major}.{minor}.{patch}"
        return next_free_rc(base, cwd=cwd) if prerelease else base
    base = bumped_base(major, minor, patch, level)
    if prerelease:
        return next_free_rc(base, cwd=cwd)
    return base


def existing_changelog_tags(path: str) -> set[str]:
    """Read the tags already present in a CHANGELOG.md file."""
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
    except FileNotFoundError:
        return set()
    return set(re.findall(r"^## (v[0-9][^\s—]*)", content, flags=re.MULTILINE))


def write_changelog(path: str, tag: str, section: str) -> None:
    """Insert or replace one release section at the top of CHANGELOG.md."""
    header = "# Changelog\n\nAll notable changes are generated from Conventional Commits — never hand-edited.\n\n"
    tag_re = re.compile(r"^## " + re.escape(tag) + r"(\s|$)", re.MULTILINE)
    if tag_re.search(header):
        raise SystemExit(f"internal error: tag {tag} already in generated header")
    try:
        with open(path, encoding="utf-8") as fh:
            current = fh.read()
    except FileNotFoundError:
        current = ""
    if current.startswith(header):
        rest = current[len(header) :]
        new_content = header + section + "\n" + rest
    elif not current:
        new_content = header + section
    else:
        raise SystemExit(f"unexpected CHANGELOG.md content at {path} — regenerate with --append-only")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=None, help="release tag being cut (default with --bump: computed)")
    parser.add_argument("--bump", action="store_true", help="print the computed next tag and exit")
    parser.add_argument("--prerelease", action="store_true", help="with --bump: compute a -rc<n> pre-release")
    parser.add_argument("--previous", nargs="?", default=None, help="previous release tag (default: auto-detect)")
    parser.add_argument("--notes", action="store_true", help="print the GitHub release notes body")
    parser.add_argument("--changelog", metavar="FILE", help="write/refresh the CHANGELOG.md section into FILE")
    parser.add_argument("--date", default=None, help="release date (ISO, default: now)")
    args = parser.parse_args()

    previous_ref = args.previous
    if previous_ref is None:
        previous_ref = latest_release_tag()

    if args.bump:
        entries = collect_entries(previous_ref)
        print(next_version(previous_ref, entries, prerelease=args.prerelease, cwd=None))
        return 0
    if not args.tag:
        raise SystemExit("--tag is required unless --bump computes it")

    entries = collect_entries(previous_ref)
    if args.notes:
        print(render_notes(args.tag, previous_ref, entries), end="")
    if args.changelog:
        date = datetime.fromisoformat(args.date) if args.date else datetime.now(UTC)
        section = render_changelog_section(args.tag, previous_ref, entries, date)
        write_changelog(args.changelog, args.tag, section)
        print(f"CHANGELOG section for {args.tag} written to {args.changelog}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
