# scrape_datasets.py
"""
Scrape dataset pages for animal behaviour datasets and produce a JSON/CSV summary
Fields: name, url, raw_text (page or pdf), capture_settings, data_size, advantages, limitations, source_citation
Usage:
    pip install -r requirements.txt
    python scrape_datasets.py
Output:
    datasets_summary.json
    datasets_summary.csv
"""

import requests
from bs4 import BeautifulSoup
import re
import json
import csv
import time
import os
from urllib.parse import urlparse

# Optional: pdf text extraction
try:
    import pdfplumber
except Exception:
    pdfplumber = None

DATASETS = [
    {
        "name": "Animal Kingdom (CVPR2022)",
        "url": "https://github.com/sutdcv/Animal-Kingdom",
        "citation": "Animal Kingdom: A Large and Diverse Dataset for Animal Behavior Understanding (CVPR 2022). https://openaccess.thecvf.com/content/CVPR2022/papers/Ng_Animal_Kingdom_A_Large_and_Diverse_Dataset_for_Animal_Behavior_CVPR_2022_paper.pdf"
    },
    {
        "name": "APT-36K (Animal Pose Tracking)",
        "url": "https://github.com/pandorgan/APT-36K",
        "citation": "APT-36K: A Large-scale Benchmark for Animal Pose Estimation and Tracking. (NeurIPS / OpenReview / paper)."
    },
    {
        "name": "iNaturalist (subset: ba188/iNaturalist_v2)",
        "url": "https://huggingface.co/datasets/ba188/iNaturalist_v2",
        "citation": "HuggingFace - ba188/iNaturalist_v2 dataset page."
    },
    {
        "name": "MammalNet",
        "url": "https://mammal-net.github.io/",
        "citation": "MammalNet: A Large-scale Video Benchmark for Mammal Recognition and Behavior Understanding (CVPR 2023)."
    }
]

# Helper functions
def fetch_text_from_html(url, timeout=20):
    """Fetch page and return visible text."""
    print(f"Fetching HTML: {url}")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; dataset-scraper/1.0)"}
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    # get main textual content
    texts = []
    # prefer article or README-like containers
    for tag in soup.find_all(["p", "li", "h1", "h2", "h3", "pre"]):
        text = tag.get_text(separator=" ", strip=True)
        if text:
            texts.append(text)
    # fallback: whole text
    full = "\n\n".join(texts)
    return full

def fetch_text_from_pdf_url(url, timeout=30):
    """Download a PDF (if it's a pdf link) and extract text with pdfplumber if available."""
    print(f"Fetching PDF: {url}")
    r = requests.get(url, stream=True, timeout=timeout)
    r.raise_for_status()
    fname = "tmp_downloaded.pdf"
    with open(fname, "wb") as f:
        for chunk in r.iter_content(1024*1024):
            f.write(chunk)
    text = ""
    if pdfplumber:
        try:
            with pdfplumber.open(fname) as pdf:
                pages = []
                for p in pdf.pages:
                    pages.append(p.extract_text() or "")
                text = "\n\n".join(pages)
        except Exception as e:
            print("pdfplumber error:", e)
            with open(fname, "rb") as f:
                text = "<binary pdf saved; pdfplumber failed>"
    else:
        text = "<pdf downloaded; pdfplumber not installed>"
    try:
        os.remove(fname)
    except:
        pass
    return text

def fetch_page_text(url):
    """Decide if url is html or pdf and fetch text."""
    parsed = urlparse(url)
    if parsed.path.endswith(".pdf"):
        return fetch_text_from_pdf_url(url)
    # fetch HTML
    try:
        return fetch_text_from_html(url)
    except Exception as e:
        print("HTML fetch failed:", e)
        # try to find a pdf link on the page
        try:
            r = requests.get(url, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            pdf_link = None
            for a in soup.find_all("a", href=True):
                if a["href"].lower().endswith(".pdf"):
                    pdf_link = a["href"]
                    break
            if pdf_link:
                if pdf_link.startswith("/"):
                    base = f"{parsed.scheme}://{parsed.netloc}"
                    pdf_link = base + pdf_link
                return fetch_text_from_pdf_url(pdf_link)
        except Exception as e2:
            print("fallback fetch failed:", e2)
    return ""

# Simple heuristic extraction functions
def extract_capture_settings(text):
    """Look for sentences mentioning capture conditions, cameras, fps, viewpoint, lab vs wild."""
    if not text:
        return ""
    # search for lines containing keywords
    keywords = ["captur", "record", "frame", "fps", "video", "image", "camera", "resolution", "scene", "lab", "wild", "YouTube", "camera trap", "micro", "high-speed", "synchronized"]
    lines = re.split(r"\n{1,3}", text)
    matches = []
    for line in lines:
        lower = line.lower()
        if any(k in lower for k in keywords):
            if len(line) < 1200:
                matches.append(line.strip())
    # return top 4 unique matches
    uniq = []
    for m in matches:
        if m not in uniq:
            uniq.append(m)
        if len(uniq) >= 6:
            break
    return " ".join(uniq[:6])

def extract_data_size(text):
    """Find numeric mentions: hours, videos, frames, images, species."""
    if not text:
        return ""
    # common patterns
    patterns = [
        r"(\d[\d,\.]*\s*(?:hours|hour))",
        r"(\d[\d,\,]*\s*(?:videos|video clips|video))",
        r"(\d[\d,\,]*\s*(?:images|images|frames|frames in total|frames))",
        r"(\d[\d,\,]*\s*(?:species|categories|classes|taxa))",
        r"(\d[\d,\,]*\s*(?:GB|TB|MB))"
    ]
    found = []
    for p in patterns:
        for m in re.findall(p, text, flags=re.IGNORECASE):
            if m not in found:
                found.append(m)
    # try to find "contains over X videos" style
    extra = []
    m = re.search(r"over\s+([\d,\.]+\s*(?:videos|images|hours|frames|species))", text, flags=re.I)
    if m:
        extra.append(m.group(0))
    return "; ".join(found + extra)[:1000]

def extract_advantages_limitations(text):
    """Heuristic: find paragraphs with 'advant' or 'limit' or 'challenge' or 'benefit' or 'contribute'."""
    if not text:
        return "", ""
    paras = re.split(r"\n{2,}", text)
    adv = []
    lim = []
    for p in paras:
        pl = p.lower()
        if any(w in pl for w in ["advant", "benefit", "good", "strength", "useful", "diverse", "versatile", "large", "scale"]):
            adv.append(p.strip())
        if any(w in pl for w in ["limitation", "limit", "challenge", "problem", "difficult", "issue", "bias", "imbalanc", "noise", "missing"]):
            lim.append(p.strip())
    # If no explicit matches, create heuristic summaries:
    if not adv:
        # if contains 'diverse' or 'large' words
        if "diverse" in text.lower() or "large-scale" in text.lower() or "large scale" in text.lower():
            adv.append("Dataset claims high diversity / large-scale coverage (heuristic).")
    if not lim:
        if "limited" in text.lower() or "few" in text.lower() or "challenge" in text.lower():
            lim.append("Dataset mentions limitations or challenges (heuristic).")
    return " ".join(adv[:3]), " ".join(lim[:3])

def summarize_dataset(entry):
    url = entry["url"]
    name = entry["name"]
    citation = entry.get("citation", "")
    try:
        text = fetch_page_text(url)
        if not text or len(text) < 200:
            # fallback: if GitHub repo, try README raw
            if "github.com" in url:
                # fetch raw README
                raw_url = url.rstrip("/") + "/raw/main/README.md"
                try:
                    r = requests.get(raw_url, timeout=10)
                    if r.ok and len(r.text) > 100:
                        text = r.text
                except:
                    pass
    except Exception as e:
        print("Fetch error:", e)
        text = ""
    # run extractors
    capture = extract_capture_settings(text)
    data_size = extract_data_size(text)
    advantages, limitations = extract_advantages_limitations(text)
    return {
        "name": name,
        "url": url,
        "citation": citation,
        "raw_text_snippet": (text[:4000] + "...") if text else "",
        "capture_settings": capture,
        "data_size": data_size,
        "advantages": advantages,
        "limitations": limitations
    }

def main():
    out = []
    for ds in DATASETS:
        print("="*60)
        print("Processing:", ds["name"])
        try:
            info = summarize_dataset(ds)
            out.append(info)
            print("Captured:", info["name"])
            time.sleep(1.0)  # be polite
        except Exception as e:
            print("Failed for", ds["name"], e)
    # save json
    with open("datasets_summary.json", "w", encoding="utf8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    # save csv
    keys = ["name","url","citation","capture_settings","data_size","advantages","limitations"]
    with open("datasets_summary.csv", "w", newline='', encoding="utf8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for r in out:
            writer.writerow({k: r.get(k,"") for k in keys})
    print("Done. Saved datasets_summary.json and datasets_summary.csv")

if __name__ == "__main__":
    main()
