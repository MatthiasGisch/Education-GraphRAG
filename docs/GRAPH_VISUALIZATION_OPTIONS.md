# Graph-Visualisierungs-Alternativen für Neo4j Knowledge Graph

## Aktuelle Implementierung: PyVis

**Status:** ✅ Funktioniert, aber limitiert

**Vorteile:**
- ✅ Einfach zu implementieren
- ✅ Interaktiv (Drag & Drop)
- ✅ Physics-Simulation (Barnes-Hut)

**Nachteile:**
- ❌ Alte Library (wenig Updates)
- ❌ Performance-Probleme bei >200 Knoten
- ❌ Limitierte Styling-Optionen
- ❌ Keine erweiterten Layouts
- ❌ Schlechte Label-Platzierung
- ❌ Keine Hierarchie-Darstellung

---

## 🚀 Empfohlene Alternativen

### Option 1: **Streamlit-Agraph** (EMPFOHLEN für kleine/mittlere Graphs)

**Library:** `streamlit-agraph`

#### Vorteile:
- ✅ Speziell für Streamlit entwickelt
- ✅ Moderne, saubere Visualisierung
- ✅ Gute Performance (bis 500 Knoten)
- ✅ Bessere Styling-Optionen
- ✅ Force-directed Layout
- ✅ Native Streamlit-Integration

#### Nachteile:
- ⚠️ Limitierte Layout-Algorithmen
- ⚠️ Weniger Anpassungsmöglichkeiten als D3.js

#### Installation:
```bash
pip install streamlit-agraph
```

#### Code-Beispiel:
```python
from streamlit_agraph import agraph, Node, Edge, Config

def visualize_with_agraph(records: List[Dict[str, Any]], height: int = 650):
    nodes = []
    edges = []
    
    # Knoten erstellen
    for nid, meta in processed_nodes.items():
        label = meta.get('label', 'Node')
        group = meta.get('group', 'default')
        
        # Farben basierend auf Typ
        color = {
            'Concept': '#FFD700',
            'Paper': '#4A90E2',
            'Paragraph': '#7ED321',
            'Figure': '#BD10E0'
        }.get(group, '#CCCCCC')
        
        nodes.append(Node(
            id=nid,
            label=label,
            size=25,
            color=color,
            shape="dot"
        ))
    
    # Kanten erstellen
    for src, dst, rel_type in processed_edges:
        edges.append(Edge(
            source=src,
            target=dst,
            label=rel_type,
            color="#666666"
        ))
    
    config = Config(
        width=1000,
        height=height,
        directed=True,
        physics=True,
        hierarchical=False,
        nodeHighlightBehavior=True,
        highlightColor="#F7A7A6",
        collapsible=False,
        node={'labelProperty': 'label'},
        link={'labelProperty': 'label', 'renderLabel': True}
    )
    
    agraph(nodes=nodes, edges=edges, config=config)
```

---

### Option 2: **Graphistry** (BESTE Performance für große Graphs)

**Library:** `graphistry`

#### Vorteile:
- ✅ **EXTREM performant** (100.000+ Knoten)
- ✅ GPU-beschleunigt
- ✅ Automatische Cluster-Erkennung
- ✅ Wunderschöne Visualisierungen
- ✅ Zeitbasierte Animationen
- ✅ Native Neo4j-Integration

#### Nachteile:
- ⚠️ Benötigt API-Key (kostenlos für Entwicklung)
- ⚠️ Cloud-basiert (Daten werden hochgeladen)
- ⚠️ Komplexere Integration

#### Installation:
```bash
pip install graphistry
```

#### Code-Beispiel:
```python
import graphistry
import pandas as pd

graphistry.register(api=3, protocol='https', server='hub.graphistry.com', 
                    username='YOUR_USERNAME', password='YOUR_PASSWORD')

def visualize_with_graphistry(records: List[Dict[str, Any]]):
    # Convert to DataFrames
    nodes_df = pd.DataFrame([
        {'node': nid, 'label': meta['label'], 'type': meta['group']}
        for nid, meta in processed_nodes.items()
    ])
    
    edges_df = pd.DataFrame([
        {'src': src, 'dst': dst, 'rel': rel_type}
        for src, dst, rel_type in processed_edges
    ])
    
    g = graphistry.edges(edges_df, 'src', 'dst')
    g = g.nodes(nodes_df, 'node')
    g = g.bind(point_color='type', edge_label='rel')
    
    # Embed in Streamlit
    st.components.v1.iframe(g.plot(render=False), height=800)
```

---

### Option 3: **Plotly Network Graph** (Gute Balance)

**Library:** `plotly` + `networkx`

#### Vorteile:
- ✅ Bereits in vielen Projekten verfügbar
- ✅ Sehr gute Performance (bis 1000 Knoten)
- ✅ Hochwertige Visualisierungen
- ✅ Viele Layout-Algorithmen (NetworkX)
- ✅ 3D-Visualisierung möglich
- ✅ Export als Bild

#### Nachteile:
- ⚠️ Mehr Code erforderlich
- ⚠️ Weniger interaktiv als PyVis

#### Installation:
```bash
pip install plotly networkx
```

#### Code-Beispiel:
```python
import plotly.graph_objects as go
import networkx as nx

def visualize_with_plotly(records: List[Dict[str, Any]], layout='spring'):
    G = nx.DiGraph()
    
    # Graph aufbauen
    for nid, meta in processed_nodes.items():
        G.add_node(nid, **meta)
    
    for src, dst, rel_type in processed_edges:
        G.add_edge(src, dst, relation=rel_type)
    
    # Layout berechnen
    if layout == 'spring':
        pos = nx.spring_layout(G, k=0.5, iterations=50)
    elif layout == 'kamada_kawai':
        pos = nx.kamada_kawai_layout(G)
    elif layout == 'circular':
        pos = nx.circular_layout(G)
    elif layout == 'hierarchical':
        pos = nx.planar_layout(G) if nx.is_planar(G) else nx.spring_layout(G)
    
    # Kanten zeichnen
    edge_trace = go.Scatter(
        x=[], y=[],
        line=dict(width=0.5, color='#888'),
        hoverinfo='none',
        mode='lines')
    
    for edge in G.edges():
        x0, y0 = pos[edge[0]]
        x1, y1 = pos[edge[1]]
        edge_trace['x'] += tuple([x0, x1, None])
        edge_trace['y'] += tuple([y0, y1, None])
    
    # Knoten zeichnen
    node_trace = go.Scatter(
        x=[], y=[],
        text=[],
        mode='markers+text',
        textposition="top center",
        hoverinfo='text',
        marker=dict(
            showscale=True,
            colorscale='YlGnBu',
            size=10,
            colorbar=dict(
                thickness=15,
                title='Node Connections',
                xanchor='left',
                titleside='right'
            ),
            line_width=2))
    
    for node in G.nodes():
        x, y = pos[node]
        node_trace['x'] += tuple([x])
        node_trace['y'] += tuple([y])
        node_info = processed_nodes[node]
        node_trace['text'] += tuple([node_info.get('label', '')])
    
    # Farben basierend auf Verbindungen
    node_adjacencies = []
    for node in G.nodes():
        node_adjacencies.append(len(list(G.neighbors(node))))
    node_trace.marker.color = node_adjacencies
    
    fig = go.Figure(data=[edge_trace, node_trace],
                    layout=go.Layout(
                        title='Knowledge Graph Visualization',
                        showlegend=False,
                        hovermode='closest',
                        margin=dict(b=0, l=0, r=0, t=40),
                        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False))
                    )
    
    st.plotly_chart(fig, use_container_width=True)
```

---

### Option 4: **Cytoscape.js via Streamlit-Cytoscape** (Professionell)

**Library:** `streamlit-cytoscape`

#### Vorteile:
- ✅ Industrie-Standard (wird von Neo4j Bloom verwendet)
- ✅ Sehr viele Layout-Algorithmen
- ✅ Hierarchische Darstellung
- ✅ Exzellente Performance
- ✅ Umfangreiche Styling-Optionen
- ✅ Community-Detection

#### Nachteile:
- ⚠️ Komplexere API
- ⚠️ Mehr Konfiguration erforderlich

#### Installation:
```bash
pip install streamlit-cytoscape
```

#### Code-Beispiel:
```python
import streamlit_cytoscape as stcyto

def visualize_with_cytoscape(records: List[Dict[str, Any]], layout='cose'):
    elements = []
    
    # Knoten
    for nid, meta in processed_nodes.items():
        elements.append({
            'data': {
                'id': nid,
                'label': meta.get('label', ''),
                'type': meta.get('group', 'default')
            },
            'classes': meta.get('group', 'default')
        })
    
    # Kanten
    for idx, (src, dst, rel_type) in enumerate(processed_edges):
        elements.append({
            'data': {
                'id': f'edge_{idx}',
                'source': src,
                'target': dst,
                'label': rel_type
            }
        })
    
    # Stylesheet
    stylesheet = [
        {
            'selector': 'node',
            'style': {
                'label': 'data(label)',
                'width': 40,
                'height': 40,
                'font-size': '10px'
            }
        },
        {
            'selector': '.Concept',
            'style': {'background-color': '#FFD700'}
        },
        {
            'selector': '.Paper',
            'style': {'background-color': '#4A90E2'}
        },
        {
            'selector': '.Paragraph',
            'style': {'background-color': '#7ED321'}
        },
        {
            'selector': 'edge',
            'style': {
                'label': 'data(label)',
                'curve-style': 'bezier',
                'target-arrow-shape': 'triangle',
                'font-size': '8px'
            }
        }
    ]
    
    # Layout-Optionen
    layout = {
        'name': layout,  # cose, circle, grid, breadthfirst, concentric
        'animate': True,
        'nodeRepulsion': 8000,
        'idealEdgeLength': 100
    }
    
    stcyto.cytoscape(
        elements=elements,
        stylesheet=stylesheet,
        layout=layout,
        height='650px',
        key='cytoscape'
    )
```

---

## 📊 Vergleichstabelle

| Feature | PyVis | Streamlit-Agraph | Graphistry | Plotly | Cytoscape |
|---------|-------|------------------|------------|--------|-----------|
| **Performance (Knoten)** | < 200 | < 500 | 100,000+ | < 1,000 | < 5,000 |
| **Einfachheit** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| **Visualisierungsqualität** | ⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **Layout-Algorithmen** | 1 | 2 | 10+ | 5+ | 15+ |
| **Interaktivität** | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ |
| **Hierarchische Layouts** | ❌ | ❌ | ✅ | ✅ | ✅ |
| **3D-Visualisierung** | ❌ | ❌ | ✅ | ✅ | ❌ |
| **Export-Optionen** | ❌ | ❌ | ✅ | ✅ | ✅ |
| **Community Detection** | ❌ | ❌ | ✅ | ❌ | ✅ |
| **Kosten** | Kostenlos | Kostenlos | Freemium | Kostenlos | Kostenlos |

---

## 🎯 Meine Empfehlung

### Für dein wissenschaftliches GraphRAG-System:

**1. Kurzfristig (Quick Win):** 
   → **Streamlit-Agraph**
   - Einfach zu implementieren (1-2 Stunden)
   - Deutlich bessere Visualisierung als PyVis
   - Gute Performance für typische Knowledge Graphs

**2. Mittelfristig (Beste Balance):**
   → **Plotly + NetworkX**
   - Exzellente Visualisierungen
   - Viele Layout-Algorithmen (hierarchisch, circular, etc.)
   - Ideal für Paper-Präsentationen
   - Export als hochauflösende Bilder

**3. Langfristig (Production-Ready):**
   → **Cytoscape.js**
   - Industrie-Standard
   - Beste Features für Knowledge Graphs
   - Community-Detection für Cluster
   - Hierarchische Darstellung für Paper → Paragraph → Concept

---

## 🚀 Implementierungs-Roadmap

### Phase 1: Streamlit-Agraph (2 Stunden)
```python
# Ersetze visualize_records_as_graph() Funktion
# Nutze gleiche Datenstruktur, andere Library
```

### Phase 2: Layout-Auswahl (1 Stunde)
```python
# Füge Dropdown hinzu für Layout-Algorithmen:
# - Force-directed (Standard)
# - Hierarchical (Paper → Paragraph → Concept)
# - Circular (für Relationen)
# - Grid (für Übersicht)
```

### Phase 3: Erweiterte Features (2-4 Stunden)
```python
# - Node-Filterung nach Typ
# - Edge-Filterung nach Relation
# - Cluster-Highlighting
# - Search/Highlight Feature
# - Export als PNG/SVG
```

---

## 💡 Spezielle Features für dein System

### 1. Hierarchische Darstellung
Perfekt für: **Paper → Section → Paragraph → Concept**
```python
# Layout: 'breadthfirst' oder 'dagre'
# Zeigt klare Hierarchie des Dokuments
```

### 2. Semantic Relations Highlighting
```python
# Farben für verschiedene Relationen:
# - IS_A: Blau
# - PART_OF: Grün
# - CAUSES: Rot
# - CO_OCCURS_WITH: Gestrichelt
```

### 3. Entity-Type Clustering
```python
# Gruppiere ähnliche Konzepte visuell
# Nutze Community-Detection Algorithmen
```

### 4. Interactive Exploration
```python
# Click auf Knoten → Zeige Details in Sidebar
# Double-Click → Expand connected nodes
# Hover → Zeige Paragraph-Text
```

---

## 📝 Nächste Schritte

Möchtest du dass ich:

1. ✅ **Streamlit-Agraph implementiere** (schnelle Verbesserung)
2. ✅ **Plotly-Version erstelle** (beste Visualisierung)
3. ✅ **Cytoscape.js integriere** (langfristig beste Lösung)
4. ✅ **Alle drei als Optionen** (User kann wählen)

Was ist deine Präferenz?
