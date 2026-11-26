"""Reconstitution des bots de copie depuis le début du journal (lancée une seule fois par copie.py).

Les bots sont rejoués heure par heure depuis DEBUT_RETRO, avec exactement les règles de copie.py, en ne donnant à chaque
instant T que ce qui était publié à T :
  - Hyperliquid : position de chaque compte à T reconstituée à partir de ses trades (userFillsByTime : position avant
    chaque trade + quantité échangée), valeur du compte à T (portfolio), cours à T (bougies d'une heure).
    Choix des comptes au départ : mêmes critères de qualité que copie.py (ancienneté, pire baisse, rotation), mais avec
    le gain AVANT le dernier mois et un historique coupé au début, pour ne pas choisir sur la période rejouée.
    Les comptes dont les trades de la période ne sont pas tous disponibles (plus de 10 000 trades : teneurs de marché)
    sont écartés, faute de pouvoir les reconstituer. Pas de revue le lundi pendant la reconstitution (le classement
    passé n'est pas publié) : la première revue réelle a lieu au premier lundi suivant.
  - ARKK : fichier de lignes publié la veille de T (historique d'arkfunds.io).
  - Sénateurs : seules les déclarations publiées avant le jour de T ; cours de clôture de la veille ; revue du lundi
    rejouée avec les données disponibles ce jour-là.
  - Buffett : déclaration 13F du 30/09/2026 (publiée le 16/11/2026, jour du début du journal) ; cours de la veille.
Limites : biais du survivant (seuls les comptes encore présents dans le classement actuel peuvent être choisis),
funding et dividendes négligés, cours à l'heure près pour la crypto et à la clôture de la veille pour les actions.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

ARK_HIST = "https://arkfunds.io/api/v2/etf/holdings?symbol=ARKK&date_from={de}&date_to={a}"
MAX_TRADES = 9500


def _ms(dt):
    return int(dt.timestamp() * 1000)


def reconstituer(c, debut_txt, maintenant):
    """c : le module copie. Renvoie (etat, historique, mouvements) à la date de maintenant (exclue)."""
    debut = datetime.strptime(debut_txt, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    notes, horloge = [], {"T": debut}
    origine = {n: getattr(c, n) for n in ("hl_info", "hl_choisir", "ark_portefeuille", "senat_get", "cours_yahoo",
                                          "sec_13f", "dataroma_brk")}
    simu = c.SIMU

    def lire(nom, url=None, corps=None, timeout=60):
        if simu:
            return json.load(open(os.path.join(simu, "retro", nom), encoding="utf-8"))
        return json.loads(c._http(url or c.HL_INFO, json.dumps(corps).encode() if corps else None, timeout=timeout))

    # ----- Hyperliquid -----
    hl = {"comptes": [], "fills": {}, "valeurs": {}, "actuel": {}, "bougies": {}}
    try:
        # même sélection que copie.py (qualité des comptes), mais avec le gain d'AVANT le dernier mois et un historique
        # coupé au début de la reconstitution ; le levier du moment n'est pas connu, il n'est donc pas filtré ici
        cand = c.hl_preselection(c.hl_classement(), lambda p: float(p["allTime"]["pnl"]) - float(p["month"]["pnl"]))
        exclus, lus = [], {}

        def accepter(a):
            try:
                fills, depart = {}, _ms(debut)
                for _ in range(6):
                    lot = lire(f"fills/{a}.json") if simu else lire(None, corps={"type": "userFillsByTime", "user": a,
                                                                               "startTime": depart})
                    for f in lot:
                        fills[f.get("tid") or (f["time"], f["coin"], f["sz"], f["px"])] = f
                    if simu or len(lot) < 2000:
                        break
                    depart = max(f["time"] for f in lot) + 1
                    time.sleep(1)
                if len(fills) >= MAX_TRADES:
                    exclus.append(a)
                    return False
                port = c.hl_info({"type": "portfolio", "user": a})
                port = {n: d for n, d in port} if isinstance(port, list) else port
                hist_v = (port.get("perpMonth") or port.get("month") or {}).get("accountValueHistory", [])
                etat_a = c.hl_info({"type": "clearinghouseState", "user": a})
                time.sleep(0 if simu else 1)
            except Exception as e:
                exclus.append(a)
                notes.append(f"Hyperliquid {a[:10]} illisible : {str(e)[:60]}")
                return False
            lus[a] = (fills, hist_v, etat_a)
            return True

        for k in c.hl_retenir(cand, _ms(debut), False, accepter):
            a = k["id"]
            fills, hist_v, etat_a = lus[a]
            par_coin = {}
            for f in sorted(fills.values(), key=lambda f: f["time"]):
                if "@" in f["coin"] or ":" in f["coin"]:      # spot et marchés HIP-3 : hors du compte suivi par copie.py
                    continue
                q, s = float(f["sz"]), float(f["startPosition"])
                par_coin.setdefault(f["coin"], []).append((f["time"], s, s + q if f["side"] == "B" else s - q))
            hl["fills"][a] = par_coin
            hl["valeurs"][a] = sorted((int(t), float(v)) for t, v in hist_v)
            actuel = {}
            for p in etat_a.get("assetPositions", []):
                p = p.get("position", p)
                if c.nombre(p.get("szi")):
                    actuel[p["coin"]] = c.nombre(p["szi"])
            hl["actuel"][a] = (actuel, c.nombre(etat_a.get("marginSummary", {}).get("accountValue")) or 0.0)
            hl["comptes"].append(k)
        if exclus:
            notes.append(f"Hyperliquid : {len(exclus)} comptes écartés (trop de trades pour être reconstitués ou illisibles)")
        notes.append(f"Hyperliquid : {len(cand)} comptes présélectionnés, {len(hl['comptes'])} retenus après les critères de qualité")
        pieces = set()
        for a in hl["fills"]:
            pieces |= set(hl["fills"][a]) | set(hl["actuel"][a][0])
        for coin in sorted(pieces):
            try:
                b = lire(f"bougies/{coin}.json") if simu else lire(None, corps={"type": "candleSnapshot", "req": {
                    "coin": coin, "interval": "1h", "startTime": _ms(debut - timedelta(hours=2)), "endTime": _ms(maintenant)}})
                hl["bougies"][coin] = sorted((int(x["T"]), float(x["c"])) for x in b)
                time.sleep(0 if simu else 1.5)
            except Exception:
                notes.append(f"Hyperliquid : pas de cours historique pour {coin}")
    except Exception as e:
        notes.append(f"Hyperliquid non reconstitué : {type(e).__name__} {str(e)[:80]}")

    def dernier_avant(serie, t):
        x = None
        for ti, v in serie:
            if ti > t:
                break
            x = v
        return x

    def hl_info(corps):
        t = _ms(horloge["T"])
        if corps["type"] == "allMids":
            return {coin: str(v) for coin, s in hl["bougies"].items() if (v := dernier_avant(s, t)) is not None}
        a = corps["user"].lower()
        pos = dict(hl["actuel"][a][0])
        for coin, ev in hl["fills"][a].items():
            avant = [x for x in ev if x[0] <= t]
            pos[coin] = avant[-1][2] if avant else ev[0][1]
        val = dernier_avant(hl["valeurs"][a], t) or hl["actuel"][a][1]
        return {"marginSummary": {"accountValue": str(val)},
                "assetPositions": [{"position": {"coin": k, "szi": str(q)}} for k, q in pos.items() if q]}

    # ----- ARKK -----
    ark = {}
    try:
        d = lire("ark_hist.json") if simu else lire(None, ARK_HIST.format(de=(debut - timedelta(days=10)).strftime("%Y-%m-%d"),
                                                                      a=maintenant.strftime("%Y-%m-%d")))
        for h in d.get("holdings", []):
            q, mv = c.nombre(h.get("shares")), c.nombre(h.get("market_value"))
            if h.get("ticker") and q and mv:
                ark.setdefault(h["date"], {})[h["ticker"]] = (q, mv / q)
    except Exception as e:
        notes.append(f"ARKK non reconstitué : {type(e).__name__} {str(e)[:80]}")

    def ark_portefeuille():
        dates = [x for x in sorted(ark) if x < horloge["T"].strftime("%Y-%m-%d")]
        if not dates:
            return {}, {}, 0.0, None
        lg = ark[dates[-1]]
        pos, px = {t: q for t, (q, _) in lg.items()}, {t: p for t, (_, p) in lg.items()}
        return pos, px, sum(pos[t] * px[t] for t in pos), dates[-1]

    # ----- Sénat, cours, Buffett : mêmes sources, coupées à T -----
    cache_senat, cache_prix, cache_13f = {}, {}, {}

    def senat_get(slug=None):
        if slug not in cache_senat:
            cache_senat[slug] = origine["senat_get"](slug)
            time.sleep(0 if simu or not slug else 0.5)
        r = cache_senat[slug]
        if not slug:
            return r
        veille = horloge["T"].strftime("%Y-%m-%d")
        tr = [t for t in r.get("data", {}).get("trades", []) if (t.get("disclosureDate") or "9999") < veille]
        return {"data": dict(r.get("data", {}), trades=tr)}

    def cours_yahoo(tickers, debut_=None):
        manque = sorted({t for t in tickers if t and t not in cache_prix})
        if manque:
            cache_prix.update(origine["cours_yahoo"](manque, c.DEBUT_COURS_CONGRES))
            for t in manque:
                cache_prix.setdefault(t, {})
        veille = horloge["T"].strftime("%Y-%m-%d")
        return {t: {d: v for d, v in cache_prix[t].items() if d < veille} for t in tickers if t and cache_prix.get(t)}

    def sec_13f():
        if "sec" not in cache_13f:
            try:
                cache_13f["sec"] = origine["sec_13f"]()
            except Exception as e:
                cache_13f["sec"] = e
        if isinstance(cache_13f["sec"], Exception):
            raise cache_13f["sec"]
        return cache_13f["sec"]

    def dataroma_brk():
        if "dr" not in cache_13f:
            cache_13f["dr"] = origine["dataroma_brk"]()
        return cache_13f["dr"]

    remplacants = {"hl_info": hl_info, "hl_choisir": lambda: list(hl["comptes"]), "ark_portefeuille": ark_portefeuille,
                   "senat_get": senat_get, "cours_yahoo": cours_yahoo, "sec_13f": sec_13f, "dataroma_brk": dataroma_brk}
    etat, hist, mouvements = {"bots": {}}, [], []
    try:
        for n, f in remplacants.items():
            setattr(c, n, f)
        t = debut
        while t < maintenant - timedelta(minutes=5):
            horloge["T"] = t
            etat, hist, mouvements = c.un_passage(etat, hist, mouvements, t)
            t += timedelta(hours=1)
    finally:
        for n, f in origine.items():
            setattr(c, n, f)
    etat["retro"] = {"debut": debut_txt, "version": c.RETRO_VERSION, "fait_utc": maintenant.strftime("%Y-%m-%d %H:%M"), "notes": notes,
                     "hyperliquid_comptes": hl["comptes"]}
    for b in etat["bots"].values():                     # les erreurs de la reconstitution restent dans les notes
        if b.get("erreurs"):
            notes.append(f"{b['classe']} : {b['erreurs'][:3]}")
    print("reconstitution :", json.dumps({n: s["valeur"] for n, s in etat.get("synthese", {}).items()}), notes[:5])
    return etat, hist, mouvements
