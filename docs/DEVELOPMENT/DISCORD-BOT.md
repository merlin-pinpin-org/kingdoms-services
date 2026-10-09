# Le bot Discord — surfaces, vues et composants

Guide de référence pour les agents et développeurs : comment le bot
présente ses surfaces sur Discord. La maintenance de ce document est
portée par la skill `discord-bot-docs` (.agents/skills/) : **toute
modification d'une surface Discord met à jour la section
correspondante dans le même commit**.

Les règles sont séparées en trois catégories — ne jamais les mélanger :

---

## Partie 1 — Conventions projet

Des choix que **le projet a faits** et qu'il peut faire évoluer seul.

### Surfaces (canaux et panels)

Chaque guilde provisionne ses canaux au démarrage (idempotent) :

- **Salons par défaut** : `🛠-bot-logs` (journaux), `🛡-bot-admins`
  (panel admin épinglé), `🏛-home` (accueil — l'ancien
  `kingdoms-home` est renommé in-place au premier passage).
- **Self-healing universel** : tout salon, forum ou post géré par le
  bot se recrée s'il est supprimé — les salons gérés via la boucle
  horaire `_maintain_managed_channels` (résolution cache-aside :
  Redis → Mongo → adoption → création), les forums et leurs posts
  via les syncs horaires, le dashboard live via son intervalle
  court, les pins via leurs boucles dédiées.
- **Catégorie `games`** : un forum par entité et par jeu accordé —
  `aoe2-maps`, `aoe2-factions`, `aoe2-map-pools`. Gated sur les jeux
  **accordés** (`granted_game_keys`), pas sur le contenu : un forum se
  crée vide et se remplit aux syncs (horaires).
- **Salons de mods** : provisionnés par chaque mod (ChannelService).

### Vues épinglées (pinned views)

Un panel épinglé par salon d'admin. Règles projet :

- Le pin est **figé** : une interaction sur le pin répond par une
  vue **ephemeral dédiée** (`send_message(ephemeral=True)`), jamais
  `edit_message` sur le pin.
- Le pin ne se met à jour que si une action (de n'importe qui) en
  change le contenu ; sinon il est édité in-place au boot seulement.
- **Origine des vues** (`view_origin.from_pin`) : message ephemeral =
  flux commande → bouton back partout (on remonte au menu de la
  commande) ; message persistant = flux pin → **pas de back** (le pin
  reste sous l'ephemeral). Le flag `from_pin` se propage dans toute
  la hiérarchie des vues.
- **Contexte de guilde (DM)** : les vues guild-scoped ouvertes en DM
  demandent la guilde via `GuildContextPicker` — helper unique
  `require_guild_context`, jamais de duplication par vue.

### Menus d'admin

**Panel épinglé** (`🛡️ Bot admin — cross-guild` en DM, panel par
guilde pour les admins) — sections : Langue (pré-rempli), Salons
(sous-menu par salon : provisionnement, visibilité journaux,
**lecture seule** — option de chaque salon, pré-remplie —, back en
flux commande), Rôles, Accès games/mods (contexte guilde seulement),
Jeux (uniquement si jeux accordés, listés dès le menu), Mods.

**Games admin** (`<Jeu> — Admin`) : Maps (catalogue, activer, créer),
Map pools (lister, créer, éditer, dupliquer, envoyer à une guilde),
Importer une map (nom → Liquipedia), Importer un map pool (id unique
d'un pool **publié** d'une autre guilde → copie verrouillée).

### Ids

- Tous les ids passent par `slug_id` (minuscules, non-alpha → `_`) :
  `map:aoe2:fish_n_fish`, `map_pool:aoe2:ladder_cf_s1_r1`.
- Chaque post affiche son id en footer (`footer(entry_id)`).
- Les ids sont stables : référencés par les pools, les contenus
  localisés et le registre de messages.

### Posts d'entités

- **Maps** : titre, type, description, fichiers (`filenames`), pools
  liées, image, id, bouton « Ajouter au map pool » (admins, pools de
  la guilde en édition). Maps de guilde : bouton « Modifier ».
- **Map pools** : nom, état du cycle de vie, description, mod à
  installer (`mod_link`), maps (nom + image + type, image = lien vers
  le post), id, bouton de transition. Cycle : `draft` → `published` →
  `used` → `closed` ; utilisé = figé ; publié peut revenir en draft ;
  l'activation par un ladder force `used` ; max 25 maps.
- **Factions** : titre **localisé** (nom du thread inclus), sections
  structurées (HTML parsé via `civ_content.py`, jamais brut), tri
  anti-alphabétique **sans accents** (NFKD + casefold) sur le nom
  localisé, source = la page de la civ.

### Providers (source de vérité)

| Provider | Rôle |
|---|---|
| `ext_aoe2techtree` | Civs : contenu FR/EN, images, structure |
| `ext_liquipedia` | Maps publiques : page par nom, image, type |
| `ext_librematch` | Stats joueur (leaderboards, W/L) et résultats |
| `ext_aoe2lobby` | Live uniquement : lobbys, statuts |

Aucune stat par civ/map/pool — les stats sont joueur seulement.

### i18n

- Catalogue `config/locales/{en,fr}.yaml`, racine par langue (`fr:` /
  `en:`) — **attention** : une section hors de la racine de langue
  est silencieusement ignorée (le rendu retombe en anglais).
- Clés : `commands.*`, `replies.*`, `replies_shared.*` (messages
  admin partagés), `live.*` (dashboard, rendu par locale de guilde).
- **Aucune chaîne user-facing n'est écrite en dur** : les réponses
  passent par `reply()` (`replies.*`, `replies_shared.*`), les labels
  de composants par `tr()`/`tr_guild()` (`ui.*`) — valeurs FR **et**
  EN dans le catalogue ; le rendu suit la locale de la guilde, le
  fallback est l'anglais. Un composant persistant résout son label au
  clic (rebuild DynamicItem) ou par la locale de guilde (builds de
  fond).

---

## Partie 2 — Contraintes de la lib (discord.py)

Des mécaniques de la bibliothèque — changent en upgradeant discord.py.

- **DynamicItem** : un composant persistant est reconstruit **au
  clic** depuis son custom_id (`from_custom_id`) — jamais d'état
  capturé dans le composant ; tout état nécessaire vit dans le
  custom_id ou se relit en base au clic.
- **LayoutView / Components V2** : budget d'enfants par vue (40),
  Sections avec accessory, ActionRow de 5 — les posts longs paginent.
- **Modals** : formulaires courts (TextInput) ; un modal ne peut pas
  ouvrir un autre modal — la chaîne passe par un message ephemeral.
- **PermissionOverwrite** : le provisionnement des salons restreint
  `default_role` (lecture) et ouvre `guild.me` (écriture/gestion).
- **Les messages ephemeral** : visibles uniquement par leur
  destinataire (les autres ne les voient pas, ni le pin, ni les
  logs Discord du salon).

---

## Partie 3 — Contraintes Discord (la plateforme)

Des limites de l'API Discord — hors de notre contrôle.

- **Embed description** : 4096 caractères max → pagination
  multi-messages (dashboard live).
- **Select** : 25 options max → pagination des catalogues ; une
  option `default=True` pré-remplit le sélecteur.
- **Custom id** : 100 caractères max → les ids longs sont tronqués
  (le suffixe d'abord : ne jamais y mettre des données critiques).
- **Quota d'édition** des messages de plus d'une heure (erreur
  30046) → les syncs hachent le contenu avant d'éditer ; une édition
  inchangée ne part jamais.
- **Messages ephemeral** : non éditables par d'autres, invisibles
  aux autres utilisateurs, et le composant `ephemeral` du flag n'est
  pas exposé sur l'interaction — l'origine d'une vue se déduit du
  message cliqué (`interaction.message.flags`).
- **Rate limits** globaux (429) : les provisioning de masse (forums,
  pins) s'étalent ; les providers externes ont leurs propres limites
  (Liquipedia : 1 req/2 s, 1 parse/30 s).

---

## Workflows de data (seed)

Layout : `data/users/`, `data/ladder/<slug>/` (season YAML daté
`activated_on` + matches CSV), `data/core/aoe2techtree/` (dataset
civs, MIT — ne pas éditer). Imports idempotents via CLIs
(`season_import_cli`, `content_refresh_cli`). Détails : `data/README.md`.
