"""Bots de copie (référence du journal de trading fictif) : portefeuilles miroirs, sans analyse ni actualité.

Principe : un bot ne part pas de liquidités qu'il placerait peu à peu. Dès le premier relevé, il recopie le portefeuille
des comptes suivis au prorata de leur taille, puis achète et vend en même temps qu'eux, dans la même proportion.

Quatre bots, 10 000 € fictifs chacun, sans levier :
  - « Hyperliquid » (crypto) : les N_COMPTES meilleurs comptes du classement public de Hyperliquid, positions publiques
    relues à chaque relevé. Liste figée à la création, revue chaque lundi (premier relevé après 15h00 UTC) par une règle
    fixe : valeur ≥ VALEUR_MIN $, gagnant sur le mois et depuis le début, actif sur la semaine, classé par gain du mois.
  - « ARKK » (actions) : le fonds ARK Innovation, qui publie chaque jour de bourse la liste complète de ses lignes.
  - « Sénateurs » (actions) : les N_SENATEURS sénateurs américains dont les achats déclarés sur 12 mois ont le mieux
    rapporté (rendement moyen pondéré par le montant déclaré, au moins MIN_ACHATS achats ; revue chaque lundi).
    Leur portefeuille est reconstitué à partir de leurs déclarations (STOCK Act, publiées 30 à 45 jours après le trade) :
    achat = + montant médian de la fourchette ÷ cours du jour du trade, vente totale = 0, vente partielle = − montant ;
    le produit des ventes reste en liquidités jusqu'au prochain achat. Seuls les actions et ETF cotés comptent ;
    les lignes détenues avant le début des données (début 2025) ne sont pas connues. Il faut un rendement positif.
    Source : tracefour.com (compilation CC BY 4.0 des déclarations officielles du Sénat).
  - « Buffett » (actions) : les lignes de Berkshire Hathaway déclarées chaque trimestre à la SEC (formulaire 13F,
    publié jusqu'à 45 jours après la fin du trimestre). Les CUSIP sont traduits en symboles par OpenFIGI.
Le bot copie aussitôt qu'il voit une déclaration : le retard des déclarations fait partie de ce qui est mesuré.

Chaque compte suivi a sa part du capital (une « poche »). Dans une poche, la quantité visée de chaque ligne vaut
  quantité du compte suivi × valeur de la poche ÷ max(valeur du compte, exposition brute du compte)
c'est-à-dire les mêmes poids que le compte, ramenés à une exposition au plus égale à la valeur de la poche (jamais de
levier). Les ventes à découvert d'un compte Hyperliquid sont recopiées en ventes à découvert.
Une ligne n'est retraitée que si le compte suivi l'a vraiment modifiée : écart d'au moins SEUIL_RELATIF de la ligne
(et SEUIL_ABS €), ouverture, fermeture ou changement de sens. La simple dérive des cours ne déclenche aucun ordre.
Frais (glissement compris) : moitié des frais aller-retour de la classe sur chaque montant acheté ou vendu.
Approximations : conversion de devise négligée (1 $ = 1 € fictif), coût de financement des positions crypto négligé,
dividendes négligés. Cours : Hyperliquid pour la crypto, fichier d'ARK pour ARKK, Yahoo (yfinance) pour les autres.

Entrées : l'état précédent (DOSSIER/copie) ; sorties : copie/etat.json, copie/historique.json, copie/mouvements.json.
Usage : python copie.py DOSSIER
Test hors ligne : COPIE_SIMULATION=dossier (leaderboard.json, mids.json, etats/<adresse>.json, arkk.csv,
senat/liste.json, senat/<slug>.json, sec/submissions.json, sec/index.json, sec/table.xml, figi.json, prix.json)
et COPIE_MAINTENANT="AAAA-MM-JJ HH:MM" (UTC).
"""
import csv
import io
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

CAPITAL = 10000.0
FRAIS_AR = {"Crypto": 0.0060, "Actions": 0.0020}      # aller-retour, comme les autres bots du journal
N_COMPTES = 10
VALEUR_MIN = 100000.0
N_SENATEURS = 5
MIN_ACHATS = 5
TOUJOURS_SUIVIS = ["nancy-pelosi"]        # suivie en plus des sénateurs choisis (demande de l'utilisateur, 26/11/2026)
DEBUT_COURS_CONGRES = "2015-01-01"        # cours chargés depuis cette date pour reconstituer les portefeuilles déclarés
SEUIL_RELATIF = 0.10
SEUIL_ABS = 10.0
HEURE_UTC = 15
# Qualité des comptes Hyperliquid (v2, 26/11/2026, d'après les conseils publiés sur le copy trading) :
HL_ANCIENNETE_MIN = 90       # jours d'historique au moins (sinon chance ou contexte favorable)
HL_BAISSE_MAX = 0.40         # pire baisse de l'équité ≤ 40 %
HL_LEVIER_MAX = 10.0         # exposition brute ≤ 10 fois la valeur du compte au moment du choix
HL_ROTATION_MAX = 200.0      # volume du mois ≤ 200 fois la valeur du compte (écarte les teneurs de marché)
HL_PRESELECTION = 40         # comptes examinés en détail à chaque revue
HL_PART_MAX_ACTIF = 0.30     # un actif ≤ 30 % de la valeur du bot (somme des comptes copiés)
HL_PART_MAX_SENS = 0.60      # exposition nette dans un sens ≤ 60 % de la valeur du bot

HL_INFO = "https://api.hyperliquid.xyz/info"
HL_CLASSEMENT = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
ARK_CSV = "https://assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv"
ARK_SECOURS = "https://arkfunds.io/api/v2/etf/holdings?symbol=ARKK"
SENAT = "https://tracefour.com/v1/congress"
BRK_CIK = "1067983"
SEC_SUB = f"https://data.sec.gov/submissions/CIK{int(BRK_CIK):010d}.json"
SEC_ARCH = f"https://www.sec.gov/Archives/edgar/data/{BRK_CIK}"
FIGI = "https://api.openfigi.com/v3/mapping"
DATAROMA = "https://www.dataroma.com/m/holdings.php?m=BRK"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
UA_SEC = {"User-Agent": "cours-marches journal fictif nicolasbeard6@users.noreply.github.com"}   # exigé par la SEC
SIMU = os.environ.get("COPIE_SIMULATION")


# ---------- accès aux sources ----------

def _http(url, data=None, timeout=30, tries=3, entetes=UA):
    err = None
    for i in range(tries):
        try:
            h = dict(entetes, **({"Content-Type": "application/json"} if data else {}))
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=timeout) as r:
                return r.read()
        except Exception as e:                       # réseau, 429 : on réessaie
            err = e
            time.sleep(2 * (i + 1))
    raise err


def _simu(*chemin, texte=False):
    f = open(os.path.join(SIMU, *chemin), encoding="utf-8")
    return f.read() if texte else json.load(f)


def nombre(x):
    try:
        return float(str(x).replace(",", "").replace("$", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def symbole_yahoo(t):
    return str(t).strip().upper().replace("/", "-").replace(".", "-").replace(" ", "-")


def cours_yahoo(tickers, debut=None):
    """{ticker: {date AAAA-MM-JJ: clôture}} pour des actions et ETF (yfinance, installé par le workflow)."""
    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return {}
    if SIMU:
        p = _simu("prix.json")
        return {t: p[t] for t in tickers if t in p}
    import yfinance as yf
    out = {}
    for i in range(0, len(tickers), 80):
        part = tickers[i:i + 80]
        quand = {"start": debut} if debut else {"period": "1mo"}
        try:
            df = yf.download(part, interval="1d", auto_adjust=False, progress=False, threads=True,
                             group_by="ticker", **quand)
        except Exception as e:
            print("cours yahoo en échec :", str(e)[:100])
            continue
        for t in part:
            try:
                sub = df[t] if t in df.columns.get_level_values(0) else df
                s = sub["Close"]
                s = (s.iloc[:, 0] if getattr(s, "ndim", 1) > 1 else s).dropna()
            except Exception:
                continue
            if len(s):
                out[t] = {d.strftime("%Y-%m-%d"): float(v) for d, v in s.items()}
    return out


def dernier(serie):
    return serie[max(serie)] if serie else None


def au_jour(serie, jour):
    """Clôture du jour donné, ou du premier jour de bourse suivant (dans les 10 jours ; sinon inconnue)."""
    limite = (datetime.strptime(jour[:10], "%Y-%m-%d") + timedelta(days=10)).strftime("%Y-%m-%d")
    for d in sorted(serie):
        if d >= jour:
            return serie[d] if d <= limite else None
    return None


# Hyperliquid

def hl_info(corps):
    if SIMU:
        if corps["type"] == "allMids":
            return _simu("mids.json")
        if corps["type"] == "portfolio":
            return _simu("portfolios", corps["user"].lower() + ".json")
        return _simu("etats", corps["user"].lower() + ".json")
    return json.loads(_http(HL_INFO, json.dumps(corps).encode()))


def fenetres(wp):
    return wp if isinstance(wp, dict) else {n: p for n, p in wp}


def hl_classement():
    lignes = _simu("leaderboard.json") if SIMU else json.loads(_http(HL_CLASSEMENT, timeout=120))
    return lignes.get("leaderboardRows", lignes) if isinstance(lignes, dict) else lignes


def hl_evaluer(adresse, jusqua_ms=None, avec_levier=True):
    """Qualité d'un compte d'après son historique public (portfolio, fenêtre allTime), coupé à jusqua_ms :
    ancienneté (jours), pire baisse de l'équité, rendement total, levier actuel. None si l'historique manque."""
    port = hl_info({"type": "portfolio", "user": adresse})
    port = {n: d for n, d in port} if isinstance(port, list) else port
    w = port.get("perpAllTime") or port.get("allTime") or {}
    av = [(int(t), float(v)) for t, v in w.get("accountValueHistory", []) if jusqua_ms is None or int(t) <= jusqua_ms]
    pnl = [(int(t), float(v)) for t, v in w.get("pnlHistory", []) if jusqua_ms is None or int(t) <= jusqua_ms]
    if len(av) < 2 or len(pnl) < 2:
        return None
    base = av[-1][1] - pnl[-1][1]                    # dépôts nets approchés : équité = base + gain cumulé
    pic, baisse = None, 0.0
    for _, g in pnl:
        eq = base + g
        if pic is None or eq > pic:
            pic = eq
        if pic and pic > 0:
            baisse = max(baisse, (pic - eq) / pic)
    rend = pnl[-1][1] / base if base > 0 else pnl[-1][1] / max(av[-1][1], 1.0)
    levier = None
    if avec_levier:
        e = hl_info({"type": "clearinghouseState", "user": adresse})
        m = e.get("marginSummary", {})
        levier = (nombre(m.get("totalNtlPos")) or 0.0) / max(nombre(m.get("accountValue")) or 1.0, 1.0)
    return {"anciennete_j": round((av[-1][0] - av[0][0]) / 86400000, 1), "pire_baisse": round(baisse, 4),
            "rendement": round(rend, 4), "levier": None if levier is None else round(levier, 2),
            "score": round(rend / max(baisse, 0.05), 4)}


def hl_preselection(lignes, gain_cle):
    """Filtre du classement : valeur ≥ VALEUR_MIN, gagnant depuis le début et sur la période gain_cle(p) > 0,
    actif sur le mois sans être un teneur de marché (rotation ≤ HL_ROTATION_MAX). Classés par rendement total."""
    cand = []
    for r in lignes:
        try:
            v, p = float(r["accountValue"]), fenetres(r["windowPerformances"])
            tout, g, vlm = float(p["allTime"]["pnl"]), gain_cle(p), float(p["month"]["vlm"])
            roi = float(p["allTime"].get("roi") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if v >= VALEUR_MIN and tout > 0 and g > 0 and 0 < vlm <= HL_ROTATION_MAX * v:
            cand.append({"id": r["ethAddress"].lower(), "nom": r.get("displayName") or "", "valeur": round(v, 2),
                         "gain_periode": round(g, 2), "roi_total": roi})
    cand.sort(key=lambda x: -x["roi_total"])
    return cand


def hl_retenir(cand, jusqua_ms=None, avec_levier=True, accepter=lambda a: True):
    """Examine les candidats dans l'ordre : ancienneté, pire baisse et levier ; classe les admis par
    rendement ÷ pire baisse (au moins 5 %) et garde les N_COMPTES meilleurs."""
    admis = []
    for k in cand[:HL_PRESELECTION]:
        try:
            q = hl_evaluer(k["id"], jusqua_ms, avec_levier)
            time.sleep(0 if SIMU else 0.5)
        except Exception:
            continue
        if not q or q["anciennete_j"] < HL_ANCIENNETE_MIN or q["pire_baisse"] > HL_BAISSE_MAX:
            continue
        if q["levier"] is not None and q["levier"] > HL_LEVIER_MAX:
            continue
        if not accepter(k["id"]):
            continue
        admis.append(dict(k, **q))
    admis.sort(key=lambda x: -x["score"])
    return admis[:N_COMPTES]


def hl_choisir():
    cand = hl_preselection(hl_classement(), lambda p: float(p["month"]["pnl"]))
    return hl_retenir(cand)


def hl_portefeuille(adresse):
    e = hl_info({"type": "clearinghouseState", "user": adresse})
    pos = {}
    for a in e.get("assetPositions", []):
        p = a.get("position", a)
        q = nombre(p.get("szi"))
        if q:
            pos[p["coin"]] = q
    return pos, nombre(e.get("marginSummary", {}).get("accountValue")) or 0.0


# ARK

def ark_portefeuille():
    if SIMU:
        brut = _simu("arkk.csv", texte=True)
    else:
        try:
            brut = _http(ARK_CSV).decode("utf-8", "replace")
        except Exception:
            brut = None
    pos, px, date = {}, {}, None
    if brut and "ticker" in brut.lower():
        for r in csv.DictReader(io.StringIO(brut)):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            t, q = r.get("ticker"), nombre(r.get("shares"))
            mv = nombre(r.get("market value ($)") or r.get("market value") or r.get("market_value"))
            if t and q and mv:
                pos[t], px[t], date = q, mv / q, r.get("date") or date
    else:                                             # secours : API publique arkfunds.io (mêmes données)
        d = json.loads(_http(ARK_SECOURS))
        for h in d.get("holdings", []):
            t, q, mv = h.get("ticker"), nombre(h.get("shares")), nombre(h.get("market_value"))
            if t and q and mv:
                pos[t], px[t] = q, mv / q
        date = d.get("date_to") or d.get("date")
    return pos, px, sum(pos[t] * px[t] for t in pos), date


# Sénat

def senat_get(slug=None):
    if SIMU:
        return _simu("senat", (slug or "liste") + ".json")
    return json.loads(_http(SENAT + (f"/{slug}" if slug else ""), timeout=40))


def senat_trades(slug):
    """Achats et ventes d'actions et d'ETF déclarés : [(date, ticker, sens, montant)], sens = +1, -1 (partielle), 0 (totale)."""
    d = senat_get(slug).get("data", {})
    out = []
    for t in d.get("trades", []):
        tk, typ = (t.get("ticker") or "").strip(), (t.get("type") or "")
        detail = (t.get("filingId") or "").split("|")
        detail = detail[3] if len(detail) > 3 else typ
        typ_actif = t.get("assetType") or ""
        if not tk or tk in ("--", "N/A") or not (typ_actif in ("Stock", "ETF") or "Option" in typ_actif):
            continue                                  # une option est copiée comme l'action sous-jacente, pour le même montant
        m = nombre(t.get("amountMid")) or 0
        if detail.startswith("Purchase"):
            out.append((t["transactionDate"], symbole_yahoo(tk), 1, m))
        elif detail.startswith("Sale (Full)"):
            out.append((t["transactionDate"], symbole_yahoo(tk), 0, m))
        elif detail.startswith("Sale"):
            out.append((t["transactionDate"], symbole_yahoo(tk), -1, m))
    return sorted(out)


def senat_portefeuille(trades, prix):
    """Portefeuille reconstitué à partir des déclarations : ({ticker: nombre d'actions}, liquidités).
    Le produit d'une vente reste en liquidités (le sénateur ne réinvestit pas forcément) ; un achat les consomme d'abord."""
    pos, liq = {}, 0.0
    for jour, tk, s, m in trades:
        c = au_jour(prix.get(tk, {}), jour)
        if not c:
            continue
        if s == 1:
            pos[tk] = pos.get(tk, 0) + m / c
            liq = max(0.0, liq - m)
        else:
            vendu = pos.get(tk, 0) if s == 0 else min(pos.get(tk, 0), m / c)
            pos[tk] = pos.get(tk, 0) - vendu
            liq += vendu * c
            if pos[tk] <= 1e-12:
                pos.pop(tk)
    return pos, liq


def senat_choisir(now):
    membres = [m for m in senat_get().get("data", []) if m.get("chamber") == "senate" or m.get("slug") in TOUJOURS_SUIVIS]
    tous, erreurs = {}, []
    for m in membres:
        try:
            tous[m["slug"]] = (m.get("name") or m["slug"], senat_trades(m["slug"]))
        except Exception as e:                        # limite de requêtes ou page absente : sénateur ignoré cette fois
            erreurs.append(m["slug"])
    debut = DEBUT_COURS_CONGRES
    prix = cours_yahoo({tk for _, tr in tous.values() for _, tk, _, _ in tr}, debut)
    il_y_a_un_an = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    scores, imposes = [], []
    for slug, (nom, tr) in tous.items():
        somme, pond, n = 0.0, 0.0, 0
        for jour, tk, s, m in tr:
            if s != 1 or jour < il_y_a_un_an:
                continue
            c0, c1 = au_jour(prix.get(tk, {}), jour), dernier(prix.get(tk, {}))
            if c0 and c1:
                somme, pond, n = somme + m * (c1 / c0 - 1), pond + m, n + 1
        ligne = {"id": slug, "nom": nom, "achats_12m": n, "rendement_achats_12m": round(somme / pond, 4) if pond else None}
        if slug in TOUJOURS_SUIVIS:
            if senat_portefeuille(tr, prix)[0]:
                imposes.append(dict(ligne, impose=True))
        elif n >= MIN_ACHATS and pond and somme > 0 and senat_portefeuille(tr, prix)[0]:
            scores.append(ligne)
    scores.sort(key=lambda x: -x["rendement_achats_12m"])
    return imposes + scores[:N_SENATEURS], {s: tr for s, (_, tr) in tous.items()}, prix, erreurs


# Berkshire (13F)

def sec_13f():
    """Dernier 13F-HR de Berkshire : ({cusip: (nom, actions)}, date du rapport, numéro de dépôt)."""
    sub = _simu("sec", "submissions.json") if SIMU else json.loads(_http(SEC_SUB, entetes=UA_SEC))
    r = sub["filings"]["recent"]
    i = next(k for k, f in enumerate(r["form"]) if f == "13F-HR")
    acc, rapport = r["accessionNumber"][i], r["reportDate"][i]
    dossier = f"{SEC_ARCH}/{acc.replace('-', '')}"
    idx = _simu("sec", "index.json") if SIMU else json.loads(_http(f"{dossier}/index.json", entetes=UA_SEC))
    xmls = [it["name"] for it in idx["directory"]["item"] if it["name"].lower().endswith(".xml")
            and "primary_doc" not in it["name"].lower()]
    brut = _simu("sec", "table.xml", texte=True) if SIMU else _http(f"{dossier}/{xmls[0]}", entetes=UA_SEC).decode()
    lignes = {}
    for el in ET.fromstring(brut).iter():
        if not el.tag.endswith("infoTable"):
            continue
        v = {c.tag.split("}")[-1]: (c.text or "").strip() for c in el.iter()}
        if v.get("putCall") or v.get("sshPrnamtType", "SH") != "SH":
            continue
        cu, q = v.get("cusip"), nombre(v.get("sshPrnamt"))
        if cu and q:
            nom, q0 = lignes.get(cu, (v.get("nameOfIssuer"), 0))
            lignes[cu] = (nom, q0 + q)
    return lignes, rapport, acc


def dataroma_brk():
    """Secours si la SEC refuse : même 13F, mis en forme par dataroma.com (symboles déjà donnés).
    Renvoie ({symbole: actions}, date du portefeuille)."""
    brut = _simu("dataroma.html", texte=True) if SIMU else _http(DATAROMA).decode("utf-8", "replace")
    date = re.search(r"Portfolio date:\s*([^\n]+)", re.sub(r"<[^>]+>", "", brut))
    pos = {}
    for ligne in brut.split("<tr")[1:]:
        sym = re.search(r'class="stock"><a href="/m/stock\.php\?sym=([^"&]+)"', ligne)
        cases = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", ligne, re.S)]
        if sym and len(cases) > 4 and nombre(cases[4]):
            pos[symbole_yahoo(sym.group(1))] = nombre(cases[4])
    return pos, (date.group(1).strip() if date else None)


def figi(cusips, cache):
    """CUSIP -> symbole Yahoo (OpenFIGI, sans clé : 10 CUSIP par requête, 25 requêtes par minute)."""
    manque = [c for c in cusips if c not in cache]
    for i in range(0, len(manque), 10):
        lot = manque[i:i + 10]
        if SIMU:
            rep = [{"data": [{"ticker": _simu("figi.json")[c]}]} if c in _simu("figi.json") else {} for c in lot]
        else:
            corps = json.dumps([{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in lot]).encode()
            try:
                rep = json.loads(_http(FIGI, corps))
            except Exception:
                continue
            time.sleep(3)
        for c, r in zip(lot, rep):
            d = (r or {}).get("data") or []
            if d and d[0].get("ticker"):
                cache[c] = symbole_yahoo(d[0]["ticker"])
    return cache


# ---------- moteur commun ----------

def valeur_poche(p, cours):
    return p["liquidites"] + sum(q * cours.get(c, p["dernier_cours"].get(c, 0)) for c, q in p["lignes"].items())


def reproduire(p, cible_src, valeur_src, cours, classe, quand, mouvements, bot, echelle=None):
    """Aligne une poche sur le portefeuille d'un compte suivi (echelle : réduction par ligne, plafonds du bot).
    Renvoie les frais payés."""
    echelle = echelle or {}
    for c, x in cours.items():
        if c in p["lignes"] or c in cible_src:
            p["dernier_cours"][c] = x
    v = valeur_poche(p, cours)
    brut = sum(abs(q) * cours[c] for c, q in cible_src.items() if c in cours)
    f = v / max(valeur_src, brut) if v > 0 and max(valeur_src, brut) > 0 else 0.0
    frais = 0.0
    for c in sorted(set(p["lignes"]) | set(cible_src)):
        x = cours.get(c) or (p["dernier_cours"].get(c) if c not in cible_src else None)
        if not x:                                     # pas de cours pour une ligne à ouvrir : laissée de côté
            continue                                  # (une ligne sortie du portefeuille suivi se revend au dernier cours connu)
        avant = p["lignes"].get(c, 0.0)
        apres = cible_src.get(c, 0.0) * f * echelle.get(c, 1.0)
        ecart = (apres - avant) * x
        change_sens = avant and apres and (avant > 0) != (apres > 0)
        if not ((avant and not apres) or (apres and not avant) or change_sens):
            if abs(ecart) < max(SEUIL_ABS, SEUIL_RELATIF * abs(avant * x)):
                continue
        elif abs(ecart) < 0.01:
            continue
        fr = abs(ecart) * FRAIS_AR[classe] / 2
        p["liquidites"] -= ecart + fr
        frais += fr
        if apres:
            p["lignes"][c] = apres
        else:
            p["lignes"].pop(c, None)
        mouvements.append({"heure_utc": quand, "bot": bot, "classe": classe, "compte": p["source"],
                           "instrument": c, "ordre": "ACHAT" if ecart > 0 else "VENTE", "montant": round(abs(ecart), 2),
                           "cours": x, "position_apres": round(apres * x, 2), "frais": round(fr, 4)})
    return frais


def nouveau_bot(classe):
    return {"classe": classe, "poches": {}, "frais_cumules": 0.0, "derniere_revue": None, "derniere_maj": None,
            "liquidites_libres": 0.0, "erreurs": [], "debut_utc": None}


def revoir(bot, choisis, cours, quand, mouvements, nom_bot):
    """Remplace les comptes qui sortent de la liste : revente totale, liquidités réparties entre les entrants."""
    nouveaux = {c["id"]: c for c in choisis}
    libres = bot["liquidites_libres"] + (CAPITAL if bot["debut_utc"] is None else 0.0)
    for a in [a for a in bot["poches"] if a not in nouveaux]:
        p = bot["poches"].pop(a)
        bot["frais_cumules"] += reproduire(p, {}, 0.0, cours, bot["classe"], quand, mouvements, nom_bot)
        libres += p["liquidites"]
    entrants = [a for a in nouveaux if a not in bot["poches"]]
    for a in entrants:
        bot["poches"][a] = {"source": a, "nom": nouveaux[a].get("nom", ""), "liquidites": libres / len(entrants),
                            "lignes": {}, "dernier_cours": {}, "entree_utc": quand}
    bot["liquidites_libres"] = 0.0 if entrants else libres
    bot["comptes_suivis"] = choisis
    bot["debut_utc"] = bot["debut_utc"] or quand


def bilan(bot, cours):
    v = sum(valeur_poche(p, cours) for p in bot["poches"].values()) + bot["liquidites_libres"]
    if bot["debut_utc"] is None:
        v = CAPITAL
    lignes = {}
    for p in bot["poches"].values():
        for c, q in p["lignes"].items():
            lignes[c] = lignes.get(c, 0.0) + q * cours.get(c, p["dernier_cours"].get(c, 0))
    return {"classe": bot["classe"], "valeur": round(v, 2), "resultat": round(v - CAPITAL, 2),
            "achats": sum(1 for m in lignes.values() if m > 0), "ventes": sum(1 for m in lignes.values() if m < 0),
            "exposition": round(sum(abs(m) for m in lignes.values()), 2),
            "exposition_achat": round(sum(m for m in lignes.values() if m > 0), 2),
            "exposition_vente": round(-sum(m for m in lignes.values() if m < 0), 2),
            "frais_cumules": round(bot["frais_cumules"], 2), "comptes": len(bot["poches"]),
            "debut_utc": bot["debut_utc"],
            "premieres_lignes": dict(sorted(((c, round(m, 2)) for c, m in lignes.items()), key=lambda x: -abs(x[1]))[:10])}


def jour_de_revue(bot, now):
    return not bot["poches"] or (now.weekday() == 0 and now.hour >= HEURE_UTC and bot["derniere_revue"] != now.strftime("%Y-%m-%d"))


def mise_a_jour_du_jour(bot, now):
    return now.weekday() < 5 and now.hour >= HEURE_UTC and bot["derniere_maj"] != now.strftime("%Y-%m-%d")


# ---------- les quatre bots ----------

def bot_hyperliquid(bot, now, quand, mouvements):
    cours = {c: float(x) for c, x in hl_info({"type": "allMids"}).items() if not c.startswith("@") and nombre(x)}
    if jour_de_revue(bot, now):
        choisis = hl_choisir()
        if choisis:
            revoir(bot, choisis, cours, quand, mouvements, "Hyperliquid")
            bot["derniere_revue"] = now.strftime("%Y-%m-%d")
        elif not bot["poches"]:
            bot["erreurs"].append("classement Hyperliquid vide ou illisible")
    lus = {}
    for a, p in bot["poches"].items():
        try:
            lus[a] = hl_portefeuille(a)
        except Exception as e:
            bot["erreurs"].append(f"{a}: {str(e)[:80]}")
    echelle = plafonds_hl(bot, lus, cours)
    for a, (pos, val) in lus.items():
        p = bot["poches"][a]
        p["valeur_compte_suivi"] = round(val, 2)
        bot["frais_cumules"] += reproduire(p, pos, val, cours, "Crypto", quand, mouvements, "Hyperliquid", echelle)
    return cours


def plafonds_hl(bot, lus, cours):
    """Réductions par actif pour qu'au niveau du bot un actif pèse au plus HL_PART_MAX_ACTIF de sa valeur et
    l'exposition nette dans un sens au plus HL_PART_MAX_SENS (les positions suivies sont additionnées)."""
    vb = sum(valeur_poche(p, cours) for p in bot["poches"].values())
    expo = {}
    for a, (pos, val) in lus.items():
        p = bot["poches"][a]
        v = valeur_poche(p, cours)
        brut = sum(abs(q) * cours[c] for c, q in pos.items() if c in cours)
        f = v / max(val, brut) if v > 0 and max(val, brut) > 0 else 0.0
        for c, q in pos.items():
            if c in cours:
                expo[c] = expo.get(c, 0.0) + q * f * cours[c]
    if vb <= 0:
        return {}
    ech = {c: min(1.0, HL_PART_MAX_ACTIF * vb / abs(e)) for c, e in expo.items() if e}
    long_ = sum(e * ech[c] for c, e in expo.items() if e > 0)
    court = -sum(e * ech[c] for c, e in expo.items() if e < 0)
    if long_ - court > HL_PART_MAX_SENS * vb:
        k = (HL_PART_MAX_SENS * vb + court) / long_
        ech = {c: x * (k if expo[c] > 0 else 1.0) for c, x in ech.items()}
    elif court - long_ > HL_PART_MAX_SENS * vb:
        k = (HL_PART_MAX_SENS * vb + long_) / court
        ech = {c: x * (k if expo[c] < 0 else 1.0) for c, x in ech.items()}
    return {c: x for c, x in ech.items() if x < 0.999}


def bot_arkk(bot, now, quand, mouvements):
    pos, px, val, date = ark_portefeuille()
    if not pos:
        bot["erreurs"].append("ARKK : fichier vide ou illisible")
        return {}
    if not bot["poches"]:
        revoir(bot, [{"id": "ARKK", "nom": "ARK Innovation ETF"}], px, quand, mouvements, "ARKK")
    p = bot["poches"]["ARKK"]
    if date != bot.get("date_fichier"):              # nouveau fichier publié : on recopie ; sinon rien ne change
        bot["frais_cumules"] += reproduire(p, pos, val, px, "Actions", quand, mouvements, "ARKK")
        bot["date_fichier"] = date
    p["valeur_compte_suivi"] = round(val, 2)
    return dict(p["dernier_cours"])


def bot_senateurs(bot, now, quand, mouvements):
    if not (jour_de_revue(bot, now) or mise_a_jour_du_jour(bot, now)):
        return {c: x for p in bot["poches"].values() for c, x in p["dernier_cours"].items()}
    debut = DEBUT_COURS_CONGRES
    if jour_de_revue(bot, now):
        choisis, trades, prix, erreurs = senat_choisir(now)
        if erreurs:
            bot["erreurs"].append(f"{len(erreurs)} sénateurs non lus à la revue")
        if not choisis:
            bot["erreurs"].append("aucun sénateur retenu (données illisibles)")
            return {}
        bot["classement"] = choisis
    else:
        trades, prix = {}, {}
        for a in bot["poches"]:
            try:
                trades[a] = senat_trades(a)
            except Exception as e:
                bot["erreurs"].append(f"{a}: {str(e)[:80]}")
        prix = cours_yahoo({tk for tr in trades.values() for _, tk, _, _ in tr}
                           | {c for p in bot["poches"].values() for c in p["lignes"]}, debut)
    cours = {t: dernier(s) for t, s in prix.items() if s}
    if jour_de_revue(bot, now):
        revoir(bot, choisis, cours, quand, mouvements, "Sénateurs")
        bot["derniere_revue"] = now.strftime("%Y-%m-%d")
    for a, p in bot["poches"].items():
        if a not in trades:
            continue
        pos, liq = senat_portefeuille(trades[a], prix)
        val = liq + sum(q * cours[t] for t, q in pos.items() if t in cours)
        p["valeur_compte_suivi"] = round(val, 2)
        bot["frais_cumules"] += reproduire(p, pos, val, cours, "Actions", quand, mouvements, "Sénateurs")
    bot["derniere_maj"] = now.strftime("%Y-%m-%d")
    return cours


def bot_buffett(bot, now, quand, mouvements):
    if bot["poches"] and not mise_a_jour_du_jour(bot, now):
        return dict(bot["poches"]["BRK"]["dernier_cours"])
    try:
        lignes, rapport, acc = sec_13f()
        cache = figi(list(lignes), bot.setdefault("cusip_vers_symbole", {}))
        pos = {}
        for cu, (nom, q) in lignes.items():
            if cu in cache:
                pos[cache[cu]] = pos.get(cache[cu], 0) + q
            else:
                bot["erreurs"].append(f"CUSIP {cu} ({nom}) sans symbole : ligne ignorée")
        bot["source_13f"] = "SEC"
    except Exception as e:                            # la SEC refuse parfois les machines de GitHub : secours dataroma
        pos, rapport = dataroma_brk()
        acc = f"dataroma {rapport}"
        bot["source_13f"] = f"dataroma (SEC : {type(e).__name__})"
        if not pos:
            raise
    prix = cours_yahoo(set(pos) | {c for p in bot["poches"].values() for c in p["lignes"]})
    cours = {t: dernier(s) for t, s in prix.items() if s}
    if not bot["poches"]:
        revoir(bot, [{"id": "BRK", "nom": "Berkshire Hathaway (13F)"}], cours, quand, mouvements, "Buffett")
    p = bot["poches"]["BRK"]
    val = sum(q * cours[t] for t, q in pos.items() if t in cours)
    p["valeur_compte_suivi"] = round(val, 2)
    if acc != bot.get("depot_13f"):                  # nouvelle déclaration : on recopie
        bot["frais_cumules"] += reproduire(p, pos, val, cours, "Actions", quand, mouvements, "Buffett")
        bot["depot_13f"], bot["date_rapport_13f"] = acc, rapport
    else:                                             # même déclaration : seuls les cours changent
        p["dernier_cours"].update({c: x for c, x in cours.items() if c in p["lignes"]})
    bot["derniere_maj"] = now.strftime("%Y-%m-%d")
    return dict(p["dernier_cours"])


BOTS = (("Hyperliquid", "Crypto", bot_hyperliquid), ("ARKK", "Actions", bot_arkk),
        ("Sénateurs", "Actions", bot_senateurs), ("Buffett", "Actions", bot_buffett))


DEBUT_RETRO = "2026-11-16 12:43"   # UTC : premier trade du journal (T0001, 16/11/2026 13:43 heure de Paris)
RETRO_VERSION = 2                  # changer ce numéro relance une reconstitution complète (règles modifiées)


def main(dossier):
    rep = os.path.join(dossier, "copie")
    os.makedirs(rep, exist_ok=True)
    chemins = {k: os.path.join(rep, k + ".json") for k in ("etat", "historique", "mouvements")}
    lire = lambda k, d: json.load(open(chemins[k], encoding="utf-8")) if os.path.exists(chemins[k]) else d
    etat, hist, mouvements = lire("etat", {"bots": {}}), lire("historique", []), lire("mouvements", [])
    now = (datetime.strptime(os.environ["COPIE_MAINTENANT"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
           if os.environ.get("COPIE_MAINTENANT") else datetime.now(timezone.utc))
    cle = f"retro_essais_v{RETRO_VERSION}"
    essais = etat.get(cle, 0)
    fait = etat.get("retro", {})
    if (fait.get("debut"), fait.get("version", 1)) != (DEBUT_RETRO, RETRO_VERSION) and essais < 3 \
            and not os.environ.get("COPIE_SANS_RETRO"):
        # Une seule fois : les bots sont reconstitués depuis le début du journal, avec les mêmes règles et les seules
        # données publiées à chaque instant, puis le relevé continue normalement (voir retro.py). Trois essais au plus.
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import retro
            etat, hist, mouvements = retro.reconstituer(sys.modules[__name__], DEBUT_RETRO, now)
        except Exception as e:
            print("reconstitution en échec :", type(e).__name__, str(e)[:200])
            etat[cle] = essais + 1
            etat["retro_erreur"] = f"{type(e).__name__}: {str(e)[:200]}"
    etat, hist, mouvements = un_passage(etat, hist, mouvements, now)
    json.dump(etat, open(chemins["etat"], "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.dump(hist[-5000:], open(chemins["historique"], "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.dump(mouvements[-20000:], open(chemins["mouvements"], "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("bots de copie :", json.dumps({n: {k: s[k] for k in ("valeur", "resultat", "exposition")} | {"erreurs": len(s["erreurs"])}
                                          for n, s in etat["synthese"].items()}, ensure_ascii=False))


def un_passage(etat, hist, mouvements, now):
    quand, jour = now.strftime("%Y-%m-%d %H:%M"), now.strftime("%Y-%m-%d")
    for nom, classe, fn in BOTS:
        bot = etat["bots"].setdefault(nom, nouveau_bot(classe))
        bot["erreurs"] = []
        try:
            cours = fn(bot, now, quand, mouvements)
        except Exception as e:                        # une source en panne ne bloque pas les autres ; état gardé
            bot["erreurs"].append(f"{type(e).__name__}: {str(e)[:120]}")
            cours = {c: x for p in bot["poches"].values() for c, x in p["dernier_cours"].items()}
        bot["synthese"] = bilan(bot, cours or {})
        bot["synthese"]["erreurs"] = bot["erreurs"]
    etat["maj_utc"] = quand
    etat["regles"] = {"capital": CAPITAL, "frais_aller_retour": FRAIS_AR, "comptes_hyperliquid": N_COMPTES,
                      "valeur_min_compte": VALEUR_MIN, "senateurs": N_SENATEURS, "min_achats_senateur": MIN_ACHATS,
                      "seuil_relatif": SEUIL_RELATIF, "seuil_abs": SEUIL_ABS,
                      "revues": f"lundi, premier relevé après {HEURE_UTC}h00 UTC",
                      "mises_a_jour_actions": f"jours ouvrés, premier relevé après {HEURE_UTC}h00 UTC"}
    etat["synthese"] = {n: b["synthese"] for n, b in etat["bots"].items()}
    for n, s in etat["synthese"].items():             # une ligne par bot et par jour (la dernière du jour est gardée)
        hist = [h for h in hist if not (h["date"] == jour and h["bot"] == n)]
        hist.append({"date": jour, "bot": n, **{k: s[k] for k in ("classe", "valeur", "resultat", "achats", "ventes", "exposition")}})
    return etat, hist, mouvements


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
