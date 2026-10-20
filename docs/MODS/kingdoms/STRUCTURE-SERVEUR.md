# Structure serveur Kingdoms — RÉFÉRENCE (figée 20/10/2026, validée Drasah)

> **On n'y touche plus.** Cet état est la référence validée par Drasah
> (« le Discord commence à être parfait »). Tout patch futur doit le
> préserver, sauf validation explicite. Miroir : Knowledge
> `kingdoms-checkpoints/etat-serveur-reference.md` (Checks 83-92).
> Image de référence : `pr-244-20261020014610-b0b7cce` (PR #244).

## Invariant « Nouvel arrivant »

Un membre sans rôle ne voit **QUE Beta + Conscription (📝 postuler)** —
ni salons, ni en-têtes de catégories. Tout le reste : deny `@everyone`
(`view_channel=False`), ouvert uniquement via Kingdoms Lord/King/Admin
(accordés à l'acceptation). Règle pour tout futur patch : tout nouveau
salon/catégorie est privé par défaut (`everyone_view: false` + rôles),
toute nouvelle catégorie joueur rejoint `_PLAYER_CATEGORY_SLUGS`
(kingdom_setup), un test couvre le deny, jamais de retour @everyone.

## Charte « grimoire » (ordre-salons-v1)

Ordre sidebar : Beta → ✒️ ᴄᴏɴsᴄʀɪᴘᴛɪᴏɴ → ⚔️ ᴄʜᴀᴍᴘs ᴅᴇ ʙᴀᴛᴀɪʟʟᴇ →
🏰 ᴋɪɴɢᴅᴏᴍs → 🏰 ɢᴀɪᴀ → 🏰 {Royaume}… → ⚙️ ᴀᴅᴍɪɴ (toujours derrière le
dernier royaume) → 👤 ᴘʀᴏꜰɪʟs (tout en bas). Catégories small caps +
emoji, salons minuscules + emoji, dividers « ┈┈ ✦ ┈┈ » muets, topics
micro-lore, royaumes « 🏰 {Nom} ». Support et Général : supprimés.
Conscription masquée aux Lord/King validés.

## Gating (permissions-serveur-v1 + bis)

- Salons joueurs (Scriptorium, Marché, Géopolitique, Territoire,
  attaque, Pourparlers) : `everyone_view: false`, rôles Lord/King/Admin.
- Salons admin (Candidatures, Royaumes, Paramètres, Gestion saison,
  Machine à temps, Distributeur, Console, Demandes) : Kingdoms Admin
  seul ; catégorie Admin masquée (deny ré-appliqué à chaque bootstrap).
- En-têtes des 4 catégories joueurs (Profils, Kingdoms, Gaïa,
  Champs de Bataille) : masqués à @everyone, débloqués par rôles —
  une seule API call par catégorie (anti rate-limit).
- Profils privés par membre ; royaumes privés par royaume.

## Panels & annonces (vues-silencieuses-v1)

- Panels épinglés (le-royaume, territoire, paroisse, contenu Gaïa,
  Marché, Paramètres) : **edit en place** + garde « contenu identique =
  zéro appel API » + purge des doublons. Plus jamais delete+send.
- Draft starter / placeholder Alliances : idempotents, placeholder
  supprimée au reveal, draft révélé jamais masqué.
- `allowed_mentions=NO_MENTIONS` sur tout send de panel — un panel ne
  ping jamais. Les vraies annonces gardent le ping explicite.

## Où vit chaque règle dans le code

- `config/mods/kingdoms.yaml` : charte, groupes, salons, blocs `access:`.
- `kingdom_setup.py` : `_slug` décoration-aveugle, charte, ordre,
  `_enforce_admin_category_deny`, `_enforce_player_category_gating`,
  `_hide_conscription_from_players`, dividers, topics.
- `panel_messages.py` : `NO_MENTIONS`, `view_text`, `_squash`.
- `kingdom_realm_content.py` `_upsert_view`, `kingdom_panels.py`
  (Marché/Paramètres edit-en-place), `kingdom_realms.py` (draft/
  placeholder idempotents) : le pattern upsert commun.
