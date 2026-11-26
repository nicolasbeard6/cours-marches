"""Reconstitution du bot de tendance automatique depuis le début du journal (16/11/2026), une seule fois.

Le bot a été lancé le 23/11. Pour le comparer au journal sur la même période, on rejoue sa règle (tendance.py, inchangée)
jour par jour depuis le 16/11, à l'heure où il se rééquilibre (premier relevé après 15h00 UTC, ici 15h07), avec les cours
qu'il aurait vus à ce moment-là :
  - cours et négociabilité : barres d'une heure publiées (barres/), en ne gardant que celles commencées avant l'heure du passage
    (une barre en cours compte à son cours d'ouverture : aucune information postérieure) ;
  - variations sur 1, 3 et 6 mois et amplitude quotidienne moyenne : clôtures, hauts et bas quotidiens Yahoo antérieurs au jour du passage.
Limites : l'univers est celui d'aujourd'hui (liste des instruments et rang des cryptos), pas celui de chaque jour.
Le résultat remplace tendance/etat.json et tendance/historique.json ; ensuite le bot continue normalement.
Usage : python retro_tendance.py DOSSIER   (après fetch.py : DOSSIER contient resume.json et barres/)
"""
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

import tendance

DEBUT = "2026-11-16"
VERSION = 1
HEURE = (15, 7)


def quotidien(symbols, lot=60):
    """{symbole: [(date 'AAAA-MM-JJ', haut, bas, clôture)]} sur un an."""
    import yfinance as yf
    out = {}
    for i in range(0, len(symbols), lot):
        part = symbols[i:i + lot]
        try:
            df = yf.download(part, period="1y", interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=True)
        except Exception as e:
            print("lot quotidien en échec :", str(e)[:120])
            continue
        for s in part:
            try:
                sub = df[s][["High", "Low", "Close"]].dropna()
            except Exception:
                continue
            out[s] = [(t.strftime("%Y-%m-%d"), float(h), float(l), float(c)) for t, (h, l, c) in zip(sub.index, sub.to_numpy())]
        time.sleep(1)
    return out


def variation(jours_q, cours, jour_t, n):
    """Variation depuis la dernière clôture datée d'au moins n jours avant le passage (comme fetch.var_longue)."""
    limite = (datetime.strptime(jour_t, "%Y-%m-%d") - timedelta(days=n)).strftime("%Y-%m-%d")
    avant = [c for d, _, _, c in jours_q if d <= limite]
    if not avant or jours_q[0][0] > (datetime.strptime(limite, "%Y-%m-%d") + timedelta(days=10)).strftime("%Y-%m-%d"):
        return None
    return round(cours / avant[-1] - 1, 5)


def resume_a(instr, barres, q, t):
    jour = t.strftime("%Y-%m-%d")
    ts = t.timestamp()
    out = []
    for i in instr:
        b = [x for x in barres.get(i["symbole"], []) if x[0] <= ts]
        jq = [x for x in q.get(i["symbole"], []) if x[0] < jour]
        if not b or len(jq) < 20:
            continue
        dern = b[-1]
        cours = dern[1] if ts - dern[0] < 3600 else dern[4]     # barre en cours : cours d'ouverture
        amp = [(h - l) / c for _, h, l, c in jq[-15:] if c]
        out.append({"classe": i["classe"], "symbole": i["symbole"], "cours": cours, "heure": dern[0],
                    "negociable": ts - dern[0] < 5400, "rang": i.get("rang"),
                    "var_1m": variation(jq, cours, jour, 30), "var_3m": variation(jq, cours, jour, 91),
                    "var_6m": variation(jq, cours, jour, 182),
                    "amplitude_jour_moy": round(sum(amp) / len(amp), 5) if amp else None})
    return {"instruments": out}


def main(dossier):
    rep = os.path.join(dossier, "tendance")
    p_etat = os.path.join(rep, "etat.json")
    if os.path.exists(p_etat):
        e = json.load(open(p_etat, encoding="utf-8"))
        if (e.get("retro") or {}).get("version") == VERSION:
            print("reconstitution du bot de tendance déjà faite")
            return
    resume = json.load(open(os.path.join(dossier, "resume.json"), encoding="utf-8"))
    instr = resume["instruments"]
    barres = {}
    for i in instr:
        f = os.path.join(dossier, "barres", i["fichier"])
        if os.path.exists(f):
            barres[i["symbole"]] = json.load(open(f))["barres"]
    q = quotidien([i["symbole"] for i in instr])
    print(f"{len(barres)} fichiers de barres, {len(q)} historiques quotidiens")
    if len(q) < 0.5 * len(instr):
        sys.exit("trop peu d'historiques quotidiens : reconstitution remise au prochain relevé")
    tmp = tempfile.mkdtemp()
    maintenant = datetime.now(timezone.utc)
    jour = datetime.strptime(DEBUT, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    jours = []
    while True:
        t = jour.replace(hour=HEURE[0], minute=HEURE[1])
        if t > maintenant:
            break
        tendance.main(tmp, now=t, resume=resume_a(instr, barres, q, t))
        s = json.load(open(os.path.join(tmp, "tendance", "etat.json"), encoding="utf-8"))["synthese"]
        jours.append({"date": t.strftime("%Y-%m-%d"), "synthese": s})
        jour += timedelta(days=1)
    etat = json.load(open(os.path.join(tmp, "tendance", "etat.json"), encoding="utf-8"))
    etat["debut"] = DEBUT
    etat["retro"] = {"version": VERSION, "debut": DEBUT, "fait_utc": maintenant.strftime("%Y-%m-%d %H:%M"), "jours": jours,
                     "notes": ["univers et rang des cryptos d'aujourd'hui", "rééquilibrage rejoué à 15h07 UTC chaque jour"]}
    os.makedirs(rep, exist_ok=True)
    json.dump(etat, open(p_etat, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    shutil.copy(os.path.join(tmp, "tendance", "historique.json"), os.path.join(rep, "historique.json"))
    print("reconstitution terminée :", json.dumps(etat["synthese"], ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
