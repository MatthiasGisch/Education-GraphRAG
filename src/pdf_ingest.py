from __future__ import annotations
import fitz, os, uuid, json
from typing import List, Dict, Any, Tuple
from tqdm import tqdm
from PIL import Image
from .config import IMAGES_DIR, DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP
from .openai_client import embed_text, describe_image
import hashlib

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1<<20), b""):
            h.update(chunk)
    return h.hexdigest()

def extract_doi_and_url(doc) -> tuple[str|None, str|None]:
    # 1) Metadaten
    meta = doc.metadata or {}
    doi = meta.get("doi") or None
    url = meta.get("identifier") or None
    # 2) Heuristik auf ersten 2 Seiten
    import re
    if not doi or not url:
        for i in range(min(2, len(doc))):
            t = doc[i].get_text("text")
            if not doi:
                m = re.search(r'\b10\.\d{4,9}/\S+\b', t)
                if m: doi = m.group(0)
            if not url:
                m2 = re.search(r'(https?://\S+)', t)
                if m2: url = m2.group(1)
    return doi, url

def extract_sections(doc) -> list[dict]:
    """Nimmt doc.get_toc(simple=True) und baut Page-Ranges."""
    toc = doc.get_toc(simple=True)  # [[level, title, page], ...]
    sections = []
    for i, (level, title, page) in enumerate(toc):
        page_start = max(1, page)
        page_end = (toc[i+1][2]-1) if i+1 < len(toc) else len(doc)
        sections.append({
            "section_id": str(uuid.uuid4()),
            "title": title.strip(),
            "level": int(level),
            "page_start": int(page_start),
            "page_end": int(page_end),
            "order": i+1,
        })
    return sections

def section_for_page(sections: list[dict], page_num1: int) -> dict|None:
    for s in sections:
        if s["page_start"] <= page_num1 <= s["page_end"]:
            return s
    return None

def extract_paragraph_blocks(page) -> list[dict]:
    """
    Robust: liest page.get_text('rawdict') und baut Absätze.
    - Unterstützt Spans ohne 'text' (nimmt dann 'chars' zusammen)
    - Ignoriert fehlerhafte / leere Lines
    Rückgabe: Liste aus Dicts mit text, bbox, order_in_page, page_width/-height, char_start/-end
    """
    rd = page.get_text("rawdict") or {}
    W, H = page.rect.width, page.rect.height
    paras: list[dict] = []
    order = 0

    blocks = rd.get("blocks", [])
    for b in blocks:
        if b.get("type") != 0:  # 0 = Textblock
            continue

        lines = b.get("lines") or []
        buf: list[str] = []

        for l in lines:
            spans = l.get("spans") or []
            line_parts: list[str] = []
            for s in spans:
                # 1) Normalfall
                t = s.get("text")
                # 2) Fallback: Zeichenliste
                if not t and isinstance(s.get("chars"), list):
                    try:
                        t = "".join(ch.get("c", "") for ch in s["chars"])
                    except Exception:
                        t = ""
                if t:
                    line_parts.append(t)
            if line_parts:
                buf.append("".join(line_parts))

        text = "\n".join(buf).strip()
        if not text:
            continue

        order += 1
        x0, y0, x1, y1 = (b.get("bbox") or (0, 0, 0, 0))
        paras.append({
            "text": text,
            "bbox": [x0, y0, x1, y1],
            "order_in_page": order,
            "page_width": W,
            "page_height": H,
            "char_start": 0,
            "char_end": len(text),
        })

    return paras

def extract_images_with_bbox(page) -> list[dict]:
    """
    Robust: holt Image-BBoxen aus 'rawdict'; falls nichts gefunden,
    liefert leere Liste (Speichern der Bilddateien passiert weiterhin via get_images()).
    """
    rd = page.get_text("rawdict") or {}
    W, H = page.rect.width, page.rect.height
    out: list[dict] = []
    for b in rd.get("blocks", []):
        if b.get("type") == 1:  # 1 = Imageblock
            x0, y0, x1, y1 = (b.get("bbox") or (0, 0, 0, 0))
            out.append({"bbox": [x0, y0, x1, y1], "page_width": W, "page_height": H})
    return out

def nearest_caption_after(image_bbox, text_blocks, y_tol=30):
    """Suche Textblock direkt unterhalb des Bilds als Caption (heuristisch)."""
    _, _, _, img_bottom = image_bbox
    candidates = [tb for tb in text_blocks if tb["bbox"][1] >= img_bottom and (tb["bbox"][1]-img_bottom) <= y_tol]
    if candidates:
        return sorted(candidates, key=lambda tb: tb["bbox"][1])[0]["text"]
    return ""

def read_pdf_text_and_images(path: str):
    """
    Wie zuvor – aber mit Fallback, falls rawdict-paragraphs leer:
    nimmt dann page.get_text('text') + Chunking.
    """
    doc = fitz.open(path)
    doi, url = extract_doi_and_url(doc)
    paper_id = str(uuid.uuid4())
    meta = doc.metadata or {}
    title = meta.get("title") or os.path.basename(path)
    paper_meta = {
        "paper_id": paper_id,
        "title": title,
        "source_path": os.path.abspath(path),
        "meta": meta,
        "doi": doi,
        "url": url,
        "file_sha256": file_sha256(path),
    }

    sections = extract_sections(doc)  # kann leer sein
    paragraphs: list[dict] = []
    figures_meta: list[dict] = []

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_num1 = page_idx + 1
        sec = section_for_page(sections, page_num1)

        # --- TEXTPFAD (robust + Fallback) ---
        text_blocks = extract_paragraph_blocks(page)
        if not text_blocks:
            # Fallback: Plain-Text der Seite + Chunking
            raw_text = page.get_text("text") or ""
            for chunk in chunk_text(raw_text, DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP):
                if not chunk.strip():
                    continue
                text_blocks.append({
                    "text": chunk.strip(),
                    "bbox": [0, 0, page.rect.width, page.rect.height],
                    "order_in_page": 9999,  # Fallback-Order
                    "page_width": page.rect.width,
                    "page_height": page.rect.height,
                    "char_start": 0,
                    "char_end": len(chunk),
                })

        for tb in text_blocks:
            paragraphs.append({
                "paragraph_id": str(uuid.uuid4()),
                "text": tb["text"],
                "page": page_num1,
                "order_in_page": tb["order_in_page"],
                "char_start": tb["char_start"],
                "char_end": tb["char_end"],
                "bbox": tb["bbox"],
                "page_width": tb["page_width"],
                "page_height": tb["page_height"],
                "sha256": sha256(tb["text"]),
                "section_id_ref": sec["section_id"] if sec else None,
            })

        # --- BILDPFAD ---
        # 1) BBoxen sammeln
        img_blocks = extract_images_with_bbox(page)  # kann leer sein
        # 2) Bilddateien physisch extrahieren
        for img_idx, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            pix = fitz.Pixmap(doc, xref)
            
            # Always check for alpha and decide format accordingly
            # If alpha channel exists, save as PNG (supports transparency)
            # Otherwise save as JPG (better compression)
            if pix.alpha:
                # Keep alpha channel and save as PNG
                img_path = os.path.join(IMAGES_DIR, f"{paper_id}_{page_num1}_{xref}.png")
                pix.save(img_path)
            else:
                # No alpha channel, safe to save as JPG
                # But ensure it's in RGB colorspace for JPG compatibility
                if pix.colorspace and pix.colorspace.name not in ["DeviceRGB", "CalRGB"]:
                    pix_rgb = fitz.Pixmap(fitz.csRGB, pix)
                    pix = None  # Release original
                    pix = pix_rgb
                img_path = os.path.join(IMAGES_DIR, f"{paper_id}_{page_num1}_{xref}.jpg")
                pix.save(img_path)

            # BBox heuristisch zuordnen (gleiche Reihenfolge); wenn nicht vorhanden -> None
            bbox = img_blocks[img_idx]["bbox"] if img_idx < len(img_blocks) else None
            pw = img_blocks[img_idx]["page_width"] if img_idx < len(img_blocks) else page.rect.width
            ph = img_blocks[img_idx]["page_height"] if img_idx < len(img_blocks) else page.rect.height

            figures_meta.append({
                "figure_id": str(uuid.uuid4()),
                "page": page_num1,
                "image_path": img_path,
                "bbox": bbox,
                "page_width": pw,
                "page_height": ph,
                "figure_label": "",  # echte Caption wird später heuristisch ermittelt/überschrieben
            })

    return paper_meta, sections, paragraphs, figures_meta

def chunk_text(text: str, size: int, overlap: int) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts: List[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        parts.append(text[start:end])
        if end == len(text):
            break
        start = max(0, end - overlap)
    return parts

def embed_paragraphs(paragraphs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for p in tqdm(paragraphs, desc="Embedding paragraphs"):
        emb = embed_text(p["text"])
        out.append({**p, "embedding": emb})
    return out

def analyze_and_embed_figures(figures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for f in tqdm(figures, desc="Analyzing figures"):
        analysis = describe_image(f["image_path"])
        caption = analysis.get("caption", "")
        emb = embed_text(caption if caption else "figure")
        out.append({
            "figure_id": f["figure_id"],
            "page": f["page"],
            "image_uri": os.path.abspath(f["image_path"]),
            "caption": caption,
            "analysis_json": json.dumps(analysis, ensure_ascii=False),
            "embedding": emb,
        })
    return out
