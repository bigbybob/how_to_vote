"""Construit proto/data.js à partir de la sélection manuelle et des fichiers open data.

Entrées :
    data/selection.json  scrutins retenus, avec leur axe et le sens du « pour » (choix éditorial)
    data/groupes.json    position des groupes sur les axes (programmes) et couleurs
    data/raw/*.zip       fichiers téléchargés par fetch_scrutins.py

Sortie :
    proto/data.js        window.DATA = {...}, chargé par proto/index.html
                         (un .js plutôt qu'un .json : le proto est ouvert en file://, où fetch() est bloqué)

Le titre, l'objet du vote et les liens viennent des sources officielles. L'open data ne contient pas de
résumé : ceux de selection.json sont rédigés à partir du texte officiel (voir fetch_textes.py), sans arguments.

Usage :
    python data/fetch_scrutins.py   # si data/raw/ est vide ou incomplet
    python data/build_data.py
"""

import json
import re
import sys
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from fetch_scrutins import GROUPES_HISTORIQUES, ORDRE_GROUPES, RAW, as_list  # noqa: E402

OUT = ROOT.parent / "proto" / "data.js"
AN = "https://www.assemblee-nationale.fr"
# Types de documents qui sont des textes soumis au vote (et non des rapports, avis, etc.)
TEXTES = ("PION", "PRJL", "PNRE", "RION")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower().replace("’", "'"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


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


def load_dossiers():
    """Retourne (dossiers, vote -> dossier, documents)."""
    dossiers, by_vote, documents = {}, {}, {}
    with zipfile.ZipFile(RAW / "Dossiers.json.zip") as z:
        for name in z.namelist():
            raw = z.read(name).decode("utf-8")
            if "/dossierParlementaire/" in name:
                d = json.loads(raw)["dossierParlementaire"]
                dossiers[d["uid"]] = {
                    "titre": d["titreDossier"]["titre"],
                    "chemin": d["titreDossier"]["titreChemin"],
                    "docs": sorted(set(re.findall(r'"texte(?:Associe|Adopte)": "(\w+)"', raw))),
                }
                for vote in re.findall(r'"(VTANR5L17V\d+)"', raw):
                    by_vote[vote] = d["uid"]
            elif "/document/" in name:
                doc = json.loads(raw)["document"]
                chrono = (doc.get("cycleDeVie") or {}).get("chrono") or {}  # métadonnées parfois incomplètes
                depot = chrono.get("dateDepot") or chrono.get("dateCreation")
                if not depot:
                    continue
                documents[doc["uid"]] = {
                    "date": depot[:10],
                    "nom": doc.get("denominationStructurelle") or "Texte",
                    "num": (doc.get("notice") or {}).get("numNotice"),
                }
    return dossiers, by_vote, documents


def find_dossier(uid: str, libelle: str, dossiers: dict, by_vote: dict):
    """Dossier lié au scrutin : par référence directe, sinon par correspondance du titre."""
    if uid in by_vote:
        return by_vote[uid]
    lib = set(re.findall(r"\w{4,}", norm(libelle)))
    best, score = None, 0.0
    for d_uid, d in dossiers.items():
        words = set(re.findall(r"\w{4,}", norm(d["titre"])))
        if len(words) >= 3:
            s = len(words & lib) / len(words)
            if s > score:
                best, score = d_uid, s
    return best if score >= 0.7 else None


def voted_text(dossier: dict, documents: dict, vote_date: str):
    """Dernier texte de l'Assemblée déposé dans le dossier avant le vote : c'est le texte mis aux voix."""
    candidates = [u for u in dossier["docs"]
                  if u.startswith(TEXTES) and "ANR5" in u and u in documents and documents[u]["date"] <= vote_date]
    if not candidates:
        return None
    uid = max(candidates, key=lambda u: (documents[u]["date"], u))
    doc = documents[uid]
    # BTC = texte adopté en commission, BTA = texte adopté en séance, sinon texte déposé
    version = " (texte de la commission)" if "BTC" in uid else " (texte adopté)" if "BTA" in uid else ""
    label = doc["nom"] + (f" n° {doc['num']}" if doc["num"] else "") + version
    return {"label": label, "url": f"{AN}/dyn/opendata/{uid}.html"}


def main() -> None:
    selection = json.loads((ROOT / "selection.json").read_text(encoding="utf-8"))["scrutins"]
    config = json.loads((ROOT / "groupes.json").read_text(encoding="utf-8"))["groupes"]
    names = group_names()
    libelles = {code: lib for code, lib in names.values()}
    dossiers, by_vote, documents = load_dossiers()

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

            libelle = " ".join(s["objet"]["libelle"].split()).rstrip(".")
            d_uid = find_dossier(s["uid"], libelle, dossiers, by_vote)
            dossier = dossiers.get(d_uid)
            dec = s["syntheseVote"]["decompte"]
            adopte = s["sort"]["code"].startswith("adopt")
            scrutins.append({
                "numero": sel["numero"],
                "date": s["dateScrutin"],
                "ax": sel["axe"],
                "dir": sel["dir"],
                "title": dossier["titre"] if dossier else libelle[0].upper() + libelle[1:],
                "objet": "Vote sur " + libelle,
                # résumé factuel rédigé à partir du texte officiel (data/raw/textes/), relu à la main
                "resume": sel.get("resume"),
                "mesures": sel.get("mesures", []),
                "version": sel.get("version"),
                "result": f"{'Adopté' if adopte else 'Rejeté'} ({dec['pour']} pour, {dec['contre']} contre, {dec['abstentions']} abstentions)",
                "url": f"{AN}/dyn/17/scrutins/{sel['numero']}",
                "dossierUrl": f"{AN}/dyn/17/dossiers/{dossier['chemin']}" if dossier else None,
                "texte": voted_text(dossier, documents, s["dateScrutin"]) if dossier else None,
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
        texte = s["texte"]["label"] if s["texte"] else "PAS DE TEXTE"
        print(f"  {s['numero']:>5} {s['ax']} {s['dir']:+d}  {s['title'][:55]:55}  {texte}"
              + ("" if s["dossierUrl"] else "  PAS DE DOSSIER"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
