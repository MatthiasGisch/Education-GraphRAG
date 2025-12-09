# Neo4j Visualisierung - Cypher Queries

Diese Queries kannst du direkt in der Neo4j Browser Console ausführen (http://localhost:7474/browser/)

---

## 1. GESAMTSTRUKTUR DES WISSENSGRAPHEN

### ⭐ 1-CLICK VISUALISIERUNG: DEN KOMPLETTEN GRAPHEN ANSCHAUEN

**Für kleine Graphen (< 5000 Nodes):**

```cypher
// KOMPLETTER GRAPH - Einfach ausführen!
MATCH (n)-[r]-(m)
RETURN n, r, m
LIMIT 10000
```

**Für mittlere Graphen (5000-50000 Nodes):**

```cypher
// Strukturierter Überblick mit besserer Performance
MATCH (n)
OPTIONAL MATCH (n)-[r]-(m)
WITH n, r, m
WHERE n IS NOT NULL
RETURN n, r, m
LIMIT 5000
```

**Für große Graphen (> 50000 Nodes) - Mit Sampling:**

```cypher
// Nimmt jeden X-ten Node um Performance zu halten
MATCH (n)
WITH n, rand() AS random
WHERE random < 0.1  // Nimmt 10% aller Nodes
OPTIONAL MATCH (n)-[r]-(m)
RETURN n, r, m
LIMIT 3000
```

**Interaktiv mit Filterung nach Node-Typ:**

```cypher
// Visualisiere nur Papers, Concepts und deren Verbindungen
MATCH (n)
WHERE labels(n)[0] IN ["Paper", "Concept", "Section"]
OPTIONAL MATCH (n)-[r]-(m)
WHERE labels(m)[0] IN ["Paper", "Concept", "Section"]
RETURN n, r, m
LIMIT 5000
```

**Mit nur wichtigen Relationen:**

```cypher
// Zeige nur die "wichtigen" Verbindungen (kein Noise)
MATCH (n)-[r:HAS_PARAGRAPH|HAS_SECTION|MENTIONED_IN|IS_A|PART_OF]-(m)
RETURN n, r, m
LIMIT 5000
```

**Hierarchische Visualisierung (Paper → Sections → Paragraphs):**

```cypher
// Zeigt die Dokumentstruktur sehr übersichtlich
MATCH (p:Paper)
OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)
OPTIONAL MATCH (sec)-[:HAS_PARAGRAPH]->(para:Paragraph)
OPTIONAL MATCH (para)-[:MENTIONS]->(c:Concept)
RETURN p, sec, para, c
LIMIT 10000
```

---

### 1a: Alle Node-Typen und deren Anzahl

```cypher
CALL db.labels() YIELD label
MATCH (n)
WHERE label IN labels(n)
RETURN label AS NodeType, count(n) AS Count
ORDER BY Count DESC
```

**Ergebnis:** Zeigt wie viele Nodes es von jedem Typ gibt (Paper, Paragraph, Concept, etc.)

---

## 2. PAPER-STRUKTUR

### 2a: Ein komplettes Paper mit allen seinen Relationen

```cypher
MATCH (p:Paper {title: "TITEL_DES_PAPERS"})
OPTIONAL MATCH (p)-[r]->(n)
RETURN p, r, n
LIMIT 1000
```

**Ergebnis:** Visualisiert ein Paper und alle direkt verbundenen Nodes

**Alternative - Paper mit beliebigem Namen:**
```cypher
MATCH (p:Paper)
WHERE p.title CONTAINS "Dieselmotor"  // Anpassen nach Bedarf
RETURN p
LIMIT 5
```

### 2b: Paper → Paragraphen → Sections

```cypher
MATCH (p:Paper)
WHERE p.title CONTAINS "Dieselmotor"
OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(para:Paragraph)
RETURN p, sec, para
```

**Ergebnis:** Hierarchie: Paper → Sections → Paragraphen

### 2c: Paper mit allen Verbindungstypen

```cypher
MATCH (p:Paper {title: "TITEL"})
OPTIONAL MATCH (p)-[r1]->()
OPTIONAL MATCH (p)<-[r2]-()
RETURN 
  p.title AS Paper,
  type(r1) AS OutgoingRelationType,
  type(r2) AS IncomingRelationType,
  count(r1) AS OutgoingCount,
  count(r2) AS IncomingCount
```

---

## 3. PARAGRAPH-STRUKTUR

### 3a: Paragraphen mit ihren Quellen und Konzepten

```cypher
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
OPTIONAL MATCH (para)-[:MENTIONS]->(c:Concept)
RETURN p.title AS Paper, para.paragraph_id AS ParagraphID, para.page AS Page, 
       collect(c.name) AS MentionedConcepts
LIMIT 50
```

**Ergebnis:** Welche Konzepte werden in welchen Paragraphen erwähnt?

### 3b: Paragraph mit voller Kontextinformation

```cypher
MATCH (para:Paragraph {paragraph_id: "PARA_ID"})
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para)
OPTIONAL MATCH (sec:Section)-[:HAS_PARAGRAPH]->(para)
OPTIONAL MATCH (para)-[:MENTIONS]->(c:Concept)
OPTIONAL MATCH (para)-[:REFERS_TO]->(f:Figure)
RETURN para, p, sec, collect(c.name) AS Concepts, collect(f.figure_label) AS Figures
```

---

## 4. CONCEPT-STRUKTUR (Wissensrepräsentation)

### 4a: Top-20 Konzepte mit ihren Relationen

```cypher
MATCH (c:Concept)
OPTIONAL MATCH (c)-[rel]->(related:Concept)
WITH c, type(rel) AS RelationType, collect(related.name) AS RelatedConcepts, count(related) AS RelatedCount
RETURN c.name AS Concept, c.category AS Category, RelationType, RelatedConcepts, RelatedCount
ORDER BY RelatedCount DESC
LIMIT 20
```

**Ergebnis:** Welche Konzepte sind wie miteinander verbunden? (IS_A, PART_OF, RELATED_TO)

### 4b: Komplettes Konzept-Netzwerk (kleine Portion)

```cypher
MATCH (c:Concept)
WHERE c.category = "Technical"  // Filter nach Kategorie
OPTIONAL MATCH (c)-[rel]->(related:Concept)
RETURN c, rel, related
LIMIT 500
```

**Kategorien zum Filtern:**
- `Technical`
- `Conceptual`
- `Application`
- `Domain`

### 4c: Konzept → Paragraphen (was ist erwähnt wo?)

```cypher
MATCH (c:Concept {name: "KONZEPT_NAME"})
MATCH (c)-[:MENTIONED_IN]->(para:Paragraph)
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para)
RETURN c.name AS Concept, 
       p.title AS Paper, 
       para.paragraph_id AS ParagraphID,
       para.page AS Page,
       count(*) AS Mentions
ORDER BY Mentions DESC
```

**Ergebnis:** In welchen Papern und Paragraphen wird ein Konzept erwähnt?

### 4d: Semantische Relationen zwischen Konzepten

```cypher
MATCH (c1:Concept)-[rel:IS_A|PART_OF|RELATED_TO]->(c2:Concept)
RETURN c1.name AS Concept1, type(rel) AS Relation, c2.name AS Concept2
LIMIT 100
```

**Ergebnis:** Alle semantischen Beziehungen im Graph (IS_A, PART_OF, RELATED_TO)

---

## 5. FIGUREN / ABBILDUNGEN

### 5a: Alle Figuren mit ihren Papern

```cypher
MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
OPTIONAL MATCH (f)-[:REFERS_TO]->(para:Paragraph)
RETURN p.title AS Paper, f.figure_label AS FigureLabel, f.caption AS Caption, 
       count(para) AS ReferencedParagraphs
ORDER BY p.title
```

**Ergebnis:** Welche Abbildungen sind in welchen Papern? Was referenziert sie?

### 5b: Figur mit kontextualisierenden Absätzen

```cypher
MATCH (f:Figure {figure_id: "FIG_ID"})
MATCH (p:Paper)-[:HAS_FIGURE]->(f)
OPTIONAL MATCH (para:Paragraph)-[:REFERS_TO]->(f)
OPTIONAL MATCH (sec:Section)-[:HAS_FIGURE]->(f)
RETURN f.figure_label AS Figure, p.title AS Paper, sec.title AS Section, 
       collect(para.paragraph_id) AS ContextParagraphs, f.caption AS Caption
```

---

## 6. EMBEDDING-INDIZES

### 6a: Vector-Index Statistiken

```cypher
SHOW INDEXES
WHERE name CONTAINS "embedding"
```

**Ergebnis:** Zeigt alle Embedding-Indizes (paragraph_embedding_index, etc.)

### 6b: Entities mit Embeddings

```cypher
MATCH (n)
WHERE n.embedding IS NOT NULL
RETURN labels(n)[0] AS Type, count(n) AS Count
```

**Ergebnis:** Welche Node-Typen haben Embeddings?

---

## 7. METADATEN UND STATISTIKEN

### 7a: Paper-Metadaten Überblick

```cypher
MATCH (p:Paper)
RETURN 
  count(p) AS TotalPapers,
  count(CASE WHEN p.author IS NOT NULL THEN 1 END) AS WithAuthor,
  count(CASE WHEN p.publication_year IS NOT NULL THEN 1 END) AS WithYear,
  count(CASE WHEN p.doi IS NOT NULL THEN 1 END) AS WithDOI,
  count(CASE WHEN p.url IS NOT NULL THEN 1 END) AS WithURL
```

**Ergebnis:** Wie vollständig sind die Metadaten?

### 7b: Paragraph-Statistiken

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

**Ergebnis:** Wie viel Textmaterial haben wir?

### 7c: Konzept-Auswertung

```cypher
MATCH (c:Concept)
OPTIONAL MATCH (c)-[:MENTIONED_IN]->(p:Paragraph)
WITH c, count(p) AS MentionCount
RETURN 
  count(c) AS TotalConcepts,
  avg(MentionCount) AS AvgMentionsPerConcept,
  min(MentionCount) AS MinMentions,
  max(MentionCount) AS MaxMentions,
  count(CASE WHEN MentionCount > 10 THEN 1 END) AS ConceptsWithManyMentions
```

**Ergebnis:** Wie gut verteilt sind die Konzepte?

---

## 8. RETRIEVAL-RELEVANTE QUERIES

### 8a: Test Concept-Based Retrieval manuell

```cypher
// Simuliere: Was würde Concept-Retrieval für "Diesel" finden?

// Schritt 1: Konzepte mit "Diesel" im Namen
MATCH (c:Concept)
WHERE c.name CONTAINS "Diesel"
RETURN c.name AS Concept, c.category AS Category, c.description AS Description
```

### 8b: Paragraphen mit bestimmtem Konzept

```cypher
// Welche Paragraphen erwähnen das Konzept "Diesel"?
MATCH (c:Concept {name: "Dieselmotor"})
MATCH (c)-[:MENTIONED_IN]->(para:Paragraph)
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para)
RETURN p.title AS Paper, para.paragraph_id AS ParagraphID, para.page AS Page,
       para.text AS Text
LIMIT 10
```

### 8c: Related Concepts (Semantic Navigation)

```cypher
// Welche Konzepte sind mit "Dieselmotor" verbunden?
MATCH (c:Concept {name: "Dieselmotor"})
OPTIONAL MATCH (c)-[rel:IS_A|PART_OF|RELATED_TO]->(related:Concept)
RETURN c.name AS Concept, type(rel) AS RelationType, related.name AS RelatedConcept
```

**Ergebnis:** Zeigt das semantische Netzwerk um ein Konzept

---

## 9. DATA QUALITY CHECKS

### 9a: Verwaiste Nodes (nicht verbunden)

```cypher
MATCH (p:Paragraph)
WHERE NOT (()-[:HAS_PARAGRAPH]->(p))
RETURN count(p) AS OrphanParagraphs
```

### 9b: Papers ohne Paragraphen

```cypher
MATCH (p:Paper)
WHERE NOT ((p)-[:HAS_PARAGRAPH]->())
RETURN p.title AS PaperWithoutContent
```

### 9c: Konzepte ohne Mentions

```cypher
MATCH (c:Concept)
WHERE NOT ((c)-[:MENTIONED_IN]->())
RETURN c.name AS ConceptNotMentioned
```

---

## 10. EXPORT & ANALYSE

### 10a: Kompletter Subgraph um ein Paper

```cypher
MATCH (p:Paper {title: "TITEL"})
MATCH subgraph = (p)-[*0..3]-()
RETURN subgraph
```

**Ergebnis:** Visualisiert Netzwerk bis 3 Hops vom Paper entfernt

### 10b: Konzept-Netzwerk exportieren (JSON)

```cypher
MATCH (c:Concept)
OPTIONAL MATCH (c)-[rel]->(related:Concept)
WITH c, collect({concept: related.name, relation: type(rel)}) AS Relationships
RETURN {
  name: c.name,
  category: c.category,
  description: c.description,
  relationships: Relationships
} AS ConceptJSON
LIMIT 100
```

---

## QUICK START - Die 5 wichtigsten Queries

### Query 1: Graphen-Übersicht

```cypher
MATCH (n)
RETURN labels(n)[0] AS NodeType, count(n) AS Count
ORDER BY Count DESC
```

### Query 2: Ein Paper mit allem

```cypher
MATCH (p:Paper)
RETURN p
LIMIT 1
```
Dann die Button-Expand klicken um Relations zu sehen!

### Query 3: Concept-Netzwerk (klein)

```cypher
MATCH (c:Concept)
OPTIONAL MATCH (c)-[rel]->(related:Concept)
RETURN c, rel, related
LIMIT 100
```

### Query 4: Retrieval-Simulation

```cypher
MATCH (c:Concept {name: "Diesel"})
MATCH (c)-[:MENTIONED_IN]->(para:Paragraph)
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para)
RETURN p, para, c
LIMIT 20
```

### Query 5: Metadaten-Qualität

```cypher
MATCH (p:Paper)
RETURN count(p) AS Papers,
       count(CASE WHEN p.author IS NOT NULL THEN 1 END) AS WithAuthor,
       count(CASE WHEN p.doi IS NOT NULL THEN 1 END) AS WithDOI
```

---

## TIPPS ZUR VISUALISIERUNG

1. **Größe der Nodes:** Neo4j Browser zeigt große Nodes = mehr Connections
2. **Farben:** Wird automatisch nach Node-Typ eingefärbt
3. **Layout:** Rechtsklick → "Show simplified" für weniger Chaos
4. **Filter:** Links in der Console: `Node: Paper` etc. um bestimmte Typen zu filtern
5. **Zoom:** Mausrad zum zoomen, Drag zum Verschieben

---

## VISUALISIERUNGS-STRATEGIEN

### Strategie 1: "Ich will ALLES sehen" (Kompletter Graph)

Nutze diese Query wenn der Graph klein ist:

```cypher
MATCH (n)-[r]-(m)
RETURN n, r, m
LIMIT 10000
```

**Besser lesbar mit Filterung:**
```cypher
// Nur wichtige Relationen zeigen
MATCH (n)-[r:HAS_PARAGRAPH|HAS_SECTION|MENTIONED_IN|IS_A|PART_OF|RELATED_TO]-(m)
RETURN n, r, m
LIMIT 5000
```

### Strategie 2: "Zeige mir die Struktur" (Hierarchisch)

Zeigt Paper → Sections → Paragraphs → Concepts in Baumform:

```cypher
MATCH (p:Paper)
OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)
OPTIONAL MATCH (sec)-[:HAS_PARAGRAPH]->(para:Paragraph)
OPTIONAL MATCH (para)-[:MENTIONS]->(c:Concept)
RETURN p, sec, para, c
LIMIT 3000
```

### Strategie 3: "Fokus auf Konzepte" (Concept-Netzwerk)

Zeigt nur das Konzept-Netzwerk mit semantischen Relationen:

```cypher
MATCH (c1:Concept)-[rel:IS_A|PART_OF|RELATED_TO]-(c2:Concept)
RETURN c1, rel, c2
UNION
MATCH (c:Concept)
WHERE NOT ((c)-[:IS_A|PART_OF|RELATED_TO]-())
RETURN c
LIMIT 2000
```

### Strategie 4: "Sampling für große Graphen" (10% der Nodes)

Für sehr große Wissensgraphen - zeigt zufällige Stichprobe:

```cypher
MATCH (n)
WITH n, rand() AS r
WHERE r < 0.1  // Nimmt 10% aller Nodes
OPTIONAL MATCH (n)-[rel]-(m)
WITH n, rel, m, r
WHERE r < 0.1
RETURN n, rel, m
LIMIT 3000
```

### Strategie 5: "Paper-fokussiert" (Nur Papers mit Struktur)

Zeigt nur Papers und deren interne Struktur:

```cypher
MATCH (p:Paper)
OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)
OPTIONAL MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
OPTIONAL MATCH (p)-[:HAS_FIGURE]->(f:Figure)
RETURN p, sec, para, f
LIMIT 2000
```

### Strategie 6: "Node-Typ Filter" (Wähle selbst)

Zeige nur spezifische Node-Typen:

```cypher
// Ändere die Liste je nach Bedarf:
// ["Paper", "Concept", "Section", "Paragraph", "Figure"]
MATCH (n)
WHERE labels(n)[0] IN ["Paper", "Concept", "Section"]
OPTIONAL MATCH (n)-[r]-(m)
WHERE labels(m)[0] IN ["Paper", "Concept", "Section"]
RETURN n, r, m
LIMIT 5000
```

---

## BEISPIEL: Eine komplette Analyse-Session

```cypher
// 1. Überblick - Wie viele Nodes von jedem Typ?
MATCH (n)
RETURN labels(n)[0] AS NodeType, count(n) AS Count
ORDER BY Count DESC;

// 2. Wie viele Papers?
MATCH (p:Paper)
RETURN count(p);

// 3. Welche Papers haben wir?
MATCH (p:Paper)
RETURN p.title ORDER BY p.title;

// 4. Ein bestimmtes Paper anschauen
MATCH (p:Paper {title: "Der Dieselmotor"})
RETURN p;

// 5. Dessen Struktur
MATCH (p:Paper {title: "Der Dieselmotor"})
OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)
OPTIONAL MATCH (sec)-[:HAS_PARAGRAPH]->(para:Paragraph)
RETURN p.title, sec.title, count(para);

// 6. Konzepte im Paper
MATCH (p:Paper {title: "Der Dieselmotor"})
OPTIONAL MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
OPTIONAL MATCH (para)-[:MENTIONS]->(c:Concept)
RETURN DISTINCT c.name ORDER BY c.name;

// 7. JETZT: Kompletten Graph visualisieren!
MATCH (n)-[r:HAS_PARAGRAPH|HAS_SECTION|MENTIONED_IN|IS_A|PART_OF]-(m)
RETURN n, r, m
LIMIT 5000;
```

