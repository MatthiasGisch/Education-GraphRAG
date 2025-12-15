# src/concept_extract.py
from __future__ import annotations
from typing import List, Dict, Any, Tuple, Optional
import os, re, uuid, json
from openai import OpenAI

# Import our new hybrid extraction module
from .entity_relation_extract import extract_entities_and_relations

# Avoid circular imports by using string type annotation for Neo4jClient
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .neo import Neo4jClient

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-large")
client = OpenAI()

# JSON fence extractor (used to robustly parse LLM output)
_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_BRACE_JSON_RE = re.compile(r"(\{.*\})", re.DOTALL)

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
def _try_parse_llm_response(raw: str) -> dict:
    """Parse LLM response with fallback strategies for robustness."""
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
    """
    Erwartet JSON:
      { "concepts":[{name, alt_labels[], description}],
        "links":[{paragraph_id, concept_name, confidence}] }
    Wenn seed_names gesetzt: Nutzer-Seedliste wird prominent vorgegeben.
      - allow_new = False -> NUR in diese Namen mappen, KEINE neuen Konzepte erzeugen.
      - allow_new = True  -> Seeds bevorzugen, aber neue Konzepte zulassen.
    """
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
    resp = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {"role":"system","content":sys},
            {"role":"user","content":json.dumps(user, ensure_ascii=False)}
        ]
    )
    raw = getattr(resp, "output_text", None) or ""

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
    neo_client: Optional['Neo4jClient'] = None,  # For deduping + optional persistence
    dedupe_threshold: float = 0.92,  # Similarity threshold for concept deduping
    min_confidence: float = 0.0,  # Min confidence for paragraph-concept links
    persist_to_topic: bool = False  # If True: automatically upsert Topic & add concepts
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Rückgabe:
      concepts: [{concept_id, name, alt_labels, description, embedding}]
      links:    [{paragraph_id, concept_id, confidence}]
    Wenn seed_names gesetzt: Konzepte für Seeds werden immer erzeugt (mit Embedding).
    Bei allow_new=False werden KEINE zusätzlichen Konzepte erzeugt; die Links mappen ausschließlich
    auf Seeds. Bei allow_new=True können LLM-Konzepte hinzukommen.

    Parameters:
        neo_client: Optional Neo4jClient for checking existing concepts
        dedupe_threshold: Similarity threshold for merging with existing concepts
        min_confidence: Minimum confidence required for paragraph-concept links
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
                            except Exception:
                                mapped_existing[name_l] = existing_id
                            mapped_to_existing = True
                except Exception:
                    # on error, fall back to creating the concept
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
            if not allow_new:
                continue
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
        except Exception:
            # Silent failure (GUI/CLI should not crash); still return concepts for preview
            pass

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
    neo_client: Optional['Neo4jClient'] = None,
    persist_to_topic: bool = False
) -> Dict[str, Any]:
    """
    SIMPLIFIED approach: Extract concepts ONLY, skip paragraph-linking.
    Paragraph-linking happens during retrieval via vector similarity (better anyway).
    This is FAST and RELIABLE.
    
    Returns concepts without paragraph_links.
    """
    import logging
    log = logging.getLogger(__name__)
    
    log.info(f"Extracting concepts for: {paper_title}")
    
    # Extract concepts from paper using LLM (single call)
    sys_prompt = (
        "Extract key concepts from academic text. "
        "Return JSON: {\"concepts\":[{\"name\":\"...\",\"description\":\"...\"}]}"
    )
    
    # Use first 2000 chars of paper text for concept extraction
    sample_text = f"{paper_title}\n\n{paper_text[:2000]}"
    
    user_msg = f"""Extract maximum {max_entities} key concepts.

Text:
{sample_text}

JSON only, no explanation."""
    
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_msg}
            ],
            temperature=0.1,
            timeout=60,
            max_tokens=1000
        )
        concepts_raw = resp.choices[0].message.content
        concepts_data = _try_parse_llm_response(concepts_raw)
        concept_list = concepts_data.get("concepts", [])
        log.info(f"LLM returned {len(concept_list)} concepts")
    except Exception as e:
        log.warning(f"Concept extraction failed: {e}")
        concept_list = []
    
    # Convert to concept objects with embeddings
    concepts = []
    seen_names = set()
    
    for c in concept_list:
        name = c.get("name", "").strip()
        if not name or name in seen_names or len(name) < 2:
            continue
        seen_names.add(name)
        
        description = c.get("description", "").strip()[:200]  # Limit description
        
        # Embed concept name
        emb = _embed([name])[0] if name else []
        
        concepts.append({
            "concept_id": _slug(name),
            "name": name,
            "type": "concept",
            "source": "llm",
            "description": description,
            "alt_labels": [],
            "embedding": emb
        })
    
    log.info(f"Created {len(concepts)} concept objects with embeddings")
    
    # NO paragraph linking - happens during retrieval via vector similarity
    # This is faster and actually works better
    paragraph_links = []
    
    # Persist concepts to Neo4j
    if persist_to_topic and neo_client and topic_hint:
        try:
            neo_client.upsert_topic(topic_hint)
            if concepts:
                neo_client.add_concepts(topic_hint, concepts)
                log.info(f"Persisted {len(concepts)} concepts")
        except Exception as e:
            log.warning(f"Neo4j persistence failed: {e}")
    
    return {
        "concepts": concepts,
        "relations": [],
        "paragraph_links": paragraph_links,  # Empty - linking happens at retrieval time
        "stats": {
            "total_paragraphs": len(paragraphs),
            "concepts_extracted": len(concepts),
            "paragraph_links": 0,  # N/A for this approach
            "note": "Paragraph linking happens during retrieval via vector similarity"
        }
    }

