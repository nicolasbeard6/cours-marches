"""Bot de tendance automatique (référence du journal de trading fictif) : aucune analyse, aucune actualité.

Quatre bots, un par classe (Devises, Matières premières, Actions, Crypto), 10 000 € fictifs chacun, sans levier.
Une fois par jour (premier relevé après 15h00 UTC ; devises, matières premières et actions du lundi au vendredi,
crypto tous les jours), chaque bot :
  1. constate le résultat des positions de la veille au dernier cours ;
  2. prend sur chaque instrument le sens de la majorité des tendances sur 1, 3 et 6 mois (achat si elles montent,
     vente si elles baissent ; tendance sur 1 mois seule tant que l'historique long manque) ;
  3. dimensionne chaque position pour risquer 50 € sur un écart de 2 amplitudes quotidiennes moyennes,
     plafonnée par classe, puis réduit l'ensemble pour ne jamais engager plus que son capital ;
  4. paie des frais (glissement compris) sur les montants achetés ou vendus. Un changement de taille de moins de 25 %
     dans le même sens n'est pas exécuté, pour limiter les frais.
Crypto : seulement les 20 premières capitalisations du classement.
Entrées : resume.json et l'état précédent ; sorties : tendance/etat.json et tendance/historique.json.
Usage : python tendance.py DOSSIER   (le dossier contient resume.json ; l'état précédent est lu dans DOSSIER/tendance)
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

CAPITAL = 10000.0
RISQUE = 50.0                        # € risqués par position sur un écart de 2 amplitudes quotidiennes
PLAFOND = {"Devises": 2500, "Matières premières": 1500, "Actions": 1000, "Crypto": 750}
FRAIS_AR = {"Devises": 0.0005, "Matières premières": 0.0010, "Actions": 0.0020, "Crypto": 0.0060}  # aller-retour
CRYPTO_MAX_RANG = 20
SEUIL_TAILLE = 0.25
HEURE_UTC = 15


def signe(x):
    return 0 if x is None or x == 0 else (1 if x > 0 else -1)


def sens_tendance(i):
    votes = [signe(i.get(k)) for k in ("var_1m", "var_3m", "var_6m") if i.get(k) is not None]
    return signe(sum(votes))


def main(dossier):
    resume = json.load(open(os.path.join(dossier, "resume.json"), encoding="utf-8"))
    rep = os.path.join(dossier, "tendance")
    os.makedirs(rep, exist_ok=True)
    p_etat, p_hist = os.path.join(rep, "etat.json"), os.path.join(rep, "historique.json")
    etat = json.load(open(p_etat, encoding="utf-8")) if os.path.exists(p_etat) else {"debut": None, "bots": {}}
    hist = json.load(open(p_hist, encoding="utf-8")) if os.path.exists(p_hist) else []
    now = datetime.now(timezone.utc)
    jour = now.strftime("%Y-%m-%d")
    par_classe = {}
    for i in resume["instruments"]:
        par_classe.setdefault(i["classe"], {})[i["symbole"]] = i

    for classe in ("Devises", "Matières premières", "Actions", "Crypto"):
        bot = etat["bots"].setdefault(classe, {"capital": CAPITAL, "positions": {}, "dernier_reequilibrage": None,
                                                 "frais_cumules": 0.0})
        cours = par_classe.get(classe, {})
        # valeur latente à chaque relevé
        latent = 0.0
        for sym, p in bot["positions"].items():
            c = cours.get(sym, {}).get("cours")
            if c:
                latent += p["montant"] * p["sens"] * (c / p["ref"] - 1)
        bot["latent"] = round(latent, 2)
        bot["valeur"] = round(bot["capital"] + latent, 2)
        ouvre = classe == "Crypto" or now.weekday() < 5
        if not (ouvre and now.hour >= HEURE_UTC and bot["dernier_reequilibrage"] != jour):
            continue
        # 1. résultat depuis le dernier rééquilibrage
        resultat = 0.0
        for sym, p in bot["positions"].items():
            c = cours.get(sym, {}).get("cours")
            if c:
                resultat += p["montant"] * p["sens"] * (c / p["ref"] - 1)
                p["ref"] = c
        # 2-3. nouvelles positions visées
        cibles = {}
        for sym, i in cours.items():
            if classe == "Crypto" and not (i.get("rang") and i["rang"] <= CRYPTO_MAX_RANG):
                continue
            s, amp = sens_tendance(i), i.get("amplitude_jour_moy")
            if s == 0 or not amp or not i.get("cours"):
                continue
            if not i.get("negociable") and sym in bot["positions"]:
                cibles[sym] = (bot["positions"][sym]["sens"], bot["positions"][sym]["montant"])
                continue
            if not i.get("negociable"):
                continue
            cibles[sym] = (s, min(PLAFOND[classe], RISQUE / (2 * amp)))
        total = sum(m for _, m in cibles.values())
        echelle = min(1.0, CAPITAL / total) if total else 1.0
        # 4. exécution, avec frais
        frais, nouv = 0.0, {}
        for sym in set(cibles) | set(bot["positions"]):
            ancien = bot["positions"].get(sym)
            if sym not in cibles:
                frais += ancien["montant"] * FRAIS_AR[classe] / 2
                continue
            s, m = cibles[sym]
            m = round(m * echelle, 2)
            c = cours[sym]["cours"]
            if ancien and ancien["sens"] == s and abs(m - ancien["montant"]) < SEUIL_TAILLE * ancien["montant"]:
                nouv[sym] = ancien
                continue
            echange = abs(m - ancien["montant"]) if ancien and ancien["sens"] == s else m + (ancien["montant"] if ancien else 0)
            frais += echange * FRAIS_AR[classe] / 2
            nouv[sym] = {"sens": s, "montant": m, "ref": c}
        bot["positions"] = nouv
        bot["capital"] = round(bot["capital"] + resultat - frais, 2)
        bot["frais_cumules"] = round(bot["frais_cumules"] + frais, 2)
        bot["dernier_reequilibrage"] = jour
        bot["latent"] = 0.0
        bot["valeur"] = bot["capital"]
        hist.append({"date": jour, "classe": classe, "capital": bot["capital"], "resultat_jour": round(resultat - frais, 2),
                     "frais_jour": round(frais, 2), "achats": sum(1 for p in nouv.values() if p["sens"] > 0),
                     "ventes": sum(1 for p in nouv.values() if p["sens"] < 0),
                     "exposition": round(sum(p["montant"] for p in nouv.values()), 2)})
    if not etat["debut"]:
        etat["debut"] = jour
    etat["maj_utc"] = now.strftime("%Y-%m-%d %H:%M")
    etat["regles"] = {"capital": CAPITAL, "risque": RISQUE, "plafond": PLAFOND, "frais_aller_retour": FRAIS_AR,
                      "crypto_max_rang": CRYPTO_MAX_RANG, "heure_utc": HEURE_UTC}
    etat["synthese"] = {c: {"valeur": b["valeur"], "capital": b["capital"], "resultat": round(b["valeur"] - CAPITAL, 2),
                            "achats": sum(1 for p in b["positions"].values() if p["sens"] > 0),
                            "ventes": sum(1 for p in b["positions"].values() if p["sens"] < 0),
                            "exposition": round(sum(p["montant"] for p in b["positions"].values()), 2),
                            "dernier_reequilibrage": b["dernier_reequilibrage"]} for c, b in etat["bots"].items()}
    json.dump(etat, open(p_etat, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.dump(hist[-5000:], open(p_hist, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("bot de tendance :", json.dumps(etat["synthese"], ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
