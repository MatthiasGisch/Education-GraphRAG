# Semantic Relations - Cypher Query Examples

Dieses Dokument enthält Beispiel-Queries für die neuen semantischen Relationen im Knowledge Graph.

## 1. Alle Relationen eines Konzepts anzeigen

```cypher
// Ausgehende Relationen von einem Konzept
MATCH (c:Concept {concept_id: 'künstliche-intelligenz'})-[r:SEMANTIC_RELATION]->(target:Concept)
RETURN c.name AS source,
       r.relation_type AS relation,
       target.name AS target,
       r.confidence AS confidence,
       r.context AS context
ORDER BY r.confidence DESC
LIMIT 20;
```

## 2. Relationen nach Typ filtern

```cypher
// Alle IS_A Relationen (Taxonomie)
MATCH (subject:Concept)-[r:SEMANTIC_RELATION {relation_type: 'IS_A'}]->(object:Concept)
RETURN subject.name, object.name, r.confidence
ORDER BY r.confidence DESC;

// Alle PART_OF Relationen (Meronymie)
MATCH (part:Concept)-[r:SEMANTIC_RELATION {relation_type: 'PART_OF'}]->(whole:Concept)
RETURN part.name AS part, whole.name AS whole, r.confidence
ORDER BY r.confidence DESC;

// Alle kausalen Relationen
MATCH (cause:Concept)-[r:SEMANTIC_RELATION {relation_type: 'CAUSES'}]->(effect:Concept)
RETURN cause.name AS cause, effect.name AS effect, r.confidence, r.context
ORDER BY r.confidence DESC;
```

## 3. Ko-Okkurrenz-Netzwerk

```cypher
// Stark korrelierte Konzepte finden
MATCH (c1:Concept)-[r:CO_OCCURS_WITH]-(c2:Concept)
WHERE r.strength > 0.7
RETURN c1.name, c2.name, r.strength, r.count
ORDER BY r.strength DESC
LIMIT 50;
```

## 4. Konzept-Pfade finden

```cypher
// Kürzester Pfad zwischen zwei Konzepten über semantische Relationen
MATCH path = shortestPath(
  (start:Concept {concept_id: 'maschinelles-lernen'})-[:SEMANTIC_RELATION*1..5]->(end:Concept {concept_id: 'neuronale-netze'})
)
RETURN path;
```

## 5. Relation-Statistiken

```cypher
// Anzahl Relationen pro Typ
MATCH ()-[r:SEMANTIC_RELATION]->()
RETURN r.relation_type AS type, count(*) AS count
ORDER BY count DESC;

// Konzepte mit den meisten ausgehenden Relationen
MATCH (c:Concept)-[r:SEMANTIC_RELATION]->()
RETURN c.name, count(r) AS outgoing_relations
ORDER BY outgoing_relations DESC
LIMIT 20;

// Konzepte mit den meisten eingehenden Relationen
MATCH ()-[r:SEMANTIC_RELATION]->(c:Concept)
RETURN c.name, count(r) AS incoming_relations
ORDER BY incoming_relations DESC
LIMIT 20;
```

## 6. Relation-Qualität prüfen

```cypher
// Relationen mit niedriger Konfidenz (zum Review)
MATCH (s:Concept)-[r:SEMANTIC_RELATION]->(o:Concept)
WHERE r.confidence < 0.5
RETURN s.name, r.relation_type, o.name, r.confidence, r.context
ORDER BY r.confidence ASC
LIMIT 50;

// Relationen nach Paper gruppieren
MATCH (s:Concept)-[r:SEMANTIC_RELATION]->(o:Concept)
WHERE r.paper_id IS NOT NULL
RETURN r.paper_id, count(*) AS relation_count
ORDER BY relation_count DESC;
```

## 7. Semantische Suche kombiniert mit Relationen

```cypher
// Finde Konzepte und ihre Relationen für ein Thema
MATCH (c:Concept)
WHERE c.name CONTAINS 'Deep Learning'
OPTIONAL MATCH (c)-[r:SEMANTIC_RELATION]->(related:Concept)
RETURN c.name AS concept,
       collect({
         type: r.relation_type,
         target: related.name,
         confidence: r.confidence
       }) AS relations;
```

## 8. Relation-Netzwerk visualisieren

```cypher
// Subgraph für Visualisierung (z.B. in Neo4j Browser)
MATCH path = (c:Concept {name: 'Machine Learning'})-[:SEMANTIC_RELATION*1..2]-(related:Concept)
WHERE ALL(r IN relationships(path) WHERE r.confidence > 0.6)
RETURN path
LIMIT 100;
```

## 9. Transitive Relationen finden

```cypher
// Transitive IS_A Hierarchie
MATCH path = (specific:Concept)-[:SEMANTIC_RELATION*1..3 {relation_type: 'IS_A'}]->(general:Concept)
WHERE specific.name = 'Convolutional Neural Network'
RETURN [node IN nodes(path) | node.name] AS hierarchy;
```

## 10. Relation-Updates und Maintenance

```cypher
// Alle Relationen eines Papers löschen (z.B. bei Re-Ingestion)
MATCH ()-[r:SEMANTIC_RELATION {paper_id: 'paper-xyz'}]->()
DELETE r;

// Relationen mit niedriger Konfidenz löschen
MATCH ()-[r:SEMANTIC_RELATION]->()
WHERE r.confidence < 0.3
DELETE r;

// Relation-Konfidenz aktualisieren
MATCH (s:Concept {name: 'Deep Learning'})-[r:SEMANTIC_RELATION {relation_type: 'IS_A'}]->(o:Concept {name: 'Machine Learning'})
SET r.confidence = 0.95;
```

## Verfügbare Relation Types

Die folgenden Relation-Typen werden vom System extrahiert:

- **IS_A**: Taxonomische Beziehung (X ist eine Art von Y)
- **PART_OF**: Meronymische Beziehung (X ist Teil von Y)
- **CAUSES**: Kausale Beziehung (X verursacht Y)
- **REQUIRES**: Abhängigkeitsbeziehung (X benötigt Y)
- **USES**: Nutzungsbeziehung (X verwendet Y)
- **APPLIES_TO**: Anwendungsbeziehung (X wird angewendet auf Y)
- **RELATED_TO**: Allgemeine Beziehung (Fallback)
- **CO_OCCURS_WITH**: Statistische Ko-Okkurrenz (separater Relationstyp)

## Python API Beispiele

```python
from src.neo import Neo4jClient

neo = Neo4jClient()

# Relationen eines Konzepts abrufen
relations = neo.get_concept_relations(
    concept_id='machine-learning',
    relation_types=['IS_A', 'PART_OF'],
    min_confidence=0.6
)

print(f"Ausgehend: {len(relations['outgoing'])}")
print(f"Eingehend: {len(relations['incoming'])}")
print(f"Ko-Okkurrenz: {len(relations['cooccurrences'])}")

# Neue Relationen hinzufügen
relations = [
    {
        "subject": "Deep Learning",
        "predicate": "IS_A",
        "object": "Machine Learning",
        "confidence": 0.95,
        "context": "Deep Learning is a subset of Machine Learning.",
        "source": "manual"
    }
]

stats = neo.add_semantic_relations("paper-123", relations)
print(f"Created: {stats['created']}, Updated: {stats['updated']}")
```
