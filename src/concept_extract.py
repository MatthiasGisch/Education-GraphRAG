"""Konzeptextraktion: LLM-basierte und hybride Methoden zur Erzeugung von Konzept-Embeddings und Paragraph-Links."""
from __future__ import annotations
from typing import List, Dict, Any, Tuple, Optional
import os, re, uuid, json, logging
import numpy as np
log = logging.getLogger(__name__)

# Ähnlichkeitsschwellwert für Embedding-basierte Paragraph→Concept-Links
EMB_LINK_THRESHOLD = 0.50

# Import our new hybrid extraction module
from .entity_relation_extract import extract_entities_and_relations

# Avoid circular imports by using string type annotation for Neo4jClient
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .neo import Neo4jClient

from .openai_client import embed_text, _chat_client, _chat_model

# JSON fence extractor (used to robustly parse LLM output)
_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_BRACE_JSON_RE = re.compile(r"(\{.*\})", re.DOTALL)

# ---------- Utils ----------
def _slug(s: str) -> str:
    """Normalisiert einen String zu einem URL-sicheren Slug für Konzept-IDs."""
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:80] or str(uuid.uuid4())

def _embed(texts: List[str]) -> List[List[float]]:
    """Erstellt Embedding-Vektoren für eine Liste von Texten."""
    if not texts:
        return []
    return [embed_text(t) for t in texts]

# ---------- Public: Seeds zu Concept-Objekten (mit Embedding) ----------
def seed_names_to_concepts(seed_names: List[str]) -> List[Dict[str, Any]]:
    """Konvertiert eine Liste von Seed-Namen in Konzept-Dicts mit Embeddings."""
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
def _try_parse_llm_response(raw: str) -> dict:
    """Parst die LLM-Antwort robust mit mehreren Fallback-Strategien zu einem Dict."""
    if not raw or raw.isspace():
        return {"concepts": [], "links": []}

    # Strategy 1: Direct JSON parse
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            data.setdefault("concepts", [])
            data.setdefault("links", [])
            return data
    except json.JSONDecodeError:
        pass

    # Strategy 2: Try to fix common JSON issues
    cleaned = re.sub(r'(?m)^\s*//.*\n?', '', raw)  # Remove comments
    cleaned = re.sub(r'(?m)^\s*#.*\n?', '', cleaned)  # Remove Python-style comments
    cleaned = re.sub(r',\s*}', '}', cleaned)  # Remove trailing commas
    cleaned = re.sub(r',\s*\]', ']', cleaned)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            data.setdefault("concepts", [])
            data.setdefault("links", [])
            return data
    except json.JSONDecodeError:
        pass

    # Strategy 3: Extract JSON-like content if wrapped in text
    json_match = re.search(r'\{[\s\S]*\}', raw)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            if isinstance(data, dict):
                data.setdefault("concepts", [])
                data.setdefault("links", [])
                return data
        except json.JSONDecodeError:
            pass

    # Fallback: Return empty structure
    return {"concepts": [], "links": []}

def _ask_llm_for_concepts(
    title: str,
    paragraphs: List[Dict[str, Any]],
    topic_hint: str | None,
    max_concepts: int,
    seed_names: List[str] | None,
    allow_new: bool
) -> dict:
    """Fragt das LLM nach Konzepten und Paragraph-Links; gibt JSON mit concepts und links zurück."""
    sys = (
        "Du bist ein erfahrener Dozent, der Konzepte (Themen/Begriffe) aus wissenschaftlichen Texten extrahiert, "
        "um daraus Lernmaterial für Studierende zu erstellen.\n"
        "Liefere kompakt JSON:\n"
        "{\n"
        '  "concepts":[{"name":"...", "alt_labels":["..."], "description":"kurze, didaktische Erklärung"}],\n'
        '  "links":[{"paragraph_id":"...", "concept_name":"...", "confidence":0.0}]\n'
        "}\n"
        f"Maximal {max_concepts} Konzepte, nutze kanonische Namen, alt_labels für Synonyme. "
        "Beschreibungen sollten für Studierende verständlich formuliert sein."
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
    resp = _chat_client().chat.completions.create(
        model=_chat_model(),
        messages=[
            {"role":"system","content":sys},
            {"role":"user","content":json.dumps(user, ensure_ascii=False)}
        ],
        temperature=0.2,
    )
    raw = (resp.choices[0].message.content or "") if resp.choices else ""

    # 1) Try explicit ```json { ... } ``` block
    m = _JSON_BLOCK_RE.search(raw)
    if m:
        payload = m.group(1)
    else:
        # 2) Try to find any JSON-like {...} substring
        m2 = _BRACE_JSON_RE.search(raw)
        payload = m2.group(1) if m2 else None

    data = {}
    if payload:
        try:
            data = json.loads(payload)
        except Exception:
            data = {}
    else:
        # 3) Last-resort: try to parse the whole output as JSON
        try:
            data = json.loads(raw)
        except Exception:
            data = {}

    if not isinstance(data, dict):
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
    allow_new: bool = True,
    neo_client: Optional['Neo4jClient'] = None,
    dedupe_threshold: float = 0.92,
    min_confidence: float = 0.0,
    persist_to_topic: bool = False
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Extrahiert Konzepte mit Embeddings per LLM und erzeugt Paragraph-Konzept-Links."""
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

    # 3b) Aggregate mention counts & confidences from LLM links (used to decide creation)
    mention_counts: Dict[str, int] = {}
    mention_conf_sums: Dict[str, float] = {}
    for link in llm.get("links", []):
        cname = (link.get("concept_name") or "").lower()
        if not cname:
            continue
        mention_counts[cname] = mention_counts.get(cname, 0) + 1
        mention_conf_sums[cname] = mention_conf_sums.get(cname, 0.0) + float(link.get("confidence") or 0.0)

    # 4) Embeddings für neue Konzepte + Dedupe gegen bestehende Konzepte (optional)
    created_new_concepts: List[Dict[str, Any]] = []
    mapped_existing: dict = {}  # name.lower() -> existing concept_id

    if new_names:
        new_embs = _embed(new_names)
        for i, e in enumerate(new_embs):
            new_concepts[i]["embedding"] = e
            name_l = new_concepts[i]["name"].lower()

            # Apply mention/confidence thresholds: only consider creating if
            # mention_counts >= min_mentions OR avg_confidence >= min_confidence
            mentions = mention_counts.get(name_l, 0)
            avg_conf = (mention_conf_sums.get(name_l, 0.0) / mentions) if mentions > 0 else 0.0
            create_allowed = (avg_conf >= min_confidence)
            if not create_allowed:
                # If the concept doesn't meet thresholds, skip creating it (but keep it in suggestions)
                # Still include in new_concepts (with embedding) so UI can preview
                continue

            # Dedupe: wenn ein Neo4j-Client vorhanden ist, suche ähnliche Konzepte
            mapped_to_existing = False
            if neo_client is not None:
                try:
                    matches = []
                    if hasattr(neo_client, 'vector_search_concepts'):
                        matches = neo_client.vector_search_concepts(e, k=1) or []
                    elif hasattr(neo_client, 'run'):
                        matches = neo_client.run(
                            "CALL db.index.vector.queryNodes('concept_embedding_index', 1, $embedding) YIELD node, score RETURN node.concept_id AS concept_id, node.name AS name, toFloat(score) AS score",
                            {"embedding": e}
                        ) or []
                    if matches:
                        top = matches[0]
                        score = float(top.get('score') or 0.0)
                        if score >= float(dedupe_threshold):
                            existing_id = top.get('concept_id')
                            # fetch canonical info if possible
                            try:
                                info = neo_client.run(
                                    "MATCH (c:Concept {concept_id:$id}) RETURN c.concept_id AS concept_id, c.name AS name, c.alt_labels AS alt_labels, c.description AS description",
                                    {"id": existing_id}
                                )
                                if info:
                                    mapped_existing[name_l] = info[0].get('concept_id')
                                else:
                                    mapped_existing[name_l] = existing_id
                            except Exception as e:
                                log.warning("Neo4j canonical fetch failed: %s", e)
                                mapped_existing[name_l] = existing_id
                            mapped_to_existing = True
                except Exception as e:
                    log.warning("Concept deduplication failed, creating new: %s", e)
                    mapped_to_existing = False

            if not mapped_to_existing:
                # schedule for creation
                created_new_concepts.append(new_concepts[i])

    # 5) Gesamtkonzepte: Seeds + tatsächlich neu zu erzeugende Konzepte + Platzhalter für gemappte bestehende
    concepts = seed_concepts.copy()
    concepts.extend(created_new_concepts)
    for nm, cid in mapped_existing.items():
        concepts.append({"concept_id": cid, "name": nm, "alt_labels": [], "description": "(mapped existing)"})

    # name -> concept mapping (inkl. alt_labels)
    by_name = {c["name"].lower(): c for c in concepts}
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
            continue
        conf = float(l.get("confidence") or 0.7)
        links.append({"paragraph_id": pid, "concept_id": c_obj["concept_id"], "confidence": conf})

    # 7) Optional: Persist concepts directly to Neo4j under the given topic
    if persist_to_topic and neo_client and topic_hint:
        try:
            neo_client.upsert_topic(topic_hint)
            if concepts:
                # MERGE semantics inside add_concepts prevent duplicates
                neo_client.add_concepts(topic_hint, concepts)
        except Exception as e:
            log.warning("Failed to persist concepts to Neo4j (non-fatal): %s", e)

    return concepts, links


# =============================================================================
# NEW: HYBRID EXTRACTION WRAPPER (NER + LLM + Relations)
# =============================================================================

def extract_and_embed_concepts_hybrid(
    paper_title: str,
    paper_text: str,
    paragraphs: List[Dict[str, Any]],
    topic_hint: str = "Künstliche Intelligenz",
    max_entities: int = 30,
    max_relations: int = 20,
    use_scispacy: bool = True,
    neo_client: Optional['Neo4jClient'] = None,
    persist_to_topic: bool = False,
    progress_fn=None,
) -> Dict[str, Any]:
    """Hybride Konzextextraktion via NER + LLM; erzeugt Konzepte, Relationen und Paragraph-Links."""
    log.info("extract_and_embed_concepts_hybrid: starting for '%s'", paper_title)
    if progress_fn:
        progress_fn("NER + LLM Extraktion läuft …", 0)

    # ------------------------------------------------------------------
    # Step 1: Full NER + LLM entity/relation extraction
    # ------------------------------------------------------------------
    try:
        extraction = extract_entities_and_relations(
            text=paper_text,
            paragraphs=paragraphs,
            max_entities=max_entities,
            max_relations=max_relations,
            use_scispacy=use_scispacy,
            extract_cooccurrence=True,
        )
    except Exception as e:
        log.warning("extract_entities_and_relations failed, falling back to empty: %s", e)
        extraction = {"entities": [], "relations": [], "stats": {}}

    raw_entities: List[Dict[str, Any]] = extraction.get("entities") or []
    relations:    List[Dict[str, Any]] = extraction.get("relations") or []
    ext_stats:    Dict[str, Any]       = extraction.get("stats") or {}

    log.info(
        "Extraction done: %d entities, %d relations",
        len(raw_entities), len(relations),
    )
    if progress_fn:
        progress_fn(
            f"{len(raw_entities)} Entitäten, {len(relations)} Relationen extrahiert – Konzepte aufbereiten …",
            40,
        )

    # ------------------------------------------------------------------
    # Step 2: Convert to concept dicts (deduplicate by slug)
    # ------------------------------------------------------------------
    concepts: List[Dict[str, Any]] = []
    seen_slugs: set = set()

    for ent in raw_entities:
        name = (ent.get("name") or "").strip()
        if not name or len(name) < 2:
            continue
        cid = _slug(name)
        if cid in seen_slugs:
            continue
        seen_slugs.add(cid)

        concepts.append({
            "concept_id":  cid,
            "name":        name,
            "type":        ent.get("type", "concept"),
            "source":      ent.get("source", "hybrid"),
            "description": (ent.get("description") or "").strip()[:300],
            "alt_labels":  [],
            "embedding":   ent.get("embedding") or [],
        })

    log.info("Built %d concept objects", len(concepts))
    if progress_fn:
        progress_fn(f"{len(concepts)} Konzepte aufgebaut – Paragraph-Links erstellen …", 60)

    # ------------------------------------------------------------------
    # Step 3: Paragraph → Concept links via substring matching
    # ------------------------------------------------------------------
    # Build a lookup: lowercase name → concept_id
    name_to_id: Dict[str, str] = {c["name"].lower(): c["concept_id"] for c in concepts}

    paragraph_links: List[Dict[str, Any]] = []
    seen_links: set = set()   # (paragraph_id, concept_id)

    for para in paragraphs:
        pid   = para.get("paragraph_id")
        ptext = (para.get("text") or "").lower()
        if not pid or not ptext:
            continue

        for cname_lower, cid in name_to_id.items():
            if len(cname_lower) < 3:
                continue  # Skip very short names to avoid false matches
            if cname_lower in ptext:
                link_key = (pid, cid)
                if link_key not in seen_links:
                    seen_links.add(link_key)
                    paragraph_links.append({
                        "paragraph_id": pid,
                        "concept_id":   cid,
                        "confidence":   0.75,
                    })

    log.info("Substring-matching: %d paragraph→concept links", len(paragraph_links))

    # ------------------------------------------------------------------
    # Step 3b: Embedding-Fallback für Paragraphen ohne Substring-Link
    # ------------------------------------------------------------------
    # Paragraphen die keinen einzigen Link haben, werden über Kosinus-Ähnlichkeit
    # mit den Konzept-Embeddings verknüpft (Schwelle: EMB_LINK_THRESHOLD).
    linked_pids = {lnk["paragraph_id"] for lnk in paragraph_links}
    unlinked_paras = [
        p for p in paragraphs
        if p.get("paragraph_id") not in linked_pids and p.get("embedding")
    ]
    concepts_with_emb = [c for c in concepts if c.get("embedding")]

    if unlinked_paras and concepts_with_emb:
        try:
            # Konzept-Embedding-Matrix (n_concepts × dim), normiert
            c_mat = np.array([c["embedding"] for c in concepts_with_emb], dtype=np.float32)
            c_norms = np.linalg.norm(c_mat, axis=1, keepdims=True)
            c_norms = np.where(c_norms < 1e-9, 1.0, c_norms)
            c_mat_norm = c_mat / c_norms

            emb_links = 0
            for para in unlinked_paras:
                p_emb = np.array(para["embedding"], dtype=np.float32)
                p_norm = np.linalg.norm(p_emb)
                if p_norm < 1e-9:
                    continue  # Zero-Vektor (Embedding fehlgeschlagen) → überspringen
                p_emb_norm = p_emb / p_norm

                # Ähnlichkeit zu allen Konzepten auf einmal (Vektorprodukt)
                sims = c_mat_norm @ p_emb_norm  # shape: (n_concepts,)

                for ci, sim in enumerate(sims):
                    if float(sim) >= EMB_LINK_THRESHOLD:
                        concept = concepts_with_emb[ci]
                        link_key = (para["paragraph_id"], concept["concept_id"])
                        if link_key not in seen_links:
                            seen_links.add(link_key)
                            paragraph_links.append({
                                "paragraph_id": para["paragraph_id"],
                                "concept_id":   concept["concept_id"],
                                "confidence":   round(float(sim), 3),
                            })
                            emb_links += 1

            log.info(
                "Embedding-Fallback: %d zusätzliche Links für %d vorher unverknüpfte Paragraphen",
                emb_links, len(unlinked_paras),
            )
        except Exception as e:
            log.warning("Embedding-Fallback fehlgeschlagen (non-fatal): %s", e)

    log.info("Gesamt paragraph→concept links: %d", len(paragraph_links))
    if progress_fn:
        progress_fn(f"{len(paragraph_links)} Para-Links erstellt – in Neo4j schreiben …", 80)

    # ------------------------------------------------------------------
    # Step 4: (optional) Persist to Neo4j
    # ------------------------------------------------------------------
    if persist_to_topic and neo_client and topic_hint:
        try:
            neo_client.upsert_topic(topic_hint)
            if concepts:
                neo_client.add_concepts(topic_hint, concepts)
                log.info("Persisted %d concepts to topic '%s'", len(concepts), topic_hint)
        except Exception as e:
            log.warning("Neo4j persistence failed (non-fatal): %s", e)

    if progress_fn:
        progress_fn(
            f"Konzeptextraktion fertig: {len(concepts)} Konzepte, "
            f"{len(paragraph_links)} Links, {len(relations)} Relationen",
            100,
        )

    return {
        "concepts": concepts,
        "relations": relations,
        "paragraph_links": paragraph_links,
        "stats": {
            **ext_stats,
            "total_paragraphs":  len(paragraphs),
            "concepts_created":  len(concepts),
            "paragraph_links":   len(paragraph_links),
            "relations_total":   len(relations),
        },
    }

