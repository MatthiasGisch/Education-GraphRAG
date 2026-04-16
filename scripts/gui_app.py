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
USER_CONFIG_PATH = EXPORTS_DIR / "user_config.json"

st.set_page_config(page_title="GraphRAG GUI", layout="wide")

# Custom CSS für übersichtlichere Tabs
st.markdown("""
<style>
    /* Tab-Buttons größer und besser lesbar machen */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        padding: 10px 0;
    }
    
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        padding: 0 24px;
        font-size: 16px;
        font-weight: 500;
    }
    
    /* Aktiver Tab hervorheben */
    .stTabs [aria-selected="true"] {
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)


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


# ---- Persistent user configuration for API keys ----
def load_user_config() -> dict:
    try:
        if USER_CONFIG_PATH.exists():
            data = json.loads(USER_CONFIG_PATH.read_text(encoding="utf-8") or "{}")
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_user_config(conf: dict) -> None:
    try:
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        USER_CONFIG_PATH.write_text(json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        raise e

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
    import streamlit.components.v1 as components

    if not records:
        st.warning("Deine Query hat 0 Records geliefert – bitte Query/Preset prüfen.")
        return

    nodes: dict[str, dict] = {}              # id -> {"labels":[...], "props":{...}, "group": str}
    edges: set[tuple[str, str, str]] = set() # (src, dst, type)

    # ---------- Heuristiken ----------
    LARGE_KEYS = {"embedding", "vector", "tokens", "content_embeddings"}
    ID_KEYS = ["paper_id", "section_id", "paragraph_id", "figure_id", "concept_id", "topic_id"]

    def _as_str(x): return "" if x is None else str(x)

    def _guess_label_from_props(props: dict) -> str:
        if "concept_id" in props:   return "Concept"
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
                neo_client=neo,
                persist_to_topic=True  # Automatisch in Topic persistieren
            )
            
        if concepts:
            # Vorschau im GUI anzeigen (bereits in DB persistiert durch persist_to_topic=True)
            try:
                with st.expander(f"Extrahierte Konzepte für {title} (Anzahl: {len(concepts)})"):
                    st.write("✅ Konzepte wurden automatisch in der Datenbank gespeichert.")
                    st.json(concepts)
            except Exception:
                pass  # Ignore preview errors

        total_concepts += len(concepts)
        total_links    += len(links)
        per_paper.append({"paper_id": pid, "title": title, "n_concepts": len(concepts), "n_links": len(links)})

    return {"total_concepts": total_concepts, "total_links": total_links, "per_paper": per_paper}


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

# Initialize session state for API keys (load persistent overrides if available)
if "api_keys" not in st.session_state:
    defaults = {
        "neo4j_uri": cfg.NEO4J_URI or "",
        "neo4j_username": cfg.NEO4J_USERNAME or "neo4j",
        "neo4j_password": cfg.NEO4J_PASSWORD or "",
        "openai_api_key": cfg.OPENAI_API_KEY or "",
        "gamma_api_key": cfg.GAMMA_API_KEY or "",
        "gamma_api_url": cfg.GAMMA_API_URL or "",
        "synthesia_api_key": cfg.SYNTHESIA_API_KEY or "",
        "synthesia_api_base": cfg.SYNTHESIA_API_BASE or "https://api.synthesia.io/v1",
        # LM Studio
        "llm_mode": cfg.LLM_MODE or "cloud",
        "lmstudio_base_url": cfg.LMSTUDIO_BASE_URL or "http://localhost:1234/v1",
        "lmstudio_chat_model": cfg.LMSTUDIO_CHAT_MODEL or "local-model",
        "lmstudio_vision_model": cfg.LMSTUDIO_VISION_MODEL or "local-vision-model",
        "lmstudio_embed_model": cfg.LMSTUDIO_EMBED_MODEL or "nomic-embed-text",
        "lmstudio_embed_dim": str(cfg.LMSTUDIO_EMBED_DIM or "768"),
    }
    persisted = load_user_config() or {}
    # Only take known keys from persisted config
    for k in list(persisted.keys()):
        if k not in defaults:
            persisted.pop(k)
    defaults.update(persisted)
    st.session_state["api_keys"] = defaults

st.sidebar.markdown("**API Keys konfigurieren**")
st.sidebar.markdown("Trage hier deine API Keys ein. Diese überschreiben die Werte aus `.env` für diese Session.")

st.sidebar.markdown("**Neo4j Datenbank**")
st.session_state["api_keys"]["neo4j_uri"] = st.sidebar.text_input(
    "NEO4J_URI",
    value=st.session_state["api_keys"]["neo4j_uri"],
    placeholder="neo4j+s://xxxxx.databases.neo4j.io",
    help="Die URI deiner Neo4j Datenbank"
)
st.session_state["api_keys"]["neo4j_username"] = st.sidebar.text_input(
    "NEO4J_USERNAME",
    value=st.session_state["api_keys"]["neo4j_username"],
    placeholder="neo4j",
    help="Username für Neo4j (meist 'neo4j')"
)
st.session_state["api_keys"]["neo4j_password"] = st.sidebar.text_input(
    "NEO4J_PASSWORD",
    value=st.session_state["api_keys"]["neo4j_password"],
    type="password",
    help="Passwort für Neo4j Datenbank"
)

# Neo4j Test-Buttons direkt nach den Neo4j-Feldern
colA, colB = st.sidebar.columns(2)
with colA:
    if st.sidebar.button("Verbindung testen", key="test_neo4j_connection"):
        try:
            neo = get_neo()
            neo.run("RETURN 1 AS ok")
            st.sidebar.success("Neo4j erreichbar ✅")
        except Exception as e:
            st.sidebar.error(f"Neo4j Fehler: {e}")

with colB:
    if st.sidebar.button("Schema anlegen", key="create_neo4j_schema"):
        try:
            create_schema()
            st.sidebar.success("Schema erstellt/aktualisiert ✅")
        except Exception as e:
            st.sidebar.error(f"Schema-Fehler: {e}")

st.sidebar.markdown("---")
st.sidebar.markdown("**Sprachmodell-Modus**")
_mode_options = ["Cloud (OpenAI)", "Lokal (LM Studio)"]
_mode_index = 1 if st.session_state["api_keys"].get("llm_mode") == "local" else 0
_selected_mode = st.sidebar.radio(
    "Betriebsmodus",
    _mode_options,
    index=_mode_index,
    key="llm_mode_radio",
    help="Cloud: GPT-4o-mini über OpenAI API  |  Lokal: eigenes Modell über LM Studio"
)
st.session_state["api_keys"]["llm_mode"] = "local" if _selected_mode == "Lokal (LM Studio)" else "cloud"

if st.session_state["api_keys"]["llm_mode"] == "cloud":
    st.sidebar.markdown("**OpenAI**")
    st.session_state["api_keys"]["openai_api_key"] = st.sidebar.text_input(
        "OPENAI_API_KEY",
        value=st.session_state["api_keys"]["openai_api_key"],
        type="password",
        placeholder="sk-...",
        help="API Key für OpenAI (GPT, Embeddings)"
    )
else:
    st.sidebar.markdown("**LM Studio Konfiguration**")
    st.sidebar.info(
        "LM Studio muss laufen und der lokale Server gestartet sein. "
        "Lade ein Chat-Modell, ein Vision-Modell (z.B. LLaVA / Qwen2-VL) "
        "und ein Embedding-Modell (z.B. nomic-embed-text).",
    )
    st.session_state["api_keys"]["lmstudio_base_url"] = st.sidebar.text_input(
        "LM Studio Server URL",
        value=st.session_state["api_keys"].get("lmstudio_base_url", "http://localhost:1234/v1"),
        placeholder="http://localhost:1234/v1",
        help="Basis-URL des LM Studio Local Servers (Standard: http://localhost:1234/v1)"
    )
    st.session_state["api_keys"]["lmstudio_chat_model"] = st.sidebar.text_input(
        "Chat-Modell (Text)",
        value=st.session_state["api_keys"].get("lmstudio_chat_model", "local-model"),
        placeholder="z.B. mistral-7b-instruct",
        help="Modellname exakt wie in LM Studio angezeigt (für Textgenerierung)"
    )
    st.session_state["api_keys"]["lmstudio_vision_model"] = st.sidebar.text_input(
        "Vision-Modell (Bilder)",
        value=st.session_state["api_keys"].get("lmstudio_vision_model", "local-vision-model"),
        placeholder="z.B. llava-v1.5-7b",
        help="Multimodales Modell für Bildbeschreibung (LLaVA, Qwen2-VL, Llama 3.2 Vision, ...)"
    )
    st.session_state["api_keys"]["lmstudio_embed_model"] = st.sidebar.text_input(
        "Embedding-Modell",
        value=st.session_state["api_keys"].get("lmstudio_embed_model", "nomic-embed-text"),
        placeholder="z.B. nomic-embed-text",
        help="Embedding-Modell für Vektorsuche (muss in LM Studio geladen sein)"
    )
    st.session_state["api_keys"]["lmstudio_embed_dim"] = st.sidebar.text_input(
        "Embedding-Dimension",
        value=st.session_state["api_keys"].get("lmstudio_embed_dim", "768"),
        placeholder="768",
        help="Ausgabe-Dimensionen des Embedding-Modells (nomic-embed-text=768, mxbai-embed-large=1024)"
    )
    st.sidebar.warning(
        "Hinweis: Wenn du von Cloud auf Lokal wechselst und ein anderes Embedding-Modell "
        "verwendest, ändern sich die Vektor-Dimensionen. Du musst dann alle PDFs neu ingestieren "
        "und die Neo4j-Vektorindizes neu aufbauen (Schema anlegen → Neu ingestieren)."
    )

st.sidebar.markdown("---")
st.sidebar.markdown("**Gamma Präsentationen**")
st.session_state["api_keys"]["gamma_api_key"] = st.sidebar.text_input(
    "GAMMA_API_KEY",
    value=st.session_state["api_keys"]["gamma_api_key"],
    type="password",
    placeholder="Optional für Gamma",
    help="API Key für Gamma.app Präsentationen"
)
st.session_state["api_keys"]["gamma_api_url"] = st.sidebar.text_input(
    "GAMMA_API_URL",
    value=st.session_state["api_keys"]["gamma_api_url"],
    placeholder="https://api.gamma.app",
    help="Gamma API Basis-URL"
)

st.sidebar.markdown("---")
st.sidebar.markdown("**Synthesia Videos**")
st.session_state["api_keys"]["synthesia_api_key"] = st.sidebar.text_input(
    "SYNTHESIA_API_KEY",
    value=st.session_state["api_keys"]["synthesia_api_key"],
    type="password",
    placeholder="Optional für Synthesia",
    help="API Key für Synthesia Video-Generierung"
)
st.session_state["api_keys"]["synthesia_api_base"] = st.sidebar.text_input(
    "SYNTHESIA_API_BASE",
    value=st.session_state["api_keys"]["synthesia_api_base"],
    placeholder="https://api.synthesia.io/v1",
    help="Synthesia API Basis-URL"
)

st.sidebar.markdown("---")
c1, c2, c3 = st.sidebar.columns(3)
with c1:
    if st.sidebar.button("Speichern (persistent)", key="save_api_keys"):
        try:
            save_user_config(st.session_state["api_keys"])
            st.sidebar.success("Gespeichert.")
        except Exception as e:
            st.sidebar.error(f"Speichern fehlgeschlagen: {e}")
with c2:
    if st.sidebar.button("Gespeicherte löschen", key="delete_saved_api_keys"):
        try:
            if USER_CONFIG_PATH.exists():
                USER_CONFIG_PATH.unlink()
            st.sidebar.success("Gespeicherte Werte gelöscht.")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Löschen fehlgeschlagen: {e}")
with c3:
    if st.sidebar.button("Auf .env Standardwerte zurücksetzen", key="reset_to_env"):
        st.session_state["api_keys"] = {
            "neo4j_uri": cfg.NEO4J_URI or "",
            "neo4j_username": cfg.NEO4J_USERNAME or "neo4j",
            "neo4j_password": cfg.NEO4J_PASSWORD or "",
            "openai_api_key": cfg.OPENAI_API_KEY or "",
            "gamma_api_key": cfg.GAMMA_API_KEY or "",
            "gamma_api_url": cfg.GAMMA_API_URL or "",
            "synthesia_api_key": cfg.SYNTHESIA_API_KEY or "",
            "synthesia_api_base": cfg.SYNTHESIA_API_BASE or "https://api.synthesia.io/v1",
            "llm_mode": cfg.LLM_MODE or "cloud",
            "lmstudio_base_url": cfg.LMSTUDIO_BASE_URL or "http://localhost:1234/v1",
            "lmstudio_chat_model": cfg.LMSTUDIO_CHAT_MODEL or "local-model",
            "lmstudio_vision_model": cfg.LMSTUDIO_VISION_MODEL or "local-vision-model",
            "lmstudio_embed_model": cfg.LMSTUDIO_EMBED_MODEL or "nomic-embed-text",
            "lmstudio_embed_dim": str(cfg.LMSTUDIO_EMBED_DIM or "768"),
        }
        st.rerun()

# Update config module with session values (these will be used by the app)
cfg.NEO4J_URI = st.session_state["api_keys"]["neo4j_uri"]
cfg.NEO4J_USERNAME = st.session_state["api_keys"]["neo4j_username"]
cfg.NEO4J_PASSWORD = st.session_state["api_keys"]["neo4j_password"]
cfg.OPENAI_API_KEY = st.session_state["api_keys"]["openai_api_key"]
cfg.GAMMA_API_KEY = st.session_state["api_keys"]["gamma_api_key"]
cfg.GAMMA_API_URL = st.session_state["api_keys"]["gamma_api_url"]
cfg.SYNTHESIA_API_KEY = st.session_state["api_keys"]["synthesia_api_key"]
cfg.SYNTHESIA_API_BASE = st.session_state["api_keys"]["synthesia_api_base"]
# LM Studio
cfg.LLM_MODE            = st.session_state["api_keys"]["llm_mode"]
cfg.LMSTUDIO_BASE_URL   = st.session_state["api_keys"].get("lmstudio_base_url", "http://localhost:1234/v1")
cfg.LMSTUDIO_CHAT_MODEL    = st.session_state["api_keys"].get("lmstudio_chat_model", "local-model")
cfg.LMSTUDIO_VISION_MODEL  = st.session_state["api_keys"].get("lmstudio_vision_model", "local-vision-model")
cfg.LMSTUDIO_EMBED_MODEL   = st.session_state["api_keys"].get("lmstudio_embed_model", "nomic-embed-text")
try:
    cfg.LMSTUDIO_EMBED_DIM = int(st.session_state["api_keys"].get("lmstudio_embed_dim", "768"))
except ValueError:
    cfg.LMSTUDIO_EMBED_DIM = 768


# =========================
# Main Tabs
# =========================
tab_ingest, tab_coursegen, tab_gamma, tab_synthesia, tab_cypher = st.tabs([
    "Dokumente aufnehmen",
    "Kursgenerator",
    "Slideexport",
    "Videoexport",
    "Cypher"
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

# ---- Tab: Gamma Export ----
with tab_gamma:
    st.subheader("Kurs als Präsentation via Gamma exportieren")
    st.markdown("Exportiere deinen erstellten Kurs als interaktive Präsentation über die Gamma API.")
    
    # Lade verfügbare gespeicherte Kurse
    from pathlib import Path
    import json
    exports_dir = Path(__file__).parent.parent / "exports"
    exports_dir.mkdir(exist_ok=True)
    
    # Suche nach gespeicherten Kursen (JSON-Dateien die mit _course.json enden)
    course_files = list(exports_dir.glob("*_course.json"))
    available_courses = {}
    
    for course_file in course_files:
        try:
            with open(course_file, 'r', encoding='utf-8') as f:
                course_data = json.load(f)
                course_name = course_data.get("Kursname", course_file.stem.replace("_course", ""))
                available_courses[course_name] = course_data
        except Exception as e:
            print(f"Could not load course from {course_file}: {e}")
    
    # Dropdown für Kursauswahl
    st.markdown("### Kurs auswählen")
    
    course_options = ["Aktueller Kurs (aus Kursgenerator)"] + list(available_courses.keys())
    selected_course_option = st.selectbox(
        "Wähle einen Kurs für den Export",
        options=course_options,
        help="Wähle den aktuell bearbeiteten Kurs oder einen zuvor gespeicherten Kurs"
    )
    
    # Hole den entsprechenden Kurs
    if selected_course_option == "Aktueller Kurs (aus Kursgenerator)":
        course = st.session_state.get("course_struct", {})
    else:
        course = available_courses.get(selected_course_option, {})
    
    if not course.get("Kapitel"):
        st.warning("Der ausgewählte Kurs hat keine Kapitel. Bitte erstelle zuerst einen Kurs im Tab 'Kursgenerator' oder wähle einen anderen Kurs.")
    else:
        with st.expander("Gamma-Einstellungen"):
            col_g1, col_g2, col_g3 = st.columns(3)
            
            with col_g1:
                from pathlib import Path
                import json
                exports_dir = Path(__file__).parent.parent / "exports"
                themes_file = exports_dir / "gamma_themes.json"
                
                if themes_file.exists():
                    themes = json.loads(themes_file.read_text(encoding="utf-8"))
                else:
                    themes = ["Oasis", "Corporate", "Minimal", "ISTE"]
                
                gamma_theme = st.selectbox(
                    "Theme",
                    options=themes,
                    index=0,
                    help="Gamma Präsentations-Theme"
                )
            
            with col_g2:
                gamma_lang = st.selectbox(
                    "Sprache",
                    options=["de", "en", "fr", "es", "it"],
                    index=0,
                    help="Sprache der Präsentation"
                )
            
            with col_g3:
                gamma_img_source = st.selectbox(
                    "Bildquelle",
                    options=["noImages", "aiGenerated", "unsplash", "webFreeToUse"],
                    index=0,
                    help="Quelle für Bilder in der Präsentation"
                )
            
            gamma_cards = st.number_input(
                "Anzahl Folien",
                min_value=5,
                max_value=100,
                value=20,
                step=5,
                help="Gamma verteilt den Inhalt automatisch auf diese Anzahl von Folien"
            )
        
        if st.button("Gamma-Präsentation generieren", type="primary"):
            try:
                from src.gamma import GammaClient
                import re
                
                neo = get_neo()
                from scripts.course_generator import fetch_content_for_chapter, generate_course_pdf
                
                # Hole die Zielgruppe aus session_state
                learner_role = st.session_state.get("coursegen_learner_role", "")
                
                with st.spinner("Generiere Kursinhalte für Gamma..."):
                    content_sections = []
                    all_sources = {}  # Sammle alle Quellen
                    
                    # Titelbereich
                    title_section = f"# {course['Kursname']}\n\nVorlesungsunterlagen basierend auf dem Wissensgraphen"
                    content_sections.append(title_section)
                    
                    for kapitel in course["Kapitel"]:
                        chapter_number = kapitel.get('Nummer', '').strip()
                        chapter_title = kapitel['Titel']
                        full_chapter_title = f"{chapter_number} {chapter_title}" if chapter_number else chapter_title
                        
                        # Kapitel-Übersicht mit Lernzielen
                        chapter_section = f"## {full_chapter_title}\n\n"
                        if kapitel.get("Lernziele"):
                            chapter_section += "**Lernziele:**\n"
                            for ziel in kapitel["Lernziele"]:
                                if ziel.strip():
                                    chapter_section += f"* {ziel}\n"
                        content_sections.append(chapter_section)
                        
                        # Abschnitte mit Inhalten hinzufügen
                        abschnitte = kapitel.get("Abschnitte", [])
                        retrieval_hints = kapitel.get("Retrieval_Hinweise", {})
                        
                        for aidx, abschnitt_title in enumerate(abschnitte):
                            if not abschnitt_title.strip():
                                continue
                            
                            # Hole spezifische Hinweise für diesen Abschnitt
                            section_hints = {}
                            section_key = str(aidx)
                            if section_key in retrieval_hints:
                                section_hints[section_key] = retrieval_hints[section_key]
                            
                            # Generiere Inhalt für diesen Abschnitt aus dem Wissensgraphen
                            section_content = fetch_content_for_chapter(
                                neo, 
                                chapter_title,
                                retrieval_hints=section_hints,
                                section_title=abschnitt_title,
                                learner_role=learner_role if learner_role.strip() else None
                            )
                            
                            # Sammle Quellen
                            if section_content.get("sources"):
                                print(f"DEBUG GUI: Section '{abschnitt_title}' has {len(section_content['sources'])} sources")
                                for source in section_content["sources"]:
                                    title = source.get("title", "")
                                    if title and title.strip() and title.strip().lower() not in ['unknown', '']:
                                        if title not in all_sources:
                                            all_sources[title] = source
                                            print(f"DEBUG GUI: Added source: {title[:50]}")
                            else:
                                print(f"DEBUG GUI: Section '{abschnitt_title}' has NO sources!")
                            
                            # Erstelle Abschnittsinhalt (Gamma teilt automatisch auf)
                            section_text = f"### {abschnitt_title}\n\n"
                            
                            # Verwende den generierten Text
                            answer_text = section_content.get("answer_text", "").strip()
                            
                            if answer_text:
                                # Gamma teilt langen Text automatisch auf - keine Begrenzung
                                section_text += answer_text
                            else:
                                section_text += "*Keine relevanten Inhalte im Wissensgraphen gefunden*"
                            
                            content_sections.append(section_text)
                    
                    # Füge Quellenverzeichnis am Ende hinzu - AUFGETEILT auf mehrere Folien
                    print(f"DEBUG GUI: Total sources collected for Gamma: {len(all_sources)}")
                    if all_sources:
                        print(f"DEBUG GUI: Creating bibliography with {len(all_sources)} sources")
                        # Sortiere alphabetisch
                        sorted_sources = sorted(all_sources.items(), key=lambda x: x[0])
                        
                        # Teile Quellen auf mehrere Folien auf (max 5 pro Folie)
                        sources_per_slide = 5
                        num_bibliography_slides = (len(sorted_sources) + sources_per_slide - 1) // sources_per_slide
                        
                        for slide_idx in range(num_bibliography_slides):
                            start_idx = slide_idx * sources_per_slide
                            end_idx = min(start_idx + sources_per_slide, len(sorted_sources))
                            slide_sources = sorted_sources[start_idx:end_idx]
                            
                            # Erstelle Literaturverzeichnis-Folie mit starker Trennung
                            # Füge viel Whitespace hinzu, damit Gamma es als separate Folie erkennt
                            if slide_idx == 0:
                                bibliography_text = "\n\n\n\n# Literaturverzeichnis\n\n"
                            else:
                                bibliography_text = f"\n\n\n\n# Literaturverzeichnis (Teil {slide_idx + 1})\n\n"
                            
                            bibliography_text += "Quellenangaben:\n\n"
                            
                            for idx, (title, source_info) in enumerate(slide_sources, start_idx + 1):
                                # Erstelle Zitat im APA-ähnlichen Format
                                citation_parts = []
                                
                                # Autoren
                                authors = (source_info.get("authors") or "").strip()
                                if authors:
                                    authors = authors.replace(";", ",")[:100]
                                    citation_parts.append(authors)
                                
                                # Jahr
                                year = source_info.get("year", "")
                                if year:
                                    citation_parts.append(f"({year})")
                                
                                # Titel
                                if title:
                                    citation_parts.append(f"{title[:100]}")
                                
                                # Zusammenbauen
                                citation = " ".join(citation_parts) if citation_parts else title[:150]
                                bibliography_text += f"**[{idx}]** {citation}\n\n"
                            
                            # Füge Padding am Ende hinzu
                            bibliography_text += "\n\n\n"
                            
                            content_sections.append(bibliography_text)
                        
                        print(f"DEBUG GUI: Bibliography split into {num_bibliography_slides} slides")
                        print(f"DEBUG GUI: Increasing numCards to accommodate bibliography")
                    else:
                        print(f"DEBUG GUI: WARNING - No sources, bibliography NOT added!")
                    
                    # Kombiniere alle Abschnitte zu einem kontinuierlichen Dokument
                    gamma_input = "\n\n".join(content_sections)
                    print(f"DEBUG GUI: Total gamma_input length: {len(gamma_input)} chars, sections: {len(content_sections)}")
                    print(f"DEBUG GUI: Last 500 chars of gamma_input:\n{gamma_input[-500:]}")
                
                with st.spinner("Sende an Gamma API..."):
                    try:
                        g = GammaClient()
                    except Exception as e:
                        st.error(f"GammaClient konnte nicht initialisiert werden: {e}")
                        g = None
                    
                    if g is not None:
                        # Verwende die vom Benutzer angegebene Folienanzahl ohne Anpassung
                        # Gamma verteilt den gesamten Content (inkl. Literaturverzeichnis) auf diese Anzahl
                        print(f"DEBUG GUI: Using numCards: {gamma_cards}")
                        
                        body = {
                            "inputText": gamma_input,
                            "textMode": "preserve",
                            "format": "presentation",
                            "themeName": gamma_theme,
                            "cardSplit": "auto",
                            "numCards": int(gamma_cards),
                            "exportAs": "pptx",
                            "textOptions": {"language": gamma_lang, "amount": "medium"},
                            "imageOptions": {"source": gamma_img_source},
                            "cardOptions": {"dimensions": "16x9"},
                            "sharingOptions": {"externalAccess": "view", "workspaceAccess": "edit"}
                        }
                        
                        with st.spinner("Generiere Präsentation..."):
                            try:
                                gen_id = g.generate(body)
                                st.info(f"Generation ID: {gen_id}")
                                status = g.poll(gen_id, interval_sec=5, timeout_sec=600)
                                
                                with st.expander("Debug: Vollständiger Gamma Status"):
                                    st.json(status)
                                
                                file_url = status.get("exportUrl")
                                if file_url:
                                    st.success("PPTX verfügbar!")
                                    try:
                                        safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", course['Kursname'])[:50]
                                        from pathlib import Path
                                        exports_dir = Path(__file__).parent.parent / "exports"
                                        out_file = g.download_file(file_url, out_dir=str(exports_dir / "gamma"), filename=f"{safe_name}.pptx")
                                        st.success(f"PPTX heruntergeladen: {out_file}")
                                        
                                        with open(out_file, "rb") as f:
                                            st.download_button(
                                                label="PPTX herunterladen",
                                                data=f.read(),
                                                file_name=f"{safe_name}.pptx",
                                                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation"
                                            )
                                    except Exception as e:
                                        st.warning(f"Download fehlgeschlagen: {e}")
                            except Exception as e:
                                st.error(f"Fehler bei der Präsentations-Generierung: {e}")
            
            except Exception as e:
                st.error(f"Fehler beim Vorbereiten der Gamma-Präsentation: {e}")
                import traceback
                st.code(traceback.format_exc())

# ---- Tab: Videoexport (Synthesia Cloud oder lokal per TTS)
with tab_synthesia:
    st.subheader("Präsentation als Video exportieren")

    video_mode = st.radio(
        "Videogenerierung",
        ["Lokal (TTS + MoviePy, kostenlos)", "Cloud (Synthesia API)"],
        index=0,
        horizontal=True,
        help="Lokal: Folientexte werden per TTS vertont und per MoviePy zu einem MP4 zusammengefügt.  "
             "Cloud: Synthesia erstellt ein Avatar-Video (kostenpflichtig).",
    )

    st.markdown("---")

    # ---- PPTX auswählen (gemeinsam für beide Modi) ----
    col1, col2 = st.columns([2, 1])
    with col1:
        uploaded = st.file_uploader("PPTX hochladen", type=["pptx"], key="video_pptx_upload")
        pptx_files = []
        if EXPORTS_DIR.exists():
            pptx_files.extend([p.name for p in EXPORTS_DIR.glob("*.pptx")])
            gamma_dir = EXPORTS_DIR / "gamma"
            if gamma_dir.exists():
                pptx_files.extend([p.name for p in gamma_dir.glob("*.pptx")])
        pptx_files = list(set(pptx_files))
        selected = None
        if pptx_files:
            selected = st.selectbox("Oder vorhandene PPTX aus exports/ wählen",
                                    ["-- none --"] + sorted(pptx_files))

    pptx_path = None
    if uploaded is not None:
        save_to = UPLOAD_DIR / uploaded.name
        with open(save_to, "wb") as fh:
            fh.write(uploaded.getbuffer())
        pptx_path = str(save_to)
        st.success(f"Hochgeladen: {save_to.name}")
    elif selected and selected != "-- none --":
        candidate = EXPORTS_DIR / selected
        if not candidate.exists():
            candidate = EXPORTS_DIR / "gamma" / selected
        pptx_path = str(candidate) if candidate.exists() else None

    # ======================================================
    # LOKAL-MODUS
    # ======================================================
    if video_mode.startswith("Lokal"):
        with col2:
            st.markdown("**TTS-Einstellungen**")
            tts_backend = st.selectbox(
                "TTS-Backend",
                ["edge-tts (empfohlen, kostenlos)", "pyttsx3 (offline)"],
                index=0,
                help="edge-tts: Microsoft Edge TTS, kostenlos, sehr gute Qualität.  "
                     "pyttsx3: vollständig offline, nutzt Windows SAPI.",
            )
            tts_backend_key = "edge-tts" if tts_backend.startswith("edge") else "pyttsx3"

            tts_voice = st.text_input(
                "Stimme",
                value="de-DE-KatjaNeural" if tts_backend_key == "edge-tts" else "",
                help="edge-tts Beispiele: de-DE-KatjaNeural, de-DE-ConradNeural, en-US-AriaNeural.  "
                     "pyttsx3: Teil des Stimmnamens (z.B. 'Katja'), leer = Standardstimme.",
            )
            use_llm = st.checkbox(
                "LLM-Narration (Vortragstexte per KI generieren)",
                value=False,
                help="Das konfigurierte Sprachmodell (Cloud oder LM Studio) schreibt einen "
                     "natürlichen Vortragstext pro Folie statt den Rohtext vorzulesen.",
            )
            narration_lang = st.selectbox("Sprache", ["de (Deutsch)", "en (Englisch)"], index=0)
            narration_lang_key = "de" if narration_lang.startswith("de") else "en"
            fallback_sec = st.number_input("Foliendauer ohne Audio (Sekunden)", min_value=1.0,
                                           value=5.0, step=0.5)
            gen_local = st.button("Video lokal generieren", type="primary")

        st.info(
            "**Benötigte Pakete** (einmalig in der Konsole installieren):  \n"
            "`pip install moviepy edge-tts`  \n"
            "Für Offline-TTS zusätzlich: `pip install pyttsx3`"
        )

        if gen_local:
            if not pptx_path:
                st.error("Bitte zuerst eine PPTX hochladen oder eine vorhandene auswählen.")
            else:
                progress_placeholder = st.empty()
                def _update_progress(msg: str):
                    progress_placeholder.info(msg)

                with st.spinner("Video wird generiert..."):
                    try:
                        from src.local_tts_video import generate_local_video
                        mp4 = generate_local_video(
                            pptx_path=pptx_path,
                            out_dir=str(EXPORTS_DIR / "videos"),
                            tts_backend=tts_backend_key,
                            tts_voice=tts_voice,
                            use_llm_narration=use_llm,
                            narration_language=narration_lang_key,
                            fallback_duration=fallback_sec,
                            progress_callback=_update_progress,
                        )
                        progress_placeholder.empty()
                        st.success(f"Video fertig: {Path(mp4).name}")
                        st.video(mp4)
                        with open(mp4, "rb") as fh:
                            st.download_button("MP4 herunterladen", fh.read(),
                                               file_name=Path(mp4).name, mime="video/mp4")
                    except RuntimeError as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(f"Fehler bei der lokalen Videogenerierung: {e}")
                        import traceback
                        st.code(traceback.format_exc())

    # ======================================================
    # CLOUD-MODUS (Synthesia)
    # ======================================================
    else:
        with col2:
            st.markdown("**Synthesia-Einstellungen**")
            voice = st.text_input("Voice (Synthesia voice id)", value="en-US")
            api_key = st.text_input("Synthesia API Key", type="password",
                                    value=st.session_state.get("synthesia_api_key", ""))
            api_url = st.text_input("Synthesia API Base URL",
                                    value=st.session_state.get("synthesia_api_base",
                                                                cfg.SYNTHESIA_API_BASE or "https://api.synthesia.io/v1"))
            st.markdown("---")
            total_minutes = st.number_input("Gesamtlänge (Minuten, optional)", min_value=0.0,
                                            value=0.0, step=0.5)
            per_slide_seconds = st.number_input("Sekunden pro Folie (optional)", min_value=0.0,
                                                value=0.0, step=0.5)
            fallback_local = st.checkbox("Bei Fehler lokal erzeugen (Fallback)", value=True)
            if api_key:
                st.session_state["synthesia_api_key"] = api_key
            if api_url:
                st.session_state["synthesia_api_base"] = api_url
            gen = st.button("An Synthesia senden", type="primary")

        if gen:
            if not pptx_path:
                st.error("Bitte zuerst eine PPTX hochladen oder eine vorhandene auswählen.")
            else:
                use_key = st.session_state.get("synthesia_api_key") or cfg.SYNTHESIA_API_KEY
                use_url = st.session_state.get("synthesia_api_base") or cfg.SYNTHESIA_API_BASE
                if not use_key:
                    st.error("Synthesia API Key nicht konfiguriert.")
                else:
                    with st.spinner("Sende an Synthesia und warte auf Ergebnis..."):
                        try:
                            mp4 = generate_video_from_pptx_via_synthesia(
                                pptx_path,
                                str(EXPORTS_DIR),
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
                                st.download_button("MP4 herunterladen", fh.read(),
                                                   file_name=Path(mp4).name, mime="video/mp4")
                        except Exception as e:
                            st.error(f"Fehler beim Synthesia-Aufruf: {e}")

# ---- Tab: Ingest & Konzepte (Unified) ----
with tab_ingest:
    st.subheader("Dokumente aufnehmen & Metadaten verwalten")
    
    # === Concept & Ingest Settings ===
    with st.expander("Konzept-Extraktion & Topic-Einstellungen", expanded=True):
        col_topic = st.columns(1)[0]
        
        with col_topic:
            # Topic-Auswahl mit Auto-Inference
            neo = get_neo()
            existing_topics = [r["name"] for r in neo.run("MATCH (t:Topic) RETURN DISTINCT t.name AS name ORDER BY name")]
            
            if not existing_topics:
                existing_topics = ["Künstliche Intelligenz"]
            
            use_auto_topic = st.checkbox("Topic automatisch aus Titel ableiten", value=False)
            
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
            new_topic = st.text_input("Oder neues Topic erstellen", key="new_topic_input")
            if new_topic:
                st.session_state["concept_topic"] = new_topic
                selected_topic = new_topic

        # Backend defaults (controls removed from GUI)
        check_duplicates = True
        quality_filter = True
        try:
            min_confidence = float(os.getenv("INGEST_MIN_CONFIDENCE", "0.6"))
        except Exception:
            min_confidence = 0.6
    
    st.markdown("---")
    
    # === File Upload ===
    uploaded = st.file_uploader("PDF-Dateien auswählen", type=["pdf"], accept_multiple_files=True)
    
    # Enhanced ingest function
    def ingest_one_pdf_enhanced(path: Path, use_auto_topic_param: bool, topic_param: str, progress_callback=None) -> Dict[str, Any]:
        """
        Ingest function with progress callback.
        progress_callback: callable(step_name: str, percent: int) for progress updates
        """
        neo = get_neo()
        
        def report_progress(step: str, pct: int):
            if progress_callback:
                progress_callback(step, pct)

        def _sub(base: int, span: int):
            """Erzeugt einen Sub-Progress-Callback, der [0, 100] auf [base, base+span] mappt."""
            if not progress_callback:
                return None
            def fn(label: str, pct: int):
                report_progress(label, min(100, base + int(pct * span / 100)))
            return fn
        
        # Read PDF
        report_progress("PDF lesen", 5)
        paper_meta, sections, paragraphs, figures = read_pdf_text_and_images(str(path))
        print(f"🔍 DEBUG after read_pdf: title = '{paper_meta.get('title')}', path = {path}")
        
        # Extract enhanced metadata
        report_progress("Metadaten extrahieren", 10)
        paper_meta = extract_enhanced_metadata(path, paper_meta)
        print(f"🔍 DEBUG after extract_enhanced: title = '{paper_meta.get('title')}'")
        
        # Check for duplicates
        report_progress("Duplikate prüfen", 15)
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
        report_progress("Topic bestimmen", 20)
        if use_auto_topic_param:
            topic = infer_topic_from_title(paper_meta.get("title", ""))
            st.info(f"📌 Auto-Topic: {topic}")
        else:
            topic = topic_param or "Künstliche Intelligenz"
        
        # Calculate dynamic parameters (always)
        params = calculate_dynamic_parameters(len(paragraphs))
        max_ent = params["max_entities"]
        max_rel = params["max_relations"]
        
        # Upsert paper
        report_progress("Paper speichern", 25)
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
        
        report_progress("Sections speichern", 30)
        if sections:
            neo.add_sections(paper_meta["paper_id"], sections)
        
        report_progress("Paragraphen embedden", 40)
        paragraphs_emb = embed_paragraphs(paragraphs, progress_fn=_sub(40, 10))
        report_progress("Paragraphen speichern", 50)
        neo.add_paragraphs(paper_meta["paper_id"], paragraphs_emb)
        
        # Figures
        report_progress("Figures analysieren", 60)
        figs_analysed = analyze_and_embed_figures(figures, progress_fn=_sub(60, 10)) if figures else []
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
        
        # Extract concepts (Hybrid: NER + LLM + deterministic text matching)
        report_progress("Konzepte extrahieren", 70)
        full_text = "\n\n".join(p.get("text", "") for p in paragraphs_emb)
        extraction_result = extract_and_embed_concepts_hybrid(
            paper_title=paper_meta.get("title") or "",
            paper_text=full_text,
            paragraphs=paragraphs_emb,
            topic_hint=topic,
            max_entities=max_ent,
            max_relations=max_rel,
            use_scispacy=True,
            neo_client=neo,
            persist_to_topic=True,
            progress_fn=_sub(70, 20),
        )
        concepts  = extraction_result["concepts"]
        links     = extraction_result["paragraph_links"]
        relations = extraction_result["relations"]

        # Link paragraphs to concepts (MENTIONS edges)
        report_progress("Links erstellen", 90)
        if links:
            try:
                neo.link_paragraphs_to_concepts(paper_meta["paper_id"], links)
            except Exception as e:
                st.warning(f"⚠️ Link-Fehler: {e}")

        # Write semantic relations (SEMANTIC_RELATION + CO_OCCURS_WITH)
        if relations:
            try:
                cooc_rels = [r for r in relations if r.get("predicate") == "co_occurs_with"]
                sem_rels  = [r for r in relations if r.get("predicate") != "co_occurs_with"]
                if sem_rels:
                    neo.add_semantic_relations(paper_meta["paper_id"], sem_rels)
                if cooc_rels:
                    cooc_fmt = [
                        {"concept1": r["subject"], "concept2": r["object"],
                         "count": 1, "strength": float(r.get("confidence", 0.6))}
                        for r in cooc_rels
                    ]
                    neo.add_cooccurrence_relations(paper_meta["paper_id"], cooc_fmt)
            except Exception as e:
                st.warning(f"⚠️ Relations-Fehler (non-fatal): {e}")
        
        # Auto-attach to umbrella
        report_progress("Finalisierung", 95)
        # Umbrella-Struktur nicht mehr verwendet
        
        report_progress("Abschließen", 100)
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
        if st.button("Ingest starten", type="primary"):
            reports = []
            duplicates = []
            errors = []
            
            overall_progress_bar = st.progress(0)
            overall_status = st.empty()
            
            # Per-Paper Progress
            paper_progress_bar = st.progress(0)
            paper_status = st.empty()
            paper_percent = st.empty()
            paper_detail = st.empty()   # Granulare Fortschrittsanzeige
            
            for idx, f in enumerate(uploaded):
                try:
                    overall_percent = int((idx / len(uploaded)) * 100)
                    overall_status.text(f"Gesamt: {idx+1}/{len(uploaded)} Papers")
                    overall_progress_bar.progress((idx) / len(uploaded))
                    
                    # Reset paper progress
                    paper_status.text(f"{f.name}")
                    paper_progress_bar.progress(0)
                    paper_percent.metric("Paper-Fortschritt", "0%")
                    
                    out_path = UPLOAD_DIR / f.name
                    out_path.write_bytes(f.read())
                    
                    # Callback für Paper-Fortschritt (auch für Sub-Steps)
                    def update_paper_progress(step: str, pct: int):
                        paper_progress_bar.progress(pct / 100)
                        paper_percent.metric("Paper-Fortschritt", f"{pct}%")
                        paper_status.text(f"{f.name} — {step}")
                        paper_detail.caption(f"↳ {step}")
                    
                    rep = ingest_one_pdf_enhanced(out_path, use_auto_topic, selected_topic, progress_callback=update_paper_progress)
                    
                    if rep.get("status") == "duplicate":
                        duplicates.append(rep)
                        st.warning(f"Duplikat übersprungen: {rep['title']}")
                    else:
                        reports.append(rep)
                        
                        # Validation
                        validation = validate_ingestion_quality(rep)
                        
                        # Show summary
                        with st.expander(f"{rep['title']} ({validation['quality_label']})"):
                            summary = create_ingestion_summary(rep, validation)
                            st.markdown(summary)
                    
                except Exception as e:
                    errors.append({"file": f.name, "error": str(e), "trace": traceback.format_exc()})
                    st.error(f"Fehler bei {f.name}: {e}")
                
                overall_progress_bar.progress((idx + 1) / len(uploaded))
            
            # Finale Anzeige
            overall_status.text(f"Gesamt: {len(uploaded)}/{len(uploaded)} Papers")
            paper_status.empty()
            paper_progress_bar.empty()
            paper_percent.empty()
            paper_detail.empty()
            
            # Final summary
            st.markdown("---")
            st.success(f"Fertig! {len(reports)} erfolgreich, {len(duplicates)} Duplikate, {len(errors)} Fehler")
            
            if reports:
                avg_quality = sum(validate_ingestion_quality(r)["quality_score"] for r in reports) / len(reports)
                st.metric("Durchschnittliche Qualität", f"{avg_quality:.0f}/100")

                # Post-Ingest: Metadaten kuratieren
                st.markdown("---")
                st.subheader("Metadaten kuratieren (fehlende Felder ergänzen)")
                st.caption("Vorhandene Felder werden gezeigt, leere Felder kannst du ergänzen. Bereits gesetzte Werte werden nicht überschrieben.")
                fields = ["author", "publication_year", "doi", "url", "source", "publisher"]
                
                # Eine gemeinsame Form für alle Papers, damit Eingaben nicht verloren gehen
                with st.form(key="meta_curate_form"):
                    for rep in reports:
                        meta = fetch_paper_metadata(rep["paper_id"])
                        st.markdown(f"**{meta.get('title') or rep.get('title') or 'Ohne Titel'}**")
                        
                        # Container für Inputs
                        with st.container(border=True):
                            # Initialisiere Session State für diese Paper
                            session_key_prefix = f"meta_input_{rep['paper_id']}"
                            if session_key_prefix not in st.session_state:
                                st.session_state[session_key_prefix] = {f: "" for f in fields}
                            
                            # Author-Feld speziell (komma-getrennt für mehrere)
                            current_author = meta.get("author") or ""
                            placeholder_author = "fehlt" if not current_author else f"vorhanden: {current_author}"
                            author_input = st.text_area(
                                label="author (komma-getrennt für mehrere)",
                                value=st.session_state[session_key_prefix].get("author", ""),
                                placeholder=placeholder_author,
                                height=60,
                                key=f"meta_input_{rep['paper_id']}_author"
                            )
                            st.session_state[session_key_prefix]["author"] = author_input
                            
                            # Andere Felder
                            cols = st.columns(len(fields) - 1)
                            other_fields = fields[1:]  # Alles außer author
                            
                            for i, field in enumerate(other_fields):
                                current = meta.get(field) or ""
                                placeholder = "fehlt" if not current else f"vorhanden: {current}"
                                
                                input_val = cols[i].text_input(
                                    label=field,
                                    value=st.session_state[session_key_prefix].get(field, ""),
                                    placeholder=placeholder,
                                    key=f"meta_input_{rep['paper_id']}_{field}"
                                )
                                st.session_state[session_key_prefix][field] = input_val
                        st.markdown("")
                    
                    # Sammel-Speichern und Reset
                    col_save_all, col_reset_all = st.columns(2)
                    with col_save_all:
                        submitted_all = st.form_submit_button("Alle Änderungen speichern", use_container_width=True)
                        if submitted_all:
                            updated_any = False
                            for rep in reports:
                                meta = fetch_paper_metadata(rep["paper_id"])
                                session_key_prefix = f"meta_input_{rep['paper_id']}"
                                updates = st.session_state[session_key_prefix].copy()
                                to_set = {k: v.strip() for k, v in updates.items() if v and v.strip()}
                                if to_set:
                                    if "author" in to_set and to_set["author"]:
                                        authors_list = [a.strip() for a in to_set["author"].split(",")]
                                        to_set["author"] = ";".join(authors_list)
                                    filtered = {k: v for k, v in to_set.items() if not meta.get(k)}
                                    if filtered:
                                        update_paper_metadata(rep["paper_id"], filtered)
                                        updated_any = True
                            if updated_any:
                                st.success("Metadaten gespeichert.")
                            else:
                                st.info("Keine neuen Eingaben zum Speichern oder Felder bereits befüllt.")
                    with col_reset_all:
                        reset_all = st.form_submit_button("Alle Eingaben löschen", use_container_width=True)
                        if reset_all:
                            for rep in reports:
                                session_key_prefix = f"meta_input_{rep['paper_id']}"
                                st.session_state[session_key_prefix] = {f: "" for f in fields}
                            st.rerun()
            
            if duplicates:
                with st.expander("Duplikate"):
                    for dup in duplicates:
                        st.markdown(f"- **{dup['file_name']}**: {dup['duplicate_info']['message']}")
            
            if errors:
                with st.expander("Fehler"):
                    st.json(errors)

    st.markdown("---")
    
    # === Paper-Metadaten bearbeiten ===
    with st.expander("Vorhandene Papers bearbeiten", expanded=False):
        st.markdown("**Bearbeite Metadaten für bereits aufgenommene Papers**")
        
        neo = get_neo()
        all_papers = neo.run("""
            MATCH (p:Paper)
            RETURN p.paper_id AS paper_id, p.title AS title,
                   p.author AS author, p.publication_year AS publication_year,
                   p.doi AS doi, p.url AS url, p.source AS source, p.publisher AS publisher
            ORDER BY p.title
        """)
        
        if not all_papers:
            st.info("Noch keine Papers im System. Nutze den Ingest unten, um Papers hochzuladen.")
        else:
            # Paper-Auswahl
            paper_titles = {p["title"] or f"Ohne Titel ({p['paper_id']})": p["paper_id"] for p in all_papers}
            selected_title = st.selectbox("Paper auswählen", options=list(paper_titles.keys()))
            selected_paper_id = paper_titles[selected_title]
            
            # Hole aktuelle Metadaten
            paper_data = next(p for p in all_papers if p["paper_id"] == selected_paper_id)
            
            st.markdown("---")
            
            # Bearbeitungsformular
            with st.form(key=f"edit_paper_{selected_paper_id}"):
                st.markdown(f"**Bearbeite: {paper_data['title'] or 'Ohne Titel'}**")
                
                col1, col2 = st.columns(2)
                with col1:
                    new_title = st.text_input("Titel", value=paper_data.get("title") or "")
                    new_author = st.text_area("Autor(en) (komma-getrennt)", value=paper_data.get("author") or "", height=80)
                    new_year = st.text_input("Erscheinungsjahr", value=paper_data.get("publication_year") or "")
                    new_doi = st.text_input("DOI", value=paper_data.get("doi") or "")
                
                with col2:
                    new_url = st.text_input("URL", value=paper_data.get("url") or "")
                    new_source = st.text_input("Quelle", value=paper_data.get("source") or "")
                    new_publisher = st.text_input("Verlag", value=paper_data.get("publisher") or "")
                
                col_save, col_reset = st.columns(2)
                with col_save:
                    save_btn = st.form_submit_button("Änderungen speichern", type="primary", use_container_width=True)
                with col_reset:
                    reset_btn = st.form_submit_button("Zurücksetzen", use_container_width=True)
                
                if save_btn:
                    updates = {}
                    if new_title != (paper_data.get("title") or ""):
                        updates["title"] = new_title
                    if new_author != (paper_data.get("author") or ""):
                        # Konvertiere komma-getrennt zu semikolon
                        authors_list = [a.strip() for a in new_author.split(",") if a.strip()]
                        updates["author"] = ";".join(authors_list)
                    if new_year != (paper_data.get("publication_year") or ""):
                        updates["publication_year"] = new_year
                    if new_doi != (paper_data.get("doi") or ""):
                        updates["doi"] = new_doi
                    if new_url != (paper_data.get("url") or ""):
                        updates["url"] = new_url
                    if new_source != (paper_data.get("source") or ""):
                        updates["source"] = new_source
                    if new_publisher != (paper_data.get("publisher") or ""):
                        updates["publisher"] = new_publisher
                    
                    if updates:
                        # Update in Neo4j
                        set_parts = []
                        params = {"pid": selected_paper_id}
                        for k, v in updates.items():
                            params[k] = v
                            set_parts.append(f"p.{k} = ${k}")
                        cypher = "MATCH (p:Paper {paper_id:$pid}) SET " + ", ".join(set_parts)
                        neo.run(cypher, params)
                        st.success("Metadaten erfolgreich aktualisiert!")
                        st.rerun()
                    else:
                        st.info("Keine Änderungen vorgenommen.")
                
                if reset_btn:
                    st.rerun()
    
    st.markdown("---")
    
    @st.dialog("Graph leeren - Bestätigung erforderlich")
    def confirm_delete_dialog():
        st.warning("Achtung: Dies löscht ALLE Knoten und Kanten. Das Schema bleibt erhalten.")
        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Ja, löschen", type="primary", use_container_width=True):
                with st.spinner("Lösche alle Knoten & Kanten …"):
                    stats = clear_graph()
                st.success("Graph erfolgreich geleert!")
                st.json(stats)
        with col2:
            if st.button("Abbrechen", type="secondary", use_container_width=True):
                st.rerun()
    
    if st.button("Graph leeren", type="secondary"):
        confirm_delete_dialog()

    with tab_cypher:
        # --- Cypher ausführen & visualisieren ---
        st.subheader("Cypher ausführen & visualisieren")

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
            "Concept-Netzwerk (SEMANTIC_RELATION)":
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
        st.markdown("**Visualisierungs-Optionen (Plotly)**")
        
        col_viz1, col_viz2, col_viz3 = st.columns([1, 1, 1])
        
        with col_viz1:
            layout_algo = st.selectbox(
                "Layout-Algorithmus",
                ["spring", "kamada_kawai", "circular", "hierarchical", "shell"],
                index=0,
                help="Spring: Force-directed (Standard)\nKamada-Kawai: Optimiert für Distanzen\nCircular: Kreisförmig\nHierarchical: Hierarchisch (wenn möglich)\nShell: Konzentrische Ringe"
            )
        
        with col_viz2:
            show_edge_labels = st.checkbox("Relationsnamen anzeigen", value=True)
        
        with col_viz3:
            color_by = st.selectbox(
                "Färbung nach",
                ["type", "degree", "community"],
                index=0,
                help="Type: Nach Node-Typ\nDegree: Nach Anzahl Verbindungen\nCommunity: Nach erkannten Clustern"
            )
        
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
                    
                    # Visualisierung mit Plotly
                    visualize_with_plotly(
                        recs_graph, 
                        height=650,
                        layout=layout_algo,
                        show_edge_labels=show_edge_labels,
                        color_by=color_by
                    )
                except Exception as e:
                    st.error(f"Cypher-Fehler: {e}")
                    st.code(cypher_in, language="cypher")

            # Diagnostic button: check Topic/Concept Counts
            if st.button("Diagnose: Topic/Concept Counts"):
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
                # Zähle Topic, Concept
                try:
                    topic_result = neo.run(
                        "MATCH (t:Topic {name:$topic}) RETURN count(t) AS c",
                        {'topic': topic_name}
                    )
                    diag['topic_count'] = topic_result[0]['c'] if topic_result and 'c' in topic_result[0] else 0
                except Exception as e:
                    diag['topic_count'] = f"Error: {e}"
                try:
                    direct_concepts = neo.run(
                        "MATCH (t:Topic {name:$topic})-[:HAS_CONCEPT]->(c:Concept) RETURN count(DISTINCT c) AS c",
                        {'topic': topic_name}
                    )
                    diag['concepts_total'] = direct_concepts[0]['c'] if direct_concepts and 'c' in direct_concepts[0] else 0
                except Exception as e:
                    diag['concepts_total'] = f"Error: {e}"
                st.info(f"Diagnose für Topic '{topic_name}':")
                st.json(diag)