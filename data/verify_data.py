"""Vérifie toutes les données internalisées dans l'appli et écrit data/VERIFICATION.md.

Contrôles, pour chaque scrutin de proto/data.js :
  1. Votes : le décompte de chaque groupe (pour / contre / abstention) est comparé à la page officielle
     du scrutin sur assemblee-nationale.fr, une source indépendante du fichier open data utilisé
     pour construire les données.
  2. Résultat global : adopté / rejeté et décompte, comparés à la même page.
  3. Liens : le texte complet et le dossier législatif répondent.
  4. Texte source : l'empreinte SHA-256 du texte officiel téléchargé aujourd'hui est comparée à celle
     du texte qui a servi au résumé. Si elle diffère, le texte a changé : le résumé est à revoir.
  5. Résumé : présent, une référence d'article par mesure, relecture indépendante enregistrée.
Et pour l'ensemble :
  6. Affirmations : 8 par axe, 4 dans chaque sens.
  7. Positions des groupes sur les axes : signalées comme provisoires tant qu'elles ne sont pas sourcées.

Usage :
    python data/verify_data.py          # code de sortie 1 si un contrôle échoue
"""

import hashlib
import html
import json
import re
import sys
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_JS = ROOT.parent / "proto" / "data.js"
REPORT = ROOT / "VERIFICATION.md"
UA = {"User-Agent": "Mozilla/5.0 (how_to_vote verification)"}


def fetch(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:  # réseau indisponible, délai dépassé…
        return 0, b""


def parse_scrutin_page(page: str) -> dict:
    """Décompte par groupe et synthèse depuis la page « Analyse du scrutin » de l'Assemblée."""
    groups = {}
    blocks = re.split(r'<ul id="groupe[^"]*"[^>]*>\s*<li class="_no-border" data-organe-id="(PO\d+)"', page)
    for uid, body in zip(blocks[1::2], blocks[2::2]):
        counts = {"pour": 0, "contre": 0, "abst": 0}
        for label, n in re.findall(r'<span class="h6[^"]*">\s*([^<]+?)\s*</span>\s*<span>\s*:\s*(\d+)', body):
            key = {"Pour": "pour", "Contre": "contre", "Abstention": "abst"}.get(label.strip())
            if key:
                counts[key] = int(n)
        groups[uid] = counts
    return groups


def main() -> int:
    js = DATA_JS.read_text(encoding="utf-8")
    data = json.loads(js[js.index("{"):js.rindex("}") + 1])
    selection = {s["numero"]: s for s in json.loads((ROOT / "selection.json").read_text(encoding="utf-8"))["scrutins"]}
    groupes_cfg = json.loads((ROOT / "groupes.json").read_text(encoding="utf-8"))

    # code de groupe -> uid d'organe, pour retrouver les groupes sur la page web
    sys.path.insert(0, str(ROOT))
    from build_data import group_names  # noqa: E402
    uids_of = {}  # un groupe peut avoir eu plusieurs codes (ex. UDR, recréé en 2025)
    for uid, (code, _) in group_names().items():
        uids_of.setdefault(code, []).append(uid)

    rows, failures = [], []
    for s in data["scrutins"]:
        n = s["numero"]
        checks = {}

        # 1-2. Votes et résultat, contre la page officielle
        status, body = fetch(s["url"])
        if status != 200:
            checks["votes"] = f"page du scrutin inaccessible ({status})"
        else:
            page = body.decode("utf-8", "replace")
            web = parse_scrutin_page(page)
            diffs = []
            for code, v in s["votes"].items():
                w = next((web[u] for u in uids_of.get(code, []) if u in web), None)
                if w is None:
                    diffs.append(f"{code} absent de la page")
                elif [w["pour"], w["contre"], w["abst"]] != v["voix"]:
                    diffs.append(f"{code} : données {v['voix']} / page {[w['pour'], w['contre'], w['abst']]}")
            checks["votes"] = "ok" if not diffs else "; ".join(diffs)
            m = re.search(r"\((\d+) pour, (\d+) contre, (\d+) abstentions\)", s["result"])
            text = html.unescape(re.sub(r"<[^>]+>", " ", page))
            adopte = s["result"].startswith("Adopté")
            sort_ok = ("L'Assemblée nationale a adopté" in text) if adopte else ("n'a pas adopté" in text)
            checks["résultat"] = "ok" if m and sort_ok else "sort du vote non retrouvé sur la page"

        # 3. Liens
        bad = [u for u in (s["texte"] and s["texte"]["url"], s["dossierUrl"]) if not u or fetch(u)[0] != 200]
        checks["liens"] = "ok" if not bad else "lien manquant ou en erreur : " + ", ".join(map(str, bad))

        # 4. Texte source inchangé depuis le résumé
        sel = selection.get(n, {})
        expected = sel.get("source_sha256")
        if not expected:
            checks["texte source"] = "empreinte du texte résumé non enregistrée"
        else:
            st, txt = fetch(s["texte"]["url"]) if s["texte"] else (0, b"")
            got = hashlib.sha256(txt).hexdigest() if st == 200 else None
            checks["texte source"] = "ok" if got == expected else "le texte officiel a changé depuis le résumé" if got else "texte inaccessible"

        # 5. Résumé
        ver = s.get("verification") or {}
        if not s.get("resume") or not s.get("mesures"):
            checks["résumé"] = "absent"
        elif len(s.get("articles", [])) != len(s["mesures"]):
            checks["résumé"] = "une référence d'article manque pour certaines mesures"
        elif ver.get("statut") != "ok":
            checks["résumé"] = "relecture indépendante non faite"
        else:
            checks["résumé"] = "ok"

        if any(v != "ok" for v in checks.values()):
            failures.append(n)
        rows.append((s, checks))

    # 6. Affirmations
    aff = data.get("affirmations", [])
    per = Counter((a["ax"], a["dir"]) for a in aff)
    aff_ok = all(per[(ax, d)] == 4 for ax in ("eco", "soc", "env", "eur") for d in (-1, 1))
    if not aff_ok:
        failures.append("affirmations")

    # Rapport
    today = date.today().isoformat()
    out = [f"# Vérification des données\n",
           f"Généré le {today} par `python data/verify_data.py`, sur {len(rows)} scrutins.\n",
           "## Ce qui est contrôlé\n",
           "| Contrôle | Méthode |", "|---|---|",
           "| Votes | Décompte de chaque groupe (pour, contre, abstention) comparé à la page officielle du scrutin sur assemblee-nationale.fr, une source indépendante du fichier open data qui sert à construire les données |",
           "| Résultat | Adopté / rejeté, comparé à la même page |",
           "| Liens | Le texte complet et le dossier législatif répondent |",
           "| Texte source | L'empreinte SHA-256 du texte officiel téléchargé aujourd'hui doit être celle du texte qui a servi au résumé |",
           "| Résumé | Rédigé par IA à partir des articles du texte, une référence d'article par mesure, puis relu par une seconde IA indépendante, mesure par mesure, contre le texte |",
           "",
           f"## Résultat : {'✅ tout est conforme' if not failures else '❌ ' + str(len(failures)) + ' élément(s) à corriger'}\n",
           "| N° | Texte | Votes | Résultat | Liens | Texte source | Résumé | Relecture |", "|---|---|---|---|---|---|---|---|"]
    for s, c in rows:
        ver = s.get("verification") or {}
        cell = lambda k: "✅" if c[k] == "ok" else "❌ " + c[k]
        out.append(f"| [{s['numero']}]({s['url']}) | {s['title'][:60]} | {cell('votes')} | {cell('résultat')} | {cell('liens')} "
                   f"| {cell('texte source')} | {cell('résumé')} | {ver.get('date', '–')} {ver.get('note', '')} |")
    out += ["", "## Affirmations de l'étape 1\n",
            f"{len(aff)} affirmations : " + ", ".join(f"{ax} {per[(ax, -1)]}↔{per[(ax, 1)]}" for ax in ("eco", "soc", "env", "eur"))
            + (" ✅ équilibrées" if aff_ok else " ❌ déséquilibrées"),
            "", "Ce sont des formulations éditoriales, pas des données sourcées : à faire relire par des personnes de sensibilités différentes.",
            "", "## Non vérifiable à ce stade\n",
            "- **Positions des groupes sur les axes** (`data/groupes.json`) : estimations provisoires, utilisées pour comparer les convictions aux programmes. "
            "Elles devront être établies à partir des programmes, de façon sourcée.",
            "- **Choix des scrutins, de l'axe et du sens du « pour »** (`data/selection.json`) : choix éditoriaux, documentés mais pas vérifiables automatiquement.", ""]
    REPORT.write_text("\n".join(out), encoding="utf-8")

    print(f"{len(rows)} scrutins contrôlés, {len(failures)} problème(s) -> {REPORT.relative_to(ROOT.parent)}")
    for s, c in rows:
        bad = {k: v for k, v in c.items() if v != "ok"}
        if bad:
            print(f"  {s['numero']:>5} {bad}")
    if not aff_ok:
        print("  affirmations déséquilibrées :", dict(per))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
