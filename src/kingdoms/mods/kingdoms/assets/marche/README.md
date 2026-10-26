# 🎴 Cartes TCG du Marché

Les **10 achats du Marché** du mod Kingdoms, déclinés en cartes (design Drasah).
Ce dossier est la source unique des cartes ; le panneau Marché révèle la carte
en éphémère au clic sur un article (pattern `assets/taverne` : priorité au PNG,
cosmétique si absent — la fiche d'achat fonctionne sans l'art).

## Déposer les cartes (drag-drop GitHub)

Ouvrir ce dossier sur GitHub → **Add file → Upload files** → glisser les PNG →
Commit directement sur `vibe/drasah/main`.

## Nommage EN VIGUEUR (cartes-tcg-v2 — noms d'affichage déposés par Drasah)

Le resolver `marche_art.py` mappe chaque clé du catalogue `TechnologyCosts`
vers le fichier déposé :

| Fichier | Clé config | Carte | Coût | Limite / saison |
|---|---|---|---|---|
| `Embuscade.png` | `embuscade` | Embuscade | 2 ⚙ | — |
| `Traquenard.png` | `traquenard` | Traquenard | 1 ⚙ | — |
| `Contre_Espionnage.png` | `contre_espionnage` | Contre-espionnage | 2 ⚙ | — |
| `Sabotage.png` | `sabotage` | Sabotage | 1 ⚙ | 2 |
| `Jeu_darme.png` | `jeu_d_armes` | Jeu d'armes | 1 ⚙ | 1 |
| `Explorateur.png` | `explorateur` | Explorateur | 2 ⚙ | 1 |
| `Corruption.png` | `corruption` | Corruption | 4 ⚙ | 2 |
| `Garde_Royale.png` | `garde_royale` | Garde Royale | 2 ⚙ | — |
| `Patrouille.png` | `patrouille` | Patrouille | 2 ⚙ | 2 |
| `Mariage_Arrange.png` | `mariage_arrange` | Mariage arrangé | 3 ⚙ | — |

`Dos_de_carte.png` (dos de carte « KINGDOM ») est réservé au futur flux
**booster** (Drasah 26/10) — volontairement hors du mapping du Marché.
PNG portrait ~2:3 (728×1024 déposés).
