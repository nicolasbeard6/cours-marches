# suivi.py : dit si un ordre a été exécuté et si son stop ou son take profit a été touché depuis une date.
# Lit les barres d'une heure publiées dans la branche "data" de ce dépôt.
# Usage : python3 suivi.py SYMBOLE SENS NIVEAU STOP TP "AAAA-MM-JJ HH:MM" [COURS_ENTREE]
#   SENS = ACHAT ou VENTE ; la date est en heure de Paris (création de l'ordre, ou ouverture si COURS_ENTREE est donné).
#   Sans COURS_ENTREE : l'ordre est en attente au NIVEAU. Avec COURS_ENTREE : la position est déjà ouverte (NIVEAU ignoré).
# Règles prudentes : si le stop et le take profit sont touchés dans la même barre, le stop est retenu ;
# si le cours saute par-dessus le stop, la sortie se fait au cours d'ouverture de la barre.
import json, os, re, subprocess, sys
from datetime import datetime
from zoneinfo import ZoneInfo
PARIS = ZoneInfo("Europe/Paris")
BASE = os.environ.get("COURS_BASE", "https://raw.githubusercontent.com/nicolasbeard6/cours-marches/data")

def bars(sym):
    name = re.sub(r"[^A-Za-z0-9.\-]", "_", sym) + ".json"
    if os.path.isdir(BASE):
        r = json.load(open(os.path.join(BASE, "barres", name)))
    else:
        r = json.loads(subprocess.run(["curl", "-sS", "-f", "-m", "30", f"{BASE}/barres/{name}"], capture_output=True, text=True, check=True).stdout)
    return [tuple(b) for b in r["barres"]], r.get("cours"), r.get("heure")

def resolve(data, side, lvl, sl, tp, since, entry=None):
    """data : liste (t, open, high, low, close). Renvoie (entree, sortie) ; chacun vaut None ou un dict."""
    long = side == "ACHAT"
    fill = None if entry is None else {"t": since, "px": entry}
    prev = None
    for t, o, h, l, c in data:
        if t + 3600 <= since:                               # barre entièrement terminée avant la date : ignorée
            prev = c
            continue
        if fill is None:
            if prev is not None and (prev - lvl) * (o - lvl) < 0:
                fill = {"t": t, "px": o}                    # le cours a sauté par-dessus le niveau : exécution à l'ouverture
            elif l <= lvl <= h:
                fill = {"t": t, "px": lvl}
            prev = c
            if fill is None:
                continue
            if (long and l <= sl) or (not long and h >= sl):   # stop touché dans la barre d'entrée : hypothèse prudente
                return fill, {"t": t, "px": sl, "motif": "stop"}
            continue
        if long:
            if o <= sl: return fill, {"t": t, "px": o, "motif": "stop (écart)"}
            if l <= sl: return fill, {"t": t, "px": sl, "motif": "stop"}
            if o >= tp: return fill, {"t": t, "px": o, "motif": "take profit"}
            if h >= tp: return fill, {"t": t, "px": tp, "motif": "take profit"}
        else:
            if o >= sl: return fill, {"t": t, "px": o, "motif": "stop (écart)"}
            if h >= sl: return fill, {"t": t, "px": sl, "motif": "stop"}
            if o <= tp: return fill, {"t": t, "px": o, "motif": "take profit"}
            if l <= tp: return fill, {"t": t, "px": tp, "motif": "take profit"}
    return fill, None

if __name__ == "__main__":
    sym, side, lvl, sl, tp = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
    since = datetime.strptime(sys.argv[6], "%Y-%m-%d %H:%M").replace(tzinfo=PARIS).timestamp()
    entry = float(sys.argv[7]) if len(sys.argv) > 7 else None
    data, last, last_t = bars(sym)
    fill, out = resolve(data, side, lvl, sl, tp, since, entry)
    fmt = lambda t: datetime.fromtimestamp(t, PARIS).strftime("%Y-%m-%d %H:%M")
    for d in (fill, out):
        if d: d["heure"] = fmt(d.pop("t"))
    print(json.dumps({"symbole": sym, "dernier_cours": last, "heure_dernier_cours": fmt(last_t) if last_t else None,
                      "entree": None if entry is not None else fill, "sortie": out}, ensure_ascii=False))
