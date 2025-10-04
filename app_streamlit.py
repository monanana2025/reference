#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web App: Auto Metadata to RIS (EndNote)
- Upload PDFs or paste DOI/URL list
- Auto fetch metadata via Crossref
- Extract DOI/title from PDFs (pdfminer.six)
- Normalize and export a single RIS for EndNote import
- Provide an audit JSONL for traceability

To run locally:
  pip install -r requirements.txt
  streamlit run app.py
"""

import io
import json
import os
import re
from typing import Dict, List, Optional, Tuple

import streamlit as st

# Optional imports (handled by requirements)
import requests
from pdfminer.high_level import extract_text

CROSSREF_API = "https://api.crossref.org/works/"
USER_AGENT = "auto-meta-web/1.0 (mailto:you@example.com)"

TYPE_MAP = {
    "journal-article": "JOUR",
    "proceedings-article": "CPAPER",
    "book-chapter": "CHAP",
    "book": "BOOK",
    "report": "RPRT",
    "thesis": "THES",
    "reference-entry": "GEN",
    "posted-content": "GEN",
    "dataset": "DATA",
    "standard": "STD",
}

DOI_REGEX = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.I)

# ---------------- Utility ----------------

def clean(s: Optional[str]) -> str:
    return (s or "").strip()

def normalize_author(crossref_author: dict) -> str:
    given = clean(crossref_author.get("given"))
    family = clean(crossref_author.get("family"))
    if family and given:
        return f"{family}, {given}"
    return family or given or ""

def ris_escape(v: str) -> str:
    return v.replace("\n", " ").strip()

def to_ris_lines(meta: Dict) -> List[str]:
    lines = []
    ty = meta.get("type") or "GEN"
    lines.append(f"TY  - {ty}")

    if meta.get("title"):
        lines.append(f"TI  - {ris_escape(meta['title'])}")
    if meta.get("journal"):
        lines.append(f"T2  - {ris_escape(meta['journal'])}")
    if meta.get("publisher"):
        lines.append(f"PB  - {ris_escape(meta['publisher'])}")
    if meta.get("year"):
        lines.append(f"PY  - {meta['year']}")
    if meta.get("volume"):
        lines.append(f"VL  - {meta['volume']}")
    if meta.get("issue"):
        lines.append(f"IS  - {meta['issue']}")
    for a in meta.get("authors", []):
        if a:
            lines.append(f"AU  - {ris_escape(a)}")
    if meta.get("sp"):
        lines.append(f"SP  - {meta['sp']}")
    if meta.get("ep"):
        lines.append(f"EP  - {meta['ep']}")
    if meta.get("doi"):
        lines.append(f"DO  - {meta['doi']}")
    if meta.get("url"):
        lines.append(f"UR  - {meta['url']}")
    if meta.get("abstract"):
        lines.append(f"AB  - {ris_escape(meta['abstract'])}")
    for kw in meta.get("keywords", []):
        if kw:
            lines.append(f"KW  - {ris_escape(kw)}")
    lines.append("ER  - ")
    return lines

def find_doi_in_text(text: str) -> Optional[str]:
    m = DOI_REGEX.search(text or "")
    return m.group(1) if m else None

def fetch_crossref_by_doi(doi: str) -> Dict:
    url = CROSSREF_API + requests.utils.quote(doi)
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json().get("message", {})

def crossref_first_year(msg: Dict) -> Optional[str]:
    for key in ["published-print", "issued", "created", "published-online"]:
        parts = msg.get(key, {}).get("date-parts", [])
        if parts and parts[0]:
            y = parts[0][0]
            try:
                return str(int(y))
            except Exception:
                pass
    return None

def crossref_to_meta(msg: Dict) -> Dict:
    ctype = (msg.get("type") or "").lower()
    ty = TYPE_MAP.get(ctype, "GEN")
    title = ""
    if isinstance(msg.get("title"), list) and msg["title"]:
        title = msg["title"][0]
    journal = ""
    if isinstance(msg.get("container-title"), list) and msg["container-title"]:
        journal = msg["container-title"][0]

    authors = [normalize_author(a) for a in (msg.get("author") or [])]

    sp, ep = "", ""
    page = clean(msg.get("page"))
    if page and "-" in page:
        sp, ep = [p.strip() for p in page.split("-", 1)]
    elif page:
        sp = page.strip()

    abstract = clean(msg.get("abstract"))
    if abstract.startswith("<jats:"):
        abstract = re.sub(r"<[^>]+>", " ", abstract).strip()

    keywords = []
    subj = msg.get("subject") or []
    if isinstance(subj, list):
        keywords = [s.strip() for s in subj if isinstance(s, str)]

    return {
        "type": ty,
        "title": clean(title),
        "authors": authors,
        "year": crossref_first_year(msg),
        "journal": clean(journal),
        "volume": clean(msg.get("volume")),
        "issue": clean(msg.get("issue")),
        "sp": sp,
        "ep": ep,
        "doi": clean(msg.get("DOI")),
        "url": clean(msg.get("URL")),
        "publisher": clean(msg.get("publisher")),
        "abstract": abstract,
        "keywords": keywords,
    }

def search_crossref_by_title(title: str) -> Optional[Dict]:
    if not title.strip():
        return None
    headers = {"User-Agent": USER_AGENT}
    params = {"query.title": title, "rows": 1}
    r = requests.get("https://api.crossref.org/works", params=params, headers=headers, timeout=20)
    r.raise_for_status()
    items = r.json().get("message", {}).get("items", [])
    return items[0] if items else None

def extract_text_from_pdf_bytes(pdf_bytes: bytes, max_chars: int = 100000) -> str:
    with open("._tmp_upload.pdf", "wb") as f:
        f.write(pdf_bytes)
    try:
        text = extract_text("._tmp_upload.pdf") or ""
        return text[:max_chars]
    finally:
        try:
            os.remove("._tmp_upload.pdf")
        except Exception:
            pass

def guess_title_from_pdf_text(text: str) -> Optional[str]:
    for line in text.splitlines():
        line = line.strip()
        if len(line.split()) >= 4 and not line.lower().startswith(("arxiv:", "doi:", "issn:", "copyright")):
            if 10 <= len(line) <= 200:
                return line
    return None

def meta_to_ris(meta_list: List[Dict]) -> str:
    blocks = []
    for m in meta_list:
        blocks.append("\n".join(to_ris_lines(m)))
    return "\n".join(blocks) + ("\n" if blocks else "")

# --------------- Streamlit UI ---------------

st.set_page_config(page_title="Auto RIS Builder", page_icon="📚")

st.title("Auto RIS Builder (EndNote-ready)")
st.write("Upload PDFs or paste DOI/URLs. I’ll fetch and normalize metadata, then export a single **.ris** file for EndNote.")

with st.expander("Input rules (team-wide)"):
    st.markdown("""
- Prefer **DOI** or **publisher landing pages** for best accuracy.
- Scanned PDFs may need manual check (OCR not enabled in this basic build).
- Output uses: `TY/TI/T2/AU/PY/VL/IS/SP/EP/DO/UR/AB/KW`.
- Authors normalized to **Last, First** per line (`AU  - ...`).
""")

tab_single, tab_batch = st.tabs(["Single input", "Batch input"])

results = []
audit = []

with tab_single:
    st.subheader("Single DOI / URL / PDF")
    doi = st.text_input("DOI (e.g., 10.1038/s41586-020-2649-2)", key="single_doi")
    url = st.text_input("URL (will try to detect DOI inside the page)", key="single_url")
    pdf = st.file_uploader("PDF", type=["pdf"], key="single_pdf")

    if st.button("Process single input"):
        if not any([doi.strip(), url.strip(), pdf]):
            st.warning("Please provide a DOI, a URL, or a PDF.")
        else:
            meta = None
            route = ""
            try:
                if doi.strip():
                    msg = fetch_crossref_by_doi(doi.strip())
                    meta = crossref_to_meta(msg)
                    route = "doi"
                elif url.strip():
                    page = requests.get(url.strip(), headers={"User-Agent": USER_AGENT}, timeout=20).text
                    found = DOI_REGEX.search(page)
                    if found:
                        msg = fetch_crossref_by_doi(found.group(1))
                        meta = crossref_to_meta(msg)
                        route = "url_doi"
                    else:
                        m = re.search(r"<title[^>]*>(.*?)</title>", page, flags=re.I|re.S)
                        title_guess = ""
                        if m:
                            title_guess = re.sub(r"\s+", " ", m.group(1)).strip()
                        if title_guess:
                            hit = search_crossref_by_title(title_guess)
                            if hit:
                                meta = crossref_to_meta(hit)
                                route = "url_title"
                elif pdf:
                    text = extract_text_from_pdf_bytes(pdf.read())
                    d = find_doi_in_text(text)
                    if d:
                        msg = fetch_crossref_by_doi(d)
                        meta = crossref_to_meta(msg)
                        route = "pdf_doi"
                    else:
                        title_guess = guess_title_from_pdf_text(text) or ""
                        if title_guess:
                            hit = search_crossref_by_title(title_guess)
                            if hit:
                                meta = crossref_to_meta(hit)
                                route = "pdf_title"
            except Exception as e:
                st.error(f"Error: {e}")

            if meta:
                results.append(meta)
                audit.append({"input": doi or url or (pdf.name if pdf else ""), "route": route, "success": True, "meta": meta})
                st.success(f"OK: {meta.get('title','(no title)')}")
            else:
                audit.append({"input": doi or url or (pdf.name if pdf else ""), "route": route, "success": False, "meta": None})
                st.error("Failed to extract metadata. Try a DOI or publisher page.")

with tab_batch:
    st.subheader("Batch mode")
    st.write("Upload multiple PDFs and/or paste a list of DOIs/URLs (one per line).")
    pdfs = st.file_uploader("PDF files", type=["pdf"], accept_multiple_files=True, key="batch_pdfs")
    list_box = st.text_area("DOIs/URLs list", placeholder="10.xxxx/abc\nhttps://doi.org/10.xxxx/abc\nhttps://publisher.com/article/...", height=150)

    if st.button("Process batch"):
        items = []
        for line in list_box.splitlines():
            val = line.strip()
            if not val:
                continue
            items.append(val)

        for f in (pdfs or []):
            try:
                text = extract_text_from_pdf_bytes(f.read())
                d = find_doi_in_text(text)
                meta = None
                route = ""
                if d:
                    msg = fetch_crossref_by_doi(d)
                    meta = crossref_to_meta(msg)
                    route = "pdf_doi"
                else:
                    title_guess = guess_title_from_pdf_text(text) or ""
                    if title_guess:
                        hit = search_crossref_by_title(title_guess)
                        if hit:
                            meta = crossref_to_meta(hit)
                            route = "pdf_title"
                if meta:
                    results.append(meta)
                    audit.append({"input": f.name, "route": route, "success": True, "meta": meta})
                else:
                    audit.append({"input": f.name, "route": route, "success": False, "meta": None})
            except Exception as e:
                audit.append({"input": f.name, "route": "pdf_error", "success": False, "error": str(e), "meta": None})

        for item in items:
            meta = None
            route = ""
            try:
                if DOI_REGEX.search(item):
                    msg = fetch_crossref_by_doi(item)
                    meta = crossref_to_meta(msg)
                    route = "doi"
                else:
                    resp = requests.get(item, headers={"User-Agent": USER_AGENT}, timeout=20)
                    page = resp.text
                    found = DOI_REGEX.search(page)
                    if found:
                        msg = fetch_crossref_by_doi(found.group(1))
                        meta = crossref_to_meta(msg)
                        route = "url_doi"
                    else:
                        m = re.search(r"<title[^>]*>(.*?)</title>", page, flags=re.I|re.S)
                        title_guess = ""
                        if m:
                            title_guess = re.sub(r"\s+", " ", m.group(1)).strip()
                        if title_guess:
                            hit = search_crossref_by_title(title_guess)
                            if hit:
                                meta = crossref_to_meta(hit)
                                route = "url_title"
                if meta:
                    results.append(meta)
                    audit.append({"input": item, "route": route, "success": True, "meta": meta})
                else:
                    audit.append({"input": item, "route": route, "success": False, "meta": None})
            except Exception as e:
                audit.append({"input": item, "route": "url_error", "success": False, "error": str(e), "meta": None})

# ---------- Output area ----------

st.markdown("---")
st.subheader("Output")

if results:
    ris_text = meta_to_ris(results)
    st.code(ris_text[:2000] + ("...\n" if len(ris_text) > 2000 else ""), language="text")
    st.download_button("Download RIS", data=ris_text, file_name="references.ris", mime="application/x-research-info-systems")

    audit_lines = [json.dumps(a, ensure_ascii=False) for a in audit]
    audit_bytes = ("\n".join(audit_lines)).encode("utf-8")
    st.download_button("Download audit log (JSONL)", data=audit_bytes, file_name="audit.jsonl", mime="application/json")

    st.success(f"Generated {len(results)} record(s). Import the .ris into your EndNote shared library.")
else:
    st.info("No records yet. Provide inputs above and click Process.")

st.caption("Tip: For best accuracy, use official publisher pages or DOIs. Scanned PDFs may need a manual check.")
