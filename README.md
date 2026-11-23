# cours-marches

Relevé automatique de cours de marché (devises, matières premières, actions, crypto), utilisé par un journal de trading fictif.
Ce dépôt ne contient que des données de marché publiques et la liste des instruments suivis.

- `univers.json` : la liste des instruments (symboles Yahoo Finance). Les cryptos sont les 50 premières capitalisations
  du classement CoinGecko, hors stablecoins ; la liste de secours ne sert que si ce classement est indisponible.
- `fetch.py` : relève les cours (barres d'une heure sur un mois) et calcule un résumé par instrument.
- `.github/workflows/cours.yml` : lance le relevé toutes les 30 minutes et publie le résultat dans la branche `data`.
- `suivi.py` (version 2) : dit si le niveau d'entrée, le stop (fixe ou suiveur, option `--suiveur DISTANCE`) ou le take profit
  d'un ordre a été touché depuis une date.
- `tendance.py` : bot de tendance automatique, sans analyse ni actualité, qui sert de référence au journal (un bot par classe,
  10 000 € fictifs chacun, sens de la majorité des tendances sur 1, 3 et 6 mois, rééquilibré une fois par jour après 15h00 UTC).

Résultat : branche `data`, fichiers `resume.json`, `barres/<symbole>.json`, `tendance/etat.json` et `tendance/historique.json`.
