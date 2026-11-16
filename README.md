# cours-marches

Relevé automatique de cours de marché (devises, matières premières, actions, crypto), utilisé par un journal de trading fictif.
Ce dépôt ne contient que des données de marché publiques et la liste des instruments suivis.

- `univers.json` : la liste des instruments (symboles Yahoo Finance). Les cryptos sont les 50 premières capitalisations
  du classement CoinGecko, hors stablecoins ; la liste de secours ne sert que si ce classement est indisponible.
- `fetch.py` : relève les cours (barres d'une heure sur un mois) et calcule un résumé par instrument.
- `.github/workflows/cours.yml` : lance le relevé toutes les 30 minutes et publie le résultat dans la branche `data`.
- `suivi.py` : dit si le niveau d'entrée, le stop ou le take profit d'un ordre a été touché depuis une date.

Résultat : branche `data`, fichiers `resume.json` et `barres/<symbole>.json`.
