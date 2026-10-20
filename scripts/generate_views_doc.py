"""Render docs/DEVELOPMENT/VIEWS.md from config/views.yaml.

The views YAML is the single source of truth; this script renders it
for humans: one section per view with GitHub anchors, every button a
link to its target's section (menus reference each other through
clickable links). Run from the kingdoms-services repo root:

    uv run python scripts/generate_views_doc.py --output ../kingdoms/docs/DEVELOPMENT/VIEWS.md

The generated doc carries a "generated" banner; hand edits are lost
on the next run — edit the YAML instead, then regenerate.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


def anchor(view_id: str) -> str:
    """The GitHub anchor of a view's heading.

    GitHub slugs a heading by lowercasing it and dropping the
    punctuation (backticks, colons, slashes) — ``menu:mod:ladder``
    renders as ``#menumodladder``. Spaces would become dashes; a
    view id carries none.
    """
    return "".join(c for c in view_id.lower() if c.isalnum())


def render_button(label: str, flags: list[str]) -> str:
    parts = [f"`{label}`"]
    parts.extend(f" {flag}" for flag in flags)
    return "".join(parts)


def render_scopes(scopes: dict) -> list[str]:
    """Render the channel-tree scopes section (the provisioned tree)."""
    lines: list[str] = []
    lines.append("## The universal home rule")
    lines.append("")
    lines.append("Every menu pinned in a scope's `general` MUST (the registry rejects the drift):")
    lines.append("")
    lines.append("- **explain its scope** — the channel notice renders from the declared")
    lines.append("  tree automatically (one line per channel: its link and access band);")
    lines.append("- **navigate** — buttons to the scope's views, at least the admin surface;")
    lines.append("- **reach admin** — one admin button (the header carries the open padlock")
    lines.append("  on the admin surfaces, the buttons carry their own emojis).")
    lines.append("")
    lines.append("The rule holds for every game and every mod to come: their `general` menus")
    lines.append("follow the same contract, their scopes declared in the same YAML.")
    lines.append("")
    lines.append("## The channel tree (the provisioned scopes)")
    lines.append("")
    lines.append("The scopes the platform provisions — the channels and categories the")
    lines.append("sync button recreates. A channel: `key` (stable id), `label` (i18n key),")
    lines.append("`kind` (text|forum), `access` (public|readonly|admin).")
    lines.append("")
    for scope, raw in scopes.items():
        category = raw.get("category", "")
        lines.append(f"### `{scope}`")
        lines.append("")
        if category:
            lines.append(f"Category: `{category}`.")
            lines.append("")
        for ch in raw.get("channels", []) or []:
            lines.append(
                f"- `{ch['key']}` — {ch.get('label', '')} · {ch.get('kind', 'text')} · {ch.get('access', 'public')}"
            )
        lines.append("")
    return lines


def render_views(raw_views: dict) -> list[str]:
    """Render one section per view with GitHub anchors and links."""
    lines: list[str] = []
    lines.append("## The views")
    lines.append("")

    for view_id, raw in raw_views.items():
        kind = raw.get("kind", "menu")
        scope = raw.get("scope", "")
        lines.append(f"### `{view_id}`")
        lines.append("")
        if scope:
            lines.append(f"_{kind}_ — pinned in `{scope}`.")
        else:
            lines.append(f"_{kind}_.")
        lines.append("")
        buttons = raw.get("buttons") or []
        if buttons:
            lines.append("Buttons:")
            lines.append("")
            for b in buttons:
                target = b.get("target", "")
                label = b.get("label", "")
                flags = []
                if b.get("admin"):
                    flags.append("\U0001F512")
                if b.get("sync"):
                    flags.append("\u2699\ufe0f sync")
                if b.get("disabled"):
                    flags.append("*(disabled)*")
                action = b.get("action", "")
                if target:
                    lines.append(
                        f"- \u2192 [`{target}`](#{anchor(target)}) — `{label}`"
                        + (" " + " ".join(flags) if flags else "")
                    )
                elif action:
                    lines.append(f"- `{label}` — action `{action}`" + (" " + " ".join(flags) if flags else ""))
                else:
                    lines.append(f"- `{label}`" + (" " + " ".join(flags) if flags else ""))
            lines.append("")
        else:
            lines.append("_(no buttons — content comes from its wired handler)_")
            lines.append("")
    return lines


def render(raw_views: dict, scopes: dict | None = None) -> str:
    lines: list[str] = []
    lines.append("# VIEWS — the map of menus and panels")
    lines.append("")
    lines.append("<!-- Generated from kingdoms-services config/views.yaml — edit the")
    lines.append("     YAML, then regenerate (scripts/generate_views_doc.py). -->")
    lines.append("")
    lines.append(
        "Every view of the bot: its id, its kind (a **menu** only navigates; a"
        " **panel** informs or acts), where it pins, and its buttons — each a"
        " link to its target."
    )
    lines.append("")
    lines.append("Shared rules:")
    lines.append("")
    lines.append("- an admin button carries the 🔒 emoji, the most discreet style;")
    lines.append("- a sync button (channels & roles) carries the ⚙️ emoji;")
    lines.append("- every view renders its id as its footer;")
    lines.append("- caps: 20 maps/pool · 20 active pools/guild · 20 games · 20 mods.")
    lines.append("")
    lines.extend(render_views(raw_views))
    if scopes:
        lines.extend(render_scopes(scopes))
    lines.append("## Adding or moving a view")
    lines.append("")
    lines.append("Platform views live in `config/views.yaml`; a mod's own views live in")
    lines.append("its own `config/mods/<mod>.views.yaml` (a mod's files never scatter —")
    lines.append("kingdoms#179) and merge into the same registry.")
    lines.append("")
    lines.append("1. **Declare** the view in the right YAML:")
    lines.append("   id, kind, pin scope, buttons (target/label/admin/sync/action);")
    lines.append("2. **Regenerate** this doc (`scripts/generate_views_doc.py`);")
    lines.append('3. **Wire** the handler — `wire_view("<id>", handler)` for a panel,')
    lines.append('   `wire_action("<view>", "<action>", handler)` for an action;')
    lines.append("4. **Translate** the labels (`fr.yaml` and `en.yaml`, same change);")
    lines.append("5. **Test**: the registry validates the YAML — an undeclared")
    lines.append("   target, a button with no driver, an action without a label")
    lines.append("   are drifts the tests reject.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/views.yaml", help="the views YAML")
    parser.add_argument("--output", required=True, help="the generated markdown path")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    views = data.get("views")
    scopes = data.get("scopes")
    if not isinstance(views, dict):
        print(f"{args.config}: no 'views' mapping", file=sys.stderr)
        return 1

    content = render(views, scopes)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"generated {output} ({len(views)} views)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
