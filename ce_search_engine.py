"""
CS 4250 project - Civil Engineering vertical search engine.
Usage:
    python ce_search_engine.py crawl     # crawl + store pages, build documents + inverted_index
    python ce_search_engine.py search    # interactive search (5 results per page)
Requires: pip install requests beautifulsoup4 pymongo
"""
import math
import re
import sys
from collections import defaultdict, deque
from urllib.parse import urljoin, urldefrag, urlparse

import requests
from bs4 import BeautifulSoup
from pymongo import MongoClient, ASCENDING

SEED = "https://www.cpp.edu/engineering/ce/index.shtml"
NUM_TARGETS = 13            # minimum required, page lists 25
ALLOWED_HOST = "www.cpp.edu"
HEADERS = {"User-Agent": "CS4250-student-crawler/1.0"}
SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".pdf", ".doc", ".docx",
            ".ppt", ".pptx", ".xls", ".xlsx", ".zip", ".mp4", ".css", ".js")

# header h2s to skip
HEADER_H2 = {"email", "phone number", "office location", "office hours"}

STOPWORDS = set("""a an and are as at be by for from has have in is it its of on or
that the their this to was were will with we our you your i not but they he she""".split())

db = MongoClient("mongodb://localhost:27017")["cpp_ce_search"]
pages, documents, inverted = db["pages"], db["documents"], db["inverted_index"]


# crawling
FACULTY_RE = re.compile(r"^/faculty/([^/]+)/?(index\.shtml)?$")


# makes urls absolute with one canonical form
def normalize(url):
    url, _ = urldefrag(url.strip())
    p = urlparse(url)
    m = FACULTY_RE.match(p.path)
    path = f"/faculty/{m.group(1)}/" if m else p.path
    return p._replace(path=path, query="", netloc=p.netloc.lower()).geturl()


# gets page html or none if broken
def fetch_html(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200 or "text/html" not in r.headers.get("Content-Type", ""):
            return None
        return r.text
    except requests.RequestException:
        return None          # broken link, skip


# checks for the faculty header pattern
def target_page(html):
    soup = BeautifulSoup(html, "html.parser")
    if not soup.find("h1"):
        return False
    h2s = {h.get_text(strip=True).lower() for h in soup.find_all("h2")}
    return "office location" in h2s and "civil engineering department" in soup.get_text().lower()


# gets faculty name from the h1
def extract_faculty_name(html):
    return BeautifulSoup(html, "html.parser").find("h1").get_text(strip=True)


# finds valid cpp.edu links on a page
def extract_links(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        link = normalize(urljoin(base_url, href))      # handles relative and absolute urls
        p = urlparse(link)
        if p.scheme in ("http", "https") and p.netloc == ALLOWED_HOST \
                and not p.path.lower().endswith(SKIP_EXT):
            out.append(link)
    return out


# crawls from the seed until all targets are found
def crawler(seed, num_targets):
    targets_found = 0
    frontier, seen = deque([seed]), {seed}
    pages.delete_many({})
    while frontier:
        url = frontier.popleft()
        html = fetch_html(url)
        if html is None:
            continue
        if target_page(html):
            faculty_name = extract_faculty_name(html)
            targets_found += 1
        else:
            faculty_name = None
        pages.insert_one({"url": url, "facultyName": faculty_name, "html": html})  # store page
        if targets_found == num_targets:
            frontier.clear()
        else:
            for link in extract_links(html, url):
                if link not in seen:
                    seen.add(link)
                    frontier.append(link)
    print(f"Crawl done. Targets found: {targets_found}")


# parsing + indexing
# gets text of the sections below the header
def extract_content(html):
    soup = BeautifulSoup(html, "html.parser")
    parts = []
    for h2 in soup.find_all("h2"):
        title = h2.get_text(strip=True)
        if title.lower() in HEADER_H2:
            continue
        parts.append(title)
        for sib in h2.find_next_siblings():
            if sib.name == "h2":
                break
            parts.append(sib.get_text(" ", strip=True))
    return " ".join(p for p in parts if p)


# lowercases text and splits it into terms
def tokenize(text):
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]


# builds the documents and inverted_index collections
def build_index():
    documents.delete_many({})
    inverted.delete_many({})
    index = defaultdict(list)
    for doc_id, page in enumerate(pages.find({"facultyName": {"$ne": None}}), start=1):
        content = extract_content(page["html"])
        documents.insert_one({"_id": doc_id, "url": page["url"],
                              "facultyName": page["facultyName"], "content": content})
        positions = defaultdict(list)
        for pos, tok in enumerate(tokenize(content)):
            positions[tok].append(pos)
        for term, pos_list in positions.items():
            index[term].append({"docId": doc_id, "tf": len(pos_list), "positions": pos_list})
    inverted.insert_many([{"term": t, "postings": p} for t, p in index.items()])
    inverted.create_index([("term", ASCENDING)])
    print(f"Indexed {documents.count_documents({})} documents, {len(index)} terms")


# searching
# makes a short text snippet around the first match
def make_snippet(content, terms, width=220):
    for t in terms:
        m = re.search(re.escape(t), content, re.IGNORECASE)
        if m:
            start = max(0, m.start() - width // 2)
            end = min(len(content), m.start() + width // 2)
            return ("..." if start else "") + content[start:end] + ("..." if end < len(content) else "")
    return content[:width] + "..."


# ranks documents for a query using tf-idf
def search(query, page=1, per_page=5):
    terms = tokenize(query)
    n_docs = documents.count_documents({})
    scores = defaultdict(float)
    for term in set(terms):
        entry = inverted.find_one({"term": term})
        if not entry:
            continue
        idf = math.log(1 + n_docs / len(entry["postings"]))
        for p in entry["postings"]:
            scores[p["docId"]] += (1 + math.log(p["tf"])) * idf      # tf-idf score
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    total = len(ranked)
    chunk = ranked[(page - 1) * per_page: page * per_page]
    results = []
    for doc_id, score in chunk:
        d = documents.find_one({"_id": doc_id})
        results.append({"url": d["url"], "facultyName": d["facultyName"],
                        "snippet": make_snippet(d["content"], terms), "score": round(score, 3)})
    return results, total


# runs the interactive search loop with paging
def search_cli():
    while True:
        q = input("\nQuery (blank to quit): ").strip()
        if not q:
            break
        page = 1
        while True:
            results, total = search(q, page)
            if not results:
                print("No results.")
                break
            print(f"\n--- {total} results, page {page} ---")
            for r in results:
                print(f"\n{r['facultyName']}  [{r['score']}]\n{r['url']}\n{r['snippet']}")
            cmd = input("\n[n]ext / [p]rev / [q]uit: ").strip().lower()
            if cmd == "n" and page * 5 < total:
                page += 1
            elif cmd == "p" and page > 1:
                page -= 1
            else:
                break


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "crawl":
        crawler(SEED, NUM_TARGETS)
        build_index()
    elif len(sys.argv) > 1 and sys.argv[1] == "search":
        search_cli()
    else:
        print(__doc__)
