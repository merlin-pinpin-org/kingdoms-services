# Le SDK modder — guide clé en main

Ce document s'adresse aux game designers et à leurs agents. Il décrit
**tout** ce qu'il faut savoir pour ajouter un mod (kingdoms, ladder, …) :
définir un modèle de données, des interactions, des menus épinglés et une
hiérarchie de salons — **sans jamais toucher à discord.py, Mongo ou la
stack**.

Le contrat : un mod déclare, la plateforme possède les conséquences
techniques. L'exemple complet (§7) fait moins de 150 lignes.

---

## 1. Le périmètre du SDK

```python
from kingdoms.modsdk import (
    Field,           # un champ d'entité (label, type, éditeur)
    EntitySpec,      # un type d'entité (territoire, royaume, match…)
    ActionSpec,      # une interaction (claim, publish, close, delete…)
    ActionResult,    # ce qu'un handler retourne
    EntityPanelSpec, # la surface forum d'une entité + ses actions admin
    MenuSpec,        # un menu épinglé figé
    EntityStore,     # (avancé) accès direct au stockage d'une entité
    bind_mod,        # brancher le mod sur la plateforme
)
```

Trois sortes de déclarations, rien d'autre :

| Déclaration | Rôle | Ce que la plateforme en fait |
|---|---|---|
| `EntitySpec` + `Field` | la donnée du mod | stockage Mongo générique, ids stables, cartes Components V2 |
| `ActionSpec` | les interactions | boutons Discord persistants, permissions, modals, persistance |
| `EntityPanelSpec` / `MenuSpec` | où vivent les surfaces | provision des salons dans la langue de la guilde, pin figé, autoheal |

Le jeu intégré aoe2 (maps, map pools, civs) est construit sur ce SDK —
son binding est l'exemple de référence dans le code.

---

## 2. Déclarer une entité

```python
from kingdoms.modsdk import EntitySpec, Field

TERRITORY = EntitySpec(
    key="territory",                       # le nom interne, unique dans le mod
    id_prefix="territory",                 # ids : territory:<scope>:<slug>
    label_key="kingdoms.entity.territory", # clé i18n du nom affiché
    states=("draft", "owned", "contested", "razed"),  # le cycle de vie
    initial_state="draft",                 # l'état d'une entité nouvelle
    fields=(
        Field(key="name", label_key="kingdoms.field.name", required=True),
        Field(key="terrain", label_key="kingdoms.field.terrain", kind="select",
              choices=(("plains", "kingdoms.terrain.plains"),
                       ("hills", "kingdoms.terrain.hills"))),
        Field(key="description", label_key="kingdoms.field.description", kind="long_text"),
        Field(key="image_url", label_key="kingdoms.field.image", kind="image"),
        Field(key="source_url", label_key="kingdoms.field.source", kind="url"),
    ),
    title_field="name",        # le titre de la carte
    image_field="image_url",   # l'image principale de la carte
    source_field="source_url", # le lien Source : affiché sous le titre
)
```

Règles :

- **`kind`** choisit le rendu **et** l'éditeur : `text`, `long_text`,
  `number`, `bool`, `url`, `image`, `select` (avec `choices`).
- **`label_key`** est une clé i18n (`config/locales/<lang>.yaml`) ; le
  fallback anglais est le nom du champ. Tous les libellés sont traduits
  dans la langue de la guilde.
- **`states`** pilote le grisé des boutons (§4) ; laisser vide pour une
  entité sans cycle de vie.
- Les ids sont attribués par la plateforme :
  `<id_prefix>:<scope>:<slug>` (`territory:public:buda_hills`,
  `territory:<guild_id>:buda_hills`). **Ne jamais forger un id à la
  main** — passer par le store (§6).

### Cycle de vie des entités

- `create` → `initial_state` ;
- chaque action peut déplacer l'état (`ActionResult.new_state`) ;
- `archive` est réversible (l'entité est marquée, jamais détruite) ;
- un bouton `confirm=True` (§4) protège les actions destructives.

---

## 3. Stockage : ce que le mod peut lire/écrire

Le mod ne écrit **jamais** de requête Mongo. Les stores du SDK sont la
seule surface de persistance autorisée :

```python
store = binding.store("territory")

entity_id = store.entity_id("Buda Hills", scope)   # l'id stable
entity = await store.create(scope, "Buda Hills", {"terrain": "hills"})
entity = await store.get(entity_id)
entities = await store.list(scope)                 # actifs, triés
entity = await store.update(entity_id, {"terrain": "plains"}, new_state="owned")
entity = await store.archive(entity_id)            # réversible
```

Les indexes Mongo sont gérés par la plateforme (scope + état, ids
uniques). Le cache Redis et le partitionnement par guilde sont
également portés par la plateforme.

---

## 4. Déclarer les interactions

```python
from kingdoms.modsdk import ActionSpec, ActionResult

async def claim(entity, actor, values):
    # handler pur : (entity, actor, input) -> ActionResult
    return ActionResult(new_state="owned",
                        message=f"{entity['name']} revient à {actor.mention} !")

ACTIONS = (
    ActionSpec(key="claim", label_key="kingdoms.action.claim",
               from_states=("draft", "contested"), permission="everyone"),
    ActionSpec(key="rename", label_key="kingdoms.action.rename",
               from_states=("draft", "owned"), permission="owner",
               prompt=(Field(key="name", label_key="kingdoms.field.name", required=True),)),
    ActionSpec(key="raze", label_key="kingdoms.action.raze",
               from_states=("owned",), permission="admin", confirm=True),
)
```

Le pipeline d'un clic (la plateforme possède tout, le mod ne fait que
répondre) :

1. **permission** — `admin` (bot-admins), `owner` (le créateur de
   l'entité), `everyone` ; vérifiée au clic, refus éphémère sinon ;
2. **gate d'état** — `from_states` grisée sur l'éphémère admin (vide =
   toujours disponible) ; un clic sur un bouton grisé répond
   « action indisponible » au lieu d'échouer ;
3. **`confirm=True`** — une modal demande de **retaper le nom exact**
   de l'entité avant d'exécuter (les actions destructives) ;
4. **`prompt=(Field...)`** — une modal collecte les champs, rendus
   selon leur `kind` (`long_text` → zone de texte multiligne, etc.) ;
5. **le handler s'exécute** — pur, testable, sans Discord ni Mongo :
   `(entity, actor, values) -> ActionResult` ;
6. **la plateforme persiste** (`entity`, `new_state`), **re-rend** toutes
   les surfaces publiques (posts forum, pools liés) via le hook `sync`,
   et répond au clic avec `message`.

Le handler ne rend jamais une vue ni un composant : `ActionResult`
seulement. Un handler qui lève une exception est capturé — l'utilisateur
voit « l'action a planté, voir les logs », rien ne casse.

---

## 5. Déclarer les surfaces

### 5.1 La carte publique (le post forum)

```python
from kingdoms.modsdk import EntityPanelSpec

PANEL = EntityPanelSpec(
    entity="territory",
    channel="territories",     # le salon/forun de l'arbre (§5.2)
    admin_actions=ACTIONS,     # les boutons de l'éphémère admin
)
```

La carte est rendue par la plateforme sur le template Components V2
partagé (`entity_cards`) :

```
# Titre + thumbnail (l'icône)
État / chaque propriété sur sa propre ligne
———
Description
———
Image principale
———
Source : [label](url)
-# `territoire:public:buda_hills`
```

**La règle ⚙️ Admin** (règle d'or du repo) : un post public ne porte
**jamais** de boutons d'action — un seul bouton **⚙️ Admin**, dont le
clic vérifie la permission et ouvre un éphémère de gestion qui porte
les actions du mod, grisées selon l'état. Les boutons dangereux ne
sont ainsi jamais exposés à qui n'a pas le droit de les utiliser.
Appliquer cette règle partout où elle s'applique ; elle est documen-
tée dans `docs/DEVELOPMENT/DISCORD-BOT.md`.

### 5.2 L'arbre de salons

Chaque mod déclare son dossier dans l'arbre de la guilde
(`ScopeSpec` + `ChannelDecl` du Panels SDK — voir
`docs/DEVELOPMENT/DISCORD-BOT.md` pour les règles d'organisation des
salons : général → discussions → données → admin → décisions, salons
de menu en lecture seule, traduction à la création, etc.). Les salons
sont provisionnés idempotemment dans la langue de la guilde, recréés
automatiquement à leur suppression (autoheal), et les menus épinglés
sont figés : leurs boutons ouvrent des éphémères, jamais d'édition
in-place du pin.

```python
from kingdoms.discord.ui.panels import ChannelDecl, PanelSpec, PinMode, ScopeSpec

KINGDOMS_SCOPE = ScopeSpec(
    scope="kingdoms",
    category_key="kingdoms.category",
    channels=(
        ChannelDecl(key="general", label_key="kingdoms.channel.general", access="readonly"),
        ChannelDecl(key="discussions", label_key="kingdoms.channel.discussions"),
        ChannelDecl(key="territories", label_key="kingdoms.channel.territories", kind="forum"),
        ChannelDecl(key="admin", label_key="kingdoms.channel.admin", access="admin"),
        ChannelDecl(key="decisions", label_key="kingdoms.channel.decisions", access="admin"),
    ),
)
```

### 5.3 Les menus épinglés

```python
from kingdoms.modsdk import MenuSpec

HOME_MENU = MenuSpec(
    key="kingdoms-home",
    label_key="kingdoms.menu.home",
    scope="kingdoms",
    channel_key="general",
    build=build_home_menu,   # async (guild_id, locale) -> LayoutView
)
```

Le SDK épingle le menu (registre `view:<guild-id>:<slug>`), le soigne
(autoheal sur suppression), et son contenu suit la template « home » :
une description claire, quelques boutons, rien de plus. Chaque bouton
ouvre un éphémère dans lequel on peut naviguer.

---

## 6. Brancher le mod (le binding)

Un seul appel au wiring du bot, au démarrage :

```python
from kingdoms.modsdk import bind_mod
from kingdoms.discord.commands_i18n import tr_guild
from kingdoms.discord.wiring import guard_bot_admin

binding = bind_mod(
    "kingdoms",
    database=get_async_database(),
    entity_specs=(TERRITORY, KINGDOM, MATCH),
    panels=(TERRITORY_PANEL, KINGDOM_PANEL),
    sync=sync_kingdoms_forum,   # (entity_key, entity_id, bot) -> re-rend le post
    tr=tr_guild,               # (guild_id, key, fallback) -> label localisé
    permission=guard_bot_admin,# (interaction) -> bool, répond seul au refus
)
bot.modsdk_bindings["kingdoms"] = binding
from kingdoms.modsdk import register_dynamic_items
register_dynamic_items(bot)   # une fois pour tous les mods
```

`bind_mod` est idempotent (dernier gagnant). Le registre
`bot.modsdk_bindings` permet aux boutons persistants de retrouver leur
binding au clic (redémarrage compris — le custom_id porte
`modsdk:admin:<entity>:<id>`).

### Hooks optionnels

| Hook | Signature | Défaut |
|---|---|---|
| `sync` | `async (entity_key, entity_id, bot)` | no-op (les posts se re-rendent au prochain sync horaire) |
| `tr` | `(guild_id, key, fallback) -> str` | fallback brut (clé/anglais) |
| `permission` | `async (interaction) -> bool` | autorisé (déconseillé) |

---

## 7. Exemple complet : un mod « kingdoms » minimal

```python
# mods/kingdoms/declaration.py
from kingdoms.modsdk import (
    ActionSpec, ActionResult, EntityPanelSpec, EntitySpec, Field,
)

TERRITORY = EntitySpec(
    key="territory",
    id_prefix="territory",
    label_key="kingdoms.entity.territory",
    states=("draft", "owned", "razed"),
    initial_state="draft",
    fields=(
        Field(key="name", label_key="kingdoms.field.name", required=True),
        Field(key="terrain", label_key="kingdoms.field.terrain",
              kind="select", choices=(("plains", "k.terrain.plains"), ("hills", "k.terrain.hills"))),
        Field(key="description", label_key="kingdoms.field.description", kind="long_text"),
    ),
)

async def claim(entity, actor, values):
    return ActionResult(new_state="owned",
                        message=f"**{entity['name']}** est revendiqué par {actor.mention}.")

async def raze(entity, actor, values):
    del actor, values
    return ActionResult(new_state="razed", message=f"**{entity['name']}** est rasé.")

TERRITORY_PANEL = EntityPanelSpec(
    entity="territory",
    channel="territories",
    admin_actions=(
        ActionSpec(key="claim", label_key="kingdoms.action.claim",
                   from_states=("draft",), permission="everyone", handler=claim),
        ActionSpec(key="raze", label_key="kingdoms.action.raze",
                   from_states=("owned",), permission="admin", confirm=True, handler=raze),
    ),
)
```

C'est tout. Aucun import de `discord`, aucune requête Mongo, aucune
gestion de pin, aucune vue à maintenir. La carte, les boutons, les
modals, les permissions, les traductions, le stockage, le re-rendu :
la plateforme.

---

## 8. Traductions

Chaque libellé est une clé i18n résolue dans la langue de la guilde.
Ajouter les clés dans `config/locales/fr.yaml` (et les autres
langues) :

```yaml
kingdoms:
  entity:
    territory: Territoire
  field:
    name: Nom
    terrain: Terrain
    description: Description
  action:
    claim: Revendiquer
    raze: Raser
```

Règles de traduction du repo : traduire **100 % du user-facing**
(labels de boutons, titres de modals, messages), ne jamais traduire
les ids ni les custom_ids.

---

## 9. Checklist « nouveau mod »

1. Déclarer les entités (`EntitySpec` + `Field`).
2. Déclarer les actions (`ActionSpec` + handlers purs).
3. Déclarer les panels (`EntityPanelSpec`) et l'arbre de salons
   (`ScopeSpec` + `ChannelDecl`) selon les règles d'organisation.
4. Ajouter les menus (`MenuSpec`) : description + quelques boutons.
5. Ajouter les clés i18n dans toutes les langues.
6. `bind_mod(...)` au wiring + `bot.modsdk_bindings[...]` +
   `register_dynamic_items(bot)` (une fois).
7. Tests : les handlers sont purs — les tester comme des fonctions
   (voir `tests/unit/test_modsdk.py` pour le pipeline complet).

En cas de doute : lire le binding du jeu aoe2 (maps/pools/civs) —
c'est la référence.
