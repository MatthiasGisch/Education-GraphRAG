# src/concept_extract.py
from __future__ import annotations
from typing import List, Dict, Any, Tuple
import os, re, uuid, json
from openai import OpenAI

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-large")
client = OpenAI()

# ---------- Utils ----------
def _slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:80] or str(uuid.uuid4())

def _embed(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]

# ---------- Public: Seeds zu Concept-Objekten (mit Embedding) ----------
def seed_names_to_concepts(seed_names: List[str]) -> List[Dict[str, Any]]:
    names = [n.strip() for n in (seed_names or []) if n and n.strip()]
    if not names:
        return []
    embs = _embed(names)
    out: List[Dict[str, Any]] = []
    for name, emb in zip(names, embs):
        out.append({
            "concept_id": _slug(name),
            "name": name,
            "alt_labels": [],
            "description": "",
            "embedding": emb,
        })
    return out

# ---------- LLM-Aufruf für Konzepte + Absatz-Zuordnung ----------
def _ask_llm_for_concepts(
    title: str,
    paragraphs: List[Dict[str, Any]],
    topic_hint: str | None,
    max_concepts: int,
    seed_names: List[str] | None,
    allow_new: bool
) -> dict:
    """
    Erwartet JSON:
      { "concepts":[{name, alt_labels[], description}],
        "links":[{paragraph_id, concept_name, confidence}] }
    Wenn seed_names gesetzt: Nutzer-Seedliste wird prominent vorgegeben.
      - allow_new = False -> NUR in diese Namen mappen, KEINE neuen Konzepte erzeugen.
      - allow_new = True  -> Seeds bevorzugen, aber neue Konzepte zulassen.
    """
    sys = (
        "Du extrahierst Konzepte (Themen/Begriffe) aus wissenschaftlichen Texten.\n"
        "Liefere kompakt JSON:\n"
        "{\n"
        '  "concepts":[{"name":"...", "alt_labels":["..."], "description":"..."}],\n'
        '  "links":[{"paragraph_id":"...", "concept_name":"...", "confidence":0.0}]\n'
        "}\n"
        f"Maximal {max_concepts} Konzepte, nutze kanonische Namen, alt_labels für Synonyme."
    )
    seeds_txt = ", ".join(seed_names or [])
    seed_rule = (
        "Nutze AUSSCHLIESSLICH diese vorgegebenen Konzepte (keine neuen erzeugen): "
        if (seed_names and not allow_new) else
        "Bevorzuge diese vorgegebenen Konzepte; wenn sinnvoll, darfst du ergänzen: "
    )
    instr = seed_rule + (seeds_txt if seeds_txt else "[keine Seeds]")

    short_paras = []
    for p in paragraphs[:80]:  # Cap Kontext
        text = p.get("text","")
        if len(text) > 500:
            text = text[:500] + " ..."
        short_paras.append({"paragraph_id": p["paragraph_id"], "text": text})

    user = {
        "title": title,
        "topic_hint": topic_hint or "",
        "seed_policy": instr,
        "paragraphs": short_paras
    }
    resp = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {"role":"system","content":sys},
            {"role":"user","content":json.dumps(user, ensure_ascii=False)}
        ]
    )
    raw = resp.output_text or "{}"
    try:
        data = json.loads(raw)
        if not isinstance(data, dict): data = {}
    except Exception:
        data = {}
    data.setdefault("concepts", [])
    data.setdefault("links", [])
    return data

# ---------- Hauptfunktion: Konzepte + Links erzeugen ----------
def extract_and_embed_concepts(
    paper_title: str,
    paragraphs: List[Dict[str, Any]],
    topic_hint: str = "Künstliche Intelligenz",
    max_concepts: int = 30,
    seed_names: List[str] | None = None,
    allow_new: bool = True
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Rückgabe:
      concepts: [{concept_id, name, alt_labels, description, embedding}]
      links:    [{paragraph_id, concept_id, confidence}]
    Wenn seed_names gesetzt: Konzepte für Seeds werden immer erzeugt (mit Embedding).
    Bei allow_new=False werden KEINE zusätzlichen Konzepte erzeugt; die Links mappen ausschließlich
    auf Seeds. Bei allow_new=True können LLM-Konzepte hinzukommen.
    """
    # 1) Seeds vorbereiten (mit Embeddings)
    seed_concepts = seed_names_to_concepts(seed_names or [])
    seed_name_to_id = {c["name"].lower(): c["concept_id"] for c in seed_concepts}

    # 2) LLM-Vorschläge + Link-Infos holen
    llm = _ask_llm_for_concepts(
        paper_title, paragraphs, topic_hint, max_concepts, seed_names or [], allow_new=allow_new
    )

    # 3) Konzepte kanonisieren (LLM)
    new_concepts: List[Dict[str, Any]] = []
    new_names: List[str] = []
    for c in llm["concepts"]:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        # Seed bereits vorhanden? -> überspringe als "neu"
        if name.lower() in seed_name_to_id:
            continue
        if not allow_new:
            continue
        alt = c.get("alt_labels") or []
        if isinstance(alt, str): alt = [alt]
        desc = c.get("description") or ""
        new_concepts.append({"concept_id": _slug(name), "name": name, "alt_labels": alt, "description": desc})
        new_names.append(name + ("; " + ", ".join(alt) if alt else ""))

    # 4) Embeddings für neue Konzepte
    new_embs = _embed(new_names)
    for i, e in enumerate(new_embs):
        new_concepts[i]["embedding"] = e

    # 5) Gesamtkonzepte
    concepts = seed_concepts + new_concepts
    by_name = {c["name"].lower(): c for c in concepts}
    # match alt_labels auch
    alt_map = {}
    for c in concepts:
        for a in c.get("alt_labels") or []:
            alt_map[a.lower()] = c

    # 6) Links Paragraph -> Concept (per Name matchen)
    links: List[Dict[str, Any]] = []
    for l in llm["links"]:
        pid = l.get("paragraph_id")
        cname = (l.get("concept_name") or "").lower()
        if not (pid and cname):
            continue
        c_obj = by_name.get(cname) or alt_map.get(cname)
        if not c_obj:
            # wenn nur Seeds erlaubt, keine neuen Zuordnungen
            if not allow_new:
                continue
            # Falls LLM einen Namen linkt, den es NICHT in concepts packte (selten) -> ignoriere
            continue
        conf = float(l.get("confidence") or 0.7)
        links.append({"paragraph_id": pid, "concept_id": c_obj["concept_id"], "confidence": conf})

    return concepts, links
