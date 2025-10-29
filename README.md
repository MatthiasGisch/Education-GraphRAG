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
3. **Schema in Neo4j anlegen**
   ```bash
   python scripts/create_schema.py
   ```
4. **PDFs ingestieren** (Pfad(e) zu deinen PDFs angeben)
   ```bash
   python scripts/ingest.py /pfad/zu/deinen.pdf /weitere/datei.pdf
   ```
   Bilder werden in `data/images/` abgelegt und als `Figure`-Knoten verknüpft.
5. **Fragen stellen (GraphRAG)**
   ```bash
   python scripts/ask.py "Erkläre Green AI und nenne Belege."
   ```
6. **GUI Starten**
   streamlit run .\scripts\gui_app.py

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

## TOPIC->CONCEPTS
MATCH p = (:Topic)-[:HAS_CONCEPT]->(:Concept)
RETURN p
LIMIT 100

## PAPER->PARAGRAPHS
MATCH p = (paper:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
RETURN p
LIMIT 100

## PAPER -> FIGURES/MENTIONS/PARAGRAPHS -> CONCEPTS
MATCH (t:Topic {name:$topic})
OPTIONAL MATCH (t)-[hu]->(u:Umbrella)-[:NARROWER]->(c1:Concept)
OPTIONAL MATCH (t)-[:HAS_CONCEPT]->(c2:Concept)
WITH t, collect(DISTINCT c1) + collect(DISTINCT c2) AS cs
UNWIND cs AS c
WITH DISTINCT c
OPTIONAL MATCH p1 = (c)<-[:MENTIONS]-(para:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
OPTIONAL MATCH p2 = (paper)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(para)
OPTIONAL MATCH p3 = (paper)-[:HAS_FIGURE]->(figP:Figure)
OPTIONAL MATCH p4 = (sec)-[:HAS_FIGURE]->(figS:Figure)
RETURN p1, p2, p3, p4
LIMIT 500;