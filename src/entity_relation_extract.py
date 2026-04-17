# src/entity_relation_extract.py
"""
Hybrid Entity & Relation Extraction
Combines NER (spaCy/SciSpacy) with LLM-based extraction for robust concept and relation discovery.
"""
from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple
import os
import re
import json
import logging
log = logging.getLogger(__name__)

from . import config as cfg
from .openai_client import embed_text, _chat_client, _chat_model

# Lazy-load spaCy models to avoid import-time failures
_SPACY_NLP = None
_SCISPACY_NLP = None

# Regex für robustes JSON-Parsing aus LLM-Antworten (auch bei Präambel / Markdown-Blöcken)
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.DOTALL)
_BRACE_JSON_RE = re.compile(r"(\{[\s\S]*\})", re.DOTALL)


def _parse_json_robust(text: str) -> dict:
    """
    Versucht JSON aus einem LLM-Response-String zu extrahieren.
    Funktioniert auch wenn das Modell Präambeln, Markdown-Blöcke oder
    Erklärungstext um das JSON herum ausgibt (häufig bei lokalen Modellen).
    """
    if not text:
        return {}
    # 1) Direkt parsen (OpenAI-Modelle liefern sauberes JSON)
    try:
        return json.loads(text)
    except Exception:
        pass
    # 2) ```json ... ``` Block extrahieren
    m = _JSON_BLOCK_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 3) Erstes { ... } im Text extrahieren
    m2 = _BRACE_JSON_RE.search(text)
    if m2:
        try:
            return json.loads(m2.group(1))
        except Exception:
            pass
    log.warning("_parse_json_robust: Kein gültiges JSON gefunden. Rohtext: %.200s", text)
    return {}


def _get_spacy_nlp():
    """Lazy-load standard spaCy model."""
    global _SPACY_NLP
    if _SPACY_NLP is None:
        try:
            import spacy
            # Try to load model; if not installed, provide helpful error
            try:
                _SPACY_NLP = spacy.load("en_core_web_sm")
            except OSError:
                log.warning(
                    "spaCy model 'en_core_web_sm' not found. "
                    "Run: python -m spacy download en_core_web_sm"
                )
                _SPACY_NLP = None
        except ImportError:
            log.warning("spaCy not installed. Install with: pip install spacy")
            _SPACY_NLP = None
    return _SPACY_NLP


def _get_scispacy_nlp():
    """Lazy-load SciSpacy model for scientific text."""
    global _SCISPACY_NLP
    if _SCISPACY_NLP is None:
        try:
            import spacy
            try:
                _SCISPACY_NLP = spacy.load("en_core_sci_sm")
            except OSError:
                log.warning(
                    "SciSpacy model 'en_core_sci_sm' not found. "
                    "Run: pip install https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/releases/v0.5.4/en_core_sci_sm-0.5.4.tar.gz"
                )
                _SCISPACY_NLP = None
        except ImportError:
            log.warning("SciSpacy not installed. Install with: pip install scispacy")
            _SCISPACY_NLP = None
    return _SCISPACY_NLP


def _embed(texts: List[str]) -> List[List[float]]:
    """Generate embeddings for a list of texts."""
    if not texts:
        return []
    try:
        return [embed_text(t) for t in texts]
    except Exception as e:
        log.error(f"Embedding failed: {e}")
        dim = cfg.LMSTUDIO_EMBED_DIM if cfg.LLM_MODE == "local" else 3072
        return [[0.0] * dim] * len(texts)  # fallback


# =============================================================================
# 1. HYBRID ENTITY EXTRACTION (NER + LLM)
# =============================================================================

def extract_entities_ner(text: str, use_scispacy: bool = True) -> Dict[str, List[str]]:
    """
    Extract entities using spaCy/SciSpacy NER.
    
    Returns:
        Dictionary with entity types as keys and lists of entity texts as values.
        Example: {
            "PERSON": ["Einstein", "Curie"],
            "ORG": ["MIT", "NASA"],
            "SCIENTIFIC_TERM": ["neural network", "gradient descent"],
            ...
        }
    """
    entities = {
        "PERSON": [],
        "ORG": [],
        "GPE": [],  # Geopolitical entities (countries, cities)
        "DATE": [],
        "SCIENTIFIC_TERM": [],
        "CHEMICAL": [],
        "DISEASE": [],
        "OTHER": []
    }
    
    # Try SciSpacy first for scientific papers
    nlp = _get_scispacy_nlp() if use_scispacy else None
    
    # Fallback to standard spaCy
    if nlp is None:
        nlp = _get_spacy_nlp()
    
    if nlp is None:
        log.warning("No spaCy model available, skipping NER extraction")
        return entities
    
    try:
        # Process text (limit to reasonable length to avoid memory issues)
        text_truncated = text[:100000] if len(text) > 100000 else text
        doc = nlp(text_truncated)
        
        for ent in doc.ents:
            label = ent.label_
            text_clean = ent.text.strip()
            
            if not text_clean or len(text_clean) < 2:
                continue
            
            # Map labels to our categories
            if label == "PERSON":
                entities["PERSON"].append(text_clean)
            elif label == "ORG":
                entities["ORG"].append(text_clean)
            elif label == "GPE":
                entities["GPE"].append(text_clean)
            elif label == "DATE":
                entities["DATE"].append(text_clean)
            elif label in ["CHEMICAL", "DRUG"]:
                entities["CHEMICAL"].append(text_clean)
            elif label == "DISEASE":
                entities["DISEASE"].append(text_clean)
            elif label in ["PRODUCT", "WORK_OF_ART", "LAW", "LANGUAGE"]:
                entities["SCIENTIFIC_TERM"].append(text_clean)
            else:
                entities["OTHER"].append(text_clean)
        
        # Deduplicate
        for key in entities:
            entities[key] = list(set(entities[key]))
        
    except Exception as e:
        log.error(f"NER extraction failed: {e}")
    
    return entities


def extract_concepts_llm(
    text: str,
    max_concepts: int = 15,
    existing_entities: Optional[Dict[str, List[str]]] = None
) -> List[Dict[str, Any]]:
    """
    Extract abstract concepts using LLM that NER might miss.
    
    Args:
        text: Text to analyze
        max_concepts: Maximum number of concepts to extract
        existing_entities: Already extracted entities from NER (to avoid duplication)
    
    Returns:
        List of concept dicts: [{"name": str, "type": str, "description": str}, ...]
    """
    # Build prompt with context about already found entities
    ner_context = ""
    if existing_entities:
        all_found = []
        for ent_type, ent_list in existing_entities.items():
            if ent_list:
                all_found.extend(ent_list[:5])  # Sample
        if all_found:
            ner_context = f"\nAlready identified entities (don't repeat): {', '.join(all_found[:10])}"
    
    system_prompt = f"""You are an experienced educator and expert at extracting key concepts from scientific text to create learning materials.
Extract abstract concepts, methodologies, theories, and technical terms that are central to understanding the text.
Focus on domain-specific concepts that NER systems typically miss.{ner_context}

For each concept, provide a brief, didactic description suitable for students learning the topic.

IMPORTANT: Respond ONLY with a valid JSON object — no explanation, no markdown, no preamble.

Return JSON format:
{{
  "concepts": [
    {{
      "name": "concept name",
      "type": "methodology|theory|technique|domain_term|abstract_concept",
      "description": "clear, educational description for students"
    }}
  ]
}}

Extract up to {max_concepts} concepts. Be selective and focus on the most important ones."""

    # Truncate text for API efficiency
    text_sample = text[:4000] if len(text) > 4000 else text

    # response_format nur bei OpenAI (cloud) setzen – lokale Modelle unterstützen es oft nicht
    extra_kwargs: dict = {}
    if cfg.LLM_MODE != "local":
        extra_kwargs["response_format"] = {"type": "json_object"}

    try:
        response = _chat_client().chat.completions.create(
            model=_chat_model(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Extract key concepts from this text:\n\n{text_sample}"}
            ],
            temperature=0.3,
            **extra_kwargs
        )

        result_text = (response.choices[0].message.content or "").strip()
        data = _parse_json_robust(result_text)

        concepts = data.get("concepts", [])

        # Validate and clean
        valid_concepts = []
        for c in concepts:
            if isinstance(c, dict) and "name" in c:
                valid_concepts.append({
                    "name": c["name"].strip(),
                    "type": c.get("type", "domain_term"),
                    "description": c.get("description", "").strip()
                })

        return valid_concepts[:max_concepts]

    except Exception as e:
        log.error(f"LLM concept extraction failed: {e}")
        return []


def extract_entities_hybrid(
    text: str,
    max_llm_concepts: int = 15,
    use_scispacy: bool = True
) -> Dict[str, Any]:
    """
    Hybrid approach: Combine NER (fast, structured) with LLM (semantic, flexible).
    
    Returns:
        {
            "ner_entities": {...},  # From spaCy/SciSpacy
            "llm_concepts": [...],  # From LLM
            "all_entities": [...]   # Merged and deduplicated list
        }
    """
    # Step 1: NER extraction
    ner_entities = extract_entities_ner(text, use_scispacy=use_scispacy)
    
    # Step 2: LLM concept extraction (aware of NER results)
    llm_concepts = extract_concepts_llm(text, max_concepts=max_llm_concepts, existing_entities=ner_entities)
    
    # Step 3: Merge into unified entity list
    all_entities = []
    
    # Define which NER entity types to include (exclude PERSON, DATE, GPE for scientific concepts)
    # Only include scientific/technical entities from NER
    RELEVANT_NER_TYPES = {"SCIENTIFIC_TERM", "CHEMICAL", "DISEASE", "ORG"}
    
    # Filter to exclude trivial entities (single chars, numbers, etc.)
    def is_valid_entity(name: str) -> bool:
        """Check if entity name is meaningful enough to be a concept."""
        name = name.strip()
        # Exclude too short
        if len(name) < 3:
            return False
        # Exclude pure numbers or dates
        if name.replace('.', '').replace(',', '').replace('/', '').replace('%', '').replace('-', '').isdigit():
            return False
        # Exclude single letters/numbers with special chars
        if len(name) <= 4 and any(c.isdigit() for c in name):
            return False
        return True
    
    # Add NER entities (ONLY relevant types and valid names)
    for ent_type, ent_list in ner_entities.items():
        if ent_type not in RELEVANT_NER_TYPES:
            continue  # Skip PERSON, DATE, GPE, etc.
        
        for entity in ent_list:
            if is_valid_entity(entity):
                all_entities.append({
                    "name": entity,
                    "type": ent_type.lower(),
                    "source": "ner",
                    "description": ""
                })
    
    # Add LLM concepts (with validation)
    for concept in llm_concepts:
        if is_valid_entity(concept["name"]):
            all_entities.append({
                "name": concept["name"],
                "type": concept.get("type", "concept"),
                "source": "llm",
                "description": concept.get("description", "")
            })
    
    # Deduplicate by name (case-insensitive)
    seen = set()
    unique_entities = []
    for ent in all_entities:
        name_lower = ent["name"].lower()
        if name_lower not in seen:
            seen.add(name_lower)
            unique_entities.append(ent)
    
    return {
        "ner_entities": ner_entities,
        "llm_concepts": llm_concepts,
        "all_entities": unique_entities
    }


# =============================================================================
# 2. RELATION EXTRACTION (TRIPLETS)
# =============================================================================

def extract_relations_llm(
    text: str,
    entities: List[Dict[str, Any]],
    max_relations: int = 20
) -> List[Dict[str, Any]]:
    """
    Extract semantic relations between entities as (subject, predicate, object) triplets.
    
    Args:
        text: Text to analyze
        entities: List of entities found in the text
        max_relations: Maximum number of relations to extract
    
    Returns:
        List of relation dicts: [
            {
                "subject": str,
                "predicate": str,
                "object": str,
                "confidence": float,
                "context": str  # sentence where relation was found
            },
            ...
        ]
    """
    if not entities:
        return []
    
    # Prepare entity list for prompt
    entity_names = [e["name"] for e in entities[:30]]  # Limit for API
    entity_list_str = ", ".join(entity_names)
    
    system_prompt = f"""You are an experienced educator and expert at extracting semantic relationships from scientific text to create learning materials.
Identify relationships between entities that help students understand connections and dependencies between concepts.

Format: (Subject, Predicate, Object)

Example relations:
- ("Deep Learning", "is_a", "Machine Learning")
- ("Neural Networks", "uses", "Backpropagation")
- ("Albert Einstein", "developed", "Theory of Relativity")
- ("Attention Mechanism", "improves", "Transformer Performance")

Focus on meaningful, factual relationships that are pedagogically valuable. Use clear, standardized predicates like:
- is_a, part_of, uses, requires, causes, leads_to, improves, evaluates, applies_to, based_on, extends

For context, include the exact sentence or phrase that expresses this relationship - this helps students see the relation in context.

IMPORTANT: Respond ONLY with a valid JSON object — no explanation, no markdown, no preamble.

Return JSON format:
{{
  "relations": [
    {{
      "subject": "entity1",
      "predicate": "relationship_type",
      "object": "entity2",
      "confidence": 0.9,
      "context": "sentence or phrase where this relation appears"
    }}
  ]
}}

Extract up to {max_relations} most important relations for learning purposes."""

    # Truncate text
    text_sample = text[:5000] if len(text) > 5000 else text

    user_prompt = f"""Text to analyze:
{text_sample}

Entities found:
{entity_list_str}

Extract semantic relationships between these entities."""

    # response_format nur bei OpenAI (cloud) setzen – lokale Modelle unterstützen es oft nicht
    extra_kwargs: dict = {}
    if cfg.LLM_MODE != "local":
        extra_kwargs["response_format"] = {"type": "json_object"}

    try:
        response = _chat_client().chat.completions.create(
            model=_chat_model(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
            **extra_kwargs
        )

        result_text = (response.choices[0].message.content or "").strip()
        data = _parse_json_robust(result_text)

        relations = data.get("relations", [])

        # Validate and clean
        valid_relations = []
        for r in relations:
            if isinstance(r, dict) and all(k in r for k in ["subject", "predicate", "object"]):
                valid_relations.append({
                    "subject": r["subject"].strip(),
                    "predicate": r["predicate"].strip().lower().replace(" ", "_"),
                    "object": r["object"].strip(),
                    "confidence": float(r.get("confidence", 0.8)),
                    "context": r.get("context", "").strip()[:200]
                })

        return valid_relations[:max_relations]

    except Exception as e:
        log.error(f"LLM relation extraction failed: {e}")
        return []


def extract_cooccurrence_relations(
    entities: List[Dict[str, Any]],
    paragraphs: List[Dict[str, Any]],
    window_size: int = 50
) -> List[Dict[str, Any]]:
    """
    Extract co-occurrence based relations (entities appearing near each other).
    
    Args:
        entities: List of entities
        paragraphs: List of paragraph dicts with 'text' field
        window_size: Character window for co-occurrence
    
    Returns:
        List of co-occurrence relations with weights
    """
    from collections import defaultdict
    
    cooccur = defaultdict(int)
    entity_names = {e["name"].lower() for e in entities}
    
    for para in paragraphs:
        text = para.get("text", "").lower()
        
        # Find all entity positions in text
        found_entities = []
        for entity in entities:
            name = entity["name"].lower()
            pos = text.find(name)
            while pos != -1:
                found_entities.append((name, pos))
                pos = text.find(name, pos + 1)
        
        # Check co-occurrence within window
        found_entities.sort(key=lambda x: x[1])
        for i, (ent1, pos1) in enumerate(found_entities):
            for ent2, pos2 in found_entities[i+1:]:
                if pos2 - pos1 > window_size:
                    break
                # Create canonical pair (alphabetically sorted)
                pair = tuple(sorted([ent1, ent2]))
                cooccur[pair] += 1
    
    # Convert to relation list
    relations = []
    for (ent1, ent2), count in cooccur.items():
        if count >= 2:  # Minimum co-occurrence threshold
            relations.append({
                "subject": ent1,
                "predicate": "co_occurs_with",
                "object": ent2,
                "confidence": min(0.9, 0.5 + (count * 0.1)),  # Weight by frequency
                "context": f"Co-occurred {count} times"
            })
    
    return relations


# =============================================================================
# 3. INTEGRATED EXTRACTION PIPELINE
# =============================================================================

def extract_entities_and_relations(
    text: str,
    paragraphs: Optional[List[Dict[str, Any]]] = None,
    max_entities: int = 30,
    max_relations: int = 20,
    use_scispacy: bool = True,
    extract_cooccurrence: bool = True
) -> Dict[str, Any]:
    """
    Complete pipeline: Extract entities (hybrid NER+LLM) and relations (LLM+co-occurrence).
    
    Args:
        text: Full text to analyze
        paragraphs: Optional list of paragraph dicts for co-occurrence analysis
        max_entities: Maximum entities to extract via LLM
        max_relations: Maximum relations to extract
        use_scispacy: Use SciSpacy for scientific text
        extract_cooccurrence: Also extract co-occurrence relations
    
    Returns:
        {
            "entities": [...],  # All extracted entities with embeddings
            "relations": [...],  # All extracted relations
            "stats": {...}  # Extraction statistics
        }
    """
    log.info("Starting hybrid entity and relation extraction...")
    
    # Step 1: Extract entities (NER + LLM)
    entity_result = extract_entities_hybrid(
        text,
        max_llm_concepts=max_entities,
        use_scispacy=use_scispacy
    )
    
    entities = entity_result["all_entities"]
    log.info(f"Extracted {len(entities)} entities ({len(entity_result['ner_entities'])} from NER, {len(entity_result['llm_concepts'])} from LLM)")
    
    # Step 2: Generate embeddings for entities
    entity_names = [e["name"] for e in entities]
    embeddings = _embed(entity_names)
    
    for entity, emb in zip(entities, embeddings):
        entity["embedding"] = emb
    
    # Step 3: Extract semantic relations (LLM)
    semantic_relations = extract_relations_llm(text, entities, max_relations=max_relations)
    log.info(f"Extracted {len(semantic_relations)} semantic relations")
    
    # Step 4: Extract co-occurrence relations (optional)
    cooccur_relations = []
    if extract_cooccurrence and paragraphs:
        cooccur_relations = extract_cooccurrence_relations(entities, paragraphs)
        log.info(f"Extracted {len(cooccur_relations)} co-occurrence relations")
    
    # Merge relations
    all_relations = semantic_relations + cooccur_relations
    
    # Deduplicate relations (same subject-predicate-object)
    seen_rels = set()
    unique_relations = []
    for rel in all_relations:
        key = (rel["subject"].lower(), rel["predicate"], rel["object"].lower())
        if key not in seen_rels:
            seen_rels.add(key)
            unique_relations.append(rel)
    
    stats = {
        "total_entities": len(entities),
        "ner_entities": sum(len(v) for v in entity_result["ner_entities"].values()),
        "llm_concepts": len(entity_result["llm_concepts"]),
        "semantic_relations": len(semantic_relations),
        "cooccurrence_relations": len(cooccur_relations),
        "total_relations": len(unique_relations)
    }
    
    return {
        "entities": entities,
        "relations": unique_relations,
        "stats": stats
    }
