# GraphRAG + Neo4j AuraDB (Text + Bilder) – Starter
Dieses Repo ist ein minimaler, lauffähiger Startpunkt für ein **GraphRAG-Hybridsystem** mit Neo4j AuraDB:
- PDFs ingestieren (Text + Bilder)
- Vektorindizes (Neo4j native Vector Index)
- Bild-Analyse via Vision-Language-Model (OpenAI GPT-4o)
- Antwortorchestrierung (Graph→RAG; Fallback Websuche optional)
- Einfaches FastAPI (optional, hier CLI-Skripte)

## Schnellstart
1. **.env anlegen**
   Kopiere `.env.example` nach `.env` und setze deine Werte (AuraDB + OpenAI):
   ```bash
   cp .env.example .env
   ```
2. **Abhängigkeiten**
   ```bash
   pip install -r requirements.txt
   ```
3. **spaCy Modelle installieren (für Hybrid-Extraktion)**
   ```bash
   python -m spacy download en_core_web_sm
   pip install https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/releases/v0.5.4/en_core_sci_sm-0.5.4.tar.gz
   ```
4. **Schema in Neo4j anlegen**
   ```bash
   python scripts/create_schema.py
   ```
5. **PDFs ingestieren** (Pfad(e) zu deinen PDFs angeben)
   ```bash
   python scripts/ingest.py /pfad/zu/deinen.pdf /weitere/datei.pdf
   ```
   Bilder werden in `data/images/` abgelegt und als `Figure`-Knoten verknüpft.
6. **Fragen stellen (GraphRAG)**
   ```bash
   python scripts/ask.py "Erkläre Green AI und nenne Belege."
   ```
7. **GUI Starten**
   ```bash
   streamlit run .\scripts\gui_app.py
   ```

## Features

### Hybrid Entity & Relation Extraction (NEU)
Das System bietet jetzt eine erweiterte Extraktion, die folgendes kombiniert:

- **Named Entity Recognition (NER)**: spaCy + SciSpacy für strukturierte Entitäten (PERSON, ORG, SCIENTIFIC_TERM, CHEMICAL, etc.)
- **LLM-basierte Konzeptextraktion**: Erfasst abstrakte Konzepte, Methodologien und Theorien
- **Semantische Relationen**: Extrahiert Tripel (Subject-Predicate-Object) wie IS_A, PART_OF, CAUSES, etc.
- **Ko-Okkurrenz-Analyse**: Statistische Beziehungen zwischen häufig gemeinsam auftretenden Konzepten

#### Verwendung im Code:
```python
from src.concept_extract import extract_and_embed_concepts_hybrid

result = extract_and_embed_concepts_hybrid(
    paper_title="Mein Paper",
    paper_text=full_text,
    paragraphs=paragraphs,
    max_entities=30,
    max_relations=20,
    use_scispacy=True,
    neo_client=neo,
    persist_to_topic=True
)

# result enthält: concepts, relations, paragraph_links, stats
```

#### GUI-Integration:
In der Streamlit-GUI kannst du unter "Konzept-Strategie" den Modus **"Hybrid (NER + LLM + Relationen)"** wählen, um die erweiterte Extraktion zu nutzen.

### Relation Types im Graph:
- `SEMANTIC_RELATION` - Semantische Beziehungen mit Properties:
  - `relation_type`: IS_A, PART_OF, CAUSES, REQUIRES, USES, etc.
  - `confidence`: Konfidenzwert (0.0-1.0)
  - `context`: Satz/Phrase, in dem die Relation erscheint
  - `source`: "llm" oder "cooccurrence"
- `CO_OCCURS_WITH` - Ko-Okkurrenz-Beziehungen mit Properties:
  - `count`: Anzahl gemeinsamer Vorkommen
  - `strength`: Normalisierte Stärke (0.0-1.0)

## Projektstruktur
```
graphrag-auradb-starter/
├─ .env.example
├─ requirements.txt
├─ README.md
├─ src/
│  ├─ config.py
│  ├─ neo.py
│  ├─ openai_client.py
│  ├─ pdf_ingest.py
│  ├─ retriever.py
│  ├─ agent.py
│  └─ graph_schema.cypher
├─ scripts/
│  ├─ create_schema.py
│  ├─ ingest.py
│  └─ ask.py
└─ data/
   └─ images/
```

# AuraDB Cypherabfragen

## DIAGNOSE: Was ist in der Datenbank?

### Alle Knoten-Typen zählen
```cypher
MATCH (n)
RETURN labels(n) AS NodeType, count(n) AS Count
ORDER BY Count DESC
```

### Topics und ihre Konzepte
```cypher
MATCH (t:Topic)
OPTIONAL MATCH (t)-[:HAS_CONCEPT]->(c:Concept)
RETURN t.name AS Topic, count(c) AS ConceptCount
```

### Papers und ihre Komponenten
```cypher
MATCH (p:Paper)
OPTIONAL MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
OPTIONAL MATCH (p)-[:HAS_FIGURE]->(fig:Figure)
OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)
RETURN p.title AS Paper, 
       count(DISTINCT para) AS Paragraphs,
       count(DISTINCT fig) AS Figures,
       count(DISTINCT sec) AS Sections
LIMIT 10
```

### Konzept-Paragraph Verknüpfungen prüfen
```cypher
MATCH (para:Paragraph)-[:MENTIONS]->(c:Concept)
RETURN count(*) AS MentionsCount
```

### Semantische Relationen prüfen (Hybrid-Modus)
```cypher
MATCH (c1:Concept)-[r:SEMANTIC_RELATION]->(c2:Concept)
RETURN c1.name, r.relation_type, c2.name, r.confidence
LIMIT 20
```

## TOPIC->CONCEPTS
```cypher
MATCH p = (:Topic)-[:HAS_CONCEPT]->(:Concept)
RETURN p
LIMIT 100
```

## PAPER->PARAGRAPHS
```cypher
MATCH p = (paper:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
RETURN p
LIMIT 100
```

## PAPER -> FIGURES/MENTIONS/PARAGRAPHS -> CONCEPTS
```cypher
MATCH (t:Topic {name:$topic})
OPTIONAL MATCH (t)-[:HAS_CONCEPT]->(c:Concept)
WITH c
WHERE c IS NOT NULL
OPTIONAL MATCH p1 = (c)<-[:MENTIONS]-(para:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
OPTIONAL MATCH p2 = (paper)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(para)
OPTIONAL MATCH p3 = (paper)-[:HAS_FIGURE]->(figP:Figure)
OPTIONAL MATCH p4 = (sec)-[:HAS_FIGURE]->(figS:Figure)
RETURN p1, p2, p3, p4
LIMIT 500
```

## Einfache Graph-Visualisierung (ohne Topic-Parameter)
```cypher
MATCH (paper:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)-[:MENTIONS]->(c:Concept)
OPTIONAL MATCH (paper)-[:HAS_FIGURE]->(fig:Figure)
RETURN paper, para, c, fig
LIMIT 100
```

## Hybrid-Modus: Konzepte mit Relationen
```cypher
MATCH (c1:Concept)-[r:SEMANTIC_RELATION]->(c2:Concept)
OPTIONAL MATCH (c1)<-[:MENTIONS]-(para:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
RETURN c1, r, c2, para, paper
LIMIT 100
```