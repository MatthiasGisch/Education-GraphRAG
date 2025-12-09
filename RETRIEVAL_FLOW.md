# Retrieval-Flow im Course Generator

## Überblick: Wo findet Retrieval statt?

Der Retrieval-Prozess läuft in **3 Hauptdateien** ab:

1. **`src/agent.py`** - Orchestrierung (Entrypoint)
2. **`src/retriever.py`** - Eigentliche Retrieval-Funktionen (VECTOR-BASED!)
3. **`src/openai_client.py`** - LLM-Integration für Antwortgenerierung

**🔄 ARCHITECTURAL CHANGE (2024):**
- Concepts are extracted WITHOUT linking to paragraphs during ingest
- Paragraph-concept matching happens at retrieval time using **vector similarity**
- No more MENTIONS relations in the graph!

---

## 1. Entry Point: `course_generator.py` → `src/agent.py`

### Stelle 1: Kursgenerierung startet Retrieval

**Datei:** `scripts/course_generator.py`, Zeile 72-79

```python
response = answer_query(
    query=query,                    # Der Abschnittstitel als Frage
    neo=neo,                        # Neo4j Datenbankverbindung
    web_mode="off",                 # NUR Wissensgraph, kein Web
    k_paragraphs=48,                # Hole 48 Absätze aus dem Graph
    k_figures=8,                    # Hole 8 Abbildungen
    use_concept_retrieval=True      # Nutze intelligentes Concept-Retrieval (VECTOR-BASED)
)
```

**Was passiert:**
- Für jeden Kursabschnitt wird eine Frage gestellt (z.B. "Was ist Deep Learning?")
- Die Antwort wird aus dem Wissensgraph geholt, NICHT vom Web
- Parameter:
  - `k_paragraphs=48`: Sucht in den Top-48 relevantesten Absätze
  - `k_figures=8`: Sucht in den Top-8 relevantesten Abbildungen
  - `use_concept_retrieval=True`: Nutzt **vector-based concept matching** (semantisch besser als String-Matching!)

---

## 2. Orchestrierung: `src/agent.py` → `answer_query()`

### Stelle 2: Decision-Tree für Retrieval-Strategie

**Datei:** `src/agent.py`, Zeilen 116-220

Die Funktion `answer_query()` entscheidet, WIE das Retrieval ablaufen soll:

```
answer_query(query, neo, web_mode="off")
    ↓
    ├─ web_mode == "force"  → Nur Websuche (überspringe Graph)
    ├─ web_mode == "off"    → NUR Wissensgraph (unser Fall!)
    └─ web_mode == "auto"   → Erst Graph, dann Web-Fallback bei zu wenig Inhalten
```

### Stelle 3: Retrieval im "OFF"-Mode (nur Wissensgraph)

**Datei:** `src/agent.py`, Zeilen 155-176

```python
if (web_mode or "").lower() in {"off", "none", "disabled"}:
    if use_concept_retrieval:
        # ← RETRIEVAL PASSIERT HIER (VECTOR-BASED)
        ret = concept_based_retrieve(  # ← intelligente Concept-Suche via embeddings
            neo, query, 
            k_paragraphs_direct=48,     # Top 48 Absätze direkt
            k_figures=8,
            k_concepts=10,              # Top 10 Konzepte via vector similarity
            k_paragraphs_via_concepts=20, # +20 Absätze via concept embedding averaging
            min_concept_score=0.6       # Minimum similarity score (lowered from 0.7)
        )
    else:
        # ← FALLBACK: einfache Hybrid-Suche
        ret = hybrid_retrieve(neo, query, k_paragraphs=48, k_figures=8)
    
    # Limitiere auf beste 30 Absätze (+ alle Abbildungen)
    supports = paragraphs[:30] + figures
    
    # Generiere Antwort aus den gefundenen Quellen
    answer = grounded_answer(query, supports)
    
    return {"mode": "graph", "answer": answer, "supports": supports, ...}
```

**Was passiert:**
1. Query wird gesendet an `concept_based_retrieve()` (VECTOR-BASED!)
2. Gibt zurück: Liste mit relevanten Absätzen + Abbildungen (via embedding similarity)
3. Top 30 Absätze werden ausgewählt (Filter für Qualität)
4. LLM generiert Antwort basierend auf diesen Quellen

---

## 3. Eigentliches Retrieval: `src/retriever.py` (VECTOR-BASED)

### 🔥 NEW: Vector-Based Concept Retrieval Strategy

**Why vectors instead of MENTIONS relations?**
- **Faster**: No LLM calls per paragraph during ingest (only 1 per paper)
- **Better quality**: Semantic similarity finds related content even without exact name matches
- **More reliable**: No fragile string matching or complex linking logic

### Strategie A: `hybrid_retrieve()` (einfache Variante)

**Datei:** `src/retriever.py`, Zeilen 239-275

```python
def hybrid_retrieve(neo, query, k_paragraphs=48, k_figures=8):
    """
    Einfache Hybrid-Suche (Fallback wenn Concepts nicht aktiv)
    
    Ablauf:
    1) Query → Embedding-Vektor
    2) Vector-Suche in Paragraph-Index
    3) Vector-Suche in Figure-Index
    4) Merge der Ergebnisse
    """
    
    # Schritt 1: Query in Vektor umwandeln
    emb = _embed_query(query)  # OpenAI text-embedding-3-large
    
    # Schritt 2: Vector-Ähnlichkeitssuche auf Paragraphen
    paras = _vsearch_paragraphs(neo, emb, k=48)
    #        ↓ Neo4j Index-Query über 48 beste Matches
    #        ↓ Sortiert nach Ähnlichkeit (score)
    
    # Schritt 3: Vector-Ähnlichkeitssuche auf Abbildungen
    figs = _vsearch_figures(neo, emb, k=8)
    
    # Schritt 4: Zusammenführen
    supports = paras + figs
    
    # Schritt 5 (optional): Für jede Abbildung die erklärenden Absätze hinzufügen
    _expand_figure_context_with_paragraphs(neo, supports)
    
    return {"supports": supports}
```

### Strategie B: `concept_based_retrieve()` (intelligente Variante) ⭐

**Datei:** `src/retriever.py`, Zeilen 278-370

Dies ist die **Standard-Strategie** für Schulungen! Sie hat 3 Retrieval-Wege:

```python
def concept_based_retrieve(neo, query, k_concepts=10, k_paragraphs_direct=48, 
                          k_paragraphs_via_concepts=20, k_figures=8, min_concept_score=0.6):
    """
    🔥 VECTOR-BASED Concept Retrieval (NO MENTIONS RELATIONS!)
    
    Weg 1: Query → Concepts (via embeddings) → Paragraphs (via embedding similarity)
    Weg 2: Query → Paragraphs (direkt via Vector-Embedding)
    Weg 3: Query → Figures (direkt via Vector-Embedding)
    """
    
    # SCHRITT 1: Embedding der Query
    emb = _embed_query(query)
    
    # WEG 1A: Finde relevante Konzepte via VECTOR SIMILARITY (z.B. "Deep Learning", "Neural Network")
    concepts = _vsearch_concepts(neo, emb, k=10, min_score=0.6)
    #          ↓ Vector-Suche in concept_embedding_index
    #          ↓ Findet Top-10 konzeptionell ähnliche Konzepte
    #          ↓ min_score=0.6 (lowered from 0.7 because concept names are short)
    
    concept_ids = [c["concept_id"] for c in concepts]
    
    # WEG 1B: Erweitere Konzepte über semantische Relationen (optional)
    if concepts:
        related = _expand_via_semantic_relations(neo, concept_ids, k=5)
        #         ↓ Folgt Graph-Relationen: IS_A, PART_OF, RELATED_TO
        #         ↓ Findet damit auch verwandte Konzepte (z.B. "CNN" wenn "Neural Network")
        concept_ids.extend(related)
    
    # WEG 1C: 🔥 NEW: Hole Absätze via VECTOR SIMILARITY (not MENTIONS!)
    paras_via_concepts = _paragraphs_via_concepts(neo, concept_ids, limit=20)
    #                    ↓ NEW APPROACH:
    #                    ↓ 1) Fetch concept embeddings from Neo4j
    #                    ↓ 2) Average them using numpy
    #                    ↓ 3) Vector search paragraph_embedding_index with averaged embedding
    #                    ↓ 4) Find paragraphs semantically similar to the concept cluster
    #                    ↓ ✅ NO MENTIONS RELATIONS NEEDED!
    
    # WEG 2: Direkte Paragraph-Suche per Vector-Ähnlichkeit
    paras_direct = _vsearch_paragraphs(neo, emb, k=48)
    #               ↓ Vector-Index-Suche (Fallback für nicht-konzeptuelle Matches)
    
    # WEG 3: Abbildungs-Suche per Vector-Ähnlichkeit
    figs = _vsearch_figures(neo, emb, k=8)
    
    # SCHRITT 2: Merge & Deduplizierung
    supports = []
    
    # Priorität: Concept-basierte Paragraphs zuerst (semantisch am relevantesten)
    supports.extend(paras_via_concepts)
    
    # Dann direkte Paragraph-Matches (können auch relevant sein)
    for p in paras_direct:
        if p["paragraph_id"] not in [s.get("paragraph_id") for s in supports]:
            supports.append(p)
    
    # Alle Abbildungen hinzufügen
    supports.extend(figs)
    
    # SCHRITT 3 (optional): Für Abbildungen die kontextualisierenden Absätze hinzufügen
    _expand_figure_context_with_paragraphs(neo, supports)
    
    return {"supports": supports, "matched_concepts": concepts, "debug": {...}}
```

**🔥 KEY CHANGE: _paragraphs_via_concepts() is now vector-based!**

**OLD approach (unreliable):**
```cypher
// Required MENTIONS relations that were hard to create during ingest
MATCH (c:Concept)<-[:MENTIONS]-(para:Paragraph)
RETURN para
```

**NEW approach (semantic & fast):**
```python
# 1) Fetch concept embeddings
concept_embeddings = [concept['embedding'] for concept in concepts]

# 2) Average embeddings using numpy
import numpy as np
avg_embedding = np.mean(concept_embeddings, axis=0).tolist()

# 3) Vector search with averaged embedding
CALL db.index.vector.queryNodes('paragraph_embedding_index', $limit, $embedding)
YIELD node, score
RETURN node AS para, score
```

**Benefits:**
- ✅ No MENTIONS relations needed (faster ingest)
- ✅ Semantic similarity > exact string matches
- ✅ Finds related content even if concept name not mentioned verbatim
- ✅ Works with short concept names (adjusted min_score to 0.6)

---

## 4. Die Low-Level Neo4j Queries: Wo die Magie passiert

### 4a: Vector-Suche auf Paragraphen

**Datei:** `src/retriever.py`, Zeilen 35-63

```python
def _vsearch_paragraphs(neo, embedding, k=48):
    """
    Neo4j Vector-Index Query für Paragraphen
    
    Nutzt: db.index.vector.queryNodes('paragraph_embedding_index', ...)
    """
    return neo.run("""
        CALL db.index.vector.queryNodes('paragraph_embedding_index', $k, $embedding)
        YIELD node, score
        WITH node, score
        MATCH (para:Paragraph) WHERE para = node
        MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para)
        OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(para)
        RETURN
          'paragraph' AS type,
          para.paragraph_id AS paragraph_id,
          para.text AS text,           ← Der actual Paragraph-Text!
          para.page AS page,
          p.paper_id AS paper_id,
          p.title AS paper_title,
          p.doi AS doi,
          p.url AS url,
          p.pdf_author AS authors,
          p.pdf_creation_date AS year,
          p.pdf_subject AS source,
          sec.title AS section_title,
          toFloat(score) AS score        ← Ähnlichkeits-Score (0-1)
        ORDER BY score DESC
        LIMIT $k
    """, {"embedding": embedding, "k": k})
```

**Was diese Query macht:**
1. `CALL db.index.vector.queryNodes(...)` - Suche die k nächsten Nachbarn im Embedding-Raum
2. `MATCH (para:Paragraph) WHERE para = node` - Hole den Paragraph-Knoten
3. `MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para)` - Finde das Paper, von dem dieser Paragraph kommt
4. `OPTIONAL MATCH (sec:Section)...` - Finde optional auch den Section (Kapitel)
5. `RETURN ...` - Gib alle relevanten Informationen zurück

**Result:** Liste mit Top-48 ähnlichsten Absätzen, sortiert nach Score (best zuerst)

### 4b: Vector-Suche auf Konzepten

**Datei:** `src/retriever.py`, Zeilen 100-125

```python
def _vsearch_concepts(neo, embedding, k=10, min_score=0.7):
    """
    Neo4j Vector-Index Query für Concepts
    """
    return neo.run("""
        CALL db.index.vector.queryNodes('concept_embedding_index', $k, $embedding)
        YIELD node, score
        WHERE score >= $min_score
        WITH node, score
        MATCH (c:Concept) WHERE c = node
        RETURN
          c.concept_id AS concept_id,
          c.name AS concept_name,
          c.description AS description,
          c.category AS category,
          toFloat(score) AS score
        ORDER BY score DESC
        LIMIT $k
    """, {"embedding": embedding, "k": k, "min_score": min_score})
```

**Was diese Query macht:**
1. Findet die Top-k Konzepte die ähnlich zur Query sind
2. Filtert nur Konzepte mit min_score >= 0.7 (nur gute Matches)
3. Gibt Konzept-Details zurück (name, description, category)

### 4c: Von Konzepten zu Paragraphen (Graph-Traversierung)

**Datei:** `src/retriever.py`, Zeilen 175-205

```python
def _paragraphs_via_concepts(neo, concept_ids, limit=20):
    """
    Hole Absätze die bestimmte Konzepte erwähnen
    
    Graph-Traversierung: Concept →[MENTIONED_IN]→ Paragraph
    """
    return neo.run("""
        MATCH (c:Concept) WHERE c.concept_id IN $concept_ids
        MATCH (c)-[:MENTIONED_IN]->(para:Paragraph)
        MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para)
        OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(para)
        WITH
          para.paragraph_id AS para_id,
          para.text AS text,
          p.paper_id AS paper_id,
          p.title AS paper_title,
          p.doi AS doi,
          p.url AS url,
          p.pdf_author AS authors,
          p.pdf_creation_date AS year,
          p.pdf_subject AS source,
          sec.title AS section_title,
          count(c) AS concept_mentions  ← Wie viele der Konzepte werden erwähnt?
        ORDER BY concept_mentions DESC
        LIMIT $limit
    """, {"concept_ids": concept_ids, "limit": limit})
```

**Was diese Query macht:**
1. Nimmt die gefundenen Konzept-IDs
2. Findet alle Paragraphen, die mindestens eines dieser Konzepte erwähnen
3. Zählt wie viele Konzepte jeder Paragraph erwähnt (Relevanz-Score)
4. Sortiert nach Anzahl der Konzept-Erwähnungen (meisten zuerst)
5. Limitiert auf Top-20 Absätze

---

## 5. Nach Retrieval: Antwort-Generierung

### Stelle: `answer_query()` → `grounded_answer()`

**Datei:** `src/agent.py`, Zeile 177 und andere

```python
# Nach Retrieval: nimm die gefundenen Quellen und generiere eine Antwort
answer = grounded_answer(query, supports)
#         ↓ LLM-Aufruf an OpenAI
#         ↓ Prompt: "Beantworte die Frage basierend auf diesen Quellen"
#         ↓ Rückgabe: Generierter Text mit Quellenverweisen
```

**Datei:** `src/openai_client.py`

```python
def grounded_answer(query, supports):
    """
    LLM-Aufruf mit Retrieval-Ergebnissen
    
    Prompt:
    "Du erstellst Schulungsmaterialien für Arbeitnehmer...
     Beantworte diese Frage basierend NUR auf den folgenden Quellen:
     [hier kommen die 30 Absätze + Abbildungen hin]
     
     Frage: {query}"
    """
    # Baue Prompt aus Supports
    sources_text = "\n\n".join([s["text"] for s in supports if s.get("type") == "paragraph"])
    
    # Sende an OpenAI
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},  # "Du bist ein Trainer..."
            {"role": "user", "content": f"{query}\n\nQuellen:\n{sources_text}"}
        ]
    )
    
    return response.choices[0].message.content
```

---

## Zusammenfassung: Der komplette Flow

```
1. course_generator.py
   └─ answer_query(query="Was ist Deep Learning?", neo=db, k_paragraphs=48)
      
      2. agent.py → answer_query()
         └─ concept_based_retrieve(neo, query)
            
            3. retriever.py → concept_based_retrieve()
               ├─ _embed_query(query)
               │  └─ OpenAI API: Query → 3072-D Vektor
               │
               ├─ WEG 1: Concept-basiert
               │  ├─ _vsearch_concepts() → Top-10 Konzepte
               │  ├─ _expand_via_semantic_relations() → verwandte Konzepte
               │  └─ _paragraphs_via_concepts() → Top-20 Absätze zu Konzepten
               │
               ├─ WEG 2: Direkter Paragraph-Match
               │  └─ _vsearch_paragraphs() → Top-48 Absätze
               │
               ├─ WEG 3: Abbildungen
               │  └─ _vsearch_figures() → Top-8 Abbildungen
               │
               ├─ Merge & Deduplizierung
               │  └─ Total: ~68 Quellen (30 Absätze + 8 Abbildungen + context)
               │
               └─ _expand_figure_context_with_paragraphs()
                  └─ Für jede Abbildung die erklärenden Absätze hinzufügen
            
            4. openai_client.py → grounded_answer()
               ├─ Baue Prompt aus den 30 besten Quellen
               ├─ Sende an OpenAI API (gpt-4o-mini)
               └─ Erhalte generierte Antwort
      
      5. course_generator.py
         └─ Formatiere Antwort als PDF-Text
            └─ Speichere Quellenverweise für Bibliographie
```

---

## Parameter die du anpassen kannst

| Parameter | Datei | Zeile | Standard | Wirkung |
|-----------|-------|-------|----------|---------|
| `k_paragraphs` | `course_generator.py` | 75 | 48 | Wie viele Absatz-Kandidaten aus dem Graph |
| `k_figures` | `course_generator.py` | 77 | 8 | Wie viele Abbildungs-Kandidaten |
| `paragraphs[:30]` | `agent.py` | 176, 199 | 30 | Wie viele der TOP-Absätze in die Antwort |
| `k_concepts` | `agent.py` | 164, 188 | 10 | Wie viele Konzepte suchen |
| `k_paragraphs_via_concepts` | `agent.py` | 165, 189 | 20 | Wie viele Absätze über Konzepte |
| `use_concept_retrieval` | `course_generator.py` | 78 | True | Intelligente Concept-Suche an/aus |

---

## Debugging: Wo sehe ich was?

### Debug-Ausgaben aktivieren

In `course_generator.py` Zeile 72-79 siehst du die `response` mit Debug-Infos:

```python
response = answer_query(...)
print(response["debug"])  # Zeigt: welche Modi, wie viele supports, etc.
```

### Typische Debug-Ausgabe:

```python
{
    "web_mode": "off",
    "decision": "graph_only",
    "graph_supports_total": 38,      ← Wie viele Quellen insgesamt
    "graph_supports_effective": 38,   ← Davon wie viele "valide"
    "matched_concepts_count": 8,      ← Wie viele Konzepte gefunden
    "matched_concepts": ["Deep Learning", "Neural Network", ...],
    "paragraphs_via_concepts": 15,    ← Absätze über Konzepte
    "paragraphs_direct": 23,          ← Direkte Absatz-Matches
    "figures": 3                      ← Abbildungen
}
```

---

## Wichtige Files im Überblick

| Datei | Rolle | Wichtige Funktionen |
|-------|-------|-------------------|
| `scripts/course_generator.py` | Orchestrierung Kurserstellung | `answer_query()` Aufruf (Zeile 72) |
| `src/agent.py` | Retrieval Decision-Logic | `answer_query()` - entscheidet Retrieval-Strategie |
| `src/retriever.py` | Eigentliche Retrieval-Funktionen | `hybrid_retrieve()`, `concept_based_retrieve()`, `_vsearch_*()` |
| `src/openai_client.py` | LLM-Integration | `grounded_answer()` - generiert Text basierend auf Quellen |
| `src/neo.py` | Neo4j Verbindung | `Neo4jClient.run()` - führt Cypher-Queries aus |

