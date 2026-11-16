"""Relève les cours (Yahoo Finance, barres d'une heure sur un mois) de tous les instruments de univers.json,
plus les 50 premières cryptos par capitalisation (classement CoinGecko), et écrit :
  out/resume.json            un résumé chiffré par instrument
  out/barres/<fichier>.json  les barres [t, ouverture, haut, bas, clôture] de chaque instrument
Bibliothèque standard uniquement. Usage : python fetch.py [dossier_de_sortie]
"""
import concurrent.futures as cf
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


def chart(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=1mo&interval=60m"
    r = get(url)["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    bars = [[t, o, h, l, c] for t, o, h, l, c in zip(r.get("timestamp") or [], q["open"], q["high"], q["low"], q["close"])
            if None not in (o, h, l, c)]
    return {"cours": r["meta"].get("regularMarketPrice"), "heure": r["meta"].get("regularMarketTime"),
            "devise": r["meta"].get("currency"), "bars": bars}


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


def yahoo_crypto(ticker, ref):
    """Trouve le symbole Yahoo d'une crypto dont le cours est proche de ref (classement). Renvoie (symbole, données) ou None."""
    cands = [f"{ticker}-USD"]
    try:
        s = get(f"https://query1.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(ticker + '-USD')}&quotesCount=8&newsCount=0", tries=1)
        cands += [q["symbol"] for q in s.get("quotes", []) if q.get("quoteType") == "CRYPTOCURRENCY"
                  and q["symbol"].startswith(ticker) and q["symbol"].endswith("-USD") and q["symbol"] not in cands]
    except Exception:
        pass
    for c in cands[:4]:
        try:
            d = chart(c)
        except Exception:
            continue
        if d["cours"] and ref and abs(d["cours"] / ref - 1) < 0.03:
            return c, d
    return None


def main(outdir):
    uni = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "univers.json"), encoding="utf-8"))
    jobs, secours = [], []
    for classe, syms in uni.items():
        if classe.startswith("Crypto"):
            secours = syms
        else:
            jobs += [(classe, s, None) for s in syms]
    instruments, erreurs, data = [], [], {}
    with cf.ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(chart, s): (c, s, n) for c, s, n in jobs}
        try:
            top = top_crypto()
            source_crypto = "classement CoinGecko"
            cfuts = {ex.submit(yahoo_crypto, t, px): (name, t) for name, t, px in top}
        except Exception as e:
            print("CoinGecko indisponible :", e)
            source_crypto, cfuts = "liste de secours", {}
            futs.update({ex.submit(chart, s): ("Crypto", s, None) for s in secours})
        for f, (classe, sym, nom) in futs.items():
            try:
                data[sym] = (classe, nom, f.result())
            except Exception as e:
                erreurs.append({"symbole": sym, "classe": classe, "erreur": str(e)[:80]})
        for f, (name, t) in cfuts.items():
            r = f.result()
            if r:
                data[r[0]] = ("Crypto", name, r[1])
            else:
                erreurs.append({"symbole": f"{t}-USD", "classe": "Crypto", "erreur": "symbole Yahoo introuvable ou cours incohérent"})
    os.makedirs(os.path.join(outdir, "barres"), exist_ok=True)
    for sym, (classe, nom, d) in data.items():
        st = stats(d)
        if not st:
            erreurs.append({"symbole": sym, "classe": classe, "erreur": "aucune barre"})
            continue
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
    attendu = len(jobs) + (len(cfuts) or len(secours))
    print(f"{len(instruments)} instruments relevés sur {attendu}, {len(erreurs)} erreurs, crypto : {source_crypto}")
    for e in erreurs[:40]:
        print("  erreur", e)
    if len(instruments) < 0.5 * attendu:
        sys.exit("Moins de la moitié des cours obtenus : on ne publie pas, pour garder le dernier relevé valable.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
