"""
FastAPI micro-service
---------------------
• /paper/pubmed/{pmid}   – PubMed/PMC metadata, abstract, full_text
• /paper/arxiv/{id}      – arXiv metadata, abstract, full_text (pdf-mined)

ENV VARS
--------
NCBI_KEY       optional – raises PubMed rate limit to 10 r/s
ARXIV_PDF      "yes"    – download & extract PDF text (needs pdfminer.six)
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os, re, time, asyncio, httpx, feedparser
from bs4 import BeautifulSoup
from Bio import Entrez
from typing import List, Optional

# ------------------------------------------------------------------ CONFIG
Entrez.email   = os.getenv("ENTREZ_EMAIL", "haseebarshadn2000@gmail.com")
Entrez.api_key = os.getenv("NCBI_KEY", "d0a28a2ac77f0b03135b097bd0dcab612108")
ARXIV_PDF      = os.getenv("ARXIV_PDF", "no").lower() == "yes"

# ------------------------------------------------------------------ MODELS
class Paper(BaseModel):
    title: str
    authors: List[str]
    published: str
    abstract: str
    full_text: Optional[str] = ""
    url: str

# ------------------------------------------------------------------ FASTAPI
app = FastAPI(title="ResearchOracle-paper-proxy", version="0.4")

# ----------------------------- UTILITIES ------------------------------
def clean(txt: str) -> str:
    return re.sub(r"\s+", " ", txt.strip())

# ----------------------------- PubMed / PMC ---------------------------
async def pmid_to_pmcid(pmid: str) -> Optional[str]:
    record = Entrez.read(Entrez.elink(dbfrom="pubmed", id=pmid, linkname="pubmed_pmc"))
    ldb = record[0]["LinkSetDb"]
    return ldb[0]["Link"][0]["Id"] if ldb else None

async def pubmed_meta_xml(pmid: str) -> BeautifulSoup:
    async with httpx.AsyncClient(timeout=15) as cli:
        resp = await cli.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db":"pubmed","id":pmid,"retmode":"xml","api_key":Entrez.api_key}
        )
    return BeautifulSoup(resp.text, "xml")

async def get_pubmed_full(pmid: str) -> Paper:
    soup = await pubmed_meta_xml(pmid)

    # TITLE (robust)
    title_tag = soup.find(["ArticleTitle","article-title","BookTitle","Title"])
    if not title_tag:
        # fall back to ESummary
        es = Entrez.read(Entrez.esummary(db="pubmed", id=pmid))
        title_tag = es[0].get("Title") if es else None
    if not title_tag:
        raise HTTPException(404, "PMID not found")

    title = clean(title_tag.text if hasattr(title_tag, "text") else title_tag)

    authors = [clean(a.text) for a in soup.find_all("LastName")]
    if not authors:
        authors = [clean(a.text) for a in soup.find_all("CollectiveName")]

    abstract = clean(" ".join(p.text for p in soup.find_all("AbstractText"))) or "(No abstract available.)"

    pub = soup.find("PubDate") or soup.find("BookDate")
    year  = pub.Year.text  if pub and pub.Year  else ""
    month = pub.Month.text if pub and pub.Month else ""
    day   = pub.Day.text   if pub and pub.Day   else ""
    published = " ".join([year, month, day]).strip()

    # Full text via PMCID
    pmcid = await pmid_to_pmcid(pmid)
    full_text = ""
    if pmcid:
        async with httpx.AsyncClient(timeout=25) as cli:
            pmc_xml = await cli.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params={"db":"pmc","id":pmcid,"retmode":"xml","api_key":Entrez.api_key}
            )
        body = BeautifulSoup(pmc_xml.text, "xml").find("body")
        if body:
            full_text = "\n\n".join(clean(p.text) for p in body.find_all("p"))

    return Paper(
        title=title, authors=authors, published=published,
        abstract=abstract, full_text=full_text,
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    )

# ----------------------------- arXiv -----------------------------------
async def get_arxiv_meta(arxiv_id: str) -> dict:
    url = f"http://export.arxiv.org/api/query?search_query=id:{arxiv_id}&max_results=1"
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent":"ResearchOracle/0.1"}) as cli:
        resp = await cli.get(url)
    feed = feedparser.parse(resp.text)
    if not feed.entries:
        raise HTTPException(404, "arXiv ID not found")
    e = feed.entries[0]
    return {
        "title": clean(e.title),
        "authors": [a.name for a in e.authors],
        "published": e.published,
        "abstract": clean(e.summary)
    }

async def arxiv_pdf_text(arxiv_id: str) -> str:
    """Download PDF and extract text (requires pdfminer.six)."""
    if not ARXIV_PDF:
        return ""
    import io, pdfminer.high_level, pdfminer.layout
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    async with httpx.AsyncClient(timeout=30) as cli:
        resp = await cli.get(pdf_url)
    if resp.status_code != 200:
        return ""
    text = pdfminer.high_level.extract_text(io.BytesIO(resp.content))
    return clean(text)[:100000]  # cap at ~100k chars

async def get_arxiv_full(arxiv_id: str) -> Paper:
    meta = await get_arxiv_meta(arxiv_id)
    full_text = await arxiv_pdf_text(arxiv_id)
    return Paper(
        **meta,
        full_text=full_text,
        url=f"https://arxiv.org/abs/{arxiv_id}"
    )

# ----------------------------- ROUTES ----------------------------------
@app.get("/paper/pubmed/{pmid}", response_model=Paper)
async def paper_pubmed(pmid: str):
    # rudimentary sleep to respect NCBI 3 req/s w/out key
    if not Entrez.api_key:
        await asyncio.sleep(0.4)
    return await get_pubmed_full(pmid)

@app.get("/paper/arxiv/{arxiv_id}", response_model=Paper)
async def paper_arxiv(arxiv_id: str):
    return await get_arxiv_full(arxiv_id)

# ----------------------------- root ------------------------------------
@app.get("/")
async def root():
    return {"ok":"ResearchOracle paper proxy up"}
