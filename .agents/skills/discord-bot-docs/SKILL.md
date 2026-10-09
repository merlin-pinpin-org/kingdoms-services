---
name: discord-bot-docs
description: Read and keep docs/DEVELOPMENT/DISCORD-BOT.md up to date whenever a Discord surface changes — views, pinned panels, menus, components, ids, i18n, providers. The doc is hand-maintained: agents update it on every surface change.
---

# Discord bot documentation

`docs/DEVELOPMENT/DISCORD-BOT.md` is the reference for every Discord
surface of the bot. It is **hand-maintained**: no generator writes it,
so it drifts silently unless sessions update it.

## When a session MUST read it

Before touching any file under `src/kingdoms/discord/` (views, panels,
flows, pinned menus, forums, i18n) — the doc states the current rules
(pin immutability, view origin, guild context, ids, i18n) and the file
map; they constrain every change.

## When a session MUST update it

After changing a Discord surface, update the matching section:

- new/changed **channel or forum** → "Surfaces" ;
- new/changed **pinned panel or its interaction rules** → "Vues
  épinglées" (and the origin rules if back-button behavior moved) ;
- new **admin menu, button, modal, select** → "Menus d'admin" ;
- new **entity post shape** (maps, pools, civs) → "Posts d'entités" ;
- new **id form or footer convention** → "Ids" ;
- new/renamed **locale keys or hardcoded strings** → "i18n" ;
- new **provider role** → "Providers" ;
- new **seed layout or CLI** → data/README.md (cross-reference).

The update lands in the same commit as the code change — a PR that
changes a surface without its doc update is incomplete.

## Rule separation (keep the doc structured this way)

The doc separates three kinds of rules, in this order:

1. **Conventions projet** — choices this project made and can change
   by itself (pin immutability, ephemeral sub-views, `slug_id`, the
   state machine, French admin strings with an English catalog fallback...).
2. **Contraintes de la lib (discord.py)** — library mechanics
   (DynamicItem rebuild from custom_id, LayoutView children budget,
   modal lifecycles, permission overwrites...).
3. **Contraintes Discord (la plateforme)** — hard platform limits
   (4096-char embeds, 25 select options, edit quotas on old messages,
   ephemeral messages invisible to others, 100-char custom ids...).

Never mix them: a rule that can only change by upgrading discord.py
is a lib constraint; one that can only change if Discord changes its
API is a platform constraint; everything else is a project convention
the team may revisit.
