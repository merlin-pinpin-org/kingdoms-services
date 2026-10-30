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

### Ssocle de commandes (2026-10-16)

**Trois commandes, et c'est tout** : `/home`, `/admin`, `/status`. Tout le
reste est une surface épinglée (autopin/autoheal) ou un menu éphémére
ouvert depuis le home : l'inscription aux profils vit dans
/home > Profil (les commandes /register, /game-link, /game-unlink et
/live ont été retirées — le dashboard live est épinglé dans son
salon). Le context menu « Nommer staff » reste (clic droit sur un
membre, pas une commande).

### Surfaces (canaux et panels)

Chaque guilde provisionne ses canaux au démarrage (idempotent) :

- **Salons par défaut** : `🛠-bot-logs` (journaux), `🛡-bot-admins`
  (panel admin épinglé), `🏛-home` (accueil — l'ancien
  `kingdoms-home` est renommé in-place au premier passage).
- **Self-healing universel, événementiel** : tout salon géré par le
  bot se recrée **à l'instant** de sa suppression
  (`on_guild_channel_delete` → re-résolution : Redis → Mongo →
  adoption → création), **et son pin revient avec lui** — le heal
  appelle `heal_static_pins`, qui recrée chaque vue épinglée
  statique dans le salon recréé. Les forums et leurs posts se
  recréent via les syncs horaires (le réactif par thread raterait le
  bookkeeping des fingerprints) ; le dashboard live via son
  intervalle court. Bouton **Recreate all channels** (section Rôles)
  pour forcer une recréation manuelle.
- **Vues épinglées statiques — registre déclaratif**
  (`static_pins.py`) : une pinned view statique s'enregistre une
  fois au wiring via `register_static_pin(StaticPinnedView(key,
  mark_suffix, resolve_channel, build_layout, registry))`. Le cycle
  partagé (`ensure_static_pin`) possède tout : résolution du salon
  (recréé au besoin), création du pin manquant, édition in-place du
  pin vivant (id stable), re-pin si désépinglé, marque `fixe:`, heal
  sur delete de salon. **Toute nouvelle pinned view statique passe
  par ce registre** — jamais de cycle ad-hoc.
- **Superseded générique** (`delete_when_superseded`, défaut
  **True** sur `StaticPinnedView`) : un pin remplacé se dépine puis
  se **supprime** (mark-guarded : seul un message `fixe:` du bot se
  supprime). Une surface qui garde son historique déclare `False` :
  **boot status (`bot-logs`) est le cas` — les anciens statuts se
  dépinent (contrat : seul le dernier reste épinglé) mais les
  messages restent lisibles dans la trace de logs.
- **Salons de mods** : provisionnés par chaque mod (ChannelService).

### Vues épinglées (pinned views)

Un panel épinglé par salon d'admin. Règles projet :

- Le pin est **figé** : une interaction sur le pin répond par une
  vue **ephemeral dédiée** (`send_message(ephemeral=True)`), jamais
  `edit_message` sur le pin.
- **Marque de pin** (`pinned_marks.py`) : chaque message pinned porte
  un footer id `fixe:<suffixe>` (`fixe:admin-panel`, `fixe:home-menu`,
  `fixe:live-dashboard`, `fixe:boot-status`). Tout chemin de
  suppression **doit** vérifier `is_pinned_view(message)` d'abord —
  un message marqué ne se supprime jamais (il se vide ou se
  remplace) ; les deletes sont réservés aux messages non marqués.
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

### Fil d'activité AoE2 (`live_activity.py`)

Chaque transition d'un joueur lié (entrée en lobby, lancement de
partie, fin) atterrit en UN message dans le channel **logs** de
chaque guild concernée (le joueur y est membre), dans l'ordre
d'arrivée — lire le channel dit qui joue / a joué et quand :

- `🟡 **Pseudo** entre dans le [lobby #123](lien join)` ;
- `🟢 **Pseudo** lance la [partie #123](lien watch) : **mode** — map`
  suivi du roster (`nom (civ) — équipe n`) ;
- `⚪ Le match de **Pseudo** est terminé` (le nom persiste via le
  dernier match connu).

La source : le flux `WatchPlayerEvents` du service gamedata (les
enrollments souscrivent déjà le provider aux profile_ids des guilds)
; le transport : `logs_service.log_event` ; la boucle est résiliente
(stream cassé → reconnexion, un envoi raté ne tue rien).

### Menus d'admin

**Panel épinglé** (`🛡️ Bot admin — cross-guild` en DM, panel par
guilde pour les admins) — sections : Langue (pré-rempli), Salons
(sous-menu par salon : provisionnement, visibilité journaux,
**lecture seule** — option de chaque salon, pré-remplie —, back en
flux commande), Rôles (rôles provisionnés + **mapping fonction →
  rôles** : n'importe quel rôle de la guilde peut porter une fonction
  du bot — `bot-admins`, `staff` ; le multi-select pré-coche le
  mapping actuel, sélection vide = retour au défaut ; BOT_ADMINS et
  admins Discord passent toujours, le mapping s'ajoute ; bouton
  **Recreate all channels**), Accès games/mods (contexte guilde seulement),
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

**`ext_librematch` et l'inscription** : la validation d'un profile id passe
par `GetPlayerStats`, qui appelle l'endpoint **public** de la community
API (`/community/leaderboard/GetPersonalStat?title=age2&profile_ids=...`)
— aucune clé API n'est requise (la leçon du 2026-10-16 : un gate par
clé refusait tous les profils alors que l'endpoint répond à tout le
monde). Seul le budget de rate Redis throttle les appels. Un provider
mal configuré ou injoignable échoue avec l'erreur explicite
`ProviderStatsUnavailableError` — jamais un refus silencieux.

**L'id à stocker est le relic-id** (`profile_id` WorldsEdge, ex.
17190369) — pas l'id de l'URL aoe2insights (ex. 12488557) : ce sont
deux nombres différents. La seam résout l'id tapé (relic-id, id
insights ou URL aoe2insights) via la page publique
`aoe2insights.com/user/<id>/relic-id/` (texte nu) ; l'extraction est
stricte (regex ancrée + host urlparse, id ≤ 15 digits, réponse
numérique nue exigée) et le binding stocke toujours le relic-id
canonique renvoyé par la validation.

**Référence libre:match (endpoints publics, sans auth)** :
https://librematch.github.io/wiki/rlink/community/leaderboard/index.html —
`getAvailableLeaderboards`, `getLeaderBoard2`, `getPersonalStat`,
`getAvatarStatForProfile`, `getMatchHistory`, `getRecentMatchHistory`,
`getReplayFiles` (base : `aoe-api.worldsedgelink.com/community/leaderboard/`).

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
- **Modals** : formulaires courts (TextInput) **et selects** — depuis
  les nouveaux composants de modal (discord.py >= 2.6), un select vit
  dans le modal et sa valeur se lit à la soumission via
  ``select.values`` (le ``Modal._refresh`` peuple l'item depuis le
  payload). **Un select dans un modal DOIT être enveloppé dans un
  composant Label** (``discord.ui.Label(text=..., component=select)``)
  — un select nu est rejeté silencieusement par l'API (le bouton
  semble ne pas répondre). Un modal ne peut toujours pas ouvrir un
  autre modal — la chaîne passe par un message ephemeral.
  `send_modal` exige le token de réponse : toute lecture avant lui doit
  être rapide (timeout 2,5 s + fallback). Un flux utilisateur qui
  échoue répond avec la cause précise (provider, jeu, quoi
  vérifier) et DM la stack aux BOT_ADMINS via
  ``kingdoms.discord.error_report.dm_admins`` — garde anti-spam : un
  DM par (type d'erreur, surface) et 5 minutes.
- **PermissionOverwrite** : le provisionnement des salons restreint
  `default_role` (lecture) et ouvre `guild.me` (écriture/gestion).
- **Les messages ephemeral** : visibles uniquement par leur
  destinataire (les autres ne les voient pas, ni le pin, ni les
  logs Discord du salon).

---

## Partie 3 — Contraintes Discord (la plateforme)

Des limites de l'API Discord — hors de notre contrôle.

- **Embed description** : 4096 caractères max → pagination
  dashboard live). The live dashboard is EPHEMERAL-ONLY (owner
  direction 2026-10-30): never pinned, never a persistent message —
  rendered on demand (/live, the menu button, the refresh button)
  as ONE components-v2 view (Container + a relative timestamp
  `<t:...:R>` + a `live:refresh:<game>` button + the id footer), in
  the guild's locale with the bot's catalog. The refresh button
  updates the message that carries it (the view SDK) and ACKs first
  (defer). The full lobby state (civs, map, settings, slots) travels
  the chain (match_updated + match_json). No channel, no loop, no
  registry entry — the 429 storm and the refresh loop are gone with
  the pinned channel.
- **View answers (the SDK rule)** (`ui/responsive.py`): every view
  callback answers through `respond_with_view` — a component click
  UPDATES the message it rode (an ephemeral through its webhook, a
  menu message in place; a pinned message answers with an ephemeral,
  its content updates through its ensure cycle), a message-less
  interaction (a slash command) always sends an ephemeral. Slow
  callbacks ack first through `ack_view_interaction` (the 3-second
  rule). The rule lives in ONE module — no view hand-rolls its
  transport.
- **Profile names** (`home._resolve_profile_names`): a link's label
  is the binding's stored `profile_name`, else the stored alias,
  else the provider-resolved alias (librematch) — never a bare '?'.
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
