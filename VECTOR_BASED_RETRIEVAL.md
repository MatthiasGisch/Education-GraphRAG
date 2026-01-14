# Vector-Based Concept Retrieval (2024 Architecture)

## 🎯 Summary

**Problem:** Original concept extraction tried to link concepts to paragraphs during ingest, but this was slow (LLM timeouts), unreliable (low coverage), and complex (fragile string matching).

**Solution:** Separate concerns - extract concepts WITHOUT paragraph links during ingest, then use **vector similarity** at retrieval time to find relevant paragraphs.

## 📊 Results

### Before (MENTIONS-based)
- ❌ 82% paragraphs without concept links
- ❌ LLM timeouts during ingest (60s per batch of 3-5 paragraphs)
- ❌ 15-20% concept coverage after re-ingest attempts
- ❌ Complex string matching logic that failed

### After (Vector-based)
- ✅ **5 matched concepts** for query "Was ist künstliche Intelligenz?"
- ✅ **10 paragraphs via concepts** (semantic similarity)
- ✅ **15 paragraphs direct** (direct vector search)
- ✅ **29 total supports** (high quality results)
- ✅ **1 LLM call per paper** (vs hundreds before)
- ✅ **min_score=0.6** works well for short concept names

## 🏗️ Architecture

### Ingest Time (Fast)
```python
# src/concept_extract.py - extract_and_embed_concepts_hybrid()
# 1 LLM call per paper extracts ~30 concepts
concepts = llm.extract_concepts(paper_title + first_2000_chars)

# Create concept nodes with embeddings
for concept in concepts:
    embedding = openai.embed(concept.name)
    neo.create_concept(concept_id, name, description, embedding)

# ✅ NO paragraph linking! Returns empty paragraph_links array
return {"concepts": concepts, "paragraph_links": []}
```

**Benefits:**
- Fast ingest (no timeouts)
- Simple extraction logic
- Clean graph structure

### Retrieval Time (Semantic)
```python
# src/retriever.py - concept_based_retrieve()

# 1) Find relevant concepts via vector similarity
emb = embed_query("Was ist künstliche Intelligenz?")
concepts = vector_search(concept_embedding_index, emb, k=10, min_score=0.6)
# → ['AI Speak', 'AI and Human Interaction', 'AI in Education', ...]

# 2) Find paragraphs semantically similar to concept cluster
concept_embeddings = [c.embedding for c in concepts]
avg_embedding = np.mean(concept_embeddings, axis=0)  # Average concept vectors
paragraphs = vector_search(paragraph_embedding_index, avg_embedding, limit=20)
# → Finds paragraphs about AI even if they don't mention exact concept names!

# 3) Merge with direct paragraph search
direct_paragraphs = vector_search(paragraph_embedding_index, emb, k=48)
supports = deduplicate(paragraphs + direct_paragraphs)
```

**Benefits:**
- Semantic similarity > exact string matches
- Finds related content even without exact names
- Handles short concept names well
- Fast (pure vector operations)

## 🔧 Implementation Details

### Key Files Changed

1. **`src/concept_extract.py` (lines 363-432)**
   - Removed all paragraph linking logic
   - Returns empty `paragraph_links` array
   - Single LLM call per paper for concepts only

2. **`src/retriever.py` (lines 176-246)**
   - Replaced MENTIONS graph traversal with vector similarity
   - `_paragraphs_via_concepts()` now:
     1. Fetches concept embeddings
     2. Averages them with numpy
     3. Does vector search on paragraph_embedding_index

3. **`src/retriever.py` (line 323)**
   - Changed default `min_concept_score` from 0.7 to 0.6
   - Reason: Short concept names match less strongly than full paragraphs

### Neo4j Indexes Used

```cypher
// Concept vector index (new usage)
CALL db.index.vector.queryNodes('concept_embedding_index', $k, $embedding)

// Paragraph vector index (existing)
CALL db.index.vector.queryNodes('paragraph_embedding_index', $limit, $embedding)
```

### No More MENTIONS Relations!

**Old approach (deleted):**
```cypher
MATCH (c:Concept)<-[:MENTIONS]-(para:Paragraph)
WHERE c.concept_id IN $concept_ids
RETURN para
```

**New approach (vector-based):**
```python
# Average concept embeddings
avg_emb = np.mean([c['embedding'] for c in concepts], axis=0)

# Vector search paragraphs
CALL db.index.vector.queryNodes('paragraph_embedding_index', $limit, $avg_emb)
YIELD node, score
RETURN node, score
```

## 📈 Performance Comparison

| Metric | MENTIONS-based | Vector-based |
|--------|---------------|--------------|
| Ingest speed | ❌ Timeouts | ✅ Fast (1 call/paper) |
| Concept coverage | ❌ 15-20% | ✅ N/A (no linking) |
| Retrieval quality | ❌ 0 results | ✅ 10 paragraphs via concepts |
| Semantic matching | ❌ Exact strings | ✅ Similarity-based |
| Maintenance | ❌ Complex | ✅ Simple |

## 🧪 Testing

**Test im CLI:**
```powershell
python scripts/ask.py "Was ist künstliche Intelligenz?"
```

**Expected output:**
```
Matched concepts: 5-10
Concept names: ['AI Speak', 'AI and Human Interaction', 'AI in Education', ...]
Paragraphs via concepts: 10-20
Paragraphs direct: 15-30
Total supports: 25-40
```

**Test in GUI:**
- Streamlit GUI starten
- Frage eingeben
- Concept-Retrieval aktivieren
- Ergebnisse + Debug-Info ansehen

## 🚀 Next Steps

1. **Re-ingest all papers** (currently only 4 test papers)
   ```bash
   python scripts/reingest_all.py
   ```

2. **Fix orphan paragraphs** (auto-stitch reduces most; run cleanup if needed)
   ```cypher
   MATCH (p:Paper)-[:HAS_SECTION]->(sec:Section)
   WITH p, collect(sec)[0] AS firstSection
   MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
   WHERE NOT EXISTS ((:Section)-[:HAS_PARAGRAPH]->(para))
   CREATE (firstSection)-[:HAS_PARAGRAPH]->(para);
   ```

3. **Test course generation** with vector-based retrieval
   ```bash
   python scripts/course_generator.py
   ```

4. **Optimize if needed:**
   - Increase `k_concepts` (10 → 15)
   - Increase `k_paragraphs_via_concepts` (20 → 30)
   - Add concept name boosting for exact matches
   - Weighted averaging (popular concepts weighted more)

## 🧵 Auto-Stitching

Stitching (Sections ↔ Paragraphs/Figures, Figures ↔ Paragraphs) runs automatically in the ingest pipeline:

- Single ingest (`scripts/ingest.py`) and bulk ingest (`scripts/reingest_all.py`) call:
   - `neo.stitch_document_hierarchy()`
   - `neo.stitch_figures_to_paragraphs(prefix_length=60, page_tolerance=1)`

GUI stitch buttons were removed to avoid redundancy.

## 📚 Documentation

- `RETRIEVAL_FLOW.md` - Updated with vector-based approach
- `REINGEST_GUIDE.md` - Re-ingest instructions
- `DOCUMENTATION.md` - Complete system documentation
- `NEO4J_VISUALIZATION_QUERIES.md` - Useful Cypher queries

## 🎓 Lessons Learned

1. **Separate concerns**: Ingest ≠ Retrieval
   - Don't try to solve retrieval problems during ingest
   - Extract minimal structure, enrich at query time

2. **Vector similarity > String matching**
   - Semantic similarity finds related content
   - More robust than exact name matches

3. **Adjust thresholds for data type**
   - Short concept names: min_score=0.6
   - Full paragraphs: min_score=0.7 (default)

4. **Test incrementally**
   - Started with 4 PDFs to validate approach
   - Created diagnostic scripts to debug issues
   - Found min_score threshold problem quickly

## ✅ Success Criteria Met

- [x] Concepts extracted successfully (31 concepts from 4 papers)
- [x] Vector-based retrieval implemented
- [x] Test shows 5 matched concepts + 10 paragraphs
- [x] No LLM timeouts during ingest
- [x] Semantic similarity working (finds AI-related content)
- [x] Documentation updated

**Status: READY FOR FULL RE-INGEST** 🚀
