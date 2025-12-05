# scripts/gui_app.py
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import re
import json
import traceback
from pathlib import Path
from typing import List, Dict, Any

_PARAM_RE = re.compile(r"^\s*:param\s+([A-Za-z_]\w*)\s*=>\s*(.+?);?\s*$")

import streamlit as st
import streamlit.components.v1 as components
from neo4j.graph import Path as NeoPath, Node as NeoNode, Relationship as NeoRel
from pyvis.network import Network
import plotly.graph_objects as go
import networkx as nx

# ---- Projekt-Root in sys.path aufnehmen ----
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---- App-Imports (aus deinem Projekt) ----
import src.config as cfg
from src.neo import Neo4jClient
from src.pdf_ingest import (
    read_pdf_text_and_images,  # -> (paper_meta, sections, paragraphs, figures)
    embed_paragraphs,          # -> paragraphs mit embedding
    analyze_and_embed_figures  # -> figures mit caption/analysis_json/embedding
)
from src.pdf_export import write_answer_pdf
from src.agent import answer_query
from src.concept_extract import (
    extract_and_embed_concepts,
    extract_and_embed_concepts_hybrid
)
from src.ingest_enhanced import (
    calculate_dynamic_parameters,
    infer_topic_from_title,
    check_for_duplicates,
    extract_enhanced_metadata,
    filter_low_quality_concepts,
    validate_ingestion_quality,
    create_ingestion_summary
)

# Import für Gamma API
from src.gamma import GammaClient, to_gamma_input_text
from src.gamma_client import generate_presentation
from src.synthesia_client import generate_video_from_pptx_via_synthesia

GRAPH_SCHEMA_PATH = ROOT / "src" / "graph_schema.cypher"
UPLOAD_DIR = ROOT / "data" / "uploads"
EXPORTS_DIR = ROOT / "exports"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="GraphRAG GUI", layout="wide")


# =========================
# Helpers
# =========================
def load_schema_text() -> str:
    return GRAPH_SCHEMA_PATH.read_text(encoding="utf-8")


def _themes_file_path() -> Path:
    return EXPORTS_DIR / "gamma_themes.json"


def load_gamma_themes() -> list:
    """Load saved Gamma theme names from exports/gamma_themes.json or return defaults."""
    p = _themes_file_path()
    defaults = ["Oasis", "Minimal", "Corporate"]
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8") or "[]")
            if isinstance(data, list) and data:
                return data
    except Exception:
        pass
    return defaults


def save_gamma_theme(name: str) -> None:
    """Save a theme name to the themes file (prepend, keep unique)."""
    name = (name or "").strip()
    if not name:
        return
    p = _themes_file_path()
    try:
        themes = load_gamma_themes()
        if name in themes:
            # move to front
            themes.remove(name)
            themes.insert(0, name)
        else:
            themes.insert(0, name)
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(themes, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # best-effort; avoid crashing the GUI
        pass


def remove_gamma_theme(name: str) -> None:
    """Remove a saved theme from exports/gamma_themes.json (best-effort)."""
    try:
        p = _themes_file_path()
        if not p.exists():
            return
        data = json.loads(p.read_text(encoding="utf-8") or "[]")
        if not isinstance(data, list):
            return
        if name in data:
            data = [t for t in data if t != name]
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

@st.cache_resource(show_spinner=False)
def get_neo() -> Neo4jClient:
    return Neo4jClient()

def get_single_value(neo: Neo4jClient, cypher: str) -> int:
    res = neo.run(cypher)
    return int(res[0]["c"]) if res and "c" in res[0] else 0

def create_schema():
    """Schema-Datei laden, Kommentare entfernen, Statements ausführen."""
    neo = get_neo()
    cypher = load_schema_text()
    lines = []
    for line in cypher.splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        lines.append(line)
    cleaned = "\n".join(lines)
    statements = [s.strip() for s in cleaned.split(";") if s.strip()]
    for stmt in statements:
        neo.run(stmt)

def stitch_graph() -> dict:
    """ Vernäht Paragraphs/Figures mit Sections (per page-range) + Dummy-Section pro Paper falls nötig. """
    neo = get_neo()
    return neo.stitch_document_hierarchy()

def clear_graph() -> dict:
    """Löscht alle Nodes und Relationships in kleinen Batches (verhindert Memory-Fehler)."""
    neo = get_neo()
    
    # Sehr konservative Batch-Größe für große Graphen
    batch_size = 1000
    deleted_total = 0
    max_iterations = 10000  # Safety limit
    
    print("Starting graph deletion in batches...")
    
    for iteration in range(max_iterations):
        try:
            result = neo.run(
                f"""
                CALL {{
                    MATCH (n)
                    WITH n LIMIT {batch_size}
                    DETACH DELETE n
                    RETURN count(n) AS deleted
                }} IN TRANSACTIONS OF 100 ROWS
                RETURN sum(deleted) AS deleted
                """
            )
            deleted = result[0]["deleted"] if result and result[0]["deleted"] else 0
            deleted_total += deleted
            
            if deleted == 0:
                break  # Keine Nodes mehr übrig
            
            if iteration % 10 == 0:
                print(f"  Deleted {deleted_total} nodes so far...")
                
        except Exception as e:
            # Fallback: Versuche noch kleinere Batches ohne TRANSACTIONS
            print(f"  Switching to simpler deletion method...")
            try:
                result = neo.run(
                    f"""
                    MATCH (n)
                    WITH n LIMIT 100
                    DETACH DELETE n
                    RETURN count(n) AS deleted
                    """
                )
                deleted = result[0]["deleted"] if result else 0
                deleted_total += deleted
                
                if deleted == 0:
                    break
            except Exception as e2:
                print(f"  Error during deletion: {e2}")
                break
    
    return {
        "deleted_total": deleted_total,
        "nodes_after": get_single_value(neo, "MATCH (n) RETURN count(n) AS c"),
        "rels_after":  get_single_value(neo, "MATCH ()-[r]->() RETURN count(r) AS c")
    }

def run_cypher(query: str) -> List[Dict[str, Any]]:
    neo = get_neo()
    return neo.run(query)

def visualize_records_as_graph(records: List[Dict[str, Any]], height: int = 650) -> None:
    """
    Visualisiert Neo4j-Resultate robust:
    - echte Neo4j-Objekte (NeoPath/NeoNode/NeoRel)
    - dict-Serialisierungen (nodes/relationships, segments)
    - TRIPLET-LISTEN: [node_like, "RELTYPE", node_like, ...]  <-- dein Format
    - filtert große Props (z.B. embedding)
    """
    import uuid
    from neo4j.graph import Path as NeoPath, Node as NeoNode, Relationship as NeoRel
    from pyvis.network import Network
    import streamlit.components.v1 as components

    if not records:
        st.warning("Deine Query hat 0 Records geliefert – bitte Query/Preset prüfen.")
        return

    nodes: dict[str, dict] = {}              # id -> {"labels":[...], "props":{...}, "group": str}
    edges: set[tuple[str, str, str]] = set() # (src, dst, type)

    # ---------- Heuristiken ----------
    LARGE_KEYS = {"embedding", "vector", "tokens", "content_embeddings"}
    ID_KEYS = ["paper_id", "section_id", "paragraph_id", "figure_id", "concept_id", "umbrella_id", "topic_id"]

    def _as_str(x): return "" if x is None else str(x)

    def _guess_label_from_props(props: dict) -> str:
        if "concept_id" in props:   return "Concept"
        if "umbrella_id" in props:  return "Umbrella"
        if "paper_id" in props:     return "Paper"
        if "section_id" in props:   return "Section"
        if "paragraph_id" in props: return "Paragraph"
        if "figure_id" in props:    return "Figure"
        if "name" in props:         return "Topic"
        return "Node"

    def _node_id_from_props(props: dict) -> str:
        # bevorzuge stabile IDs
        for k in ID_KEYS:
            if k in props:
                return f"{k}:{props[k]}"
        if "name" in props:
            return f"name:{props['name']}"
        return str(uuid.uuid4())

    def _node_id(n: Any) -> str:
        if isinstance(n, NeoNode):
            if getattr(n, "element_id", None): return str(n.element_id)
            if getattr(n, "id", None) is not None: return str(n.id)
            try: return _node_id_from_props(dict(n))
            except Exception: return str(uuid.uuid4())
        if isinstance(n, dict):
            # häufige Felder für Ids
            for k in ("element_id","elementId","id","identity"):
                if k in n and n[k] is not None:
                    return _as_str(n[k])
            return _node_id_from_props(n)
        # falls nur eine „ID“ (string/int) geliefert wird
        if isinstance(n, (str, int)):
            return f"id:{n}"
        return str(uuid.uuid4())

    def _clean_props(d: dict) -> dict:
        out = {}
        for k, v in (d or {}).items():
            if k in LARGE_KEYS:  # riesige Felder nicht in Tooltip
                continue
            if isinstance(v, (list, tuple)) and len(v) > 30:
                continue
            s = _as_str(v)
            if len(s) > 300:
                s = s[:300] + " …"
            out[k] = s
        return out

    def _ensure_node(n_like: Any) -> str:
        # n_like kann dict, NeoNode, scalar-ID sein
        if isinstance(n_like, (NeoNode, dict)):
            props = dict(n_like) if isinstance(n_like, NeoNode) else (n_like or {})
            nid = _node_id(n_like)
            lbl = _guess_label_from_props(props if isinstance(props, dict) else {})
            if nid not in nodes:
                nodes[nid] = {"labels":[lbl], "props": _clean_props(props), "group": lbl}
            else:
                nodes[nid]["props"].update(_clean_props(props))
                if lbl not in nodes[nid]["labels"]:
                    nodes[nid]["labels"].append(lbl)
                nodes[nid]["group"] = nodes[nid]["group"] or lbl
            return nid
        else:
            nid = _node_id(n_like)
            if nid not in nodes:
                nodes[nid] = {"labels":["Node"], "props":{}, "group":"Node"}
            return nid

    def _add_edge(sid: str, tid: str, rtype: str):
        edges.add((sid, tid, rtype or "REL"))

    # ---------- Relationship & Path ----------
    def _add_rel_obj(r: Any):
        try:
            s = _ensure_node(r.start_node); t = _ensure_node(r.end_node)
            _add_edge(s, t, str(r.type))
        except Exception:
            pass

    def _add_rel_dict(d: dict):
        rtype = _as_str(d.get("type") or d.get("rel_type") or d.get("label") or "REL")
        s_obj = d.get("start") or d.get("from") or d.get("source") or d.get("startNode")
        t_obj = d.get("end")   or d.get("to")   or d.get("target") or d.get("endNode")
        s = _ensure_node(s_obj); t = _ensure_node(t_obj)
        _add_edge(s, t, rtype)

    def _is_triplet_path(lst: list) -> bool:
        # Muster: [node_like, "RELTYPE", node_like, "RELTYPE", node_like, ...], Länge ungerade >=3
        if not isinstance(lst, list) or len(lst) < 3 or len(lst) % 2 == 0:
            return False
        for i in range(0, len(lst), 2):
            if not isinstance(lst[i], (dict, NeoNode, str, int)):
                return False
        for j in range(1, len(lst), 2):
            if not isinstance(lst[j], str):
                return False
        return True

    def _add_triplet_path(lst: list):
        # läuft paarweise über [node, "REL", node, ...]
        for i in range(0, len(lst)-2, 2):
            n1 = lst[i]
            rel = lst[i+1]
            n2 = lst[i+2]
            s = _ensure_node(n1)
            t = _ensure_node(n2)
            _add_edge(s, t, _as_str(rel))

    def _add_path_like(p: Any):
        if isinstance(p, NeoPath):
            for n in p.nodes: _ensure_node(n)
            for r in p.relationships: _add_rel_obj(r)
            return
        if isinstance(p, dict) and "nodes" in p and "relationships" in p:
            for nd in p["nodes"]: _ensure_node(nd)
            for rd in p["relationships"]:
                if isinstance(rd, NeoRel): _add_rel_obj(rd)
                elif isinstance(rd, dict): _add_rel_dict(rd)
            return
        if isinstance(p, dict) and "segments" in p:
            for seg in p["segments"] or []:
                if not isinstance(seg, dict): continue
                s = seg.get("start"); r = seg.get("relationship"); e = seg.get("end")
                if s is not None: _ensure_node(s)
                if e is not None: _ensure_node(e)
                if r is not None:
                    if isinstance(r, NeoRel): _add_rel_obj(r)
                    elif isinstance(r, dict):
                        if isinstance(s, dict): r.setdefault("start", s)
                        if isinstance(e, dict): r.setdefault("end", e)
                        _add_rel_dict(r)
            return
        # neu: Triplet-Listen
        if isinstance(p, list) and _is_triplet_path(p):
            _add_triplet_path(p)
            return

    # ---------- Rekursives Traversieren ----------
    def _walk(obj: Any):
        if obj is None: return
        if isinstance(obj, (NeoPath,)):
            _add_path_like(obj); return
        if isinstance(obj, NeoNode):
            _ensure_node(obj); return
        if isinstance(obj, NeoRel):
            _add_rel_obj(obj); return
        if isinstance(obj, dict):
            # direkt Node/Rel/Path?
            if ("nodes" in obj and "relationships" in obj) or ("segments" in obj):
                _add_path_like(obj)
            elif ("labels" in obj) or ("properties" in obj) or any(k in obj for k in ID_KEYS):
                _ensure_node(obj)
            elif ("type" in obj) and any(k in obj for k in ("start","end","startNode","endNode","from","to","source","target")):
                _add_rel_dict(obj)
            # tiefer gehen
            for v in obj.values():
                _walk(v)
            return
        if isinstance(obj, list):
            if _is_triplet_path(obj):
                _add_triplet_path(obj); return
            for v in obj: _walk(v)
            return
        # scalars ignorieren

    # ---------- Records verarbeiten ----------
    for rec in records:
        try:
            vals = rec.values() if hasattr(rec, "values") else (rec if isinstance(rec, dict) else [rec])
        except Exception:
            vals = [rec]
        for v in vals: _walk(v)

    if not nodes and not edges:
        st.warning("Keine Knoten/Kanten aus dem Ergebnis extrahiert. Prüfe deine Query oder aktiviere den Test unten.")
        return

    net = Network(height=f"{height}px", width="100%", directed=True, notebook=False, bgcolor="#ffffff")
    net.barnes_hut()

    for nid, meta in nodes.items():
        labels = meta.get("labels") or []
        props  = meta.get("props") or {}
        label  = props.get("title") or props.get("name") or props.get("display") \
                 or props.get("paper_id") or props.get("section_id") or props.get("paragraph_id") \
                 or (labels[0] if labels else "Node")
        tooltip = "<br/>".join(f"{k}: {v}" for k, v in props.items())
        group   = meta.get("group") or (labels[0] if labels else "Node")
        net.add_node(nid, label=str(label), title=tooltip, group=group)

    for s, t, rt in edges:
        net.add_edge(s, t, label=rt)

    st.caption(f"Visualisierung: {len(nodes)} Knoten, {len(edges)} Kanten")
    html = net.generate_html(notebook=False)
    components.html(html, height=height, scrolling=True)

def _parse_browser_params(lines: list[str]) -> tuple[dict, list[str]]:
    """
    Extrahiert Browser-Param-Zeilen (:param key => value) aus dem Query-Text.
    Gibt (params, rest_lines) zurück. value darf string ('"…"','\'…\''), Zahl, JSON ({} / []) sein.
    """
    params = {}
    rest = []
    for line in lines:
        m = _PARAM_RE.match(line)
        if not m:
            rest.append(line)
            continue
        key, raw = m.group(1), m.group(2).strip()
        # String?
        if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
            val = raw[1:-1]
        else:
            # JSON / Zahl / bool / null
            try:
                val = json.loads(raw)
            except Exception:
                # einfache Nummer?
                try:
                    if "." in raw:
                        val = float(raw)
                    else:
                        val = int(raw)
                except Exception:
                    val = raw  # als String durchreichen
        params[key] = val
    return params, rest

def _extract_params_from_textarea(json_text: str) -> dict:
    """
    Versucht, JSON aus der separaten Param-Textbox zu parsen.
    """
    if not json_text or not json_text.strip():
        return {}
    try:
        data = json.loads(json_text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}

def run_cypher_with_params(query_text: str, for_graph: bool = False) -> list[dict]:
    """
    Erlaubt Browser-Style :param-Zeilen und/oder JSON-Param-Textbox.
    Setzt, falls nicht vorhanden, automatisch ein Topic aus der DB.
    
    Args:
        query_text: Cypher Query mit optionalen :param Zeilen
        for_graph: Wenn True, verwende run_graph() für Graph-Visualisierung (behält Neo4j-Objekte)
    """
    neo = get_neo()
    lines = query_text.splitlines()
    browser_params, rest_lines = _parse_browser_params(lines)
    cypher = "\n".join([ln for ln in rest_lines if ln.strip()])

    ui_params = st.session_state.get("cypher_ui_params", {}) or {}
    params = {**browser_params, **ui_params}

    # falls topic-Param fehlt: erstes Topic aus DB nehmen
    if "topic" not in params or not params["topic"]:
        try:
            topics = list_topics()
            if topics:
                params["topic"] = topics[0]
        except Exception:
            pass

    # Nur erstes Statement nehmen (falls ; im Text)
    if ";" in cypher.strip():
        cypher = cypher.split(";", 1)[0]

    # Für Graph-Visualisierung: rohe Neo4j-Objekte behalten
    if for_graph:
        return neo.run_graph(cypher, params)
    else:
        return neo.run(cypher, params)


def visualize_with_plotly(
    records: List[Dict[str, Any]], 
    height: int = 650,
    layout: str = 'spring',
    node_size_mode: str = 'degree',
    show_edge_labels: bool = True,
    color_by: str = 'type'
) -> None:
    """
    Plotly + NetworkX basierte Graph-Visualisierung mit mehreren Layout-Algorithmen.
    
    Args:
        records: Liste von Neo4j-Records (wie bei visualize_records_as_graph)
        height: Höhe der Visualisierung in Pixeln
        layout: Layout-Algorithmus ('spring', 'kamada_kawai', 'circular', 'hierarchical', 'shell')
        node_size_mode: Größenberechnung ('degree', 'uniform', 'betweenness')
        show_edge_labels: Zeige Relationsnamen auf Kanten
        color_by: Färbung ('type', 'degree', 'community')
    """
    # 1) Datenstrukturen für Knoten & Kanten sammeln
    processed_nodes: Dict[str, Dict[str, Any]] = {}
    processed_edges: List[tuple] = []
    node_set = set()
    
    def _extract_node_info(obj: Any) -> tuple:
        """Hilfsfunktion: Extrahiere (node_id, label, group) aus verschiedenen Objekttypen."""
        if isinstance(obj, NeoNode):
            nid = str(obj.element_id)
            labels = list(obj.labels) if obj.labels else ["Node"]
            label_str = ":".join(labels)
            props = dict(obj)
            
            # Bestimme Anzeige-Label
            display_label = props.get("name", props.get("title", props.get("text", nid)))
            if isinstance(display_label, str) and len(display_label) > 50:
                display_label = display_label[:47] + "..."
            
            return nid, display_label, label_str, props
        elif isinstance(obj, dict):
            nid = str(obj.get("id", obj.get("element_id", id(obj))))
            label = obj.get("label", obj.get("name", "Node"))
            group = obj.get("group", "default")
            return nid, label, group, obj
        else:
            nid = str(id(obj))
            return nid, str(obj), "unknown", {}
    
    def _walk(obj: Any):
        """Rekursiv durch Neo4j-Objekte laufen."""
        if isinstance(obj, NeoPath):
            for node in obj.nodes:
                _walk(node)
            for rel in obj.relationships:
                _walk(rel)
        elif isinstance(obj, NeoNode):
            nid, label, group, props = _extract_node_info(obj)
            if nid not in processed_nodes:
                processed_nodes[nid] = {
                    'label': label,
                    'group': group,
                    'properties': props
                }
                node_set.add(nid)
        elif isinstance(obj, NeoRel):
            src_id = str(obj.start_node.element_id)
            dst_id = str(obj.end_node.element_id)
            rel_type = obj.type if obj.type else "RELATED"
            
            _walk(obj.start_node)
            _walk(obj.end_node)
            processed_edges.append((src_id, dst_id, rel_type))
        elif isinstance(obj, dict):
            # Triplet-Format: {source, target, relation}
            if "source" in obj and "target" in obj:
                src = obj["source"]
                tgt = obj["target"]
                rel = obj.get("relation", "RELATED")
                
                src_info = _extract_node_info(src)
                tgt_info = _extract_node_info(tgt)
                
                if src_info[0] not in processed_nodes:
                    processed_nodes[src_info[0]] = {
                        'label': src_info[1],
                        'group': src_info[2],
                        'properties': src_info[3]
                    }
                if tgt_info[0] not in processed_nodes:
                    processed_nodes[tgt_info[0]] = {
                        'label': tgt_info[1],
                        'group': tgt_info[2],
                        'properties': tgt_info[3]
                    }
                
                processed_edges.append((src_info[0], tgt_info[0], rel))
            else:
                # Nested dicts
                for v in obj.values():
                    _walk(v)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                _walk(item)
    
    # Records durchlaufen
    for rec in records:
        if isinstance(rec, dict):
            _walk(rec)
        else:
            _walk(rec)
    
    # Debug-Ausgabe
    st.info(f"🔍 Debug: {len(processed_nodes)} Knoten, {len(processed_edges)} Kanten gefunden")
    
    if not processed_nodes:
        st.warning("⚠️ Keine Knoten gefunden zum Visualisieren. Prüfe ob die Query Nodes/Paths zurückgibt.")
        return
    
    # 2) NetworkX Graph aufbauen
    G = nx.DiGraph()
    
    for nid, meta in processed_nodes.items():
        G.add_node(nid, **meta)
    
    for src, dst, rel_type in processed_edges:
        if src in G and dst in G:
            G.add_edge(src, dst, relation=rel_type)
    
    # Debug-Ausgabe
    st.info(f"📊 NetworkX Graph: {len(G.nodes())} Knoten, {len(G.edges())} Kanten")
    
    if len(G.nodes()) == 0:
        st.warning("⚠️ Graph ist leer nach NetworkX-Erstellung.")
        return
    
    # 3) Layout berechnen
    try:
        if layout == 'spring':
            pos = nx.spring_layout(G, k=0.5, iterations=50, seed=42)
        elif layout == 'kamada_kawai':
            pos = nx.kamada_kawai_layout(G)
        elif layout == 'circular':
            pos = nx.circular_layout(G)
        elif layout == 'shell':
            pos = nx.shell_layout(G)
        elif layout == 'hierarchical':
            # Versuche hierarchisches Layout
            if nx.is_directed_acyclic_graph(G):
                pos = nx.planar_layout(G) if nx.check_planarity(G)[0] else nx.spring_layout(G, k=1.0, iterations=50)
            else:
                # Fallback zu spring wenn nicht DAG
                pos = nx.spring_layout(G, k=1.0, iterations=50, seed=42)
        else:
            pos = nx.spring_layout(G, k=0.5, iterations=50, seed=42)
    except:
        # Fallback bei Layout-Problemen
        pos = nx.spring_layout(G, k=0.5, iterations=50, seed=42)
    
    # 4) Kanten-Visualisierung
    edge_traces = []
    
    for edge in G.edges(data=True):
        x0, y0 = pos[edge[0]]
        x1, y1 = pos[edge[1]]
        
        # Kanten-Linie
        edge_trace = go.Scatter(
            x=[x0, x1, None],
            y=[y0, y1, None],
            mode='lines',
            line=dict(width=1, color='#888'),
            hoverinfo='none',
            showlegend=False
        )
        edge_traces.append(edge_trace)
        
        # Kanten-Label (optional)
        if show_edge_labels:
            rel_type = edge[2].get('relation', '')
            if rel_type:
                edge_label_trace = go.Scatter(
                    x=[(x0 + x1) / 2],
                    y=[(y0 + y1) / 2],
                    mode='text',
                    text=[rel_type],
                    textfont=dict(size=8, color='#666'),
                    hoverinfo='none',
                    showlegend=False
                )
                edge_traces.append(edge_label_trace)
    
    # 5) Knoten-Visualisierung
    node_x = []
    node_y = []
    node_text = []
    node_hover = []
    node_color = []
    node_size = []
    
    # Farb-Mapping nach Typ
    color_map = {
        'Concept': '#FFD700',      # Gold
        'Paper': '#4A90E2',        # Blau
        'Paragraph': '#7ED321',    # Grün
        'Figure': '#BD10E0',       # Lila
        'Section': '#F5A623',      # Orange
        'default': '#CCCCCC'       # Grau
    }
    
    # Community Detection (optional)
    communities = None
    if color_by == 'community' and len(G) > 2:
        try:
            communities = nx.community.greedy_modularity_communities(G.to_undirected())
            node_to_community = {}
            for i, comm in enumerate(communities):
                for node in comm:
                    node_to_community[node] = i
        except:
            communities = None
    
    # Betweenness Centrality (optional)
    betweenness = None
    if node_size_mode == 'betweenness':
        try:
            betweenness = nx.betweenness_centrality(G)
        except:
            betweenness = None
    
    for node in G.nodes():
        x, y = pos[node]
        node_x.append(x)
        node_y.append(y)
        
        node_info = processed_nodes[node]
        label = node_info.get('label', '')
        group = node_info.get('group', 'default')
        props = node_info.get('properties', {})
        
        node_text.append(label)
        
        # Hover-Text mit Details
        hover_parts = [f"<b>{label}</b>", f"Typ: {group}"]
        if props:
            for k, v in list(props.items())[:5]:  # Nur erste 5 Properties
                if k not in ['embedding', 'label', 'name', 'title']:
                    v_str = str(v)
                    if len(v_str) > 50:
                        v_str = v_str[:47] + "..."
                    hover_parts.append(f"{k}: {v_str}")
        
        degree = G.degree(node)
        hover_parts.append(f"Verbindungen: {degree}")
        node_hover.append("<br>".join(hover_parts))
        
        # Farbe bestimmen
        if color_by == 'type':
            node_color.append(color_map.get(group, color_map['default']))
        elif color_by == 'degree':
            node_color.append(degree)
        elif color_by == 'community' and communities:
            comm_id = node_to_community.get(node, 0)
            node_color.append(comm_id)
        else:
            node_color.append(color_map.get(group, color_map['default']))
        
        # Größe bestimmen
        if node_size_mode == 'degree':
            size = 10 + min(degree * 3, 50)
        elif node_size_mode == 'betweenness' and betweenness:
            size = 10 + betweenness[node] * 100
        else:
            size = 20
        node_size.append(size)
    
    # Knoten-Trace
    node_trace = go.Scatter(
        x=node_x,
        y=node_y,
        mode='markers+text',
        text=node_text,
        textposition="top center",
        textfont=dict(size=10, color='#000'),
        hovertext=node_hover,
        hoverinfo='text',
        marker=dict(
            size=node_size,
            color=node_color,
            colorscale='Viridis' if color_by in ['degree', 'community'] else None,
            showscale=color_by in ['degree', 'community'],
            colorbar=dict(
                thickness=15,
                title=color_by.capitalize(),
                xanchor='left',
                titleside='right'
            ) if color_by in ['degree', 'community'] else None,
            line=dict(width=2, color='#FFF')
        ),
        showlegend=False
    )
    
    # 6) Figure erstellen
    fig = go.Figure(data=edge_traces + [node_trace])
    
    fig.update_layout(
        title=dict(
            text=f'Knowledge Graph Visualization ({layout.replace("_", " ").title()} Layout)',
            font=dict(size=16)
        ),
        showlegend=False,
        hovermode='closest',
        margin=dict(b=20, l=5, r=5, t=40),
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        plot_bgcolor='#F8F9FA',
        height=height
    )
    
    # 7) In Streamlit anzeigen
    st.plotly_chart(fig, use_container_width=True)


# ---------- Query orchestrator (UI helper) ----------
def run_query(
    q: str, 
    web_mode_ui: str, 
    k_paragraphs: int = 24, 
    k_figures: int = 8,
    use_concept_retrieval: bool = True
) -> Dict[str, Any]:
    neo = get_neo()
    mapping = {"Auto": "auto", "Erzwingen": "force", "Aus": "off"}
    return answer_query(
        q, neo, 
        web_mode=mapping.get(web_mode_ui, "auto"), 
        k_paragraphs=k_paragraphs, 
        k_figures=k_figures,
        use_concept_retrieval=use_concept_retrieval
    )

import math
import numpy as np
from uuid import uuid4

def rebuild_concepts_for_all(topic: str, strategy: str) -> dict:
    """
    Extrahiert Konzepte + Links für bereits ingestierte Paper neu (per aktueller Strategie).
    - strategy: "LLM" | "Hybrid (NER + LLM + Relationen)"
    """
    from src.concept_extract import extract_and_embed_concepts, extract_and_embed_concepts_hybrid

    neo = get_neo()
    papers = neo.run("MATCH (p:Paper) RETURN p.paper_id AS id, p.title AS title ORDER BY title")
    total_concepts, total_links = 0, 0
    per_paper = []

    hybrid_mode = (strategy == "Hybrid (NER + LLM + Relationen)")

    for row in papers:
        pid = row["id"]; title = row.get("title","")
        paras = neo.run("""
            MATCH (p:Paper {paper_id:$pid})-[:HAS_PARAGRAPH]->(para:Paragraph)
            RETURN para.paragraph_id AS paragraph_id, para.text AS text, para.paper_id AS paper_id
            ORDER BY para.paragraph_id
        """, {"pid": pid})
        if not paras:
            per_paper.append({"paper_id": pid, "title": title, "n_concepts": 0, "n_links": 0, "note": "keine Paragraphs"})
            continue

        # Choose extraction method based on strategy
        if hybrid_mode:
            # Use hybrid NER+LLM extraction with relations
            full_text = "\n\n".join([p.get("text", "") for p in paras])
            extraction_result = extract_and_embed_concepts_hybrid(
                paper_title=title,
                paper_text=full_text,
                paragraphs=paras,
                topic_hint=topic,
                max_entities=30,
                max_relations=20,
                use_scispacy=True,
                neo_client=neo,
                persist_to_topic=True
            )
            concepts = extraction_result["concepts"]
            links = extraction_result["paragraph_links"]
            relations = extraction_result["relations"]
            
            # Display relation info
            if relations:
                with st.expander(f"Extrahierte Relationen für {title} (Anzahl: {len(relations)})"):
                    st.json(relations[:10])  # Show first 10
        else:
            # Use LLM-only extraction
            concepts, links = extract_and_embed_concepts(
                paper_title=title,
                paragraphs=paras,
                topic_hint=topic,
                max_concepts=30,
                seed_names=[],
                allow_new=True,
                neo_client=neo
            )
            
        if concepts:
            # Vorschau im GUI anzeigen und optional anlegen
            try:
                with st.expander(f"Vorgeschlagene Konzepte für {title} (Anzahl: {len(concepts)})"):
                    st.write("Preview der vorgeschlagenen Konzepte.")
                    st.json(concepts)
                    if not hybrid_mode:  # Hybrid mode already persists
                        auto_add = st.checkbox("Vorgeschlagene Konzepte automatisch in DB anlegen", value=True, key=f"auto_add_preview_{pid}")
                    else:
                        auto_add = False  # Already persisted in hybrid mode
            except Exception:
                auto_add = True
            if auto_add and not hybrid_mode:
                neo.upsert_topic(topic)
                neo.add_concepts(topic, concepts)
        if links and not hybrid_mode:  # Hybrid mode already persists links
            neo.link_paragraphs_to_concepts(pid, links)

        total_concepts += len(concepts)
        total_links    += len(links)
        per_paper.append({"paper_id": pid, "title": title, "n_concepts": len(concepts), "n_links": len(links)})

    return {"total_concepts": total_concepts, "total_links": total_links, "per_paper": per_paper}


def cluster_concepts_into_umbrellas(topic: str, sim_threshold: float = 0.86, min_cluster_size: int = 2) -> dict:
    """
    Bildet Umbrella-Knoten durch einfaches, schnelles Clustering auf Concept-Embeddings:
    - Greedy-Clustering mit Cosinus-Ähnlichkeit (ohne sklearn-Abhängigkeit)
    - Für jeden Cluster: (u:Umbrella {umbrella_id, name, size, keywords[]})
      und Kanten (u)-[:NARROWER]->(c:Concept)
      sowie (t:Topic)-[:HAS_UMBRELLA]->(u)
    """
    # Delegate to backend implementation on Neo4jClient for consistency and reuse.
    neo = get_neo()
    try:
        return neo.cluster_concepts_into_umbrellas(topic, sim_threshold=sim_threshold, min_cluster_size=min_cluster_size)
    except Exception as e:
        return {"clusters": 0, "assigned": 0, "message": f"Fehler beim Clustern: {e}"}

def list_topics() -> list[str]:
    neo = get_neo()
    rows = neo.run("MATCH (t:Topic) RETURN t.name AS name ORDER BY name")
    return [r["name"] for r in rows]

def stitch_figures_paragraphs() -> dict:
    """
    Vernäht Figures mit Paragraphen über CAPTIONS/REFERS_TO/NEAR Beziehungen.
    Delegiert an Backend-Implementierung in Neo4jClient.
    """
    neo = get_neo()
    return neo.stitch_figures_to_paragraphs(prefix_length=60, page_tolerance=1)


# =========================
# Sidebar (Einstellungen)
# =========================
st.sidebar.title("⚙️ Einstellungen")
st.sidebar.write("Werte aus `.env` (Read-Only).")
st.sidebar.text_input("NEO4J_URI", value=cfg.NEO4J_URI or "", disabled=True)
st.sidebar.text_input("NEO4J_USERNAME", value=cfg.NEO4J_USERNAME or "", disabled=True)
st.sidebar.text_input("OPENAI_API_KEY (maskiert)", value=("•••" if cfg.OPENAI_API_KEY else ""), disabled=True)

colA, colB = st.sidebar.columns(2)
with colA:
    if st.button("Verbindung testen"):
        try:
            neo = get_neo()
            neo.run("RETURN 1 AS ok")
            st.success("Neo4j erreichbar ✅")
        except Exception as e:
            st.error(f"Neo4j Fehler: {e}")

with colB:
    if st.button("Schema anlegen"):
        try:
            create_schema()
            st.success("Schema erstellt/aktualisiert ✅")
        except Exception as e:
            st.error(f"Schema-Fehler: {e}")

st.sidebar.markdown("---")
st.sidebar.markdown("**Tipp:** `.env` anpassen und App neu starten, wenn Keys/URI geändert wurden.")

# =========================
# Main Tabs
# =========================
st.title("GraphRAG Pipeline – GUI")
tab_concepts, tab_ingest, tab_query, tab_cypher, tab_synthesia, tab_coursegen = st.tabs([
    "🧩 Konzepte",
    "📥 Ingest",
    "❓ Fragen & Export",
    "🔎 Cypher",
    "🎬 PPTX → Synthesia",
    "📚 Kursgenerator"
])

# === Prototyp: Kursgenerator ===
import importlib
try:
    coursegen = importlib.import_module("scripts.course_generator")
except Exception:
    coursegen = None

# ---- Tab: Kursgenerator ----
with tab_coursegen:
    if coursegen and hasattr(coursegen, "show_course_generator"):
        coursegen.show_course_generator()
    else:
        st.warning("Modul 'course_generator' nicht gefunden oder fehlerhaft. Bitte prüfen.")

# ---- Tab: PPTX -> Synthesia (upload / select PPTX then send to Synthesia)
with tab_synthesia:
    st.subheader("🎬 PPTX → Synthesia: Erzeuge Lernvideo via Synthesia API")
    st.markdown("Lade eine `.pptx` hoch oder wähle eine vorhandene Datei aus `exports/` und sende sie an Synthesia.")
    col1, col2 = st.columns([2, 1])
    with col1:
        uploaded = st.file_uploader("PPTX hochladen (für Synthesia)", type=["pptx"])
        pptx_files = [p.name for p in EXPORTS_DIR.glob("*.pptx")] if EXPORTS_DIR.exists() else []
        selected = None
        if pptx_files:
            selected = st.selectbox("Vorhandene PPTX aus exports/ wählen", ["-- none --"] + pptx_files)
    with col2:
        voice = st.text_input("Voice (Synthesia voice id)", value="en-US")
        model = st.text_input("Model (optional)", value="")
        # Allow pasting an API key here if .env cannot be edited
        api_key = st.text_input("Synthesia API Key (paste here if not in .env)", value=st.session_state.get("synthesia_api_key", ""), type="password")
        api_url = st.text_input("Synthesia API Base URL", value=st.session_state.get("synthesia_api_base", cfg.SYNTHESIA_API_BASE or "https://api.synthesia.io/v1"))
        # duration controls
        st.markdown("---")
        total_minutes = st.number_input("Gesamtlänge (Minuten, optional)", min_value=0.0, value=0.0, step=0.5)
        per_slide_seconds = st.number_input("Sekunden pro Folie (optional, überschreibt Gesamtlänge)", min_value=0.0, value=0.0, step=0.5)
        fallback_local = st.checkbox("Bei Fehler lokal erzeugen (Fallback)", value=True)
        # persist in session for the current user/session only
        if api_key:
            st.session_state["synthesia_api_key"] = api_key
        if api_url:
            st.session_state["synthesia_api_base"] = api_url
        gen = st.button("An Synthesia senden und Video erzeugen")

    pptx_path = None
    if uploaded is not None:
        save_to = UPLOAD_DIR / uploaded.name
        with open(save_to, "wb") as fh:
            fh.write(uploaded.getbuffer())
        pptx_path = str(save_to)
        st.success(f"Hochgeladen: {save_to.name}")
    elif selected and selected != "-- none --":
        pptx_path = str(EXPORTS_DIR / selected)

    if gen:
        if not pptx_path:
            st.error("Bitte zuerst eine PPTX hochladen oder eine vorhandene auswählen.")
        else:
            # prefer API key provided in the UI/session, otherwise fallback to cfg
            use_key = st.session_state.get("synthesia_api_key") or cfg.SYNTHESIA_API_KEY
            use_url = st.session_state.get("synthesia_api_base") or cfg.SYNTHESIA_API_BASE
            if not use_key:
                st.error("Synthesia API Key nicht konfiguriert. Füge ihn in .env ein oder füge ihn hier in das Feld 'Synthesia API Key' ein.")
            else:
                out_dir = EXPORTS_DIR
                with st.spinner("Sende an Synthesia und warte auf Ergebnis (kann einige Minuten dauern)…"):
                    try:
                        mp4 = generate_video_from_pptx_via_synthesia(
                            pptx_path,
                            str(out_dir),
                            voice=voice or "en-US",
                            api_key=use_key,
                            api_base=(use_url or None),
                            total_minutes=(total_minutes or None),
                            per_slide_seconds=(per_slide_seconds or None),
                            fallback_local=fallback_local,
                        )
                        st.success(f"Video erhalten: {Path(mp4).name}")
                        st.video(mp4)
                        with open(mp4, "rb") as fh:
                            st.download_button("MP4 herunterladen", fh.read(), file_name=Path(mp4).name, mime="video/mp4")
                    except Exception as e:
                        st.error(f"Fehler beim Synthesia-Aufruf: {e}")

# ---- Tab: Konzepte (Topic + Strategie, Pre-Ingest) ----
with tab_concepts:
    st.subheader("Topic & Konzepte festlegen")
    default_topic = st.session_state.get("concept_topic", "Künstliche Intelligenz")
    topic = st.text_input("Topic-Name", value=default_topic)
    st.session_state["concept_topic"] = topic

    mode = st.radio(
        "Konzept-Strategie",
        ["LLM", "Hybrid (NER + LLM + Relationen)"],
        index=1,
        help="Bestimmt, wie beim Ingest Konzepte erzeugt/verknüpft werden. Hybrid-Modus nutzt Named Entity Recognition + LLM und extrahiert auch semantische Relationen."
    )
    st.session_state["concept_mode"] = mode

    colC1, colC2 = st.columns(2)
    with colC1:
        if st.button("Vorhandene Konzepte anzeigen"):
            neo = get_neo()
            rows = neo.run("""
                MATCH (:Topic {name:$topic})-[:HAS_CONCEPT]->(c:Concept)
                OPTIONAL MATCH (c)<-[:MENTIONS]-(para:Paragraph)<-[:HAS_PARAGRAPH]-(p:Paper)
                RETURN c.name AS name,
                    count(DISTINCT para) AS para_mentions,
                    count(DISTINCT p)    AS papers
                ORDER BY para_mentions DESC, name ASC
                LIMIT 200
            """, {"topic": topic})
            st.dataframe(rows, use_container_width=True)
    with colC2:
        st.info("Konzepte werden automatisch beim Ingest extrahiert. Wähle oben den gewünschten Modus.")

    st.markdown("---")
    st.subheader("🔁 Nachträgliche Verarbeitung")

    if st.button("Konzepte aus bestehenden Papern extrahieren"):
        with st.spinner("Extrahiere Konzepte & verknüpfe Absätze …"):
            topic = st.session_state.get("concept_topic", "Künstliche Intelligenz")
            strategy = st.session_state.get("concept_mode", "Hybrid (NER + LLM + Relationen)")
            rep = rebuild_concepts_for_all(topic, strategy)
        st.success(f"Fertig: {rep['total_concepts']} Konzepte, {rep['total_links']} Links.")
        with st.expander("Details pro Paper"):
            st.json(rep["per_paper"])

    st.markdown("---")
    st.subheader("📝 Concept-Verwaltung")
    
    topic = st.session_state.get("concept_topic", "Künstliche Intelligenz")
    neo = get_neo()
    concepts = neo.list_concepts_for_topic(topic)
    
    if not concepts:
        st.info(f"Keine Concepts für Topic '{topic}' gefunden.")
    else:
        st.markdown(f"**{len(concepts)} Concept(s) für Topic '{topic}'**")
        
        # Show as table with actions
        for i, concept in enumerate(concepts[:50]):  # Limit to 50 for performance
            with st.expander(f"🏷️ {concept['name']} ({concept.get('mentions', 0)} mentions)"):
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    new_name = st.text_input("Name", value=concept['name'], key=f"c_name_{i}")
                    new_desc = st.text_area("Beschreibung", value=concept.get('description', ''), key=f"c_desc_{i}", height=80)
                    new_alts = st.text_input("Alt-Labels (kommasepariert)", value=', '.join(concept.get('alt_labels', [])), key=f"c_alts_{i}")
                    
                    if st.button("Aktualisieren", key=f"btn_update_{i}"):
                        try:
                            alts_list = [a.strip() for a in new_alts.split(',') if a.strip()]
                            neo.update_concept(
                                concept['concept_id'],
                                name=new_name if new_name != concept['name'] else None,
                                description=new_desc if new_desc != concept.get('description', '') else None,
                                alt_labels=alts_list if alts_list != concept.get('alt_labels', []) else None
                            )
                            st.success("Aktualisiert")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Fehler: {e}")
                
                with col2:
                    st.markdown(f"**ID:** `{concept['concept_id'][:20]}...`")
                    
                    # Merge with another concept
                    merge_target_name = st.selectbox(
                        "Mergen in",
                        options=[c['name'] for c in concepts if c['concept_id'] != concept['concept_id']],
                        key=f"merge_c_{i}"
                    )
                    if st.button("Mergen", key=f"btn_merge_c_{i}"):
                        target = next((c for c in concepts if c['name'] == merge_target_name), None)
                        if target:
                            try:
                                result = neo.merge_concepts(concept['concept_id'], target['concept_id'])
                                st.success(f"Gemerged: {result}")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Fehler: {e}")
                    
                    if st.button("🗑️ Löschen", key=f"btn_delete_c_{i}"):
                        try:
                            result = neo.delete_concept(concept['concept_id'])
                            st.success(f"Gelöscht: {result}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Fehler: {e}")

# ---- Tab: Ingest (Enhanced) ----
with tab_ingest:
    st.subheader("📥 PDFs ingestieren (Erweitert)")
    
    # === Erweiterte Einstellungen ===
    with st.expander("⚙️ Ingest-Einstellungen", expanded=True):
        col_topic, col_strategy = st.columns(2)
        
        with col_topic:
            # Topic-Auswahl mit Auto-Inference
            neo = get_neo()
            existing_topics = [r["name"] for r in neo.run("MATCH (t:Topic) RETURN DISTINCT t.name AS name ORDER BY name")]
            
            if not existing_topics:
                existing_topics = ["Künstliche Intelligenz"]
            
            use_auto_topic = st.checkbox("📌 Topic automatisch aus Titel ableiten", value=False)
            
            if not use_auto_topic:
                selected_topic = st.selectbox(
                    "Topic zuordnen",
                    options=existing_topics,
                    index=0 if "Künstliche Intelligenz" in existing_topics else 0,
                    key="ingest_topic_select"
                )
                st.session_state["concept_topic"] = selected_topic
            else:
                st.info("Topic wird automatisch aus dem Dokumenttitel abgeleitet")
                selected_topic = None
            
            # Option für neues Topic
            new_topic = st.text_input("➕ Oder neues Topic erstellen", key="new_topic_input")
            if new_topic:
                st.session_state["concept_topic"] = new_topic
                selected_topic = new_topic
        
        with col_strategy:
            strategy = st.radio(
                "Konzept-Extraktions-Strategie",
                ["Hybrid (NER + LLM + Relationen)", "LLM"],
                index=0,
                help="Hybrid nutzt Named Entity Recognition + LLM und extrahiert semantische Relationen. Empfohlen für wissenschaftliche Texte."
            )
            st.session_state["concept_mode"] = strategy
            
            use_scispacy = st.checkbox("🔬 SciSpacy verwenden", value=True, help="Optimiert für wissenschaftliche Texte")
        
        col_params1, col_params2 = st.columns(2)
        with col_params1:
            auto_params = st.checkbox("🎯 Parameter automatisch anpassen", value=True, 
                                      help="Passt max_entities/max_relations an Dokumentgröße an")
            if not auto_params:
                max_entities = st.slider("Max. Entitäten", 10, 100, 30)
                max_relations = st.slider("Max. Relationen", 5, 50, 20)
            else:
                st.info("Parameter werden dynamisch berechnet")
                max_entities = None
                max_relations = None
        
        with col_params2:
            check_duplicates = st.checkbox("🔍 Duplikate erkennen", value=True,
                                          help="Prüft auf SHA256, DOI und Titel-Duplikate")
            quality_filter = st.checkbox("✨ Qualitätsfilter aktivieren", value=True,
                                        help="Filtert Konzepte mit niedriger Confidence")
            if quality_filter:
                min_confidence = st.slider("Min. Confidence", 0.0, 1.0, 0.6, 0.05)
            else:
                min_confidence = 0.0
    
    st.markdown("---")
    
    # === File Upload ===
    uploaded = st.file_uploader("📄 PDF-Dateien auswählen", type=["pdf"], accept_multiple_files=True)
    
    # Enhanced ingest function
    def ingest_one_pdf_enhanced(path: Path, use_auto_topic_param: bool, topic_param: str) -> Dict[str, Any]:
        neo = get_neo()
        
        # Read PDF
        paper_meta, sections, paragraphs, figures = read_pdf_text_and_images(str(path))
        print(f"🔍 DEBUG after read_pdf: title = '{paper_meta.get('title')}', path = {path}")
        
        # Extract enhanced metadata
        paper_meta = extract_enhanced_metadata(path, paper_meta)
        print(f"🔍 DEBUG after extract_enhanced: title = '{paper_meta.get('title')}'")
        
        # Check for duplicates
        if check_duplicates:
            duplicate = check_for_duplicates(neo, paper_meta)
            if duplicate:
                return {
                    "status": "duplicate",
                    "duplicate_info": duplicate,
                    "title": paper_meta.get("title"),
                    "file_name": path.name
                }
        
        # Infer topic if needed
        if use_auto_topic_param:
            topic = infer_topic_from_title(paper_meta.get("title", ""))
            st.info(f"📌 Auto-Topic: {topic}")
        else:
            topic = topic_param or "Künstliche Intelligenz"
        
        # Calculate dynamic parameters
        if auto_params:
            params = calculate_dynamic_parameters(len(paragraphs))
            max_ent = params["max_entities"]
            max_rel = params["max_relations"]
            st.info(f"🎯 Dynamische Parameter: max_entities={max_ent}, max_relations={max_rel}")
        else:
            max_ent = max_entities
            max_rel = max_relations
        
        # Upsert paper
        print(f"🔍 DEBUG before upsert_paper: title = '{paper_meta.get('title')}'")
        
        # Build meta dict, but exclude 'title' from PDF metadata to prevent override
        pdf_meta = paper_meta.get("meta", {}).copy()
        pdf_meta.pop("title", None)  # Remove title from PDF metadata
        
        neo.upsert_paper(
            paper_meta["paper_id"],
            paper_meta.get("title"),
            {
                "source_path": paper_meta["source_path"],
                "doi": paper_meta.get("doi"),
                "url": paper_meta.get("url"),
                "file_sha256": paper_meta.get("file_sha256"),
                "file_size": paper_meta.get("file_size"),
                "ingested_at": paper_meta.get("ingested_at"),
                "page_count": paper_meta.get("page_count"),
                **pdf_meta,
            },
        )
        
        if sections:
            neo.add_sections(paper_meta["paper_id"], sections)
        
        paragraphs_emb = embed_paragraphs(paragraphs)
        neo.add_paragraphs(paper_meta["paper_id"], paragraphs_emb)
        
        # Figures
        figs_analysed = analyze_and_embed_figures(figures) if figures else []
        by_id = {f["figure_id"]: f for f in figures}
        figs_ready = []
        for fa in figs_analysed:
            base = by_id.get(fa["figure_id"], {})
            image_uri = fa.get("image_uri") or base.get("image_path")
            figs_ready.append({
                "figure_id": fa["figure_id"],
                "page": fa.get("page", base.get("page")),
                "image_uri": image_uri,
                "caption": fa.get("caption", base.get("figure_label", "")),
                "analysis_json": fa.get("analysis_json"),
                "embedding": fa.get("embedding"),
                "bbox": base.get("bbox"),
                "page_width": base.get("page_width"),
                "page_height": base.get("page_height"),
                "figure_label": base.get("figure_label"),
            })
        if figs_ready:
            neo.add_figures(paper_meta["paper_id"], figs_ready)
        
        # Extract concepts
        hybrid_mode = (strategy == "Hybrid (NER + LLM + Relationen)")
        
        if hybrid_mode:
            full_text = "\n\n".join([p.get("text", "") for p in paragraphs_emb])
            result = extract_and_embed_concepts_hybrid(
                paper_title=paper_meta.get("title") or "",
                paper_text=full_text,
                paragraphs=paragraphs_emb,
                topic_hint=topic,
                max_entities=max_ent,
                max_relations=max_rel,
                use_scispacy=use_scispacy,
                neo_client=neo,
                persist_to_topic=True
            )
            concepts = result.get('concepts', [])
            links = result.get('paragraph_links', [])
        else:
            concepts, links = extract_and_embed_concepts(
                paper_title=paper_meta.get("title") or "",
                paragraphs=paragraphs_emb,
                topic_hint=topic,
                max_concepts=max_ent,
                seed_names=None,
                allow_new=True,
                neo_client=neo,
                persist_to_topic=True
            )
        
        # Quality filter
        if quality_filter and concepts:
            concepts, links = filter_low_quality_concepts(concepts, min_confidence, links)
        
        # Link paragraphs to concepts
        if links:
            try:
                neo.link_paragraphs_to_concepts(paper_meta["paper_id"], links)
            except Exception as e:
                st.warning(f"⚠️ Link-Fehler: {e}")
        
        # Auto-attach to umbrella
        try:
            neo.attach_concepts_to_existing_umbrella(topic)
        except Exception:
            pass
        
        report = {
            "status": "success",
            "paper_id": paper_meta["paper_id"],
            "title": paper_meta.get("title"),
            "topic": topic,
            "n_sections": len(sections),
            "n_paragraphs": len(paragraphs_emb),
            "n_figures": len(figs_ready),
            "n_concepts": len(concepts),
            "n_links": len(links),
            "file_name": path.name
        }
        
        return report

    # --- Metadata helper for post-ingest curation ---
    def fetch_paper_metadata(paper_id: str) -> Dict[str, Any]:
        neo = get_neo()
        res = neo.run(
            """
            MATCH (p:Paper {paper_id:$pid})
            RETURN p.paper_id AS paper_id, p.title AS title,
                   p.author AS author, p.publication_year AS publication_year,
                   p.doi AS doi, p.url AS url, p.source AS source, p.publisher AS publisher
            """,
            {"pid": paper_id}
        )
        return res[0] if res else {}

    def update_paper_metadata(paper_id: str, updates: Dict[str, Any]):
        if not updates:
            return
        neo = get_neo()
        set_parts = []
        params = {"pid": paper_id}
        for k, v in updates.items():
            params[k] = v
            set_parts.append(f"p.{k} = ${k}")
        cypher = "MATCH (p:Paper {paper_id:$pid}) SET " + ", ".join(set_parts)
        neo.run(cypher, params)
    
    # === Ingest Button ===
    if uploaded:
        if st.button("🚀 Ingest starten", type="primary"):
            reports = []
            duplicates = []
            errors = []
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            for idx, f in enumerate(uploaded):
                try:
                    status_text.text(f"📄 Verarbeite {idx+1}/{len(uploaded)}: {f.name}")
                    
                    out_path = UPLOAD_DIR / f.name
                    out_path.write_bytes(f.read())
                    
                    rep = ingest_one_pdf_enhanced(out_path, use_auto_topic, selected_topic)
                    
                    if rep.get("status") == "duplicate":
                        duplicates.append(rep)
                        st.warning(f"⚠️ Duplikat übersprungen: {rep['title']}")
                    else:
                        reports.append(rep)
                        
                        # Validation
                        validation = validate_ingestion_quality(rep)
                        
                        # Show summary
                        with st.expander(f"✅ {rep['title']} ({validation['quality_label']})"):
                            summary = create_ingestion_summary(rep, validation)
                            st.markdown(summary)
                    
                except Exception as e:
                    errors.append({"file": f.name, "error": str(e), "trace": traceback.format_exc()})
                    st.error(f"❌ Fehler bei {f.name}: {e}")
                
                progress_bar.progress((idx + 1) / len(uploaded))
            
            status_text.empty()
            progress_bar.empty()
            
            # Final summary
            st.markdown("---")
            st.success(f"🎉 Fertig! {len(reports)} erfolgreich, {len(duplicates)} Duplikate, {len(errors)} Fehler")
            
            if reports:
                avg_quality = sum(validate_ingestion_quality(r)["quality_score"] for r in reports) / len(reports)
                st.metric("Durchschnittliche Qualität", f"{avg_quality:.0f}/100")

                # Post-Ingest: Metadaten kuratieren
                st.markdown("---")
                st.subheader("✏️ Metadaten kuratieren (fehlende Felder ergänzen)")
                st.caption("Vorhandene Felder werden gezeigt, leere Felder kannst du ergänzen. Bereits gesetzte Werte werden nicht überschrieben.")
                fields = ["author", "publication_year", "doi", "url", "source", "publisher"]
                for rep in reports:
                    meta = fetch_paper_metadata(rep["paper_id"])
                    st.markdown(f"**{meta.get('title') or rep.get('title') or 'Ohne Titel'}**")
                    cols = st.columns(len(fields))
                    updates = {}
                    for i, field in enumerate(fields):
                        current = meta.get(field) or ""
                        placeholder = "fehlt" if not current else current
                        updates[field] = cols[i].text_input(
                            label=field,
                            value="",
                            placeholder=placeholder,
                            key=f"meta_{rep['paper_id']}_{field}"
                        )
                    if st.button(f"Speichern für {rep['title']}", key=f"save_meta_{rep['paper_id']}"):
                        to_set = {k: v.strip() for k, v in updates.items() if v and v.strip()}
                        if to_set:
                            filtered = {k: v for k, v in to_set.items() if not meta.get(k)}
                            if filtered:
                                update_paper_metadata(rep["paper_id"], filtered)
                                st.success(f"Metadaten aktualisiert: {', '.join(filtered.keys())}")
                            else:
                                st.info("Keine Updates nötig – Felder bereits befüllt.")
                        else:
                            st.info("Keine Eingaben zum Speichern.")
            
            if duplicates:
                with st.expander("⚠️ Duplikate"):
                    for dup in duplicates:
                        st.markdown(f"- **{dup['file_name']}**: {dup['duplicate_info']['message']}")
            
            if errors:
                with st.expander("❌ Fehler"):
                    st.json(errors)

    st.markdown("---")
    st.subheader("🧵 Graph stitchen & Dienstprogramme")

    col_stitch, col_figstitch, col_clear = st.columns(3)

    with col_stitch:
        if st.button("Graph stitchen"):
            with st.spinner("Vernähe Knoten …"):
                stats = stitch_graph()
            st.success("Graph vernäht ✅")
            st.json(stats)
            st.info("Aura Browser/Bloom neu laden und Pfad-Queries (RETURN p) nutzen.")

    with col_figstitch:
        if st.button("Figure↔Paragraph stitch"):
            with st.spinner("Verbinde Figures mit passenden Paragraphen …"):
                stats = stitch_figures_paragraphs()
            st.success("Figure↔Paragraph-Kanten erstellt/aktualisiert ✅")
            st.json(stats)
            st.info("Tipp: Prüfe z. B. mit\n"
                    "`MATCH p = (paper:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)-[:REFERS_TO|:CAPTIONS]->(f:Figure)<-[:HAS_FIGURE]-(paper) RETURN p LIMIT 200`")

    with col_clear:
        st.warning("Achtung: Löscht alle Knoten & Kanten (Schema bleibt erhalten).")
        confirm = st.text_input("Zum Bestätigen 'DELETE' tippen", key="confirm_clear")
        if st.button("Graph leeren"):
            if confirm.strip().upper() == "DELETE":
                with st.spinner("Lösche alle Knoten & Kanten …"):
                    stats = clear_graph()
                st.success("Graph geleert ✅")
                st.json(stats)
            else:
                st.error("Bestätigung fehlt: tippe exakt 'DELETE'.")


# ---- Tab: Fragen & Export ----
with tab_query:
    st.subheader("Frage stellen")
    q = st.text_area("Deine Frage", placeholder="Erkläre Green AI mit Belegen.")
    
    # Kombinierte Einstellungen in einem Expander
    with st.expander("⚙️ Einstellungen", expanded=False):
        st.markdown("**Retrieval-Optionen**")
        
        col_r1, col_r2 = st.columns(2)
        with col_r1:
            use_concept_retrieval = st.checkbox(
                "Concept-basiertes Retrieval",
                value=True,
                help="Nutzt extrahierte Concepts + Graph-Traversierung für intelligenteres Retrieval."
            )
            k_paragraphs = st.number_input(
                "Anzahl Paragraphen",
                min_value=5,
                max_value=50,
                value=24,
                step=1,
                help="Wie viele Paragraphen sollen abgerufen werden?"
            )
        with col_r2:
            web_mode_ui = st.selectbox(
                "Websuche",
                ["Auto", "Erzwingen", "Aus"],
                index=0,
                help="Auto: Websuche bei zu wenig Graph-Belegen. Erzwingen: Immer Web. Aus: Nur Graph."
            )
            k_figures = st.number_input(
                "Anzahl Abbildungen",
                min_value=0,
                max_value=20,
                value=8,
                step=1,
                help="Wie viele Abbildungen sollen abgerufen werden?"
            )
        
        st.markdown("---")
        st.markdown("**PDF-Export-Optionen**")
        
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            export_pdf = st.checkbox("Antwort als PDF exportieren", value=True)
            inline_figs = st.checkbox(
                "Bilder inline einfügen",
                value=True,
                help="Bilder werden direkt unter der Referenz im Text eingefügt."
            )
        with col_p2:
            fallback_k = st.number_input(
                "Fallback: Top-K Figuren anhängen",
                min_value=0,
                max_value=10,
                value=3,
                step=1,
                help="Wenn keine Inline-Refs vorhanden, werden die Top-K Figuren am Ende angehängt (0 = deaktiviert)."
            )

    if st.button("Antwort abrufen", type="primary"):
        if not q.strip():
            st.warning("Bitte eine Frage eingeben.")
        else:
            with st.spinner("Suche & Synthese …"):
                res = run_query(
                    q, 
                    web_mode_ui=web_mode_ui, 
                    k_paragraphs=k_paragraphs, 
                    k_figures=k_figures,
                    use_concept_retrieval=use_concept_retrieval
                )
            st.session_state["last_result"] = res
            st.session_state["last_query"] = q

    if "last_result" in st.session_state:
        res = st.session_state["last_result"]
        q0 = st.session_state.get("last_query", "")
        st.markdown("### Antwort")
        st.write(res["answer"])
        st.markdown("**Modus:** " + res.get("mode",""))
        with st.expander("Debug (Entscheidung & Zählwerte)"):
            st.json(res.get("debug", {}))
        st.markdown("---")
        st.markdown("### Belege")
        rows = []
        for s in res["supports"]:
            r = {
                "id": f"{'P' if s['type']=='paragraph' else 'F' if s['type']=='figure' else 'W'}{s.get('paragraph_id') or s.get('figure_id') or ''}",
                "type": s["type"],
                "paper": s.get("paper_title"),
                "page": s.get("page"),
                "section": s.get("section_title"),
                "doi": s.get("doi"),
                "url": s.get("url"),
                "score": round(float(s.get("score", 0) or 0), 4),
            }
            rows.append(r)
        st.dataframe(rows, use_container_width=True)

        figs = [s for s in res["supports"] if s.get("type") == "figure"]
        if figs:
            st.markdown("### Verwendete Abbildungen (Vorschau)")
            for f in figs[:6]:
                if f.get("image_uri") and Path(f["image_uri"]).exists():
                    st.image(
                        f["image_uri"],
                        caption=f"[F{f['figure_id']}] {f.get('caption') or f.get('figure_label') or ''}"
                    )

        if export_pdf:
            out_path = EXPORTS_DIR / f"answer_{os.getpid()}.pdf"
            path = write_answer_pdf(
                q0, res["answer"], res["supports"], str(out_path),
                inline_figures=inline_figs,
                max_inline_figures_total=None,
                fallback_append_top_k_if_no_refs=int(fallback_k),
            )
            st.success(f"PDF erzeugt: {path}")
            st.download_button(
                "PDF herunterladen",
                data=Path(path).read_bytes(),
                file_name=Path(path).name,
                mime="application/pdf",
            )


        st.markdown("### 🎞️ Slides mit Gamma erzeugen")

        colA, colB, colC = st.columns(3)
        # Theme selection: show saved/favorite themes and allow a custom name
        themes = load_gamma_themes()
        # ensure Oasis is available as a sensible default
        if "Oasis" not in themes:
            themes.append("Oasis")
        selected = colA.selectbox("Theme (Gamma)", options=themes + ["<custom>"], index=0)
        if selected == "<custom>":
            custom_theme = colA.text_input("Custom theme name", value="")
        else:
            custom_theme = selected
        # allow saving the current custom theme into favorites
        c1, c2 = colA.columns([1,1])
        if c1.button("Add theme to favorites"):
            if custom_theme and custom_theme.strip():
                save_gamma_theme(custom_theme.strip())
                st.success(f"Theme '{custom_theme.strip()}' saved to favorites.")
        if c2.button("Remove selected theme"):
            if selected and selected != "<custom>":
                remove_gamma_theme(selected)
                st.info(f"Theme '{selected}' removed from favorites.")
                # refresh themes in session by reloading
                st.experimental_rerun()
        # Refresh themes from Gamma (query provider for available themes)
        if c2.button("Refresh themes from Gamma"):
            try:
                try:
                    gcli = GammaClient()
                except Exception as e:
                    st.error(f"GammaClient nicht konfiguriert: {e}")
                    gcli = None
                if gcli is not None:
                    with st.spinner("Rufe Themes von Gamma ab …"):
                        try:
                            remote = gcli.list_themes()
                            if remote:
                                EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
                                (EXPORTS_DIR / "gamma_themes.json").write_text(json.dumps(remote, ensure_ascii=False, indent=2), encoding="utf-8")
                                st.success(f"{len(remote)} Themes von Gamma importiert.")
                                st.experimental_rerun()
                            else:
                                st.info("Keine Themes von Gamma zurückgegeben.")
                        except Exception as e:
                            st.error(f"Fehler beim Abrufen der Themes: {e}")
            except Exception as e:
                st.error(f"Unerwarteter Fehler: {e}")
        theme = custom_theme or "Oasis"
        # Template upload: optional .pptx/.potx template to use for local generation
        tpl_col = st.columns([1, 3])[1]
        uploaded_tpl = tpl_col.file_uploader("Optional: PPTX-Template (.pptx/.potx)", type=["pptx", "potx"], key="gamma_template_uploader")
        if uploaded_tpl is not None:
            # save uploaded template to uploads/templates
            tpl_dir = UPLOAD_DIR / "templates"
            tpl_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", uploaded_tpl.name)[:120]
            out_path = tpl_dir / safe_name
            with open(out_path, "wb") as f:
                f.write(uploaded_tpl.getvalue())
            st.session_state["gamma_template_path"] = str(out_path)
            st.success(f"Template hochgeladen: {out_path}")
        else:
            # maintain existing session value if any
            _ = st.session_state.get("gamma_template_path")
        cards = colB.number_input("Ziel-Folien (bei auto)", 1, 60, 10)
        lang  = colC.selectbox("Sprache", ["de","en","fr","es","it"], index=0)

        img_src = st.selectbox(
            "Bildquelle",
            ["noImages","aiGenerated","unsplash","webFreeToUse","placeholder"],
            index=0
        )
        split = st.radio(
            "Folienaufteilung",
            ["inputTextBreaks","auto"],
            index=0,
            horizontal=True
        )

        # Font size controls for local PPTX
        with st.expander("📝 Schriftgrößen (nur lokales PPTX)"):
            st.info("Diese Einstellungen gelten nur für das lokale python-pptx Fallback, nicht für Gamma API.")
            colF1, colF2, colF3 = st.columns(3)
            title_font = colF1.number_input("Folientitel (pt)", min_value=16, max_value=72, value=32, step=2)
            body_font = colF2.number_input("Body-Text (pt)", min_value=12, max_value=48, value=18, step=2)
            bullet_font = colF3.number_input("Bulletpoints (pt)", min_value=10, max_value=36, value=14, step=2)
            st.session_state["pptx_title_font"] = title_font
            st.session_state["pptx_body_font"] = body_font
            st.session_state["pptx_bullet_font"] = bullet_font

        # Theme quick-test: runs a short Gamma generation to validate the theme name
        if colB.button("Test theme"):
            test_name = theme.strip() or "Oasis"
            st.info(f"Teste Theme: {test_name}")
            try:
                gtest = None
                try:
                    gtest = GammaClient()
                except Exception as e:
                    st.error(f"GammaClient nicht konfiguriert: {e}")
                if gtest is not None:
                    test_body = {
                        "inputText": "# Test\n* Theme validation",
                        "textMode": "preserve",
                        "format": "presentation",
                        "themeName": test_name,
                        "cardSplit": "inputTextBreaks",
                        "numCards": 1,
                        "exportAs": "pptx",
                        # note: valid amounts: brief, medium, detailed, extensive
                        "textOptions": {"language": "en", "amount": "brief"},
                        "imageOptions": {"source": "noImages"},
                    }
                    with st.spinner("Validiere Theme bei Gamma …"):
                        try:
                            gen_id = gtest.generate(test_body)
                            status = gtest.poll(gen_id, interval_sec=1, timeout_sec=20)
                            outp = {"generationId": gen_id, "status": status}
                            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
                            safe = re.sub(r"[^A-Za-z0-9_-]", "_", test_name)[:40]
                            fn = EXPORTS_DIR / f"gamma_theme_test_{safe}.json"
                            fn.write_text(json.dumps(outp, ensure_ascii=False, indent=2), encoding="utf-8")
                            st.success("Theme validiert (siehe Ergebnis unten).")
                            st.json(outp)
                        except Exception as e:
                            st.error(f"Theme Test fehlgeschlagen: {e}")
                            try:
                                # save error details
                                tb = traceback.format_exc()
                            except Exception:
                                tb = str(e)
                            err = {"error": str(e), "traceback": tb}
                            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
                            safe = re.sub(r"[^A-Za-z0-9_-]", "_", test_name)[:40]
                            fn = EXPORTS_DIR / f"gamma_theme_test_{safe}_error.json"
                            fn.write_text(json.dumps(err, ensure_ascii=False, indent=2), encoding="utf-8")
                            st.info(f"Fehlerdetails gespeichert in: {fn}")
            except Exception as e:
                st.error(f"Unerwarteter Fehler beim Theme-Test: {e}")

        if st.button("Als PPTX mit Gamma erstellen"):
            title_for_deck = st.session_state.get("last_query", "Ergebnis")

            # Try Gamma first (existing GammaClient), fall back to local generator.
            tried_gamma = False
            gamma_failed = None
            out_file: str | None = None

            # Prepare body for Gamma if available
            try:
                g = GammaClient()  # nutzt GAMMA_API_KEY aus .env
                tried_gamma = True
            except Exception as e:
                g = None
                gamma_failed = e

            if g is not None:
                body = {
                    "inputText": to_gamma_input_text(
                        res["answer"],
                        res.get("supports", []),
                        title=title_for_deck,
                    ),
                    "textMode": "preserve",
                    "format": "presentation",
                    "themeName": theme.strip() or "Oasis",
                    "cardSplit": split,
                    "numCards": int(cards),
                    "exportAs": "pptx",
                    "textOptions": {"language": lang, "amount": "medium"},
                    "imageOptions": {"source": img_src},
                    "cardOptions": {"dimensions": "16x9"},
                    "sharingOptions": {"externalAccess": "noAccess", "workspaceAccess": "view"},
                }
                with st.spinner("Gamma generiert Deck …"):
                    try:
                        gen_id = g.generate(body)
                        status = g.poll(gen_id, interval_sec=5, timeout_sec=600)
                        st.success("Gamma-Generation abgeschlossen.")
                        st.write("**Gamma Raw-Status (Debug):**")
                        st.json(status)

                        def _find_pptx_url(obj):
                            # rekursiv nach einer URL suchen, die auf .pptx endet
                            if isinstance(obj, dict):
                                for k, v in obj.items():
                                    if isinstance(v, str) and v.lower().endswith('.pptx'):
                                        return v
                                    res = _find_pptx_url(v)
                                    if res:
                                        return res
                            elif isinstance(obj, list):
                                for it in obj:
                                    res = _find_pptx_url(it)
                                    if res:
                                        return res
                            elif isinstance(obj, str):
                                if obj.lower().endswith('.pptx'):
                                    return obj
                            return None

                        pptx_url = status.get("pptxUrl") or status.get("fileUrl") or status.get("downloadUrl") or _find_pptx_url(status)
                        if pptx_url:
                            try:
                                out_path = g.download_file(pptx_url)
                                out_file = out_path
                                st.write("**PPTX gespeichert:**", out_path)
                            except Exception as e:
                                st.warning(f"Fehler beim Herunterladen der PPTX von Gamma: {e}")
                                st.info("Nutze lokalen Fallback.")
                        else:
                            st.warning("Kein PPTX-Link in der Antwort von Gamma gefunden. Verwende lokalen Fallback.")
                    except Exception as e:
                        # capture exception for later display and debugging
                        gamma_failed = e
                        try:
                            tb = traceback.format_exc()
                        except Exception:
                            tb = str(e)
                        err_obj = {
                            "error": str(e),
                            "args": getattr(e, "args", []),
                            "traceback": tb,
                        }
                        try:
                            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
                            err_path = EXPORTS_DIR / "gamma_last_error.json"
                            err_path.write_text(json.dumps(err_obj, ensure_ascii=False, indent=2), encoding="utf-8")
                        except Exception:
                            # best-effort; don't crash GUI
                            pass

            # If Gamma not configured or failed, use local generator
            if out_file is None:
                st.info("Erzeuge lokale PPTX-Fallback (python-pptx).")
                try:
                    gen = generate_presentation(
                        title=title_for_deck,
                        answer_text=res.get("answer", ""),
                        supports=res.get("supports", []),
                        use_gamma=False,
                        out_dir=str(EXPORTS_DIR),
                        template_path=st.session_state.get("gamma_template_path"),
                        title_font_size=st.session_state.get("pptx_title_font", 32),
                        body_font_size=st.session_state.get("pptx_body_font", 18),
                        bullet_font_size=st.session_state.get("pptx_bullet_font", 14),
                    )
                    if gen.get("method") == "local":
                        out_file = gen["result"]["path"]
                        st.success(f"Lokales PPTX erzeugt: {out_file}")
                    else:
                        # unexpected, but show response
                        st.write(gen)
                except Exception as e:
                    st.error(f"Lokale PPTX-Erzeugung fehlgeschlagen: {e}")

            # Offer download if file exists
            if out_file and Path(out_file).exists():
                st.download_button(
                    label="PPTX herunterladen",
                    data=Path(out_file).read_bytes(),
                    file_name=Path(out_file).name,
                    mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
            else:
                if tried_gamma and gamma_failed:
                    st.warning(f"Gamma fehlgeschlagen: {gamma_failed}")
                    # Show detailed error if we saved it
                    try:
                        err_path = EXPORTS_DIR / "gamma_last_error.json"
                        if err_path.exists():
                            with st.expander("Gamma Fehlerdetails anzeigen"):
                                try:
                                    obj = json.loads(err_path.read_text(encoding="utf-8"))
                                    st.json(obj)
                                except Exception:
                                    st.text(err_path.read_text(encoding="utf-8"))
                            st.info(f"Fehlerdetails gespeichert in: {err_path}")
                        else:
                            with st.expander("Gamma Fehlerdetails anzeigen"):
                                st.text(str(gamma_failed))
                    except Exception:
                        # don't let the error UI crash the app
                        st.text(str(gamma_failed))

    with tab_cypher:
        # --- Cypher ausführen & visualisieren ---
        st.subheader("🔎 Cypher ausführen & visualisieren")

        # Topics aus DB (Dropdown)
        topics = list_topics()
        colTop, colPreset = st.columns([1,1])
        with colTop:
            selected_topic = st.selectbox("Topic aus der DB", topics or ["(keins gefunden)"])
            # JSON-Param für topic setzen
            st.session_state["cypher_params_box"] = json.dumps({"topic": selected_topic}, ensure_ascii=False, indent=2)
            st.session_state["cypher_ui_params"] = {"topic": selected_topic}

        # Simplified presets (kept small for clarity)
        presets = {
            "Topic → Concepts → Paragraphs & Figures":
            """\
    :param topic => "Künstliche Intelligenz";
    MATCH p1 = (t:Topic {name:$topic})-[:HAS_CONCEPT]->(c:Concept)
    OPTIONAL MATCH p2 = (c)<-[:MENTIONS]-(para:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
    OPTIONAL MATCH p3 = (paper)-[:HAS_FIGURE]->(figP:Figure)
    OPTIONAL MATCH p4 = (para)-[:HAS_FIGURE]->(figS:Figure)
    RETURN p1, p2, p3, p4
    LIMIT 500
    """,
            "Alle Paper → Paragraphen":
            """\
    MATCH p = (paper:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
    RETURN p
    LIMIT 400
    """,
            "Papers → ABOUT → Concepts":
            """\
    MATCH p = (paper:Paper)-[ab:ABOUT]->(c:Concept)
    WHERE ab.weight > 0.5
    RETURN p
    LIMIT 300
    """,
            "Concepts mit SEMANTIC_RELATION":
            """\
    MATCH p = (c1:Concept)-[r:SEMANTIC_RELATION]->(c2:Concept)
    RETURN p
    LIMIT 200
    """,
        }

        with colPreset:
            preset_name = st.selectbox("Preset wählen", list(presets.keys()))
            if st.button("Preset laden"):
                st.session_state["cypher_box"] = presets[preset_name]

        cypher_in = st.text_area("Cypher", value=st.session_state.get("cypher_box", list(presets.values())[0]), height=240, key="cypher_box")

        # Parameter (JSON) anzeigen/änderbar
        params_text = st.text_area("Parameter (JSON)", value=st.session_state.get("cypher_params_box", json.dumps({"topic": selected_topic}, ensure_ascii=False, indent=2)), height=100, key="cypher_params_box")
        st.session_state["cypher_ui_params"] = _extract_params_from_textarea(params_text)

        # Visualisierungs-Optionen
        st.markdown("---")
        st.markdown("**🎨 Visualisierungs-Optionen**")
        
        col_viz1, col_viz2, col_viz3 = st.columns([1, 1, 1])
        
        with col_viz1:
            viz_engine = st.radio(
                "Visualisierungs-Engine",
                ["Plotly (empfohlen)", "PyVis (klassisch)"],
                index=0,
                help="Plotly bietet bessere Layouts und Visualisierungen"
            )
        
        with col_viz2:
            if viz_engine == "Plotly (empfohlen)":
                layout_algo = st.selectbox(
                    "Layout-Algorithmus",
                    ["spring", "kamada_kawai", "circular", "hierarchical", "shell"],
                    index=0,
                    help="Spring: Force-directed (Standard)\nKamada-Kawai: Optimiert für Distanzen\nCircular: Kreisförmig\nHierarchical: Hierarchisch (wenn möglich)\nShell: Konzentrische Ringe"
                )
            else:
                layout_algo = None
        
        with col_viz3:
            if viz_engine == "Plotly (empfohlen)":
                show_edge_labels = st.checkbox("Relationsnamen anzeigen", value=True)
                color_by = st.selectbox(
                    "Färbung nach",
                    ["type", "degree", "community"],
                    index=0,
                    help="Type: Nach Node-Typ\nDegree: Nach Anzahl Verbindungen\nCommunity: Nach erkannten Clustern"
                )
            else:
                show_edge_labels = None
                color_by = None
        
        st.markdown("---")

        colQ1, colQ2 = st.columns([1,1])
        with colQ1:
            if st.button("Query ausführen"):
                try:
                    # Für Visualisierung: rohe Neo4j-Objekte holen
                    recs_graph = run_cypher_with_params(cypher_in, for_graph=True)
                    # Für Daten-Anzeige: normale Daten
                    recs_data = run_cypher_with_params(cypher_in, for_graph=False)
                    
                    st.success(f"{len(recs_data)} Record(s) erhalten.")
                    with st.expander("Rohdaten anzeigen"):
                        st.write(recs_data)
                    
                    # Visualisierung basierend auf gewählter Engine
                    if viz_engine == "Plotly (empfohlen)":
                        visualize_with_plotly(
                            recs_graph, 
                            height=650,
                            layout=layout_algo,
                            show_edge_labels=show_edge_labels,
                            color_by=color_by
                        )
                    else:
                        visualize_records_as_graph(recs_graph, height=650)
                except Exception as e:
                    st.error(f"Cypher-Fehler: {e}")
                    st.code(cypher_in, language="cypher")
                    st.code(cypher_in, language="cypher")

            # Diagnostic button: check Topic, Umbrellas, Concepts
            if st.button("Diagnose: Topic/Umbrella/Concept Counts"):
                neo = get_neo()
                topic_name = selected_topic
                diag = {}
                # Zeige alle Topic-Namen
                try:
                    all_topics = neo.run("MATCH (t:Topic) RETURN t.name AS name", {})
                    topic_names = [t['name'] for t in all_topics if 'name' in t]
                    diag['all_topic_names'] = topic_names
                except Exception as e:
                    diag['all_topic_names'] = f"Error: {e}"
                # Zähle Topic, Umbrella, Concept
                try:
                    topic_result = neo.run(
                        "MATCH (t:Topic {name:$topic}) RETURN count(t) AS c",
                        {'topic': topic_name}
                    )
                    diag['topic_count'] = topic_result[0]['c'] if topic_result and 'c' in topic_result[0] else 0
                except Exception as e:
                    diag['topic_count'] = f"Error: {e}"
                try:
                    umbrella_result = neo.run(
                        "MATCH (t:Topic {name:$topic})-[:HAS_UMBRELLA]->(u:Umbrella) RETURN count(u) AS c",
                        {'topic': topic_name}
                    )
                    diag['umbrella_count'] = umbrella_result[0]['c'] if umbrella_result and 'c' in umbrella_result[0] else 0
                except Exception as e:
                    diag['umbrella_count'] = f"Error: {e}"
                try:
                    concept_result = neo.run(
                        "MATCH (t:Topic {name:$topic})-[:HAS_UMBRELLA]->(u:Umbrella)-[:NARROWER]->(c:Concept) RETURN count(DISTINCT c) AS c",
                        {'topic': topic_name}
                    )
                    diag['concept_count'] = concept_result[0]['c'] if concept_result and 'c' in concept_result[0] else 0
                except Exception as e:
                    diag['concept_count'] = f"Error: {e}"
                try:
                    direct_concepts = neo.run(
                        "MATCH (t:Topic {name:$topic})-[:HAS_CONCEPT]->(c:Concept) RETURN count(DISTINCT c) AS c",
                        {'topic': topic_name}
                    )
                    diag['concepts_direct'] = direct_concepts[0]['c'] if direct_concepts and 'c' in direct_concepts[0] else 0
                except Exception as e:
                    diag['concepts_direct'] = f"Error: {e}"
                st.info(f"Diagnose für Topic '{topic_name}':")
                st.json(diag)

        with colQ2:
            # One-click Topic->Concepts view (simplified - no Umbrellas needed)
            if st.button("Show Topic + Concepts + Paragraphs/Figures"):
                try:
                    cy = """
    :param topic => "Künstliche Intelligenz";
    // Haupt-Pfad: Topic -> Concepts
    MATCH p1 = (t:Topic {name:$topic})-[:HAS_CONCEPT]->(c:Concept)
    // Optional: Concepts <- MENTIONS von Paragraphs <- HAS_PARAGRAPH von Papers
    OPTIONAL MATCH p2 = (c)<-[:MENTIONS]-(para:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
    // Optional: Papers mit Figures
    OPTIONAL MATCH p3 = (paper)-[:HAS_FIGURE]->(figP:Figure)
    // Optional: Paragraphs mit Figures
    OPTIONAL MATCH p4 = (para)-[:HAS_FIGURE]->(figS:Figure)
    RETURN p1, p2, p3, p4
    LIMIT 500
                    """
                    # Für Visualisierung: rohe Neo4j-Objekte holen
                    recs_graph = run_cypher_with_params(cy, for_graph=True)
                    # Für Daten-Anzeige: normale Daten
                    recs_data = run_cypher_with_params(cy, for_graph=False)
                    
                    st.success(f"{len(recs_data)} Record(s) erhalten.")
                    with st.expander("Rohdaten anzeigen (Topic→Concepts)"):
                        st.write(recs_data)
                    
                    # Visualisierung basierend auf gewählter Engine
                    if viz_engine == "Plotly (empfohlen)":
                        visualize_with_plotly(
                            recs_graph, 
                            height=700,
                            layout=layout_algo,
                            show_edge_labels=show_edge_labels,
                            color_by=color_by
                        )
                    else:
                        visualize_records_as_graph(recs_graph, height=700)
                except Exception as e:
                    st.error(f"Fehler beim Laden der Umbrella-Ansicht: {e}")