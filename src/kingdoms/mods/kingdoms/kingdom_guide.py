"""Kingdoms mod — the designer's guide of the Règles forum (guide-regles-v1).

Drasah (17/10/2026) opens the beta and wants the existing Règles forum
to carry a complete player guide, A→Z: one thread per topic, authored
in the designer's voice (French-only, like ``kingdom_content`` — it is
the designer speaking, not the bot UI).

Every value shown to the players rides the LIVE season config and the
beta preset: when the admin retunes a delay, a budget or a cooldown,
the guide threads refresh themselves — the guide never lies. The
refresh is marker-based (each thread's starter message carries
``kingdoms:guide:<key>``), idempotent (an existing thread is edited in
place, renamed if its title evolved, never duplicated) and never
raises: a missing forum channel simply skips the refresh, exactly like
the other ``refresh_salons_content`` members.

The console command ``regles`` (admin_console) re-triggers the refresh
on demand — after a salon reset, one command rebuilds the whole guide.
"""

from __future__ import annotations

import logging
from typing import Any

from kingdoms.mods.kingdoms.kingdom_setup import _slug

logger = logging.getLogger("kingdoms.kingdom_guide")

REGLES_FORUM = "Règles"
GUIDE_MARKER_PREFIX = "kingdoms:guide:"

_WEEKDAYS = {
    "MON": "lundi",
    "TUE": "mardi",
    "WED": "mercredi",
    "THU": "jeudi",
    "FRI": "vendredi",
    "SAT": "samedi",
    "SUN": "dimanche",
}


def _fr_minutes(minutes: Any) -> str:
    """Render a minutes value in French (« 6 h », « 45 min »)."""
    value = int(minutes or 0)
    if value and value % 60 == 0:
        return f"{value // 60} h"
    return f"{value} min"


def _fr_hours(hours: Any) -> str:
    """Render an hours value in French (« 3 h », « 0,5 h » stays raw)."""
    value = float(hours or 0)
    if value.is_integer():
        return f"{int(value)} h"
    text = f"{value:g}".replace(".", ",")
    return f"{text} h"


def _cron_fr(cron: str) -> str:
    """Render one weekly cron as French (« 30 23 * * SUN » → dimanche 23 h 30)."""
    parts = str(cron).split()
    if len(parts) < 5:
        return str(cron)
    minute, hour, _day, _month, weekday = parts[:5]
    day = _WEEKDAYS.get(weekday.upper(), weekday)
    minute = f" h {minute}" if minute not in ("0", "00") else " h"
    return f"{day} {hour}{minute}"


def _ffa_hours(hours: tuple[int, ...]) -> str:
    """Render the FFA start-hours pool (« entre 10 h et 20 h »)."""
    values = [int(h) for h in (hours or ())]
    if not values:
        return "—"
    return f"entre {min(values)} h et {max(values)} h" if len(values) > 1 else f"à {values[0]} h"


def _ages_line(config: Any) -> str:
    """Render the season's age scale (« Âge sombre → Âge féodal »)."""
    names = [str(getattr(age, "display_name", age)) for age in (getattr(config, "ages", None) or [])]
    return " → ".join(names) if names else "—"


def _tech_table(technologies: Any) -> str:
    """Render the special-actions table with live costs and limits."""
    labels = (
        ("embuscade", "Embuscade"),
        ("traquenard", "Traquenard"),
        ("patrouille", "Patrouille"),
        ("contre_espionnage", "Contre-espionnage"),
        ("sabotage", "Sabotage"),
        ("explorateur", "Explorateur"),
        ("jeu_d_armes", "Jeu d'armes"),
        ("mariage_arrange", "Mariage arrangé"),
        ("corruption", "Corruption"),
        ("garde_royale", "Garde Royale"),
    )
    limits = dict(getattr(technologies, "limits", None) or {})
    lines = ["| Action | Coût | Limite / saison |", "|---|---|---|"]
    for key, label in labels:
        cost = getattr(technologies, key, 0)
        limit = limits.get(key, 0)
        lines.append(f"| {label} | {cost} ⚙️ | {limit or '—'} |")
    return "\n".join(lines)


def _parish_table(parish: Any) -> str:
    """Render the parish tier table with live locks and offerings."""
    lines = ["| Palier | Coût | Verrou mariage | Offrande |", "|---|---|---|---|"]
    for tier in (getattr(parish, "tiers", None) or ()):
        lines.append(
            f"| {getattr(tier, 'display_name', tier)} | {getattr(tier, 'cost', 0)} ⚙️ "
            f"| {getattr(tier, 'marriage_lock_hours', 0)} h | {getattr(tier, 'offering', 0)} |"
        )
    return "\n".join(lines)


def guide_posts(config: Any) -> list[tuple[str, str, str]]:
    """Return the guide threads as (key, title, content) with live values."""
    attacks = getattr(config, "attacks", None)
    events = getattr(config, "events", None)
    voliere = getattr(config, "voliere", None)
    parish = getattr(config, "parish", None)
    territories = int(getattr(config, "territories_per_kingdom", 5))
    gaia_territories = int(getattr(config, "gaia_territories", 8))
    starting_civs = int(getattr(config, "starting_civilizations", 0))
    weeks = int(getattr(config, "weeks", 4))
    kingdoms_count = int(getattr(config, "kingdoms_count", 2))
    lords = int(getattr(config, "lords_per_kingdom", 4))
    cycle_day = _cron_fr(getattr(events, "cycle_cron", ""))
    age_day = _cron_fr(getattr(events, "age_cron", ""))
    explore_day = _cron_fr(getattr(events, "exploration_cron", ""))
    new_maps = int(getattr(events, "lords_day_new_maps", 8))
    window_end = int(getattr(events, "lords_day_end_hour", 10))
    ffa_lead = int(getattr(events, "ffa_announce_lead_hours", 2))
    ffa_game = int(getattr(events, "ffa_game_minutes", 75))
    ffa_lock = int(getattr(events, "ffa_no_show_lock_hours", 72))
    ffa_hours = _ffa_hours(getattr(events, "ffa_start_hours", ()) or ())
    chantier = int(getattr(parish, "cathedral_chantier_hours", 72))
    attack_budget = int(getattr(attacks, "attacks_per_week", 1))
    defense_budget = int(getattr(attacks, "defenses_per_week", 1))
    attack_delay = _fr_minutes(getattr(attacks, "player_attack_delay_minutes", 360))
    gaia_delay = _fr_minutes(getattr(attacks, "gaia_attack_delay_minutes", 180))
    embuscade_delay = _fr_minutes(getattr(attacks, "embuscade_delay_minutes", 60))
    cutoff = int(getattr(attacks, "defense_cutoff_minutes", 15))
    grace = int(getattr(attacks, "ready_grace_minutes", 5))
    confirm = int(getattr(attacks, "score_confirm_minutes", 10))
    reminder = _fr_hours(getattr(attacks, "score_reminder_hours", 3))
    dispute = _fr_hours(getattr(attacks, "score_dispute_after_reminder_hours", 12))
    gaia_max_kingdom = int(getattr(attacks, "gaia_attack_max_per_kingdom", 1))
    gaia_max_total = int(getattr(attacks, "gaia_attack_max_total", 7))
    gaia_max_players = int(getattr(attacks, "gaia_max_participants", 7))
    civs_text = (
        f"{starting_civs} civilisations de départ" if starting_civs else "des civilisations de départ"
    )
    posts: list[tuple[str, str, str]] = [
        (
            "bienvenue",
            "🗺️ Bienvenue dans Kingdoms — le monde en 5 minutes",
            (
                "# ⚔️ Bienvenue dans Kingdoms\n\n"
                "Kingdoms est un événement, un mod externe au jeu Age of Empires II : "
                "vos parties ne s'arrêtent pas au GG — chaque map gagnée devient un "
                "**territoire** de votre royaume, jusqu'à ce que quelqu'un vous la prenne.\n\n"
                "**Les acteurs :**\n"
                f"• **Les Royaumes** — les équipes. Jusqu'à {kingdoms_count} royaumes "
                f"s'affrontent chaque saison ; chacun compte jusqu'à {lords} **Seigneurs**.\n"
                "• **Le Roi** — le chef d'un royaume. Il ne joue pas plus que les autres, "
                "mais il tient le trésor, le Marché et la diplomatie (post 👑).\n"
                f"• **Le Royaume Gaïa** — tenu par une **IA**, avec {gaia_territories} "
                "territoires. Idéal pour apprendre… ou pour se faire surprendre.\n\n"
                f"**Le but :** détenir le plus de territoires à la fin de la saison "
                f"({weeks} semaines). Chaque semaine avance d'un âge, un cycle du Jour "
                "du Seigneur remet des cartes sur la table, et les budgets se rechargent.\n\n"
                "**Où tout se passe :** chaque royaume a sa catégorie Discord avec ses "
                "salons (Alliances, Territoires, Marché, Âge). Le bot y épingle tout ce "
                "qui vous concerne — vous n'avez presque rien à retenir.\n\n"
                "➡️ Prochaine étape : 🏰 Créer ou rejoindre un royaume."
            ),
        ),
        (
            "royaume",
            "🏰 Créer ou rejoindre un royaume",
            (
                "# 🏰 Créer ou rejoindre un royaume\n\n"
                "**Fonder votre royaume** — postulez via le panneau de candidature du "
                "serveur : nom du royaume (validé par l'admin), votre rôle de Roi, vos "
                "comptes. L'admin valide, et le bot crée toute votre catégorie Discord.\n\n"
                "**Rejoindre un royaume existant** — postulez pour devenir Seigneur : le "
                "Roi du royaume accepte ou refuse votre candidature.\n\n"
                "**La Draft Starter** — au lancement de la saison, le bot tire la main de "
                f"départ de votre royaume : {civs_text} et **{territories} territoires**, "
                "tous tirés au sort dans le cadastre (sans doublon entre royaumes). Elle "
                "est épinglée dans votre salon **Alliances** (« 🎲 Draft starter »). "
                "Personne ne choisit sa main : tous les royaumes démarrent à égalité.\n\n"
                "**Vos salons :**\n"
                "• **📜 Alliances** — votre main de civilisations et son draft épinglé ;\n"
                "• **Territoires** — votre cadastre : chaque territoire détenu, ses effets, "
                "les attaques en cours ;\n"
                "• **Marché** — réservé au Roi : trésor, achats, jetons ;\n"
                "• **Géopolitique** — l'âge actuel de la saison (message épinglé, "
                "l'emoji du salon suit l'âge) et le prochain passage.\n\n"
                "➡️ Prochaine étape : 👑 Roi et Seigneurs."
            ),
        ),
        (
            "roles",
            "👑 Roi et Seigneurs — qui fait quoi",
            (
                "# 👑 Roi et Seigneurs — qui fait quoi\n\n"
                "Le royaume est une équipe, mais chacun son métier.\n\n"
                "**Le Roi est seul détenteur de :**\n"
                "• ⚙️ **le trésor** (les points de tech du royaume) — chaque achat le débite ;\n"
                "• **le Marché** — seul le Roi peut acheter : technologies, Paroisse, jetons "
                "de mariage pour un autre seigneur ;\n"
                "• 💍 **le mariage royal** — régime spécifique (post 🕊️) ;\n"
                "• 🐦 **le pigeon-voyageur** — seul destinataire.\n\n"
                "**Les Seigneurs ont la juridiction du combat :**\n"
                "• ⚔️ déclarations d'attaque, défenses, pick & ban, résultats — chaque "
                "seigneur agit pour le royaume ;\n"
                "• leurs budgets d'attaque et de défense sont les compteurs du royaume.\n\n"
                "En clair : un seigneur attaque et défend ; le Roi finance, achète et "
                "négocie. Un royaume dont le Roi est muet est un royaume qui dépense mal.\n\n"
                "➡️ Prochaine étape : ⚔️ Attaquer et défendre."
            ),
        ),
        (
            "attaques",
            "⚔️ Attaquer et défendre — les règles du combat",
            (
                "# ⚔️ Attaquer et défendre\n\n"
                "On attaque **un territoire**, jamais un joueur.\n\n"
                f"**Les budgets :** chaque Seigneur dispose de **{attack_budget} attaque(s)** "
                f"et **{defense_budget} défense(s) par semaine** (rechargées au cycle du Jour "
                f"du Seigneur). Contre Gaïa : {gaia_max_kingdom} attaque à la fois par "
                f"royaume ({gaia_max_total} max en saison), jusqu'à {gaia_max_players} "
                "participants peuvent s'inviter.\n\n"
                f"**La déclaration :** dans le panneau ⚔️ Déclarer une attaque, choisissez "
                f"un territoire. Le RDV de la partie est fixé à **+{attack_delay}** "
                f"(Gaïa : +{gaia_delay}) ; l'annonce part dans les salons concernés.\n\n"
                "**Les phases, dans l'ordre :**\n"
                "1. **Siège** — déclaration et défense : la défense est possible "
                "**dès la déclaration** (à la seconde) et se ferme à "
                f"**T-{cutoff}** — le reveal lève la couverture et, sans défenseur, "
                "**l'IA du royaume défenseur** joue la partie.\n"
                f"2. **Draft** — dès le reveal (**T-{cutoff}**, Drasah 18/10), le "
                "pick & ban part en DM : chaque camp classe 3 civilisations, en "
                "aveugle (aucun camp ne voit la main adverse). La révélation annonce "
                "« ⚔️ Bataille en vue » ou « 🏳️ Assaut irrésistible ». "
                "**Règle anti-blocage** : sans choix validé avant le RDV, 3 civilisations "
                "sont tirées **au hasard** de la main — le RDV a lieu quoi qu'il arrive. "
                "Sabotage/Faucon : dans le salon de la bataille.\n"
                f"3. **Ready** — à l'heure du RDV, le bot ouvre le **salon de bataille** "
                "(vocal, 2 places) : civilisations en jeu affichées dans un ordre "
                "aléatoire, URL de la partie et résultat à déclarer sur place. "
                f"Rejoignez le salon — une absence vocale à **+{grace} min** est un "
                "forfait.\n"
                "4. **Score** — chaque camp déclare le résultat sur place (ID de game "
                "requis). Le premier déclarant fait foi après "
                f"**{confirm} min** sans "
                "contestation ; une déclaration contraire ouvre un **litige** "
                "arbitré par un admin.\n\n"
                f"**Garde-fous :** rappel DM {reminder} après l'ouverture du score sans "
                f"résultat ; litige auto {dispute} après le rappel ; **Embuscade** "
                f"(action spéciale) : attaque déclarée à T-{embuscade_delay} — la défense "
                "n'a que la fenêtre jusqu'au cutoff pour réagir.\n\n"
                "➡️ Prochaine étape : 🕊️ Alliances et mariages."
            ),
        ),
        (
            "alliances",
            "🕊️ Alliances et mariages — la diplomatie",
            (
                "# 🕊️ Alliances et mariages\n\n"
                "Vos civilisations ne sont pas gravées : la diplomatie fait grossir "
                "votre main.\n\n"
                "**La main du royaume.** Toutes les civilisations tirées (Draft Starter, "
                "Jour du Seigneur, volière, mariages) sont **communes à tous les "
                "Seigneurs** du royaume. C'est LA ressource collective à protéger.\n\n"
                "**Les mariages.** Se marier avec une civilisation l'ajoute à la main du "
                "royaume. Deux régimes :\n"
                "• **Mariage libre** — négocié entre royaumes ;\n"
                "• **Mariage arrangé** — acheté au Marché (jeton 💰), conditions "
                "simplifiées.\n\n"
                "Certaines civilisations ont des **conditions d'accès** : un nombre de "
                "mariages, un type de territoire (forêt, désert, eau…), un niveau de "
                "trésor, une taille de main… Le moteur recalcule en continu : perdre le "
                "territoire qui conditionnait une civ peut la faire perdre aussi. Une "
                "civilisation peut être **exclusive** — première demande servie.\n\n"
                "**L'information est une arme :** le Corbeau et le Pigeon permettent de "
                "voir les mains adverses (post 🐦) — la diplomatie se joue aussi sur "
                "l'information.\n\n"
                "➡️ Prochaine étape : 🐦 La Volière."
            ),
        ),
        (
            "voliere",
            "🐦 La Volière — corbeau, faucon et pigeon",
            (
                "# 🐦 La Volière\n\n"
                "Ouverte dès l'**Âge féodal**. Les oiseaux appartiennent au royaume : "
                "les cooldowns sont partagés entre tous les Seigneurs.\n\n"
                "**🐦 Corbeau — l'espion.**\n"
                f"• Civilisation — révèle la main d'un royaume adverse "
                f"({getattr(voliere, 'corbeau_civ_cooldown_hours', 72)} h) ;\n"
                f"• Vérification — confirme une attaque en cours "
                f"({getattr(voliere, 'corbeau_verify_cooldown_hours', 24)} h) ;\n"
                f"• Pistage — suit un seigneur adverse, fenêtre "
                f"{getattr(voliere, 'corbeau_track_window_hours', 24)} h "
                f"({getattr(voliere, 'corbeau_track_cooldown_hours', 48)} h) ;\n"
                f"• Vol de ⚙️ — vole des points du trésor adverse : le vol part "
                f"{getattr(voliere, 'corbeau_theft_delay_hours', 1)} h après l'envoi, et le "
                f"royaume volé a {getattr(voliere, 'corbeau_theft_contest_minutes', 5)} min "
                "pour contester après l'alarme — la contestation tue le corbeau et "
                "rembourse tout.\n\n"
                "**🦅 Faucon — le saboteur.**\n"
                f"• Territoire — s'abat sur un territoire adverse "
                f"({getattr(voliere, 'faucon_territory_cooldown_hours', 96)} h) ;\n"
                f"• Sabotage — dégrade une civilisation adverse "
                f"({getattr(voliere, 'faucon_sabotage_cooldown_hours', 48)} h) ;\n"
                f"• Libération — libère un oiseau capturé "
                f"({getattr(voliere, 'faucon_release_cooldown_hours', 168)} h, fenêtre "
                f"{getattr(voliere, 'faucon_release_window_hours', 24)} h).\n\n"
                "**🕊️ Pigeon-voyageur — le messager du Roi.**\n"
                f"• Civilisation ({getattr(voliere, 'pigeon_civ_cooldown_hours', 72)} h) · "
                f"Question ({getattr(voliere, 'pigeon_question_cooldown_hours', 1)} h) · "
                f"DM ({getattr(voliere, 'pigeon_dm_cooldown_hours', 1)} h).\n\n"
                "➡️ Prochaine étape : ⚙️ Le trésor."
            ),
        ),
        (
            "tresor",
            "⚙️ Le trésor et les actions spéciales",
            (
                "# ⚙️ Le trésor et les actions spéciales\n\n"
                "**⚙️ Le trésor** est la banque de points de tech du royaume. Il se "
                "remplit par les offrandes (Paroisse), le Jour du Seigneur et les "
                "récompenses de fin de saison — et se dépense au Marché, uniquement "
                "par le Roi.\n\n"
                "**Les 10 actions spéciales** (coût ⚙️, limites par saison) :\n\n"
                f"{_tech_table(getattr(config, 'technologies', None))}\n\n"
                "**La Paroisse** (amélioration du royaume, achetée au Marché) :\n\n"
                f"{_parish_table(parish)}\n\n"
                f"La **Cathédrale** est un chantier de **{chantier} h** : une fois "
                "consacrée, elle rapporte au Jour du Seigneur ; sa destruction rapporte "
                "des points à l'ennemi — défendez-la.\n\n"
                "➡️ Prochaine étape : 📅 Le rythme de la saison."
            ),
        ),
        (
            "rythme",
            "📅 Le rythme de la saison",
            (
                "# 📅 Le rythme de la saison\n\n"
                f"La saison dure **{weeks} semaines**, en heure de Paris :\n\n"
                f"• **Chaque semaine = un âge** — passage {age_day} à minuit. L'échelle : "
                f"{_ages_line(config)}. Chaque âge monte le niveau de l'IA Gaïa et accorde "
                "des points de tech et des mariages bonus.\n"
                f"• **Le Jour du Seigneur** — cycle {cycle_day} : rechargement des "
                f"**budgets**, tirage de **{new_maps} nouvelles maps** dans le cadastre, "
                "points de Paroisse — et ouverture d'une **fenêtre protégée** jusqu'au "
                f"lendemain {window_end} h : aucune partie d'attaque ne s'y joue (on peut "
                "déclarer si le RDV tombe après la fenêtre).\n"
                f"• **Exploration** — {explore_day} : les explorateurs peuvent rapporter "
                "de nouvelles maps (les technologies d'exploration amplifient le gain).\n"
                f"• **FFA** — partie libre tous royaumes confondus, {ffa_hours} : "
                f"l'annonce part {ffa_lead} h avant (inscriptions à ce moment), la partie "
                f"dure {ffa_game} min, par défaut 1 Seigneur par royaume. ⚠️ No-show : "
                f"un inscrit absent est **verrouillé hors combat {ffa_lock} h** à partir "
                "du prochain cycle. Ne vous inscrivez pas pour rien.\n\n"
                "**La fin de saison :** les résultats tombent, les récompenses sont "
                "distribuées, la Chronique raconte la saison. Puis tout recommence.\n\n"
                "➡️ Prochaine étape : 🧪 La beta."
            ),
        ),
        (
            "beta",
            "🧪 La beta — ce qui change",
            (
                "# 🧪 La beta\n\n"
                "La beta reprend exactement les mêmes mécaniques, mais **compressées "
                "sur 52 heures** pour tout tester. La saison part d'un **T0** fixé par "
                "l'admin (commande console) et se termine à **T0+52 h** (résultats + "
                "récompenses inclus).\n\n"
                "**Le plan relatif :**\n"
                "• passage d'âge : **T0+4 h** (Âge sombre → Âge féodal) ;\n"
                "• cycles Jour du Seigneur : **T0+10 h** et **T0+34 h** (fenêtre "
                "protégée 2 h) ;\n"
                "• fin de saison officielle : **T0+52 h**.\n\n"
                "**Les compressions :** 2 attaques / 2 défenses par Seigneur ; délais "
                "d'attaque, Gaïa et embuscade à **10 minutes** ; cutoff de défense "
                "**T-5** ; pick & ban **T-3** ; grâce Score 3 min ; rappel 30 min ; "
                "litige +1 h ; actions spéciales **gratuites et illimitées** ; volière "
                "compressée (corbeau 4 h/2 h/6 h, faucon 8 h/4 h/12 h, pigeon "
                "4 h/30 min/30 min) ; verrous Paroisse 4 h/2 h/1 h, chantier 8 h.\n\n"
                "Le but : **tester en profondeur, sans contrainte d'argent ni de "
                "temps.** Chaque retour est lu et pesé — les modifications restent à "
                "l'appréciation du game designer.\n\n"
                "➡️ Dernière étape : 📖 Lexique."
            ),
        ),
        (
            "lexique",
            "📖 Lexique",
            (
                "# 📖 Lexique\n\n"
                "• **Royaume** — une équipe : jusqu'à 4 Seigneurs et un Roi.\n"
                "• **Seigneur** — un joueur d'un royaume.\n"
                "• **Roi** — le chef : trésor, Marché, mariages royaux, pigeon.\n"
                "• **Gaïa** — le royaume IA et ses territoires.\n"
                "• **Territoire** — une map du cadastre possédée par un royaume.\n"
                "• **Cadastre** — le catalogue des maps jouables, avec leurs types et "
                "effets.\n"
                "• **Draft Starter** — la main de départ tirée au sort au lancement.\n"
                "• **Main / Alliances** — les civilisations du royaume, communes à "
                "tous ses Seigneurs.\n"
                "• **Trésor (⚙️)** — les points de tech du royaume.\n"
                "• **Jeton mariage (💰)** — acheté au Marché, permet un mariage "
                "arrangé.\n"
                "• **Paroisse** — Chapelle → Église → Cathédrale : verrous de "
                "mariage et offrandes.\n"
                "• **Jour du Seigneur** — le cycle hebdo : budgets, nouvelles maps, "
                "fenêtre protégée.\n"
                "• **Âge** — l'époque de la saison ; un âge = une semaine.\n"
                "• **Fenêtre protégée** — période où aucune partie d'attaque ne se "
                "joue.\n"
                "• **FFA** — la partie libre du samedi, tous royaumes confondus.\n"
                "• **Embuscade** — attaque déclarée en retard : piège à défenseurs "
                "endormis.\n"
                "• **Pick & ban** — le classement des 3 civs par camp, en "
                "aveugle, avant la partie.\n"
                "• **Litige** — deux déclarations de score contraires : un admin "
                "tranche.\n"
                "• **Volière** — corbeau (espion), faucon (saboteur), pigeon "
                "(messager du Roi).\n"
                "• **Chronique** — le récit de la saison, écrit par le bot.\n"
                "• **T0** — le départ exact d'une beta, fixé par l'admin."
            ),
        ),
    ]
    return [(key, title, f"{content}\n-# {GUIDE_MARKER_PREFIX}{key}") for key, title, content in posts]


def _find_forum(guild: Any) -> Any:
    """Find the Règles forum channel (or a text channel of the same slug)."""
    wanted = _slug(REGLES_FORUM)
    pool = [*(getattr(guild, "forums", None) or []), *(getattr(guild, "text_channels", None) or [])]
    return next((c for c in pool if _slug(getattr(c, "name", "")) == wanted), None)


async def _starter_message(thread: Any) -> Any:
    """Return the forum thread's starter message, best effort."""
    starter = getattr(thread, "starter_message", None)
    if starter is not None:
        return starter
    getter = getattr(thread, "get_starter_message", None)
    if getter is None:
        return None
    try:
        return await getter()
    except Exception:
        return None


async def _refresh_thread(channel: Any, threads: list[Any], key: str, title: str, content: str) -> str:
    """Create or edit one guide thread; return the report line."""
    marker = f"{GUIDE_MARKER_PREFIX}{key}"
    thread = next((t for t in threads if _slug(getattr(t, "name", "")) == _slug(title)), None)
    if thread is None:
        for candidate in threads:
            starter = await _starter_message(candidate)
            if starter is not None and marker in str(getattr(starter, "content", "") or ""):
                thread = candidate
                break
    if thread is None:
        await channel.create_thread(name=title, content=content)
        return "created"
    if getattr(thread, "name", "") != title:
        await thread.edit(name=title)
    starter = await _starter_message(thread)
    if starter is not None:
        await starter.edit(content=content)
    return "updated"


async def refresh_guide_forum(guild: Any, kingdoms_service: Any) -> dict[str, str]:
    """Deploy or refresh the guide threads of the Règles forum (best effort).

    Returns a deploy-style report keyed by post key (« created » /
    « updated ») — empty when the forum channel is missing.
    """
    config = getattr(kingdoms_service, "config", None)
    if config is None:
        return {}
    channel = _find_forum(guild)
    if channel is None:
        return {}
    report: dict[str, str] = {}
    try:
        threads = list(getattr(channel, "threads", None) or [])
        for key, title, content in guide_posts(config):
            try:
                report[key] = await _refresh_thread(channel, threads, key, title, content)
            except Exception:
                logger.warning("KINGDOM GUIDE: thread %s refresh failed", key, exc_info=True)
                report[key] = "failed"
    except Exception:
        logger.warning("KINGDOM GUIDE: refresh skipped", exc_info=True)
        return {}
    return report
