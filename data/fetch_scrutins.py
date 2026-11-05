"""Extraction des scrutins de l'Assemblée nationale (17e législature) et pré-sélection de candidats.

Télécharge les fichiers open data officiels (data.assemblee-nationale.fr) dans data/raw/,
puis produit data/candidats.csv : les scrutins susceptibles de départager les groupes,
avec la position et la cohésion de chaque groupe, pour une sélection manuelle ensuite.

Usage :
    python data/fetch_scrutins.py            # utilise les fichiers déjà téléchargés
    python data/fetch_scrutins.py --refresh  # retélécharge les fichiers
    python data/fetch_scrutins.py --all      # exporte tous les scrutins, sans filtre
"""

import argparse
import csv
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
BASE = "https://data.assemblee-nationale.fr/static/openData/repository/17"
SOURCES = {
    "Scrutins.json.zip": f"{BASE}/loi/scrutins/Scrutins.json.zip",
    "AMO10.json.zip": f"{BASE}/amo/deputes_actifs_mandats_actifs_organes/AMO10_deputes_actifs_mandats_actifs_organes.json.zip",
}

# Groupes absents du fichier des organes "actifs" (groupes renommés ou recréés en cours de législature).
# PO847173 : 16 membres, présent d'octobre 2024 à juillet 2025, puis remplacé par PO872880 (UDR) à la rentrée 2025.
GROUPES_HISTORIQUES = {"PO847173": "UDR"}

# Ordre d'affichage des colonnes, approximativement de gauche à droite de l'hémicycle
ORDRE_GROUPES = ["LFI-NFP", "GDR", "EcoS", "SOC", "LIOT", "Dem", "EPR", "HOR", "DR", "UDR", "RN", "NI"]

# Critères de pré-sélection
MIN_VOTANTS = 150        # en dessous, l'hémicycle était trop vide pour que le vote soit représentatif
MIN_VOTANTS_AUTRE = 350  # pour un amendement ou un article : seuls les votes très suivis sont gardés
MIN_CLIVAGE = 0.15       # part minimale des sièges dans le camp minoritaire (0 = unanimité, 0,5 = 50/50)


def download(refresh: bool) -> None:
    RAW.mkdir(exist_ok=True)
    for name, url in SOURCES.items():
        target = RAW / name
        if target.exists() and not refresh:
            continue
        print(f"Téléchargement de {url}")
        urllib.request.urlretrieve(url, target)


def load_groups() -> dict:
    groups = dict(GROUPES_HISTORIQUES)
    with zipfile.ZipFile(RAW / "AMO10.json.zip") as z:
        for name in z.namelist():
            if "/organe/" not in name:
                continue
            o = json.loads(z.read(name))["organe"]
            if o["codeType"] == "GP":
                groups[o["uid"]] = o["libelleAbrege"]
    return groups


def as_list(x):
    return x if isinstance(x, list) else [x]


def analyse(s: dict, groups: dict) -> dict:
    """Résume un scrutin : position, cohésion et participation de chaque groupe, et clivage global."""
    par_groupe = {}
    for g in as_list(s["ventilationVotes"]["organe"]["groupes"]["groupe"]):
        code = groups.get(g["organeRef"])
        if code is None:  # organe inconnu (ex. PO0, donnée parasite) : ignoré
            continue
        d = g["vote"]["decompteVoix"]
        pour, contre, abst = int(d["pour"]), int(d["contre"]), int(d["abstentions"])
        exprimes = pour + contre + abst
        membres = int(g["nombreMembresGroupe"])
        if s["typeVote"]["codeTypeVote"] == "MOC":
            # Motion de censure : seuls les votes "pour" sont recueillis ; ne pas voter revient à voter contre
            share = pour / membres if membres else 0
            pos, cohesion = ("P", share) if share >= 0.5 else ("C", 1 - share)
            exprimes = membres
        elif exprimes == 0:
            pos, cohesion = "-", None
        else:
            pos = max((("P", pour), ("C", contre), ("A", abst)), key=lambda t: t[1])[0]
            cohesion = max(pour, contre, abst) / exprimes
        par_groupe[code] = {
            "pos": pos,
            "cohesion": cohesion,
            "participation": exprimes / membres if membres else 0,
            "membres": membres,
        }

    # Clivage : sièges des groupes majoritairement "pour" face à ceux majoritairement "contre"
    sieges_pour = sum(g["membres"] for g in par_groupe.values() if g["pos"] == "P")
    sieges_contre = sum(g["membres"] for g in par_groupe.values() if g["pos"] == "C")
    total = sieges_pour + sieges_contre
    clivage = min(sieges_pour, sieges_contre) / total if total else 0

    synth = s["syntheseVote"]
    titre = " ".join(s["titre"].split())  # certains titres contiennent des retours chariot
    return {
        "uid": s["uid"],
        "numero": int(s["numero"]),
        "date": s["dateScrutin"],
        "type": s["typeVote"]["codeTypeVote"],  # SPO ordinaire, SPS solennel, MOC motion de censure
        "ensemble": bool(re.search(r"l'ensemble", titre)),
        "sort": s["sort"]["code"],
        "votants": int(synth["nombreVotants"]),
        "pour": int(synth["decompte"]["pour"]),
        "contre": int(synth["decompte"]["contre"]),
        "abst": int(synth["decompte"]["abstentions"]),
        "clivage": clivage,
        "groupes": par_groupe,
        "titre": titre.rstrip("."),
        # plusieurs demandeurs sont séparés par des retours chariot
        "demandeur": " / ".join(((s.get("demandeur") or {}).get("texte") or "").splitlines()).strip(),
    }


def est_candidat(r: dict) -> bool:
    if r["clivage"] < MIN_CLIVAGE:
        return False
    if r["type"] in ("SPS", "MOC"):
        return True
    if r["ensemble"]:
        return r["votants"] >= MIN_VOTANTS
    return r["votants"] >= MIN_VOTANTS_AUTRE


def categorie(r: dict) -> str:
    if r["type"] == "MOC":
        return "motion de censure"
    if r["type"] == "SPS":
        return "solennel"
    # Motions de rejet préalable / de renvoi : vote de procédure qui écarte tout le texte, souvent tactique
    if re.search(r"motion de (rejet|renvoi)", r["titre"]):
        return "motion de procédure"
    return "ensemble du texte" if r["ensemble"] else "amendement / article"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="retélécharger les fichiers open data")
    parser.add_argument("--all", action="store_true", help="exporter tous les scrutins, sans filtre")
    args = parser.parse_args()

    download(args.refresh)
    groups = load_groups()

    rows = []
    with zipfile.ZipFile(RAW / "Scrutins.json.zip") as z:
        for name in z.namelist():
            rows.append(analyse(json.loads(z.read(name))["scrutin"], groups))
    total = len(rows)
    if not args.all:
        rows = [r for r in rows if est_candidat(r)]
    rows.sort(key=lambda r: (r["date"], r["numero"]), reverse=True)

    codes = [c for c in ORDRE_GROUPES if c in set(groups.values())]
    out = ROOT / ("scrutins_tous.csv" if args.all else "candidats.csv")
    # Point-virgule et BOM UTF-8 : s'ouvre directement dans Excel en français
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["numero", "date", "categorie", "sort", "votants", "pour", "contre", "abst", "clivage"]
                   + codes + ["titre", "demandeur", "lien"])
        for r in rows:
            cells = []
            for c in codes:
                g = r["groupes"].get(c)
                if not g or g["pos"] == "-":
                    cells.append("")
                else:
                    # ex. "P 95%" ; un astérisque signale un groupe divisé (cohésion < 75 %)
                    cells.append(f"{g['pos']} {round(100 * g['cohesion'])}%" + ("*" if g["cohesion"] < 0.75 else ""))
            w.writerow([r["numero"], r["date"], categorie(r), r["sort"], r["votants"], r["pour"], r["contre"],
                        r["abst"], f"{r['clivage']:.2f}"] + cells
                       + [r["titre"], r["demandeur"], f"https://www.assemblee-nationale.fr/dyn/17/scrutins/{r['numero']}"])

    print(f"{len(rows)} scrutins exportés sur {total} -> {out.relative_to(ROOT.parent)}")
    if not args.all:
        par_cat = {}
        for r in rows:
            par_cat[categorie(r)] = par_cat.get(categorie(r), 0) + 1
        for k, v in sorted(par_cat.items(), key=lambda t: -t[1]):
            print(f"  {v:4d}  {k}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
