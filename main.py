"""
FastAPI micro-service
---------------------
• /paper/pubmed/{pmid}   – PubMed/PMC metadata, abstract, full_text
• /paper/arxiv/{id}      – arXiv metadata, abstract, full_text (optional PDF)

Optional ENV VARS
-----------------
ENTREZ_EMAIL   your@email.com
NCBI_KEY       PubMed API key (raises rate limit to 10 r/s)
ARXIV_PDF      "yes" → download & OCR PDF via pdfminer.six
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os, re, asyncio, httpx, feedparser
from bs4 import BeautifulSoup
from Bio import Entrez
from typing import List, Optional

# ------------------------------------------------------------------ CONFIG
Entrez.email   = os.getenv("ENTREZ_EMAIL", "haseebarshadn2000@gmail.com")
Entrez.api_key = os.getenv("NCBI_KEY", "d0a28a2ac77f0b03135b097bd0dcab612108")
ARXIV_PDF      = os.getenv("ARXIV_PDF", "no").lower() == "yes"

# ----------------------------- MODELS ----------------------------------
class Paper(BaseModel):
    title: str
    authors: List[str]
    published: str
    abstract: str
    full_text: Optional[str] = ""
    url: str

app = FastAPI(title="ResearchOracle-paper-proxy", version="1.0")

# ----------------------------- HELPERS ---------------------------------
def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())

async def pmid_to_pmcid(pmid: str) -> Optional[str]:
    rec = Entrez.read(Entrez.elink(dbfrom="pubmed", id=pmid, linkname="pubmed_pmc"))
    ldb = rec[0]["LinkSetDb"]
    return ldb[0]["Link"][0]["Id"] if ldb else None

# ----------------------------- PubMed ----------------------------------
async def get_pubmed_full(pmid: str) -> Paper:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    async with httpx.AsyncClient(timeout=15) as cli:
        xml = (await cli.get(base, params={
            "db": "pubmed", "id": pmid, "retmode": "xml", "api_key": Entrez.api_key
        })).text
    soup = BeautifulSoup(xml, "xml")

    # robust title extraction
    title_tag = soup.find(["ArticleTitle", "article-title", "BookTitle", "Title"])
    if not title_tag:
        es = Entrez.read(Entrez.esummary(db="pubmed", id=pmid))
        title_tag = es[0].get("Title") if es else None
    if not title_tag:
        raise HTTPException(404, "PMID not found")
    title = clean(title_tag.text if hasattr(title_tag, "text") else title_tag)

    authors = [clean(a.text) for a in soup.find_all("LastName")]
    if not authors:
        authors = [clean(a.text) for a in soup.find_all("CollectiveName")]

    abstract = clean(" ".join(a.text for a in soup.find_all("AbstractText"))) \
              or "(No abstract available.)"

    pub = soup.find("PubDate") or soup.find("BookDate")
    year = pub.Year.text if pub and pub.Year else ""
    month = pub.Month.text if pub and pub.Month else ""
    day = pub.Day.text if pub and pub.Day else ""
    published = " ".join([year, month, day]).strip()

    # optional full text
    full_text = ""
    pmcid = await pmid_to_pmcid(pmid)
    if pmcid:
        async with httpx.AsyncClient(timeout=25) as cli:
            pmc_xml = (await cli.get(base, params={
                "db":"pmc","id":pmcid,"retmode":"xml","api_key":Entrez.api_key
            })).text
        body = BeautifulSoup(pmc_xml, "xml").find("body")
        if body:
            full_text = "\n\n".join(clean(p.text) for p in body.find_all("p"))

    return Paper(
        title=title,
        authors=authors,
        published=published,
        abstract=abstract,
        full_text=full_text,
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    )

# ----------------------------- arXiv -----------------------------------
async def get_arxiv_full(arxiv_id: str) -> Paper:
    api_url = f"http://export.arxiv.org/api/query?search_query=id:{arxiv_id}&max_results=1"
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "ResearchOracle/1.0"}) as cli:
        feed = feedparser.parse((await cli.get(api_url)).text)
    if not feed.entries:
        raise HTTPException(404, "arXiv ID not found")
    e = feed.entries[0]
    meta = {
        "title": clean(e.title),
        "authors": [a.name for a in e.authors],
        "published": e.published,
        "abstract": clean(e.summary)
    }

    full_text = ""
    if ARXIV_PDF:
        import io, pdfminer.high_level
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        async with httpx.AsyncClient(timeout=30) as cli:
            pdf_resp = await cli.get(pdf_url)
        if pdf_resp.status_code == 200:
            full_text = clean(pdfminer.high_level.extract_text(io.BytesIO(pdf_resp.content)))[:100000]

    return Paper(**meta, full_text=full_text, url=f"https://arxiv.org/abs/{arxiv_id}")

# ----------------------------- ROUTES ----------------------------------
@app.get("/paper/pubmed/{pmid}", response_model=Paper)
async def paper_pubmed(pmid: str):
    if not Entrez.api_key:
        await asyncio.sleep(0.4)          # 3 requests/sec etiquette
    return await get_pubmed_full(pmid)

@app.get("/paper/arxiv/{arxiv_id}", response_model=Paper)
async def paper_arxiv(arxiv_id: str):
    return await get_arxiv_full(arxiv_id)

@app.get("/")
async def root():
    return {"status": "ResearchOracle paper proxy running"}
