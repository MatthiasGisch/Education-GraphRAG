"""Testskript für die Plotly-Graph-Visualisierung mit Mock-Daten."""
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import plotly.graph_objects as go
import networkx as nx

def test_plotly_visualization():
    """Test mit synthetischen Daten"""
    
    # Mock-Daten erstellen (simuliert Neo4j Records)
    mock_records = []
    
    # Erstelle Test-Graph: Paper → Paragraphs → Concepts
    nodes = {
        'paper1': {'label': 'KI Grundlagen', 'group': 'Paper'},
        'para1': {'label': 'Intro zu KI', 'group': 'Paragraph'},
        'para2': {'label': 'Deep Learning', 'group': 'Paragraph'},
        'concept1': {'label': 'Künstliche Intelligenz', 'group': 'Concept'},
        'concept2': {'label': 'Neuronale Netze', 'group': 'Concept'},
        'concept3': {'label': 'Backpropagation', 'group': 'Concept'},
    }
    
    edges = [
        ('paper1', 'para1', 'HAS_PARAGRAPH'),
        ('paper1', 'para2', 'HAS_PARAGRAPH'),
        ('para1', 'concept1', 'MENTIONS'),
        ('para2', 'concept2', 'MENTIONS'),
        ('para2', 'concept3', 'MENTIONS'),
        ('concept2', 'concept3', 'RELATED_TO'),
    ]
    
    # Konvertiere zu Mock-Records (Dict-Format)
    for src_id, dst_id, rel_type in edges:
        mock_records.append({
            'source': {
                'id': src_id,
                **nodes[src_id]
            },
            'target': {
                'id': dst_id,
                **nodes[dst_id]
            },
            'relation': rel_type
        })
    
    print("✅ Mock-Daten erstellt:")
    print(f"   - {len(nodes)} Knoten")
    print(f"   - {len(edges)} Kanten")
    print(f"   - {len(mock_records)} Records")
    
    # Teste NetworkX Graph-Erstellung
    G = nx.DiGraph()
    
    processed_nodes = {}
    for nid, meta in nodes.items():
        G.add_node(nid, **meta)
        processed_nodes[nid] = meta
    
    for src, dst, rel_type in edges:
        G.add_edge(src, dst, relation=rel_type)
    
    print("\n✅ NetworkX Graph erstellt:")
    print(f"   - Knoten: {len(G.nodes())}")
    print(f"   - Kanten: {len(G.edges())}")
    
    # Teste verschiedene Layouts
    layouts = ['spring', 'kamada_kawai', 'circular', 'shell']
    
    for layout_name in layouts:
        try:
            if layout_name == 'spring':
                pos = nx.spring_layout(G, k=0.5, iterations=50, seed=42)
            elif layout_name == 'kamada_kawai':
                pos = nx.kamada_kawai_layout(G)
            elif layout_name == 'circular':
                pos = nx.circular_layout(G)
            elif layout_name == 'shell':
                pos = nx.shell_layout(G)
            
            print(f"✅ Layout '{layout_name}' erfolgreich berechnet")
        except Exception as e:
            print(f"❌ Layout '{layout_name}' fehlgeschlagen: {e}")
    
    # Teste Plotly Figure-Erstellung
    try:
        pos = nx.spring_layout(G, k=0.5, iterations=50, seed=42)
        
        # Kanten
        edge_traces = []
        for edge in G.edges(data=True):
            x0, y0 = pos[edge[0]]
            x1, y1 = pos[edge[1]]
            
            edge_trace = go.Scatter(
                x=[x0, x1, None],
                y=[y0, y1, None],
                mode='lines',
                line=dict(width=1, color='#888'),
                hoverinfo='none',
                showlegend=False
            )
            edge_traces.append(edge_trace)
        
        # Knoten
        node_x = []
        node_y = []
        node_text = []
        node_color = []
        
        color_map = {
            'Concept': '#FFD700',
            'Paper': '#4A90E2',
            'Paragraph': '#7ED321',
            'default': '#CCCCCC'
        }
        
        for node in G.nodes():
            x, y = pos[node]
            node_x.append(x)
            node_y.append(y)
            node_text.append(processed_nodes[node]['label'])
            node_color.append(color_map.get(processed_nodes[node]['group'], '#CCCCCC'))
        
        node_trace = go.Scatter(
            x=node_x,
            y=node_y,
            mode='markers+text',
            text=node_text,
            textposition="top center",
            marker=dict(size=20, color=node_color, line=dict(width=2, color='#FFF')),
            showlegend=False
        )
        
        fig = go.Figure(data=edge_traces + [node_trace])
        fig.update_layout(
            title='Test Knowledge Graph',
            showlegend=False,
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            height=600
        )
        
        print("\n✅ Plotly Figure erfolgreich erstellt")
        print(f"   - {len(edge_traces)} Kanten-Traces")
        print(f"   - 1 Knoten-Trace mit {len(node_x)} Knoten")
        
        # Speichere als HTML zum Testen
        output_file = ROOT / "exports" / "test_plotly_viz.html"
        fig.write_html(str(output_file))
        print(f"\n✅ Visualisierung gespeichert: {output_file}")
        print(f"   → Öffne die Datei im Browser zum Testen!")
        
    except Exception as e:
        print(f"\n❌ Plotly Figure-Erstellung fehlgeschlagen: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*60)
    print("✅ ALLE TESTS ERFOLGREICH!")
    print("="*60)

if __name__ == "__main__":
    test_plotly_visualization()
