"""Construit proto/data.js à partir de la sélection manuelle et des fichiers open data.

Entrées :
    data/selection.json  scrutins retenus, avec axe, sens, résumé, arguments (rédigés à la main)
    data/groupes.json    position des groupes sur les axes (programmes) et couleurs
    data/raw/*.zip       fichiers téléchargés par fetch_scrutins.py

Sortie :
    proto/data.js        window.DATA = {...}, chargé par proto/index.html
                         (un .js plutôt qu'un .json : le proto est ouvert en file://, où fetch() est bloqué)

Usage :
    python data/fetch_scrutins.py   # si data/raw/ est vide
    python data/build_data.py
"""

import json
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from fetch_scrutins import GROUPES_HISTORIQUES, ORDRE_GROUPES, RAW, as_list  # noqa: E402

OUT = ROOT.parent / "proto" / "data.js"


def group_names() -> dict:
    """uid d'organe -> (code, libellé) pour les groupes politiques."""
    names = {uid: (code, code) for uid, code in GROUPES_HISTORIQUES.items()}
    with zipfile.ZipFile(RAW / "AMO10.json.zip") as z:
        for name in z.namelist():
            if "/organe/" in name:
                o = json.loads(z.read(name))["organe"]
                if o["codeType"] == "GP":
                    names[o["uid"]] = (o["libelleAbrege"], o["libelle"])
    return names


def main() -> None:
    selection = json.loads((ROOT / "selection.json").read_text(encoding="utf-8"))["scrutins"]
    config = json.loads((ROOT / "groupes.json").read_text(encoding="utf-8"))["groupes"]
    names = group_names()
    libelles = {code: lib for code, lib in names.values()}

    scrutins, sieges = [], {}
    with zipfile.ZipFile(RAW / "Scrutins.json.zip") as z:
        for sel in selection:
            s = json.loads(z.read(f"json/VTANR5L17V{sel['numero']}.json"))["scrutin"]
            votes = {}
            for g in as_list(s["ventilationVotes"]["organe"]["groupes"]["groupe"]):
                code = names.get(g["organeRef"], (None,))[0]
                if code not in config:  # NI et organes inconnus : ignorés
                    continue
                d = g["vote"]["decompteVoix"]
                p, c, a = int(d["pour"]), int(d["contre"]), int(d["abstentions"])
                n = p + c + a
                sieges[code] = int(g["nombreMembresGroupe"])
                if n == 0:
                    continue  # le groupe n'a pas pris part au vote
                pos = max((("pour", p), ("contre", c), ("abst", a)), key=lambda t: t[1])[0]
                # parts de chaque position parmi les votants du groupe, pour tenir compte des groupes divisés
                votes[code] = {"pos": pos, "p": round(p / n, 3), "c": round(c / n, 3), "a": round(a / n, 3), "n": n}

            dec = s["syntheseVote"]["decompte"]
            adopte = s["sort"]["code"].startswith("adopt")
            scrutins.append({
                "numero": sel["numero"],
                "date": s["dateScrutin"],
                "ax": sel["axe"],
                "dir": sel["dir"],
                "title": sel["titre"],
                "desc": sel["desc"],
                "pour": sel["pour"],
                "contre": sel["contre"],
                "assos": sel["assos"],
                "relu": sel["relu"],
                "officiel": " ".join(s["titre"].split()).rstrip("."),
                "result": f"{'Adopté' if adopte else 'Rejeté'} ({dec['pour']} pour, {dec['contre']} contre, {dec['abstentions']} abstentions)",
                "url": f"https://www.assemblee-nationale.fr/dyn/17/scrutins/{sel['numero']}",
                "votes": votes,
            })

    groups = {
        code: {
            "name": libelles.get(code, code),
            "short": code,
            "color": config[code]["couleur"],
            "ax": config[code]["ax"],
            "seats": sieges.get(code),
        }
        for code in ORDRE_GROUPES if code in config
    }
    data = {"generated": date.today().isoformat(), "source": "data.assemblee-nationale.fr (17e législature)",
            "groups": groups, "scrutins": scrutins}
    OUT.write_text("// Généré par data/build_data.py, ne pas modifier à la main.\nwindow.DATA = "
                   + json.dumps(data, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8")

    print(f"{len(scrutins)} scrutins, {len(groups)} groupes -> {OUT.relative_to(ROOT.parent)}")
    for s in scrutins:
        line = " ".join(f"{k}:{v['pos'][0].upper()}" for k, v in s["votes"].items())
        print(f"  {s['numero']:>5} {s['ax']} {s['dir']:+d}  {s['title'][:45]:45}  {line}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
