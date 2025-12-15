# Neo4j Visualisierung - Cypher Queries

Diese Queries kannst du direkt in der Neo4j Browser Console ausführen (http://localhost:7474/browser/)

---

## QUICK START - Sofort loslegegen

### 1. Graph-Überblick (Statistik)

```cypher
CALL db.labels() YIELD label
MATCH (n)
WHERE label IN labels(n)
RETURN label AS NodeType, count(n) AS Count
ORDER BY Count DESC
```

**Was:** Übersicht aller Node-Typen und deren Anzahl

### 2. Hierarchische Struktur (Dokumentstruktur)

```cypher
MATCH (p:Paper)
OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)
OPTIONAL MATCH (sec)-[:HAS_PARAGRAPH]->(para:Paragraph)
OPTIONAL MATCH (para)-[:MENTIONS]->(c:Concept)
RETURN p, sec, para, c
LIMIT 5000
```

**Was:** Paper → Sections → Paragraphs → Concepts (schön strukturiert)

### 3. Concept-Netzwerk (klein)

```cypher
MATCH (c:Concept)
OPTIONAL MATCH (c)-[rel:SEMANTIC_RELATION]->(related:Concept)
RETURN c, rel, related
LIMIT 500
```

**Was:** Zeigt Konzept-Verbindungen mit semantischen Relationen

### 4. Concept-Retrieval Test

```cypher
MATCH (c:Concept)
WHERE c.name CONTAINS "SUCHBEGRIFF"
MATCH (c)-[:MENTIONS]->(para:Paragraph)
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para)
RETURN p, para, c
LIMIT 20
```

**Was:** Simuliert Concept-Based Retrieval

### 5. Datenqualität überprüfen

```cypher
MATCH (p:Paper)
RETURN 
  count(p) AS TotalPapers,
  count(CASE WHEN p.author IS NOT NULL THEN 1 END) AS WithAuthor,
  count(CASE WHEN p.doi IS NOT NULL THEN 1 END) AS WithDOI
```

**Was:** Wie vollständig sind die Metadaten?

---

## GESAMTSTRUKTUR DES WISSENSGRAPHEN

### Nur wichtigste Relationen visualisieren

```cypher
MATCH (n)-[r:HAS_PARAGRAPH|HAS_SECTION|MENTIONS|SEMANTIC_RELATION]-(m)
RETURN n, r, m
LIMIT 5000
```

### Mit Sampling für große Graphen (10% der Nodes)

```cypher
MATCH (n)
WITH n, rand() AS r
WHERE r < 0.1
OPTIONAL MATCH (n)-[rel:HAS_PARAGRAPH|HAS_SECTION|MENTIONS|SEMANTIC_RELATION]-(m)
RETURN n, rel, m
LIMIT 3000
```

### Nur bestimmte Node-Typen

```cypher
MATCH (n)
WHERE labels(n)[0] IN ["Paper", "Concept", "Section"]
OPTIONAL MATCH (n)-[r]-(m)
WHERE labels(m)[0] IN ["Paper", "Concept", "Section"]
RETURN n, r, m
LIMIT 5000
```

---

## PAPER-STRUKTUR

### Ein spezifisches Paper anschauen

```cypher
MATCH (p:Paper)
WHERE p.title CONTAINS "SUCHBEGRIFF"
OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)
OPTIONAL MATCH (sec)-[:HAS_PARAGRAPH]->(para:Paragraph)
OPTIONAL MATCH (para)-[:MENTIONS]->(c:Concept)
RETURN p, sec, para, c
LIMIT 5000
```

**Was:** Komplette Struktur eines Papers mit allen Konzepten

### Paper-Metadaten Qualität

```cypher
MATCH (p:Paper)
RETURN 
  p.title AS Title,
  p.author AS Author,
  p.publication_year AS Year,
  p.doi AS DOI,
  p.url AS URL
ORDER BY p.title
```

**Was:** Zeigt vorhandene und fehlende Metadaten

---

## CONCEPT-STRUKTUR (Wissensrepräsentation)

### Alle Konzepte mit ihren Verbindungen

```cypher
MATCH (c:Concept)
OPTIONAL MATCH (c)-[rel:SEMANTIC_RELATION]->(related:Concept)
WITH c, type(rel) AS RelationType, collect(related.name) AS RelatedConcepts, count(related) AS RelatedCount
RETURN c.name AS Concept, c.category AS Category, RelationType, RelatedConcepts, RelatedCount
ORDER BY RelatedCount DESC
LIMIT 50
```

**Was:** Welche Konzepte sind wie miteinander verbunden?

### Ein spezifisches Konzept mit seinen Verbindungen

```cypher
MATCH (c:Concept)
WHERE c.name CONTAINS "SUCHBEGRIFF"
OPTIONAL MATCH (c)-[rel:SEMANTIC_RELATION]->(related:Concept)
RETURN c.name AS Concept, type(rel) AS RelationType, related.name AS RelatedConcept
LIMIT 50
```

**Was:** Zeigt semantisches Netzwerk um ein spezifisches Konzept

### Konzept → Paragraphen (wo wird es erwähnt?)

```cypher
MATCH (c:Concept)
WHERE c.name CONTAINS "SUCHBEGRIFF"
MATCH (c)-[:MENTIONS]->(para:Paragraph)
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para)
RETURN DISTINCT
  p.title AS Paper, 
  para.paragraph_id AS ParagraphID,
  para.page AS Page
ORDER BY p.title
```

**Was:** In welchen Papern wird ein Konzept erwähnt?

### Konzepte nach Kategorie

```cypher
MATCH (c:Concept)
WHERE c.category = "Technical"
OPTIONAL MATCH (c)-[:MENTIONS]->(para:Paragraph)
RETURN c.name AS Concept, c.category AS Category, count(para) AS MentionCount
ORDER BY MentionCount DESC
LIMIT 50
```

**Kategorien:** Technical, Conceptual, Application, Domain

---

## FIGUREN / ABBILDUNGEN

### Alle Figuren mit ihren Papers

```cypher
MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
RETURN p.title AS Paper, f.figure_label AS FigureLabel, f.caption AS Caption
ORDER BY p.title
LIMIT 100
```

**Was:** Welche Abbildungen sind in welchen Papern?

---

## METADATEN UND STATISTIKEN

### Paragraph-Statistiken

```cypher
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
WITH p, count(para) AS ParagraphCount
RETURN 
  count(p) AS TotalPapers,
  avg(ParagraphCount) AS AvgParagraphsPerPaper,
  min(ParagraphCount) AS MinParagraphs,
  max(ParagraphCount) AS MaxParagraphs,
  sum(ParagraphCount) AS TotalParagraphs
```

**Was:** Wie viel Textmaterial haben wir?

### Concept-Statistiken

```cypher
MATCH (c:Concept)
OPTIONAL MATCH (c)-[:MENTIONS]->(para:Paragraph)
WITH c, count(para) AS MentionCount
RETURN 
  count(c) AS TotalConcepts,
  avg(MentionCount) AS AvgMentionsPerConcept,
  min(MentionCount) AS MinMentions,
  max(MentionCount) AS MaxMentions,
  count(CASE WHEN MentionCount > 10 THEN 1 END) AS HighFrequentConcepts
```

**Was:** Wie gut verteilt sind die Konzepte?

### Relationen-Übersicht

```cypher
CALL db.relationshipTypes() YIELD relationshipType
MATCH ()-[r]->()
WHERE type(r) = relationshipType
RETURN relationshipType AS RelationType, count(r) AS Count
ORDER BY Count DESC
```

**Was:** Alle Relation-Typen und deren Häufigkeit

---

## DATA QUALITY CHECKS

### Verwaiste Paragraphen (nicht mit Paper verbunden)

```cypher
MATCH (para:Paragraph)
WHERE NOT (()-[:HAS_PARAGRAPH]->(para))
RETURN count(para) AS OrphanParagraphs
```

**Was:** Paragraphen ohne Verbindung zu Paper?

### Papers ohne Inhalt

```cypher
MATCH (p:Paper)
WHERE NOT ((p)-[:HAS_PARAGRAPH|HAS_SECTION]->())
RETURN p.title AS PaperWithoutContent
```

**Was:** Papers ohne Paragraphen oder Sections?

### Konzepte ohne Mentions

```cypher
MATCH (c:Concept)
WHERE NOT ((c)-[:MENTIONS]->())
RETURN c.name AS ConceptNotMentioned
LIMIT 50
```

**Was:** Konzepte die nirgendwo erwähnt werden (möglicherweise Noise)

---

## 📋 HILFREICHE REFERENZEN

### Node-Typen im Graph
- `Paper` - Dokumente
- `Section` - Abschnitte/Kapitel
- `Paragraph` - Absätze
- `Figure` - Abbildungen
- `Concept` - Konzepte aus dem Wissensgraph
- `Topic` - Thema-Sammlung

### Wichtige Relation-Typen
- `HAS_SECTION` - Paper → Section
- `HAS_PARAGRAPH` - Section/Paper → Paragraph
- `HAS_FIGURE` - Paper → Figure
- `MENTIONS` - Paragraph → Concept (wenn ein Konzept erwähnt wird)
- `SEMANTIC_RELATION` - Concept ↔ Concept (mit relation_type Property)
- `CO_OCCURS_WITH` - Concept ↔ Concept (Co-Occurrenz-Stärke)

### Vector Embeddings vorhanden für
- `Paragraph.embedding` (3072-D, text-embedding-3-large)
- `Figure.embedding` (3072-D, text-embedding-3-large)
- `Concept.embedding` (3072-D, text-embedding-3-large)

