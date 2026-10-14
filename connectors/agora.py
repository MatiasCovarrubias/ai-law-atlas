"""Compare our manifest with the latest AGORA release (ETO, Zenodo; CC BY-NC 4.0).

    python connectors/agora.py                # downloads latest AGORA into cache/agora/
    python connectors/agora.py --no-download

Writes registry/crosswalk/agora_profile.txt and registry/crosswalk/agora_overlap_candidates.csv (best AGORA match per manifest entry,
with a score: 1.0 = same URL, otherwise title-token Jaccard). Matches below ~0.6 need a manual check;
in the Oct 2026 comparison about 55 of 410 entries had a true AGORA counterpart.
"""

from __future__ import annotations

import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
AGORA_DIR = ROOT / "cache" / "agora"
CROSSWALK = ROOT / "registry" / "crosswalk"
MANIFEST = ROOT / "registry" / "manifest_v0.jsonl"
CONCEPT_RECORD = "20229467"  # any AGORA record id; the API resolves the latest version from it
UA = {"User-Agent": "Mozilla/5.0 (research script)"}
STOP = {"the", "and", "for", "act", "law", "with", "from", "regulation", "relating", "concerning", "use"}


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.load(r)


def download_latest() -> str:
    versions = fetch_json(f"https://zenodo.org/api/records/{CONCEPT_RECORD}/versions?size=1&sort=version")
    rec = versions["hits"]["hits"][0]
    url = rec["files"][0]["links"]["self"]
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600) as r:
        zipfile.ZipFile(io.BytesIO(r.read())).extractall(AGORA_DIR)
    version = f"{rec['metadata'].get('version')} ({rec['metadata']['publication_date']})"
    (AGORA_DIR / "VERSION").write_text(version)
    return version


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 2 and w not in STOP}


def norm_url(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", str(url).lower().split("#")[0].rstrip("/"))


def main() -> None:
    AGORA_DIR.mkdir(exist_ok=True)
    version = (AGORA_DIR / "VERSION").read_text() if "--no-download" in sys.argv else download_latest()
    docs = pd.read_csv(AGORA_DIR / "agora" / "documents.csv", low_memory=False)
    auth = pd.read_csv(AGORA_DIR / "agora" / "authorities.csv")
    docs["jurisdiction"] = docs["Authority"].map(dict(zip(auth["Name"], auth["Jurisdiction"]))).fillna("?")
    dates = pd.to_datetime(docs["Most recent activity date"], errors="coerce")

    profile = [
        f"AGORA {version}: {len(docs)} documents",
        f"jurisdiction: {docs['jurisdiction'].value_counts().to_dict()}",
        f"status: {docs['Most recent activity'].value_counts().to_dict()}",
        f"activity by year: {dates.dt.year.value_counts().sort_index().to_dict()}",
        f"non-US authorities: {docs.loc[docs['jurisdiction'] != 'United States', 'Authority'].value_counts().to_dict()}",
    ]
    (CROSSWALK / "agora_profile.txt").write_text("\n".join(profile) + "\n")
    print("\n".join(profile))

    agora = [(tokens(f"{r['Official name']} {r['Casual name']}"), norm_url(r["Link to document"]), r["AGORA ID"],
              r["Casual name"], r["Most recent activity"]) for _, r in docs.iterrows()]
    rows = []
    for line in MANIFEST.open():
        o = json.loads(line)
        ot, urls = tokens(f"{o['title']} {o.get('short_title', '')}"), {norm_url(o.get("official_url")), norm_url(o.get("fulltext_url"))}
        best = max(((1.0 if au in urls else len(ot & at) / max(len(ot | at), 1)), aid, name, status)
                   for at, au, aid, name, status in agora)
        rows.append({"id": o["id"], "title": o["title"], "score": round(best[0], 2), "agora_id": best[1],
                     "agora_name": best[2], "agora_status": best[3]})
    out = pd.DataFrame(rows).sort_values("score", ascending=False)
    out.to_csv(CROSSWALK / "agora_overlap_candidates.csv", index=False)
    print(f"matches with score >= 0.6: {(out.score >= 0.6).sum()} (manual check needed below that)")


if __name__ == "__main__":
    main()
