"""Relève les cours (Yahoo Finance, barres d'une heure sur un mois) de tous les instruments de univers.json,
plus les 50 premières cryptos par capitalisation (classement CoinGecko), et écrit :
  out/resume.json            un résumé chiffré par instrument
  out/barres/<fichier>.json  les barres [t, ouverture, haut, bas, clôture] de chaque instrument
Dépendance : yfinance. Usage : python fetch.py [dossier_de_sortie]
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
STABLES = {"usdt", "usdc", "dai", "usds", "usde", "fdusd", "pyusd", "tusd", "usdd", "usd1", "usdf", "rlusd", "bfusd", "usdtb",
           "susde", "susds", "usd0", "gho", "frax", "eurs", "eurc", "usdg", "usdx", "usdy", "usyc", "buidl", "xaut", "paxg",
           "steth", "wsteth", "weth", "wbtc", "cbbtc", "weeth", "wbeth", "lbtc", "reth", "meth", "jitosol", "bnsol", "ezeth",
           "rseth", "solvbtc", "tbtc", "fbtc", "jlp", "msol", "ousg", "usdo", "usual"}
# Symboles Yahoo particuliers (suffixe numérique). Chacun est vérifié par comparaison de cours avant d'être retenu.
ALIAS = {"HYPE": "HYPE32196-USD", "UNI": "UNI7083-USD", "SUI": "SUI20947-USD", "TAO": "TAO22974-USD",
         "PEPE": "PEPE24478-USD", "MNT": "MNT27075-USD", "TON": "TON11419-USD", "APT": "APT21794-USD",
         "ARB": "ARB11841-USD", "POL": "POL28321-USD", "STX": "STX4847-USD", "IMX": "IMX10603-USD",
         "GRT": "GRT6719-USD", "JUP": "JUP29210-USD"}
MOTS_EXCLUS = ("wrapped", "staked", "staking", "bridged", "restaked", "liquid staking", "tether", "usd coin", "stablecoin")


def get(url, timeout=20, tries=3):
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:                       # réseau, 429, JSON invalide : on réessaie
            err = e
            time.sleep(2 * (i + 1))
    raise err


def load(symbols, lot=40):
    """Barres d'une heure sur un mois pour une liste de symboles Yahoo, via la bibliothèque yfinance
    (Yahoo refuse les requêtes directes venant des machines de GitHub). Renvoie {symbole: données}."""
    import yfinance as yf
    out = {}
    for i in range(0, len(symbols), lot):
        part = symbols[i:i + lot]
        try:
            df = yf.download(part, period="1mo", interval="60m", group_by="ticker", auto_adjust=False,
                             progress=False, threads=True)
        except Exception as e:
            print("lot en échec :", str(e)[:120])
            continue
        for s in part:
            try:
                sub = df[s][["Open", "High", "Low", "Close"]].dropna()
            except Exception:
                continue
            if sub.empty:
                continue
            bars = [[int(t.timestamp()), float(o), float(h), float(l), float(c)]
                    for t, (o, h, l, c) in zip(sub.index, sub.to_numpy())]
            out[s] = {"cours": bars[-1][4], "heure": bars[-1][0], "devise": None, "bars": bars}
        if i == 0 and not out:
            print("Premier lot entièrement vide : Yahoo refuse probablement les requêtes, on arrête.")
            break
        time.sleep(1)
    return out


def sig(x, n=7):
    return float(f"{x:.{n}g}")


def stats(d):
    """Résumé chiffré à partir des barres horaires."""
    bars, last = d["bars"], d["cours"]
    if not bars or not last:
        return None
    days = {}
    for t, o, h, l, c in bars:
        k = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
        x = days.setdefault(k, [h, l, c])
        x[0], x[1], x[2] = max(x[0], h), min(x[1], l), c
    keys = sorted(days)
    closes = [days[k][2] for k in keys]
    prev = lambda n: closes[-1 - n] if len(closes) > n else None
    var = lambda ref: None if not ref else round(last / ref - 1, 5)
    amp = [(days[k][0] - days[k][1]) / days[k][2] for k in keys[-15:] if days[k][2]]
    return {"cours": last, "heure": d["heure"], "devise": d["devise"],
            "var_1j": var(prev(1)), "var_5j": var(prev(5)), "var_1m": var(bars[0][1]),
            "haut_1m": sig(max(b[2] for b in bars)), "bas_1m": sig(min(b[3] for b in bars)),
            "amplitude_jour_moy": round(sum(amp) / len(amp), 5) if amp else None, "nb_barres": len(bars)}


def fichier(sym):
    return re.sub(r"[^A-Za-z0-9.\-]", "_", sym)


def top_crypto(n=50):
    """Les n premières cryptos par capitalisation, hors stablecoins et jetons adossés. Renvoie [(nom, ticker, cours)]."""
    rows = get("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=120&page=1")
    out = []
    for r in rows:
        s, name = (r.get("symbol") or "").lower(), (r.get("name") or "").lower()
        px = r.get("current_price") or 0
        if s in STABLES or any(m in name for m in MOTS_EXCLUS):
            continue
        if "usd" in s and 0.95 < px < 1.05:
            continue
        out.append((r["name"], s.upper(), px))
        if len(out) == n:
            break
    return out


def main(outdir):
    uni = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "univers.json"), encoding="utf-8"))
    classe_de, noms, secours, ref = {}, {}, [], {}
    for classe, syms in uni.items():
        if classe.startswith("Crypto"):
            secours = syms
        else:
            classe_de.update({s: classe for s in syms})
    try:
        for name, t, px in top_crypto(75):               # 75 candidates, on garde les 50 premières valides
            if not re.fullmatch(r"[A-Z0-9]{1,10}", t):
                continue
            for sym in (f"{t}-USD", ALIAS.get(t)):
                if sym:
                    classe_de[sym], noms[sym], ref[sym] = "Crypto", name, px
        source_crypto = "classement CoinGecko"
    except Exception as e:
        print("CoinGecko indisponible :", e)
        source_crypto = "liste de secours"
        classe_de.update({s: "Crypto" for s in secours})
    brut = load(list(classe_de))
    instruments, erreurs, data = [], [], {}
    for sym, classe in classe_de.items():
        d = brut.get(sym)
        if not d:
            erreurs.append({"symbole": sym, "classe": classe, "erreur": "aucun cours renvoyé par Yahoo"})
        elif sym in ref and ref[sym] and abs(d["cours"] / ref[sym] - 1) > 0.03:
            erreurs.append({"symbole": sym, "classe": classe, "erreur": "cours Yahoo incohérent avec le classement (autre jeton du même nom)"})
        else:
            data[sym] = (classe, noms.get(sym), d)
    # crypto : une seule ligne par jeton, 50 au plus, dans l'ordre du classement ; les alias sans cours ne sont pas des erreurs
    vus, garde = set(), 0
    for sym in [x for x in classe_de if classe_de[x] == "Crypto"]:
        if sym in data and (noms.get(sym) in vus or garde >= 50):
            del data[sym]
        elif sym in data:
            vus.add(noms.get(sym)); garde += 1
    erreurs = [e for e in erreurs if not (e["classe"] == "Crypto" and (noms.get(e["symbole"]) in vus or e["symbole"] in ALIAS.values()))]
    attendu = len([x for x in classe_de if classe_de[x] != "Crypto"]) + 50
    os.makedirs(os.path.join(outdir, "barres"), exist_ok=True)
    for sym, (classe, nom, d) in data.items():
        st = stats(d)
        if not st:
            erreurs.append({"symbole": sym, "classe": classe, "erreur": "aucune barre"})
            continue
        st["negociable"] = time.time() - d["heure"] < 5400      # dernière barre commencée il y a moins de 90 minutes
        instruments.append({"classe": classe, "symbole": sym, "nom": nom, "fichier": fichier(sym) + ".json", **st})
        with open(os.path.join(outdir, "barres", fichier(sym) + ".json"), "w") as f:
            json.dump({"symbole": sym, "cours": d["cours"], "heure": d["heure"],
                       "barres": [[b[0]] + [sig(x) for x in b[1:]] for b in d["bars"]]}, f, separators=(",", ":"))
    instruments.sort(key=lambda x: (x["classe"], x["symbole"]))
    resume = {"maj_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), "maj_epoch": int(time.time()),
              "source": "Yahoo Finance, barres d'une heure sur un mois", "liste_crypto": source_crypto,
              "nb_instruments": len(instruments), "instruments": instruments, "erreurs": erreurs}
    with open(os.path.join(outdir, "resume.json"), "w", encoding="utf-8") as f:
        json.dump(resume, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(instruments)} instruments relevés sur {attendu}, {len(erreurs)} erreurs, crypto : {source_crypto}")
    for e in erreurs[:40]:
        print("  erreur", e)
    if len(instruments) < 0.5 * attendu:
        sys.exit("Moins de la moitié des cours obtenus : on ne publie pas, pour garder le dernier relevé valable.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
