# Diagnose: Struktur-Probleme im Wissensgraph

Führe diese Queries aus um potenzielle Probleme zu identifizieren.

---

## 1. STRUKTURPROBLEME IDENTIFIZIEREN

### 1a: Verwaiste Paragraphen (nicht mit Paper verbunden)

```cypher
MATCH (para:Paragraph)
WHERE NOT ((para)<-[:HAS_PARAGRAPH]-(:Paper))
RETURN count(para) AS OrphanParagraphs, 
       collect(para.paragraph_id)[0..5] AS Examples
```

**Problem wenn > 0:** Paragraphen ohne Paper = Struktur-Fehler beim Ingest

---

### 1b: Papers ohne Sections oder Paragraphen

```cypher
MATCH (p:Paper)
WHERE NOT ((p)-[:HAS_SECTION]->() OR (p)-[:HAS_PARAGRAPH]->())
RETURN p.title AS EmptyPaper, p.paper_id AS ID
```

**Problem wenn > 0:** Leere Papers (nur Metadaten, keine Inhalte)

---

### 1c: Sections ohne Paragraphen

```cypher
MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
RETURN count(sec) AS EmptySections,
       collect(sec.title)[0..5] AS Examples
```

**Problem wenn > 0:** Sections ohne Inhalte

---

### 1d: Paragraphen-Duplikate (gleiche ID mehrfach)

```cypher
MATCH (p:Paragraph)
WITH p.paragraph_id AS para_id, count(p) AS cnt
WHERE cnt > 1
RETURN para_id, cnt
ORDER BY cnt DESC
LIMIT 20
```

**Problem wenn > 0:** Doppelte Paragraphen im Graph (Ingest-Fehler)

---

## 2. HIERARCHIE-PROBLEME

### 2a: Paragraphen direkt an Paper STATT über Section

```cypher
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
WHERE NOT EXISTS ((:Section)-[:HAS_PARAGRAPH]->(para))
RETURN p.title AS Paper, count(para) AS DirectParagraphCount
ORDER BY DirectParagraphCount DESC
LIMIT 20
```

**Problem:** Manche Paragraphen sind direkt am Paper, nicht in Sections
**Auswirkung:** Unstrukturierte Darstellung im Graph

**Wie viele insgesamt?**
```cypher
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
WHERE NOT EXISTS ((:Section)-[:HAS_PARAGRAPH]->(para))
RETURN count(para) AS ParagraphsDirectOnPaper,
       count(DISTINCT p) AS AffectedPapers
```

---

### 2b: Sections ohne übergeordnete Struktur

```cypher
MATCH (sec:Section)
WHERE NOT ((p:Paper)-[:HAS_SECTION]->(sec))
RETURN count(sec) AS OrphanSections,
       collect(sec.title)[0..10] AS Examples
```

**Problem wenn > 0:** Sections gehören zu keinem Paper

---

## 3. KONZEPT-VERBINDUNGS-PROBLEME

### 3a: Paragraphen die KEINE Konzepte erwähnen

```cypher
MATCH (para:Paragraph)
WITH count(para) AS total
MATCH (para2:Paragraph)
WHERE NOT ((para2)-[:MENTIONS]->())
WITH total, count(para2) AS withoutConcepts
RETURN withoutConcepts AS ParagraphsWithoutConcepts,
       round(withoutConcepts * 100.0 / total) AS PercentageOfAll
```

**Problem wenn hoch:** Viele Paragraphen haben keine Concept-Verknüpfung
**Auswirkung:** Concept-basiertes Retrieval funktioniert schlecht

---

### 3b: Verwaiste Concepts (nicht erwähnt in Paragraphen)

```cypher
MATCH (c:Concept)
WITH count(c) AS total
MATCH (c2:Concept)
WHERE NOT ((c2)-[:MENTIONED_IN]->())
WITH total, count(c2) AS unused, collect(c2.name)[0..10] AS examples
RETURN unused AS UnusedConcepts,
       round(unused * 100.0 / total) AS PercentageOfAll,
       examples AS Examples
```

**Problem wenn hoch:** Viele Concepts sind nicht im Text verankert
**Auswirkung:** Wissensrepräsentation ist schwach

---

### 3c: Concepts mit zu wenigen Mentions

```cypher
MATCH (c:Concept)
WITH count(c) AS total
MATCH (c2:Concept)
OPTIONAL MATCH (c2)-[:MENTIONED_IN]->(p:Paragraph)
WITH c2.name AS ConceptName, count(p) AS MentionCount, total
WHERE MentionCount < 2
RETURN count(*) AS ConceptsWithLessThan2Mentions,
       round(count(*) * 100.0 / total) AS Percentage
```

**Problem wenn sehr hoch:** Concepts sind zu granular (zu viele einmalige Erwähnungen)

---

## 4. RELATION-PROBLEME

### 4a: Fehlende Paper-Metadaten

```cypher
MATCH (p:Paper)
RETURN 
  count(p) AS TotalPapers,
  count(CASE WHEN p.author IS NOT NULL AND p.author <> "" THEN 1 END) AS WithAuthor,
  count(CASE WHEN p.publication_year IS NOT NULL THEN 1 END) AS WithYear,
  count(CASE WHEN p.doi IS NOT NULL AND p.doi <> "" THEN 1 END) AS WithDOI,
  count(CASE WHEN p.url IS NOT NULL AND p.url <> "" THEN 1 END) AS WithURL,
  count(CASE WHEN p.source IS NOT NULL AND p.source <> "" THEN 1 END) AS WithSource,
  count(CASE WHEN p.publisher IS NOT NULL AND p.publisher <> "" THEN 1 END) AS WithPublisher
```

**Problem:** Wenn Werte << 100%, fehlen Metadaten für Zitationen

---

### 4b: Fehlerhafte Paper-Relationen (Nicht-Existent Types)

```cypher
MATCH (p:Paper)-[r]->()
RETURN type(r) AS RelationType, count(r) AS Count
ORDER BY Count DESC
```

**Sollte sein:** HAS_PARAGRAPH, HAS_SECTION, HAS_FIGURE (hauptsächlich)
**Problem wenn:** Unerwartete Relation-Typen vorhanden

---

## 5. PERFORMANCE-PROBLEME

### 5a: Paragraphen mit extrem vielen Concepts

```cypher
MATCH (para:Paragraph)
OPTIONAL MATCH (para)-[:MENTIONS]->(c:Concept)
WITH para.paragraph_id AS para_id, count(c) AS concept_count
WHERE concept_count > 50
RETURN para_id, concept_count
ORDER BY concept_count DESC
LIMIT 10
```

**Problem:** Paragraphen mit 100+ Concepts → Noise statt Signal

---

### 5b: Größte Papers (nach Paragraph-Anzahl)

```cypher
MATCH (p:Paper)
OPTIONAL MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
WITH p.title AS Paper, count(para) AS ParaCount
ORDER BY ParaCount DESC
RETURN Paper, ParaCount
LIMIT 20
```

**Checken:** Sind diese realistisch oder Duplikate?

---

## 6. VISUALISIERUNGS-PROBLEME DIAGNOSTIZIEREN

### 6a: Warum sieht der Graph so unstrukturiert aus?

Diese Query zeigt die Ursache:

```cypher
RETURN 
  "DIAGNOSE: Graph-Struktur" AS Check,
  CASE 
    WHEN EXISTS((MATCH (para:Paragraph) WHERE NOT ((para)<-[:HAS_PARAGRAPH]-()) RETURN para LIMIT 1))
    THEN "❌ Verwaiste Paragraphen gefunden!"
    ELSE "✓ Alle Paragraphen verbunden"
  END AS Structure1,
  CASE 
    WHEN EXISTS((MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para) WHERE NOT EXISTS((sec:Section)-[:HAS_PARAGRAPH]->(para)) RETURN 1 LIMIT 1))
    THEN "⚠️ Manche Paragraphen sind direkt am Paper (nicht in Sections)"
    ELSE "✓ Alle Paragraphen in Sections"
  END AS Structure2,
  CASE
    WHEN EXISTS((MATCH (para:Paragraph) WHERE NOT ((para)-[:MENTIONS]->()) RETURN para LIMIT 1))
    THEN "⚠️ Viele Paragraphen ohne Concepts"
    ELSE "✓ Alle Paragraphen haben Concepts"
  END AS Structure3
```

---

## 7. HÄUFIGSTE PROBLEME & LÖSUNGEN

| Problem | Query zum Checken | Ursache | Lösung |
|---------|-------------------|--------|--------|
| Graph wirkt "flach" | `MATCH (p)-[:HAS_PARAGRAPH]->(para) WHERE NOT...` | Keine Sections | Re-Ingest mit Sections |
| Zu viele isolierte Nodes | `MATCH (n) WHERE NOT ((n)-[]-())` | Verwaiste Concepts | Löschen + Neu-Extrahieren |
| Concept-Retrieval schwach | `MATCH (c) WHERE NOT ((c)-[:MENTIONED_IN]->())` | Concepts nicht verlinkt | Concept-Extraction verbessern |
| Duplicate Paragraphen | `WITH para_id, count(*) AS c WHERE c > 1` | Doppelter Ingest | DB clearen + Single Ingest |
| Performance schlecht | `MATCH (para) OPTIONAL MATCH (para)-[:MENTIONS]->() ... count() > 100` | Zu viele Edges pro Node | Concepts filtern (z.B. nur Top-20) |

---

## 8. KOMPLETTE STRUKTUR-ANALYSE

```cypher
// Führe alle wichtigen Checks auf einmal aus
WITH
  // Zähle alle Node-Typen
  {
    Papers: size([() WHERE exists(p:Paper)]),
    Paragraphs: size([() WHERE exists(p:Paragraph)]),
    Concepts: size([() WHERE exists(c:Concept)]),
    Sections: size([() WHERE exists(s:Section)]),
    Figures: size([() WHERE exists(f:Figure)])
  } AS NodeCounts,
  
  // Check auf Probleme
  {
    OrphanParagraphs: size([(p:Paragraph) WHERE NOT ((p)<-[:HAS_PARAGRAPH]-())]),
    EmptyPapers: size([(p:Paper) WHERE NOT ((p)-[:HAS_SECTION|HAS_PARAGRAPH]->())]),
    ParagraphsWithoutConcepts: size([(p:Paragraph) WHERE NOT ((p)-[:MENTIONS]->())])
  } AS Problems
  
RETURN NodeCounts, Problems
```

---

## HÄUFIGE ROOT-CAUSES

### ❌ Problem 1: Paragraphen ohne Sections

```cypher
// Zeige welche Papers nur direkte Paragraphen haben:
MATCH (p:Paper)
WITH p, 
     size([(p)-[:HAS_SECTION]->()]) AS section_count,
     size([(p)-[:HAS_PARAGRAPH]->()]) AS para_count
WHERE section_count = 0 AND para_count > 0
RETURN p.title, section_count, para_count
```

**Auswirkung:** Visuelle Darstellung wird linear statt hierarchisch

**Lösung:** Script zum Einfügen von Default-Sections:
```cypher
// Erstelle eine Default-Section für Papers ohne Sections
MATCH (p:Paper)
WHERE NOT EXISTS ((p)-[:HAS_SECTION]->())
AND EXISTS ((p)-[:HAS_PARAGRAPH]->())
CREATE (sec:Section {
  section_id: p.paper_id + "_default",
  title: "Inhalte"
})
CREATE (p)-[:HAS_SECTION]->(sec)
WITH p, sec
MATCH (p)-[:HAS_PARAGRAPH]->(para)
CREATE (sec)-[:HAS_PARAGRAPH]->(para)
```

---

### ❌ Problem 2: Zu viele isolierte Concepts

```cypher
// Zähle Concepts nach Mention-Häufigkeit
MATCH (c:Concept)
OPTIONAL MATCH (c)-[:MENTIONED_IN]->(p:Paragraph)
WITH c.name AS ConceptName, count(p) AS Mentions, c
WITH count(c) AS total_concepts, Mentions, count(*) AS ConceptCount
RETURN 
  Mentions,
  ConceptCount,
  round(ConceptCount * 100.0 / total_concepts) AS Percentage
ORDER BY Mentions
```

**Auswirkung:** Graph wird zu komplex, Visualisierung unklar

**Lösung:** Nur wichtige Concepts behalten (mindestens 2-3 Mentions)

---

### ❌ Problem 3: Keine Concept-Verlinkung

```cypher
// Wie viele Paragraphen haben Concepts?
MATCH (para:Paragraph)
WITH count(para) AS total_paras,
     size([(para)-[:MENTIONS]->()]) > 0 AS has_concepts
RETURN
  total_paras AS TotalParagraphs,
  sum(CASE WHEN has_concepts THEN 1 ELSE 0 END) AS WithConcepts,
  round(sum(CASE WHEN has_concepts THEN 1 ELSE 0 END) * 100.0 / total_paras) AS PercentageWithConcepts
```

**Auswirkung:** Concept-Retrieval funktioniert nicht

**Lösung:** Concept-Extraction neu durchführen

---

## ERSTE SCHRITTE ZUR DIAGNOSE

1. **Query 1a ausführen:** Verwaiste Paragraphen?
2. **Query 2a ausführen:** Papers ohne Sections?
3. **Query 3a ausführen:** Paragraphen ohne Concepts?
4. **Query 4a ausführen:** Fehlende Metadaten?
5. **Query 6a ausführen:** Gesamt-Diagnose

Basierend auf den Ergebnissen kannst du dann gezielt ein Cleanup-Script fahren.

