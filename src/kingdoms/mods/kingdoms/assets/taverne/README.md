# Assets Taverne (taverne-v2, Drasah 26/10/2026)

Images de la direction artistique de la Taverne — embarquées dans le
bot (le Dockerfile copie tout `src/`), SERVIES PAR LE CODE sans
aucune dépendance à un CDN externe (contrairement aux blasons de civs,
qui eux sont téléchargés d'aoe2techtree au runtime).

Le code les charge par `Path(__file__).parent / "assets" / "taverne"`.

## Le contrat de nommage (à ne pas casser)

| Fichier | Surface | Description |
| --- | --- | --- |
| `taverne-entree.png` | L'écran d'ENTRÉE | La taverne vue de l'extérieur au crépuscule — première chose que voit un seigneur qui clique sur la Taverne, avec le bouton « Entrer dans la Taverne ». |
| `taverne-comptoir.png` | Le panneau du comptoir | L'intérieur, le tenancier derrière son comptoir (chope, écus épars, cheminée) — tête du panneau de la catégorie Taverne. |
| `taverne-mercenaire.png` | La table du Chef Mercenaire | L'angle de la table du Chef, sombre et dangereux — surface du marché Chef Mercenaire. |

## Règles

- PNG uniquement, ratio large (bannière Discord), ≤ 1 Mo par image.
- Ne JAMAIS renommer ou supprimer un fichier référencé par le code —
  ajouter, oui ; remplacer par une nouvelle version, oui (même nom).
- Les trois images partagent la même DA painterly AoE2 (tons ambre/
  brun, lisible en petit) — toute nouvelle scène doit respecter cette
  direction avant commit.
