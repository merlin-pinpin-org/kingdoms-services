# Le bot Discord — surfaces, vues et composants

Guide de référence pour les agents et développeurs : comment le bot
présente ses surfaces sur Discord, les règles des vues épinglées,
les menus d'admin, la conventions des ids et l'i18n.

## Surfaces (canaux et panels)

Chaque guilde provisionne ses canaux au démarrage (idempotent) :

- **Salons par défaut** : `🛠-bot-logs` (journaux), `🛡-bot-admins`
  (panel admin épinglé). Provisionnés par le bot au boot.
- **Catégorie `games`** : un forum par entité et par jeu accordé —
  `aoe2-maps`, `aoe2-factions`, `aoe2-map-pools`. La création est
  **gated sur les jeux accordés** (`granted_game_keys`), pas sur le
  contenu du catalogue : un forum se crée vide et se remplit au fil
  des syncs (périodiques, horaires).
- **Salons de mods** : provisionnés par le mod (ex. ladder) via le
  `ChannelService`, selon les déclarations du mod.

## Vues épinglées (pinned views)

Un panel épinglé par salon d'admin — `🛡-bot-admins` (bot admin),
`PinnedMenuService` gère le cycle de vie : créé au boot, re-épinglé si
désépinglé, édité in-place à chaque boot (jamais de doublon). Le pin
ne doit **jamais** être remplacé par un sous-menu :

- les interactions sur le pin répondent par une vue **ephemeral dédiée**
  (`send_message(ephemeral=True)`), jamais `edit_message` sur le pin ;
- un pin ne se met à jour que si une action de n'importe qui en change
  le contenu (refresh des données, provisionnement).

### Origine des vues : `view_origin.from_pin(interaction)`

Détecte l'origine au clic : message ephemeral = flux commande,
message persistant (épinglé) = flux pin.

- **Flux pin** : pas de bouton back — le pin reste sous l'ephemeral,
  la navigation envoie de nouvelles ephemerals.
- **Flux commande** (`/home`, `/admin`...) : bouton back partout —
  l'utilisateur remonte jusqu'au menu ouvert par la commande.

Le flag `from_pin` se propage dans toute la hiérarchie des vues games
(game menu → maps/pools → éditeur de pool → pagination).

## Contexte de guilde (DM)

Les vues guild-scoped ouvertes en DM demandent la guilde via le
`GuildContextPicker` (`guild_context.py`) : un select énumère les
guildes partagées, la vue est re-dispatchée pour la guilde choisie.
Helper unique : `require_guild_context(interaction, view_key)` —
jamais de duplication par vue.

## Menus d'admin

### Panel admin épinglé (`🛡-bot-admins`)

Titres : `🛡️ Bot admin — cross-guild` en DM (bot admin), et le panel
de chaque guilde pour les admins guild + bot. Sections :

- **Langue** — select de locale (pré-rempli sur la valeur courante).
- **Salons** — select par canal géré (journaux, accueil, admin) ;
  chaque sous-menu de salon porte : provisionnement/résolution du
  canal, la visibilité (journaux), **l'option lecture seule**
  (verrouiller / ouvrir, pré-remplie sur l'état courant), et le
  bouton back. Plus de select global lecture seule dans le menu
  principal : c'est une option de chaque salon.
- **Rôles** — gestion des rôles provisionnés.
- **Accès games/mods** — demande d'accès de la guilde (bot admin
  approuve en DM) ; le bouton n'apparaît que dans un contexte de
  guilde.
- **Jeux** — visible seulement si la guilde a des jeux accordés ;
  liste les jeux (ex. `aoe2`) et le select ouvre le sous-menu du jeu
  en ephemeral dédiée.
- **Mods** — les mods enregistrés (mod registry).

### Games admin (`<Jeu> — Admin`)

Depuis le pin : jeux accordés dans le select, sous-menu du jeu en
ephemeral dédiée. Le sous-menu porte :

- **Maps** : catalogue + activer/désactiver, créer (`+ Map` locale
  guilde, `+ Map globale` bot admin).
- **Map pools** : liste, créer, éditer (rename, pagination, toggle
  des maps), dupliquer, envoyer à une autre guilde.
- **Importer une map** : par nom depuis Liquipedia (page du même nom,
  rate-limit 2s/req ; crée la map publique, stocke l'image).
- **Importer un map pool** : par id unique d'un pool **publié** d'une
  autre guilde (copie verrouillée, non modifiable).

## Ids et footer

- Tous les ids d'entités passent par `slug_id` (minuscules, non-alpha
  → `_`) : `map:aoe2:fish_n_fish`, `map_pool:aoe2:ladder_cf_s1_r1`,
  `faction:aoe2:franks`.
- Chaque post (map, pool, faction) affiche son id en footer
  (`footer(entry_id)`) — format `-# \`id\``.
- Les ids sont stables : référencés par les pools (`map_ids`), les
  contenus localisés (`entity_id`) et le registre de messages.

## Posts d'entités

### Maps (`<jeu>-maps`)

Chaque map a un post : titre, stats de jeu éventuelles, type,
description, fichiers associés (`filenames`), pools liées, image
(Liquipedia), id en footer, bouton « Ajouter au map pool » (admins,
pools de la guilde en édition uniquement). Les maps de guilde portent
un bouton « Modifier » (admin de la guilde).

### Map pools (`<jeu>-map-pools`)

Chaque pool a un post : nom, état du cycle de vie (créé / publié /
utilisé / fermé), description, mod à installer (`mod_link`), maps
(nom + image + type, chaque image lien vers le post de la map),
matchs joués éventuels, id en footer, bouton de transition d'état.

**Cycle de vie** : `draft` → `published` → `used` → `closed`.
Un pool **utilisé** est figé pour toujours ; un pool **publié** peut
revenir en `draft` (édition). L'activation par un ladder force
`used` (l'usage est un fait, pas un choix). Max 25 maps par pool.

### Factions (`<jeu>-factions`)

Chaque civ a un post : titre **localisé** (nom dans la locale de la
guilde, ex. « Francs » en FR — c'est aussi le nom du thread), sections
structurées (type, bonus, unité unique, technologies uniques, bonus
d'équipe) — le HTML du dataset est parsé (`civ_content.py`), jamais
affiché brut. Tri anti-alphabétique **sans accents** sur le nom
localisé (NFKD + casefold). Source : la page de la civ
(`aoe2techtree.net/#<Civ>`), jamais la home.

## Providers (source de vérité)

| Provider | Rôle |
|---|---|
| `ext_aoe2techtree` | Civs : contenu FR/EN, images, structure (dataset MIT local) |
| `ext_liquipedia` | Maps publiques : page par nom, image, type (API MediaWiki, rate-limit 2s/req, licence CC-BY-SA) |
| `ext_librematch` | Stats joueur (leaderboards, W/L, matchs joués) et résultats |
| `ext_aoe2lobby` | Live uniquement : qui est dans quel lobby, statut |

Les civs se synchronisent via le refresh (panel DM admin) : dataset →
catalogue + contenus FR/EN. Les maps publiques s'importent par nom
(Liquipedia) ; les maps créées dans une guilde sont privées à la
guilde. **Aucune stat par civ/map/pool** — les stats sont joueur
seulement.

## i18n

Catalogue : `config/locales/{en,fr}.yaml`, lu par `MessageCatalog`.
Règles :

- Les commandes et messages user-facing passent par `localized()` /
  `reply()` (clés `commands.*`, `replies.*`).
- Le dashboard live (`live.*`) rend selon la locale de la guilde.
- Les vues admin récentes (games, pools) sont encore en dur (FR) —
  chantier connu : migrer vers le catalogue en suivant le modèle
  `live.*` (helper `_t(bot, locale, key, fallback)`).

## Workflows de data (seed)

Layout : `data/users/` (associations), `data/ladder/<slug>/`
(season YAML + matches CSV), `data/core/aoe2techtree/` (dataset civs).
Les rotations de pools sont datées (`activated_on`), rejouées par
l'import CLI (`season_import_cli`) ; idempotent. Voir
`data/README.md`.
