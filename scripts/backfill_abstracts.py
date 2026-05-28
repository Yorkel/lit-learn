"""
backfill_abstracts.py — fetch missing abstracts for existing lr_sources rows
from CrossRef + arXiv. One-off run after adding the abstract column.

Usage:
    python scripts/backfill_abstracts.py                # all projects
    python scripts/backfill_abstracts.py llm-judge      # one project

Set CROSSREF_EMAIL env var for polite CrossRef API usage (recommended).
Skips rows that already have an abstract.
"""
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote
import urllib.request

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lit-review-tool-staging"))

import db  # noqa


USER_AGENT = f"lit-learn/0.1 (mailto:{os.environ.get('CROSSREF_EMAIL', 'noreply@example.com')})"
HEADERS = {"User-Agent": USER_AGENT}


def _strip_jats(text: str) -> str:
    """Strip JATS XML tags that CrossRef wraps abstracts in."""
    if not text:
        return ""
    text = re.sub(r"</?jats:[^>]+>", "", text)
    text = re.sub(r"<[^>]+>", "", text)  # any other tags
    return re.sub(r"\s+", " ", text).strip()


def _crossref_by_doi(doi: str) -> str | None:
    if not doi:
        return None
    doi = doi.strip().replace("https://doi.org/", "").replace("http://doi.org/", "")
    try:
        req = urllib.request.Request(f"https://api.crossref.org/works/{quote(doi, safe='/')}",
                                     headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            import json
            data = json.loads(r.read())
            return _strip_jats(data.get("message", {}).get("abstract", "") or "")
    except Exception:
        return None


def _crossref_by_title(title: str, authors: str = "") -> str | None:
    if not title:
        return None
    try:
        params = f"query.title={quote(title)}&rows=1"
        if authors:
            params += f"&query.author={quote(authors.split(',')[0])}"
        req = urllib.request.Request(f"https://api.crossref.org/works?{params}",
                                     headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            import json
            data = json.loads(r.read())
            items = data.get("message", {}).get("items", [])
            if not items:
                return None
            # Sanity check: title must roughly match
            got_title = " ".join(items[0].get("title", []) or []).lower()
            if title.lower()[:40] not in got_title and got_title[:40] not in title.lower():
                return None
            return _strip_jats(items[0].get("abstract", "") or "")
    except Exception:
        return None


def _arxiv_by_id(arxiv_id: str) -> str | None:
    if not arxiv_id:
        return None
    try:
        req = urllib.request.Request(
            f"http://export.arxiv.org/api/query?id_list={quote(arxiv_id)}",
            headers=HEADERS,
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            xml = r.read().decode("utf-8", errors="replace")
        m = re.search(r"<summary>(.*?)</summary>", xml, re.DOTALL)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    except Exception:
        pass
    return None


def _arxiv_by_title(title: str) -> str | None:
    """Search arxiv by title; return abstract if the top hit's title matches."""
    if not title:
        return None
    try:
        query = f'ti:"{title}"'
        req = urllib.request.Request(
            f"http://export.arxiv.org/api/query?search_query={quote(query)}&max_results=1",
            headers=HEADERS,
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            xml = r.read().decode("utf-8", errors="replace")
        title_m = re.search(r"<entry>.*?<title>(.*?)</title>", xml, re.DOTALL)
        sum_m = re.search(r"<entry>.*?<summary>(.*?)</summary>", xml, re.DOTALL)
        if not (title_m and sum_m):
            return None
        got_title = re.sub(r"\s+", " ", title_m.group(1)).strip().lower()
        # Sanity: titles should overlap substantially
        if title.lower()[:40] not in got_title and got_title[:40] not in title.lower():
            return None
        return re.sub(r"\s+", " ", sum_m.group(1)).strip()
    except Exception:
        return None


def _extract_arxiv_id(url_or_venue: str) -> str | None:
    if not url_or_venue:
        return None
    m = re.search(r"arxiv(?:\.org)?[/:](?:abs/)?([\d\.]+(?:v\d+)?)", url_or_venue, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"arxiv preprint arxiv:\s*([\d\.]+)", url_or_venue, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def backfill(project_id: str | None = None) -> None:
    reg = db.load_registry()
    if project_id:
        projects = [p for p in reg["projects"] if p["id"] == project_id]
        if not projects:
            print(f"project {project_id!r} not found; available: {[p['id'] for p in reg['projects']]}")
            return
    else:
        projects = reg["projects"]

    for project in projects:
        print(f"\n=== {project['id']} ===")
        df = db.load_sources(project)
        if df.empty:
            print("  (no sources)")
            continue

        # Only operate on rows where abstract is empty
        empty_mask = df["abstract"].fillna("").str.strip().eq("")
        todo = df[empty_mask]
        print(f"  {len(df)} total, {len(todo)} missing abstract")

        changed = False
        for i, row in todo.iterrows():
            key = row["key"]
            title = row.get("title", "")
            doi = row.get("doi", "")
            url = row.get("url", "")
            venue = row.get("venue", "")
            authors = row.get("authors", "")

            abstract = _crossref_by_doi(doi)
            source = "crossref/doi" if abstract else None

            if not abstract:
                arxiv_id = _extract_arxiv_id(doi) or _extract_arxiv_id(url) or _extract_arxiv_id(venue)
                if arxiv_id:
                    abstract = _arxiv_by_id(arxiv_id)
                    if abstract:
                        source = "arxiv"

            if not abstract:
                abstract = _crossref_by_title(title, authors)
                if abstract:
                    source = "crossref/title"

            if not abstract:
                abstract = _arxiv_by_title(title)
                if abstract:
                    source = "arxiv/title"

            if abstract:
                df.loc[i, "abstract"] = abstract
                changed = True
                print(f"  ✓ {key}  ({source}, {len(abstract)} chars)")
            else:
                print(f"  ✗ {key}  (no abstract found)")

            time.sleep(0.5)  # be polite to CrossRef / arxiv

        if changed:
            db.save_sources(project, df)
            print(f"  saved")


if __name__ == "__main__":
    backfill(sys.argv[1] if len(sys.argv) > 1 else None)
