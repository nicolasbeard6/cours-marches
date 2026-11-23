# suivi.py (version 2) : dit si un ordre a été exécuté et si son stop ou son take profit a été touché depuis une date.
# Lit les barres d'une heure publiées dans la branche "data" de ce dépôt.
# Usage : python3 suivi.py SYMBOLE SENS NIVEAU STOP TP "AAAA-MM-JJ HH:MM" [COURS_ENTREE] [--suiveur DISTANCE]
#   SENS = ACHAT ou VENTE ; la date est en heure de Paris (création de l'ordre, ou ouverture si COURS_ENTREE est donné).
#   Sans COURS_ENTREE : l'ordre est en attente au NIVEAU. Avec COURS_ENTREE : la position est déjà ouverte (NIVEAU ignoré).
#   TP = 0 : pas de take profit.
#   --suiveur DISTANCE (en unités de cours) : stop suiveur. Après chaque barre, le stop est rapproché à DISTANCE du
#   meilleur cours atteint depuis l'entrée (plus haut pour un achat, plus bas pour une vente) ; il ne recule jamais.
#   Le nouveau stop ne s'applique qu'à partir de la barre suivante (hypothèse prudente).
# Règles prudentes : si le stop et le take profit sont touchés dans la même barre, le stop est retenu ;
# si le cours saute par-dessus le stop, la sortie se fait au cours d'ouverture de la barre.
import json, os, re, subprocess, sys
from datetime import datetime
from zoneinfo import ZoneInfo
PARIS = ZoneInfo("Europe/Paris")
BASE = os.environ.get("COURS_BASE", "https://raw.githubusercontent.com/nicolasbeard6/cours-marches/data")
VERSION = 2


def bars(sym):
    name = re.sub(r"[^A-Za-z0-9.\-]", "_", sym) + ".json"
    if os.path.isdir(BASE):
        r = json.load(open(os.path.join(BASE, "barres", name)))
    else:
        r = json.loads(subprocess.run(["curl", "-sS", "-f", "-m", "30", f"{BASE}/barres/{name}"], capture_output=True, text=True, check=True).stdout)
    return [tuple(b) for b in r["barres"]], r.get("cours"), r.get("heure")


def resolve(data, side, lvl, sl, tp, since, entry=None, trail=None):
    """data : liste (t, open, high, low, close). Renvoie (entree, sortie, stop_actuel) ; entree et sortie valent None ou un dict."""
    long = side == "ACHAT"
    tp = tp if tp and tp > 0 else None
    sl0 = sl
    fill = None if entry is None else {"t": since, "px": entry}
    prev = None

    def motif(base):
        return base if (trail is None or sl == sl0) else "stop suiveur" + (" (écart)" if "écart" in base else "")

    def ratchet(h, l):
        nonlocal sl
        if trail:
            sl = max(sl, h - trail) if long else min(sl, l + trail)

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
                return fill, {"t": t, "px": sl, "motif": "stop"}, sl
            ratchet(h, l)
            continue
        if long:
            if o <= sl: return fill, {"t": t, "px": o, "motif": motif("stop (écart)")}, sl
            if l <= sl: return fill, {"t": t, "px": sl, "motif": motif("stop")}, sl
            if tp is not None and o >= tp: return fill, {"t": t, "px": o, "motif": "take profit"}, sl
            if tp is not None and h >= tp: return fill, {"t": t, "px": tp, "motif": "take profit"}, sl
        else:
            if o >= sl: return fill, {"t": t, "px": o, "motif": motif("stop (écart)")}, sl
            if h >= sl: return fill, {"t": t, "px": sl, "motif": motif("stop")}, sl
            if tp is not None and o <= tp: return fill, {"t": t, "px": o, "motif": "take profit"}, sl
            if tp is not None and l <= tp: return fill, {"t": t, "px": tp, "motif": "take profit"}, sl
        ratchet(h, l)
    return fill, None, sl


if __name__ == "__main__":
    args, trail = list(sys.argv[1:]), None
    if "--suiveur" in args:
        i = args.index("--suiveur")
        trail = float(args[i + 1])
        del args[i:i + 2]
        if trail <= 0:
            trail = None
    sym, side, lvl, sl, tp = args[0], args[1], float(args[2]), float(args[3]), float(args[4])
    since = datetime.strptime(args[5], "%Y-%m-%d %H:%M").replace(tzinfo=PARIS).timestamp()
    entry = float(args[6]) if len(args) > 6 else None
    data, last, last_t = bars(sym)
    fill, out, sl_now = resolve(data, side, lvl, sl, tp, since, entry, trail)
    fmt = lambda t: datetime.fromtimestamp(t, PARIS).strftime("%Y-%m-%d %H:%M")
    for d in (fill, out):
        if d: d["heure"] = fmt(d.pop("t"))
    print(json.dumps({"version": VERSION, "symbole": sym, "dernier_cours": last,
                      "heure_dernier_cours": fmt(last_t) if last_t else None,
                      "entree": None if entry is not None else fill, "sortie": out,
                      "stop_actuel": round(sl_now, 8)}, ensure_ascii=False))
