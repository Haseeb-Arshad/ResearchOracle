"""
papers-proxy: ultra-light gateway for arXiv & PubMed
––––––––––––––––––––––––––––––––––––––––––––––––––––
* /arxiv/search  ?query=...&max_results=5
* /pubmed/search ?query=...&max_results=5
* /paper/{source}/{paper_id}   source ∈ {arxiv,pubmed}
"""

import os, re, feedparser, httpx
from fastapi import FastAPI, Query, Path, HTTPException

ARXIV_API   = "http://export.arxiv.org/api/query"
PUBMED_SEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
NCBI_API_KEY  = os.getenv("NCBI_KEY", "")          # optional

UA_HDR = {"User-Agent": "ResearchOracle/0.1 (+https://yourdomain.com)"}

app = FastAPI(title="papers-proxy", version="0.1")

# ---------- helpers ----------
def clean(txt: str) -> str:
    return re.sub(r"\s+", " ", txt or "").strip()

# ---------- arXiv search ----------
@app.get("/arxiv/search")
async def arxiv_search(
    query: str = Query(..., min_length=3, description="arXiv query syntax"),
    max_results: int = Query(5, le=20)
):
    params = {"search_query": query, "start": 0, "max_results": max_results}
    async with httpx.AsyncClient(headers=UA_HDR, timeout=15) as cli:
        resp = await cli.get(ARXIV_API, params=params)
    feed = feedparser.parse(resp.text)
    papers = [{
        "id":       e.id.split("/")[-1],
        "title":    clean(e.title),
        "authors":  [a.name for a in e.authors],
        "published":e.published,
        "summary":  clean(e.summary)
    } for e in feed.entries]
    return {"papers": papers}

# ---------- PubMed search ----------
@app.get("/pubmed/search")
async def pubmed_search(
    query: str = Query(..., min_length=3),
    max_results: int = Query(5, le=20)
):
    s_params = {
        "db":"pubmed","term":query,"retmax":max_results,
        "api_key":NCBI_API_KEY
    }
    async with httpx.AsyncClient(timeout=15) as cli:
        ids_xml = (await cli.get(PUBMED_SEARCH, params=s_params)).text
    id_list = re.findall(r"<Id>(\d+)</Id>", ids_xml)
    if not id_list:
        return {"papers":[]}

    f_params = {
        "db":"pubmed","id":",".join(id_list),
        "rettype":"abstract","api_key":NCBI_API_KEY
    }
    async with httpx.AsyncClient(timeout=15) as cli:
        abstracts = (await cli.get(PUBMED_FETCH, params=f_params)).text.split("\n\n")

    papers=[]
    for blob in abstracts:
        pmid  = re.search(r"PMID-\s+(\d+)", blob)
        title = re.search(r"TI  - (.+)", blob)
        if pmid and title:
            papers.append({
                "id":pmid.group(1),
                "title":clean(title.group(1)),
                "authors":re.findall(r"AU  - (.+)", blob),
                "published":re.search(r"DP  - (.+)", blob).group(1) if re.search(r"DP  - (.+)", blob) else "",
                "summary":""
            })
    return {"papers": papers}

# ---------- paper details ----------
@app.get("/paper/{source}/{paper_id}")
async def get_paper(
    source: str  = Path(..., regex="^(arxiv|pubmed)$"),
    paper_id: str = Path(...)
):
    if source=="arxiv":
        async with httpx.AsyncClient(headers=UA_HDR, timeout=15) as cli:
            url = f"{ARXIV_API}?search_query=id:{paper_id}&max_results=1"
            feed = feedparser.parse((await cli.get(url)).text)
        if not feed.entries:
            raise HTTPException(404,"Paper not found")
        e=feed.entries[0]
        return {
            "title":clean(e.title),
            "authors":[a.name for a in e.authors],
            "published":e.published,
            "abstract":clean(e.summary),
            "url":f"https://arxiv.org/abs/{paper_id}"
        }

    # ---- PubMed ----
    f_params={"db":"pubmed","id":paper_id,"rettype":"abstract","api_key":NCBI_API_KEY}
    async with httpx.AsyncClient(timeout=15) as cli:
        txt=(await cli.get(PUBMED_FETCH,params=f_params)).text
    title=re.search(r"TI  - (.+)",txt)
    if not title:
        raise HTTPException(404,"Paper not found")
    return {
        "title":clean(title.group(1)),
        "authors":re.findall(r"AU  - (.+)",txt),
        "published":re.search(r"DP  - (.+)",txt).group(1) if re.search(r"DP  - (.+)",txt) else "",
        "abstract":" ".join(re.findall(r"AB  - (.+)",txt)),
        "url":f"https://pubmed.ncbi.nlm.nih.gov/{paper_id}/"
    }
