#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import io, json, os, re
from typing import Dict, List, Optional
import streamlit as st, requests

PDF_BACKEND = None
try:
    from pdfminer.high_level import extract_text as _pdfminer_extract
    PDF_BACKEND = "pdfminer"
except Exception:
    _pdfminer_extract = None

try:
    from PyPDF2 import PdfReader as _PdfReader
    if PDF_BACKEND is None:
        PDF_BACKEND = "pypdf2"
except Exception:
    _PdfReader = None

CROSSREF_API = "https://api.crossref.org/works/"
USER_AGENT = "auto-meta-web/1.1 (mailto:you@example.com)"

TYPE_MAP = {"journal-article":"JOUR","proceedings-article":"CPAPER","book-chapter":"CHAP","book":"BOOK","report":"RPRT","thesis":"THES","reference-entry":"GEN","posted-content":"GEN","dataset":"DATA","standard":"STD"}
DOI_REGEX = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.I)

def clean(s: Optional[str]) -> str: return (s or "").strip()
def normalize_author(a: dict) -> str:
    given, family = clean(a.get("given")), clean(a.get("family"))
    return f"{family}, {given}" if family and given else (family or given or "")
def ris_escape(v: str) -> str: return v.replace("\n"," ").strip()

def to_ris_lines(m: Dict) -> List[str]:
    L=["TY  - "+(m.get("type") or "GEN")]
    if m.get("title"): L.append("TI  - "+ris_escape(m["title"]))
    if m.get("journal"): L.append("T2  - "+ris_escape(m["journal"]))
    if m.get("publisher"): L.append("PB  - "+ris_escape(m["publisher"]))
    if m.get("year"): L.append("PY  - "+m["year"])
    if m.get("volume"): L.append("VL  - "+m["volume"])
    if m.get("issue"): L.append("IS  - "+m["issue"])
    for a in m.get("authors",[]): 
        if a: L.append("AU  - "+ris_escape(a))
    if m.get("sp"): L.append("SP  - "+m["sp"])
    if m.get("ep"): L.append("EP  - "+m["ep"])
    if m.get("doi"): L.append("DO  - "+m["doi"])
    if m.get("url"): L.append("UR  - "+m["url"])
    if m.get("abstract"): L.append("AB  - "+ris_escape(m["abstract"]))
    for kw in m.get("keywords",[]): 
        if kw: L.append("KW  - "+ris_escape(kw))
    L.append("ER  - ")
    return L

def find_doi_in_text(t: str)->Optional[str]:
    m = DOI_REGEX.search(t or "")
    return m.group(1) if m else None

def fetch_crossref_by_doi(doi: str)->Dict:
    url = CROSSREF_API + requests.utils.quote(doi)
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()
    return r.json().get("message",{})

def crossref_first_year(msg: Dict)->Optional[str]:
    for k in ["published-print","issued","created","published-online"]:
        parts = msg.get(k,{}).get("date-parts",[])
        if parts and parts[0]:
            try: return str(int(parts[0][0]))
            except: pass
    return None

def crossref_to_meta(msg: Dict)->Dict:
    ty = TYPE_MAP.get((msg.get("type") or "").lower(),"GEN")
    title = (msg.get("title") or [""])[0] if isinstance(msg.get("title"),list) and msg["title"] else ""
    journal = (msg.get("container-title") or [""])[0] if isinstance(msg.get("container-title"),list) and msg["container-title"] else ""
    authors = [normalize_author(a) for a in (msg.get("author") or [])]
    page = clean(msg.get("page")); sp,ep="",""
    if page and "-" in page: sp,ep=[p.strip() for p in page.split("-",1)]
    elif page: sp=page.strip()
    abstract = clean(msg.get("abstract"))
    if abstract.startswith("<jats:"): abstract = re.sub(r"<[^>]+>"," ",abstract).strip()
    keywords = [s.strip() for s in (msg.get("subject") or []) if isinstance(s,str)]
    return {"type":ty,"title":clean(title),"authors":authors,"year":crossref_first_year(msg),
            "journal":clean(journal),"volume":clean(msg.get("volume")),"issue":clean(msg.get("issue")),
            "sp":sp,"ep":ep,"doi":clean(msg.get("DOI")),"url":clean(msg.get("URL")),"publisher":clean(msg.get("publisher")),
            "abstract":abstract,"keywords":keywords}

def search_crossref_by_title(title: str)->Optional[Dict]:
    if not title.strip(): return None
    r = requests.get("https://api.crossref.org/works", params={"query.title":title,"rows":1},
                     headers={"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status(); items = r.json().get("message",{}).get("items",[])
    return items[0] if items else None

def extract_text_from_pdf_bytes(pdf_bytes: bytes, max_chars:int=120000)->str:
    tmp=".__upload.pdf"; open(tmp,"wb").write(pdf_bytes)
    text=""
    try:
        if PDF_BACKEND=="pdfminer" and _pdfminer_extract:
            try: text = _pdfminer_extract(tmp) or ""
            except: text=""
        if not text and _PdfReader:
            try:
                from PyPDF2.errors import PdfReadError
            except Exception:
                class PdfReadError(Exception): pass
            try:
                reader=_PdfReader(tmp); pages=[]
                for p in reader.pages[:20]:
                    try: pages.append(p.extract_text() or "")
                    except Exception: pages.append("")
                text="\n".join(pages)
            except Exception:
                pass
    finally:
        try: os.remove(tmp)
        except: pass
    return (text or "")[:max_chars]

def guess_title_from_pdf_text(text: str)->Optional[str]:
    for line in text.splitlines():
        line=line.strip()
        if len(line.split())>=4 and not line.lower().startswith(("arxiv:","doi:","issn:","copyright")) and 10<=len(line)<=200:
            return line
    return None

def meta_to_ris(L: List[Dict])->str:
    return "\n".join("\n".join(to_ris_lines(m)) for m in L) + ("\n" if L else "")

# ---------------- UI ----------------
st.set_page_config(page_title="Auto RIS Builder", page_icon="📚")
st.title("Auto RIS Builder (EndNote-ready)")
st.write("Upload PDFs or paste DOI/URLs. I'll fetch and normalize metadata, then export a single **.ris** file for EndNote.")

with st.expander("Environment status"):
    st.write(f"PDF backend: **{PDF_BACKEND or 'none'}**")
    if PDF_BACKEND is None:
        st.warning("No PDF text extractor available. Paste DOIs/URLs for best results, or install pdfminer.six/PyPDF2.")

with st.expander("Input rules"):
    st.markdown("- Prefer **DOI** or **publisher landing pages**.\n- Scanned PDFs (images) need manual checks.\n- Output fields: TY/TI/T2/AU/PY/VL/IS/SP/EP/DO/UR/AB/KW.\n- Authors normalized to **Last, First**.")

tab_single, tab_batch = st.tabs(["Single input","Batch input"])

results: List[Dict]=[]; audit: List[Dict]=[]

with tab_single:
    doi = st.text_input("DOI (e.g., 10.1038/s41586-020-2649-2)")
    url = st.text_input("URL (the app will try to detect a DOI on the page)")
    pdf = st.file_uploader("PDF", type=["pdf"])
    if st.button("Process single input"):
        if not any([doi.strip(), url.strip(), pdf]):
            st.warning("Please provide a DOI, a URL, or a PDF.")
        else:
            meta=None; route=""
            try:
                if doi.strip():
                    meta=crossref_to_meta(fetch_crossref_by_doi(doi.strip())); route="doi"
                elif url.strip():
                    page = requests.get(url.strip(), headers={"User-Agent": USER_AGENT}, timeout=20).text
                    m = DOI_REGEX.search(page)
                    if m:
                        meta=crossref_to_meta(fetch_crossref_by_doi(m.group(1))); route="url_doi"
                    else:
                        m=re.search(r"<title[^>]*>(.*?)</title>", page, flags=re.I|re.S)
                        t=re.sub(r"\s+"," ",m.group(1)).strip() if m else ""
                        if t:
                            hit=search_crossref_by_title(t)
                            if hit: meta=crossref_to_meta(hit); route="url_title"
                elif pdf:
                    text=extract_text_from_pdf_bytes(pdf.read())
                    d=find_doi_in_text(text)
                    if d:
                        meta=crossref_to_meta(fetch_crossref_by_doi(d)); route="pdf_doi"
                    else:
                        t=guess_title_from_pdf_text(text) or ""
                        if t:
                            hit=search_crossref_by_title(t)
                            if hit: meta=crossref_to_meta(hit); route="pdf_title"
            except Exception as e:
                st.error(f"Error: {e}")
            if meta:
                results.append(meta); audit.append({"input": doi or url or (pdf.name if pdf else ""), "route": route, "success": True, "meta": meta}); st.success(f"OK: {meta.get('title','(no title)')}")
            else:
                audit.append({"input": doi or url or (pdf.name if pdf else ""), "route": route, "success": False, "meta": None}); st.error("Failed to extract metadata. Try a DOI or publisher page.")

with tab_batch:
    pdfs = st.file_uploader("PDF files", type=["pdf"], accept_multiple_files=True)
    list_box = st.text_area("DOIs/URLs list (one per line)", height=150)
    if st.button("Process batch"):
        items=[ln.strip() for ln in list_box.splitlines() if ln.strip()]
        for f in (pdfs or []):
            try:
                text=extract_text_from_pdf_bytes(f.read()); d=find_doi_in_text(text)
                meta=None; route=""
                if d:
                    meta=crossref_to_meta(fetch_crossref_by_doi(d)); route="pdf_doi"
                else:
                    t=guess_title_from_pdf_text(text) or ""
                    if t:
                        hit=search_crossref_by_title(t)
                        if hit: meta=crossref_to_meta(hit); route="pdf_title"
                if meta: results.append(meta); audit.append({"input": f.name,"route":route,"success":True,"meta":meta})
                else: audit.append({"input": f.name,"route":route,"success":False,"meta":None})
            except Exception as e:
                audit.append({"input": f.name,"route":"pdf_error","success":False,"error":str(e),"meta":None})
        for item in items:
            meta=None; route=""
            try:
                if DOI_REGEX.search(item):
                    meta=crossref_to_meta(fetch_crossref_by_doi(item)); route="doi"
                else:
                    page=requests.get(item, headers={"User-Agent": USER_AGENT}, timeout=20).text
                    m=DOI_REGEX.search(page)
                    if m:
                        meta=crossref_to_meta(fetch_crossref_by_doi(m.group(1))); route="url_doi"
                    else:
                        m=re.search(r"<title[^>]*>(.*?)</title>", page, flags=re.I|re.S)
                        t=re.sub(r"\s+"," ",m.group(1)).strip() if m else ""
                        if t:
                            hit=search_crossref_by_title(t)
                            if hit: meta=crossref_to_meta(hit); route="url_title"
                if meta: results.append(meta); audit.append({"input": item,"route":route,"success":True,"meta":meta})
                else: audit.append({"input": item,"route":route,"success":False,"meta":None})
            except Exception as e:
                audit.append({"input": item,"route":"url_error","success":False,"error":str(e),"meta":None})

st.markdown("---"); st.subheader("Output")
if results:
    ris_text = meta_to_ris(results)
    st.code(ris_text[:2000]+("...\n" if len(ris_text)>2000 else ""), language="text")
    st.download_button("Download RIS", data=ris_text, file_name="references.ris", mime="application/x-research-info-systems")
    audit_bytes = ("\n".join(json.dumps(a, ensure_ascii=False) for a in audit)).encode("utf-8")
    st.download_button("Download audit log (JSONL)", data=audit_bytes, file_name="audit.jsonl", mime="application/json")
    st.success(f"Generated {len(results)} record(s). Import the .ris into your EndNote shared library.")
else:
    st.info("No records yet. Provide inputs above and click Process.")
