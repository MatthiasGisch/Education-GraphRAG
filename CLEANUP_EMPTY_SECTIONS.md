# Cleanup-Script: Behebung von leeren Sections

Führe diese Queries aus um die 118 leeren Sections zu beheben.

---

## PROBLEM: 118 leere Sections

**Auswirkung:**
- Visualisierung wird sehr unstrukturiert (orphane Nodes)
- Retrieval kann verwirrt werden
- Metadaten-Hierarchie ist unterbrochen

**Ursache:** Sections wurden erstellt, aber keine Paragraphen zugeordnet

---

## LÖSUNG 1: Leere Sections löschen (SCHNELL & SAUBER)

### 1a: Zuerst anschauen welche Sections leer sind

```cypher
MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
RETURN sec.section_id AS SectionID, sec.title AS SectionTitle, id(sec) AS NodeID
ORDER BY sec.title
LIMIT 50
```

### 1b: Alle leeren Sections löschen

```cypher
// WARNUNG: Das löscht ALLE 118 leeren Sections!
// Nur ausführen wenn du sicher bist!

MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
DETACH DELETE sec
```

**Ergebnis:** 118 orphane Sections sind weg, Graph wird sofort strukturierter

**Nach Löschung checken:**
```cypher
MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
RETURN count(sec) AS RemainingEmptySections
```

Sollte 0 sein!

---

## LÖSUNG 2: Leere Sections reparieren (LÄNGER, VORSICHTIGER)

Falls du die Sections behalten möchtest:

### 2a: Finde welche Papers zu den leeren Sections gehören

```cypher
MATCH (p:Paper)-[:HAS_SECTION]->(sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
RETURN p.title AS Paper, sec.title AS EmptySection, p.paper_id AS PaperID
ORDER BY p.title
LIMIT 20
```

### 2b: Für jede leere Section: Finde die besten Paragraphen vom Paper

```cypher
// Für ein bestimmtes Paper: Verschiebe Paragraphen in die Sections
MATCH (p:Paper {paper_id: "PAPER_ID"})
OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
WITH p, sec, 
     [para IN (p)-[:HAS_PARAGRAPH]->(:Paragraph) | para] AS orphanParas
RETURN p.title, sec.title, size(orphanParas) AS OrphanParagraphs
```

### 2c: Automatisches Reparatur-Script

```cypher
// Füge Paragraphen, die direkt am Paper hängen, in die erste Section ein
MATCH (p:Paper)
WHERE EXISTS ((p)-[:HAS_SECTION]->())
WITH p
MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
WHERE NOT EXISTS ((sec:Section)-[:HAS_PARAGRAPH]->(para))
WITH p, para
MATCH (p)-[:HAS_SECTION]->(sec:Section)
WITH p, sec, para, row_number() OVER (PARTITION BY p ORDER BY para.paragraph_id) AS seq
WHERE seq = 1  // Nur die erste Section
MERGE (sec)-[:HAS_PARAGRAPH]->(para)
```

---

## EMPFEHLUNG: Kombinierte Lösung

### Schritt 1: Analyse - Wie ist die Situation?

```cypher
MATCH (p:Paper)
WITH p,
     size([(p)-[:HAS_SECTION]->()]) AS section_count,
     size([(p)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->()]) AS paragraphs_in_sections,
     size([(p)-[:HAS_PARAGRAPH]->()]) AS total_paragraphs
WHERE section_count > 0
RETURN 
  p.title AS Paper,
  section_count AS Sections,
  paragraphs_in_sections AS ParagraphsInSections,
  total_paragraphs AS TotalParagraphs,
  total_paragraphs - paragraphs_in_sections AS OrphanParagraphs
ORDER BY (total_paragraphs - paragraphs_in_sections) DESC
LIMIT 20
```

### Schritt 2: Entscheide für jeden Paper

Basierend auf den Ergebnissen:

**Option A - Einfach:** Leere Sections löschen
```cypher
MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
DETACH DELETE sec
```

**Option B - Komplex:** Paragraphen in Sections reorganisieren
```cypher
// Für jedes Paper: Verteile Paragraphen auf Sections
MATCH (p:Paper)
WHERE EXISTS ((p)-[:HAS_SECTION]->())
WITH p
MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
WHERE NOT EXISTS ((sec:Section)-[:HAS_PARAGRAPH]->(para))
WITH p, para,
     [(p)-[:HAS_SECTION]->(sec:Section) | sec] AS sections
WHERE size(sections) > 0
WITH p, para, sections, row_number() OVER (ORDER BY para.paragraph_id) AS idx
WITH p, para, sections[(idx - 1) % size(sections)] AS target_sec
CREATE (target_sec)-[:HAS_PARAGRAPH]->(para)
```

---

## SCHRITT-FÜR-SCHRITT REPARATUR

### Plan A: Schnell reparieren (Einfach + Effektiv)

```cypher
// 1. Zeige wie viele leere Sections es gibt
MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
RETURN count(sec) AS EmptySections;

// 2. Lösche sie
MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
DETACH DELETE sec;

// 3. Verifiziere
MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
RETURN count(sec) AS RemainingEmpty;
```

**Ergebnis:** Graph ist sofort strukturierter

---

### Plan B: Vorsichtig reparieren (Behalte Sections)

```cypher
// 1. Für jedes Paper mit Orphan-Paragraphen:
MATCH (p:Paper)
WHERE EXISTS ((p)-[:HAS_PARAGRAPH]->())
AND EXISTS ((p)-[:HAS_SECTION]->())
WITH p
MATCH (p)-[:HAS_PARAGRAPH]->(orphan:Paragraph)
WHERE NOT EXISTS ((sec:Section)-[:HAS_PARAGRAPH]->(orphan))
WITH p, orphan
MATCH (p)-[:HAS_SECTION]->(first_sec:Section)
WITH p, orphan, first_sec,
     row_number() OVER (PARTITION BY p ORDER BY first_sec.section_id) AS seq
WHERE seq = 1
MERGE (first_sec)-[:HAS_PARAGRAPH]->(orphan)
RETURN count(*) AS MigratedParagraphs;

// 2. Danach: Lösche immer noch leere Sections
MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
DETACH DELETE sec;
```

---

## VERIFIKATION NACH CLEANUP

```cypher
// Check 1: Keine leeren Sections mehr?
MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
RETURN count(sec) AS StillEmptySections;

// Check 2: Alle Paragraphen verbunden?
MATCH (para:Paragraph)
WHERE NOT ((para)<-[:HAS_PARAGRAPH]-())
RETURN count(para) AS OrphanParagraphs;

// Check 3: Struktur OK?
MATCH (p:Paper)
WITH p,
     size([(p)-[:HAS_SECTION]->()]) AS sections,
     size([(p)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->()]) AS paras_in_sections
WHERE sections > 0 AND paras_in_sections = 0
RETURN count(p) AS PapersWithEmptySections;
```

Alle sollten 0 sein!

---

## GRAPH-VISUALISIERUNG DANACH

Nach dem Cleanup sollte diese Query viel sauberer aussehen:

```cypher
// Hierarchische Struktur: Paper → Sections → Paragraphs → Concepts
MATCH (p:Paper)
OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)
OPTIONAL MATCH (sec)-[:HAS_PARAGRAPH]->(para:Paragraph)
OPTIONAL MATCH (para)-[:MENTIONS]->(c:Concept)
RETURN p, sec, para, c
LIMIT 5000
```

---

## PROBLEM 2: 367 Paragraphen ohne Section-Verbindung

**Situation:** 367 Paragraphen in 6 Papers hängen direkt am Paper statt in Sections

**Auswirkung:** 
- Graph wirkt sehr flach und unstrukturiert
- Hierarchie fehlt: Paper → Section → Paragraph
- Visualisierung ist verwirrend

---

### Schritt 1: Analysiere die betroffenen Papers

```cypher
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
WHERE NOT EXISTS ((:Section)-[:HAS_PARAGRAPH]->(para))
WITH p, count(para) AS orphan_count
OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)
WITH p.title AS Paper, 
     orphan_count AS OrphanParagraphs,
     count(DISTINCT sec) AS ExistingSections
RETURN Paper, OrphanParagraphs, ExistingSections
ORDER BY OrphanParagraphs DESC
```

**Zeigt:** Welche Papers haben Sections und welche nicht

---

### Schritt 2a: Für Papers MIT Sections - Paragraphen einfügen

Wenn ein Paper bereits Sections hat, füge die Paragraphen in die erste Section ein:

```cypher
// Finde Papers mit Sections UND Orphan-Paragraphen
MATCH (p:Paper)-[:HAS_SECTION]->(sec:Section)
WITH p, sec
ORDER BY sec.section_id
WITH p, collect(sec)[0] AS firstSection
MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
WHERE NOT EXISTS ((:Section)-[:HAS_PARAGRAPH]->(para))
CREATE (firstSection)-[:HAS_PARAGRAPH]->(para)
RETURN p.title AS Paper, 
       count(para) AS ParagraphsMoved,
       firstSection.title AS TargetSection
```

**Ergebnis:** Orphan-Paragraphen werden in die erste Section jedes Papers eingefügt

---

### Schritt 2b: Für Papers OHNE Sections - Default-Section erstellen

Wenn ein Paper keine Sections hat, erstelle eine Default-Section:

```cypher
// Finde Papers OHNE Sections aber MIT Paragraphen
MATCH (p:Paper)
WHERE NOT EXISTS ((p)-[:HAS_SECTION]->())
AND EXISTS ((p)-[:HAS_PARAGRAPH]->())
CREATE (sec:Section {
  section_id: p.paper_id + '_default',
  title: 'Inhalt'
})
CREATE (p)-[:HAS_SECTION]->(sec)
WITH p, sec
MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
CREATE (sec)-[:HAS_PARAGRAPH]->(para)
RETURN p.title AS Paper,
       count(para) AS ParagraphsOrganized
```

**Ergebnis:** Neue Section "Inhalt" wird für jedes Paper erstellt

---

### KOMBINIERTER REPARATUR-WORKFLOW

```cypher
// Schritt 1: Papers MIT Sections - füge Paragraphen in erste Section ein
MATCH (p:Paper)-[:HAS_SECTION]->(sec:Section)
WITH p, sec
ORDER BY sec.section_id
WITH p, collect(sec)[0] AS firstSection
MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
WHERE NOT EXISTS ((:Section)-[:HAS_PARAGRAPH]->(para))
CREATE (firstSection)-[:HAS_PARAGRAPH]->(para);

// 2b: Papers OHNE Sections - erstelle Default-Section
MATCH (p:Paper)
WHERE NOT EXISTS ((p)-[:HAS_SECTION]->())
AND EXISTS ((p)-[:HAS_PARAGRAPH]->())
CREATE (sec:Section {
  section_id: p.paper_id + '_default',
  title: 'Inhalt'
})
CREATE (p)-[:HAS_SECTION]->(sec)
WITH p, sec
MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
CREATE (sec)-[:HAS_PARAGRAPH]->(para);

// Schritt 3: Verifiziere - sollte 0 sein!
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
WHERE NOT EXISTS ((:Section)-[:HAS_PARAGRAPH]->(para))
RETURN count(para) AS RemainingOrphanParagraphs;
```

---

### Verifikation nach Reparatur

```cypher
// Check 1: Keine Orphan-Paragraphen mehr?
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
WHERE NOT EXISTS ((:Section)-[:HAS_PARAGRAPH]->(para))
RETURN count(para) AS OrphanParagraphs;
// Sollte: 0

// Check 2: Alle Papers haben Sections?
MATCH (p:Paper)
WHERE EXISTS ((p)-[:HAS_PARAGRAPH]->())
AND NOT EXISTS ((p)-[:HAS_SECTION]->())
RETURN count(p) AS PapersWithoutSections;
// Sollte: 0

// Check 3: Struktur-Integrität
MATCH (p:Paper)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(para:Paragraph)
RETURN count(DISTINCT p) AS Papers,
       count(DISTINCT sec) AS Sections,
       count(para) AS ParagraphsInSections;
```

---

## EMPFEHLUNG FÜR DEINE 367 PARAGRAPHEN

**Führe den kombinierten Workflow aus:**

1. **Zuerst:** Leere Sections löschen (aus vorherigem Problem)
2. **Dann:** Kombinierter Reparatur-Workflow (oben)
3. **Zuletzt:** Verifikation

**Komplettes Script:**

```cypher
// === PHASE 1: Leere Sections löschen ===
MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
DETACH DELETE sec;

// === PHASE 2: Orphan-Paragraphen reparieren ===

// 2a: Papers MIT Sections
MATCH (p:Paper)-[:HAS_SECTION]->(sec:Section)
WITH p, sec
ORDER BY sec.section_id
WITH p, collect(sec)[0] AS firstSection
MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
WHERE NOT EXISTS ((:Section)-[:HAS_PARAGRAPH]->(para))
CREATE (firstSection)-[:HAS_PARAGRAPH]->(para);

// 2b: Papers OHNE Sections
MATCH (p:Paper)
WHERE NOT EXISTS ((p)-[:HAS_SECTION]->())
AND EXISTS ((p)-[:HAS_PARAGRAPH]->())
CREATE (sec:Section {
  section_id: p.paper_id + "_default",
  title: "Inhalt",
  level: 1
})
CREATE (p)-[:HAS_SECTION]->(sec)
WITH p, sec
MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
CREATE (sec)-[:HAS_PARAGRAPH]->(para);

// === PHASE 3: Verifikation ===
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
WHERE NOT EXISTS ((:Section)-[:HAS_PARAGRAPH]->(para))
RETURN count(para) AS RemainingOrphans;
```

**Nach diesem Script:** 
- ✅ Alle 367 Paragraphen in Sections organisiert
- ✅ Hierarchische Struktur: Paper → Section → Paragraph
- ✅ Visualisierung ist strukturiert und übersichtlich

---

## MEINE EMPFEHLUNG

**Nutze Plan A (Schnell):**

1. Führe aus:
```cypher
MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
DETACH DELETE sec
```

2. Verifiziere:
```cypher
MATCH (sec:Section)
WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
RETURN count(sec)
```

3. Schaue ob Paragraphen verloren gingen:
```cypher
MATCH (para:Paragraph)
WHERE NOT ((para)<-[:HAS_PARAGRAPH]-())
RETURN count(para)
```

Falls hier > 0: Nutze Plan B um die zu retten.

**Ergebnis:** Saubere, hierarchische Struktur, viel bessere Visualisierung! 🎯

