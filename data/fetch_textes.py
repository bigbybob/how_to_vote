"""Télécharge le texte officiel de chaque scrutin sélectionné et le convertit en texte brut.

Lit les liens générés par build_data.py dans proto/data.js, et enregistre dans data/raw/textes/ :
    {numero}.html  le document officiel (dyn/opendata de l'Assemblée)
    {numero}.txt   le même en texte brut, pour rédiger et vérifier les résumés de data/selection.json

Usage :
    python data/build_data.py
    python data/fetch_textes.py
"""

import json
import re
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "raw" / "textes"
DATA_JS = ROOT.parent / "proto" / "data.js"


class TextExtractor(HTMLParser):
    BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table"}

    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        t = "".join(self.parts).replace("\xa0", " ")
        t = re.sub(r"[ \t]+", " ", t)
        return re.sub(r"\n\s*\n+", "\n\n", t).strip()


def main() -> None:
    js = DATA_JS.read_text(encoding="utf-8")
    data = json.loads(js[js.index("{"):js.rindex("}") + 1])
    OUT.mkdir(parents=True, exist_ok=True)
    for s in data["scrutins"]:
        if not s.get("texte"):
            print(f"{s['numero']:>5}  pas de texte lié")
            continue
        html_path = OUT / f"{s['numero']}.html"
        if not html_path.exists():
            req = urllib.request.Request(s["texte"]["url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                html_path.write_bytes(r.read())
        parser = TextExtractor()
        parser.feed(html_path.read_text(encoding="utf-8", errors="replace"))
        txt = parser.text()
        (OUT / f"{s['numero']}.txt").write_text(txt, encoding="utf-8")
        print(f"{s['numero']:>5}  {len(txt):>8} caractères  {s['title'][:60]}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
