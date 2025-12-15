# Re-Ingest Guide: Fixing Concept Coverage

## Problem Summary

**Current Status:**
- ✗ 82% of paragraphs have NO concept links
- ✗ Concept-based retrieval only works for 18% of content
- ✗ 118 empty Sections
- ✗ 367 orphan Paragraphs (not in Sections)

**Root Cause:**
The old concept linking code in `src/concept_extract.py` used simple substring matching:
```python
if name in para_text:  # Too simple!
```

This failed to match:
- Variations (plural vs singular): "Netze" vs "Netz"
- Umlauts: "Künstliche" vs "Kunstliche"
- Short terms without word boundaries: "KI" matched in "Technik"

**Fix Applied:**
✓ Improved matching with:
- Pattern variations (plural, singular)
- Umlaut normalization (ä→ae, ö→oe, ü→ue)
- Word boundary detection for short terms
- Fuzzy matching across normalized text

---

## Re-Ingest Process

### Step 1: Backup Current State (Optional)

If you want to preserve current data:

```cypher
// Export all nodes and relationships
CALL apoc.export.cypher.all("backup.cypher", {
    format: "cypher-shell",
    useOptimizations: {type: "UNWIND_BATCH"}
})
```

### Step 2: Run Re-Ingest Script (Auto-Stitch enabled)

```powershell
# From project root
python scripts/reingest_all.py
```

The script will:
1. Ask for confirmation (type `yes`)
2. Clear entire Neo4j database
3. Re-ingest all PDFs from `data/uploads/`
4. Use improved concept extraction
5. Automatically stitch the graph:
   - Sections ↔ Paragraphs/Figures via `stitch_document_hierarchy()`
   - Figures ↔ Paragraphs via `stitch_figures_to_paragraphs()`
6. Show diagnostics at the end

**Expected Duration:** 30-60 minutes (depends on number of papers)

### Step 3: Verify Results (No GUI stitching required)

After completion, check the diagnostics output:

```
RUNNING DIAGNOSTICS
============================================================
  Total Papers: 6
  Total Paragraphs: 11000
  Total Concepts: 400
  MENTIONS Relations: 8000+
  Paragraphs WITH Concepts: 7000+ (60-70%)  ← Should be MUCH higher now
  Empty Sections: 0  ← Should be 0
  Orphan Paragraphs: 0  ← Should be 0
```

**Expected Improvement:**
- Paragraph concept coverage: 82% → **60-80%** ✓
- Empty Sections: 118 → **0** ✓
- Orphan Paragraphs: 367 → **0** ✓

### Step 4: Run Additional Diagnostics

Run these queries in Neo4j Browser to verify:

```cypher
// 1. Check concept distribution per paper
MATCH (p:Paper)
OPTIONAL MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
OPTIONAL MATCH (para)-[:MENTIONS]->(c:Concept)
WITH p.title AS Paper, 
     count(DISTINCT para) AS TotalParas,
     count(DISTINCT CASE WHEN c IS NOT NULL THEN para END) AS ParasWithConcepts
RETURN Paper, TotalParas, ParasWithConcepts,
       round(ParasWithConcepts * 100.0 / TotalParas, 1) AS Coverage
ORDER BY Coverage ASC;

// 2. Verify hierarchy (should return 0 for all)
MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
RETURN "Empty Sections" AS Problem, count(sec) AS Count
UNION
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
WHERE NOT EXISTS ((:Section)-[:HAS_PARAGRAPH]->(para))
RETURN "Orphan Paragraphs" AS Problem, count(para) AS Count;

// 3. Check concept usage
MATCH (c:Concept)
OPTIONAL MATCH (c)<-[:MENTIONS]-(para:Paragraph)
WITH c.name AS Concept, count(para) AS Mentions
WHERE Mentions = 0
RETURN count(*) AS UnusedConcepts;
```

---

## Troubleshooting

### Problem: Coverage still below 50%

**Possible causes:**
1. Entity extraction returned too few entities
2. PDFs have poor text extraction quality
3. Concepts are too specific/technical

**Solutions:**
```python
# Increase max_entities in scripts/ingest.py line 105
extraction_result = extract_and_embed_concepts_hybrid(
    ...
    max_entities=50,  # Increase from 30
    max_relations=30,  # Increase from 20
    ...
)
```

Then re-run `reingest_all.py`.

### Problem: "Database not fully cleared"

**Solution:**
```cypher
// Manually clear in Neo4j Browser
MATCH (n) DETACH DELETE n;
```

Then re-run `reingest_all.py`.

### Problem: Ingest fails for specific paper

**Check error message:**
- "PDF text extraction failed" → PDF is image-based, needs OCR
- "Concept extraction timeout" → Increase timeout or reduce max_entities
- "Neo4j connection error" → Check Neo4j is running

**Skip problematic paper:**
Move it out of `data/uploads/` temporarily, then re-run.

---

## Post-Ingest: Cleanup Scripts

Even with improved code and auto-stitching, you may still need to run cleanup scripts from `CLEANUP_EMPTY_SECTIONS.md`:

```cypher
// 1. Delete any remaining empty sections
MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
DETACH DELETE sec;

// 2. Organize any remaining orphan paragraphs
MATCH (p:Paper)-[:HAS_SECTION]->(sec:Section)
WITH p, collect(sec)[0] AS firstSection
MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
WHERE NOT EXISTS ((:Section)-[:HAS_PARAGRAPH]->(para))
CREATE (firstSection)-[:HAS_PARAGRAPH]->(para);
```

---

## Verification Checklist

After re-ingest, verify these metrics:

- [ ] Paragraphs with concepts: **>60%** (was 18%)
- [ ] Empty sections: **0** (was 118)
- [ ] Orphan paragraphs: **0** (was 367)
- [ ] Total MENTIONS relations: **>5000** (was 2574)
- [ ] Unused concepts: **<20%** (was 100%)
- [ ] Papers with 0% concept coverage: **0** (was all papers)

---

## Expected Performance Improvement

### Before Re-Ingest:
- Concept-based retrieval: **Only 18% of content accessible**
- Course generation: **Sparse, repetitive content**
- Graph visualization: **Cluttered, unstructured**

### After Re-Ingest:
- Concept-based retrieval: **60-80% of content accessible** ✓
- Course generation: **Rich, diverse content from more paragraphs** ✓
- Graph visualization: **Clean hierarchy (Paper→Section→Paragraph→Concept)** ✓

---

## Alternative: Partial Fix (Without Re-Ingest)

If you don't want to re-ingest everything, you can:

1. **Only fix structure issues:**
   ```cypher
   // Run cleanup scripts from CLEANUP_EMPTY_SECTIONS.md
   ```

2. **Accept 18% concept coverage:**
   - Rely more on direct paragraph vector search
   - Reduce k_paragraphs_via_concepts to 5 (from 20)
   - Increase k_paragraphs to 60 (from 48)

3. **Manually add concepts for specific papers:**
   - Use `scripts/ingest.py` to re-ingest only specific papers
   - Keep existing papers untouched

---

## Contact Points

If re-ingest fails or results are unexpected:

1. **Check logs:** Look for "ERROR" or "WARNING" messages during ingest
2. **Run diagnostics:** Use queries from `GRAPH_DIAGNOSIS.md`
3. **Verify code changes:** Ensure `src/concept_extract.py` has improved matching logic (lines 417-500)
4. **Test with single paper:** Before full re-ingest, test with one PDF

---

## Summary

**Commands to run:**
```powershell
# 1. Re-ingest all papers with improved concept linking
python scripts/reingest_all.py

# 2. Verify results with diagnostics queries (in Neo4j Browser)
MATCH (para:Paragraph)
WITH count(para) AS total
MATCH (para2:Paragraph)-[:MENTIONS]->()
RETURN count(DISTINCT para2) * 100.0 / total AS ConceptCoverage;

# 3. If needed: Run cleanup scripts
MATCH (sec:Section) WHERE NOT ((sec)-[:HAS_PARAGRAPH]->()) DETACH DELETE sec;
```

**Expected outcome:**
- ✓ Concept coverage: 82% → **60-80%**
- ✓ Empty sections: 118 → **0**
- ✓ Orphan paragraphs: 367 → **0**
- ✓ Course generation quality significantly improved
