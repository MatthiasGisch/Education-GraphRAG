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
    seed_names_to_concepts
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

def parse_seed_input(text: str) -> List[str]:
    seeds = []
    for line in (text or "").splitlines():
        name = line.strip()
        if name:
            seeds.append(name)
    # Duplikate raus
    seen = set(); out=[]
    for n in seeds:
        if n.lower() in seen: continue
        seen.add(n.lower()); out.append(n)
    return out

def stitch_graph() -> dict:
    """ Vernäht Paragraphs/Figures mit Sections (per page-range) + Dummy-Section pro Paper falls nötig. """
    neo = get_neo()
    neo.run("""
    MATCH (p:Paper)
    WHERE NOT (p)-[:HAS_SECTION]->()
    MERGE (s:Section {section_id: p.paper_id + ":DOC"})
    SET s.title = "Document",
        s.level = 1,
        s.page_start = 1,
        s.page_end = 999999,
        s.`order` = 1
    MERGE (p)-[:HAS_SECTION]->(s)
    """)
    neo.run("""
    MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
    MATCH (p)-[:HAS_SECTION]->(sec:Section)
    WHERE para.page >= coalesce(sec.page_start, 1)
      AND para.page <= coalesce(sec.page_end, 999999)
    MERGE (sec)-[:HAS_PARAGRAPH]->(para)
    """)
    neo.run("""
    MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
    MATCH (p)-[:HAS_SECTION]->(sec:Section)
    WHERE f.page >= coalesce(sec.page_start, 1)
      AND f.page <= coalesce(sec.page_end, 999999)
    MERGE (sec)-[:HAS_FIGURE]->(f)
    """)
    stats = {}
    stats["paras_direct_at_paper"] = get_single_value(neo, "MATCH (p:Paper)-[:HAS_PARAGRAPH]->(:Paragraph) RETURN count(*) AS c")
    stats["paras_via_section"]     = get_single_value(neo, "MATCH (:Section)-[:HAS_PARAGRAPH]->(:Paragraph) RETURN count(*) AS c")
    stats["figs_at_paper"]         = get_single_value(neo, "MATCH (p:Paper)-[:HAS_FIGURE]->(:Figure) RETURN count(*) AS c")
    stats["figs_via_section"]      = get_single_value(neo, "MATCH (:Section)-[:HAS_FIGURE]->(:Figure) RETURN count(*) AS c")
    stats["paragraph_orphans"]     = get_single_value(neo, "MATCH (para:Paragraph) WHERE NOT (()-[:HAS_PARAGRAPH]->(para)) RETURN count(para) AS c")
    stats["figure_orphans"]        = get_single_value(neo, "MATCH (f:Figure) WHERE NOT (()-[:HAS_FIGURE]->(f)) RETURN count(f) AS c")
    stats["papers_without_sections"]= get_single_value(neo, "MATCH (p:Paper) WHERE NOT (p)-[:HAS_SECTION]->() RETURN count(p) AS c")
    return stats

def clear_graph() -> dict:
    neo = get_neo()
    neo.run("MATCH (n) DETACH DELETE n")
    return {
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

def run_cypher_with_params(query_text: str) -> list[dict]:
    """
    Erlaubt Browser-Style :param-Zeilen und/oder JSON-Param-Textbox.
    Setzt, falls nicht vorhanden, automatisch ein Topic aus der DB.
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

    return neo.run(cypher, params)


# ---------- Query orchestrator (UI helper) ----------
def run_query(q: str, web_mode_ui: str) -> Dict[str, Any]:
    neo = get_neo()
    mapping = {"Auto": "auto", "Erzwingen": "force", "Aus": "off"}
    return answer_query(q, neo, web_mode=mapping.get(web_mode_ui, "auto"))

import math
import numpy as np
from uuid import uuid4

def rebuild_concepts_for_all(topic: str, strategy: str, seeds: list[str]) -> dict:
    """
    Extrahiert Konzepte + Links für bereits ingestierte Paper neu (per aktueller Strategie).
    - strategy: "Nur Seeds (keine neuen Konzepte)" | "Seeds + LLM-Erweiterung" | "Nur LLM"
    - seeds: Liste von Seed-Namen
    """
    from src.concept_extract import extract_and_embed_concepts

    neo = get_neo()
    papers = neo.run("MATCH (p:Paper) RETURN p.paper_id AS id, p.title AS title ORDER BY title")
    total_concepts, total_links = 0, 0
    per_paper = []

    seed_only = (strategy == "Nur Seeds (keine neuen Konzepte)")
    llm_only  = (strategy == "Nur LLM")
    allow_new = not seed_only
    seeds_for_llm = [] if llm_only else seeds

    for row in papers:
        pid = row["id"]; title = row.get("title","")
        paras = neo.run("""
            MATCH (p:Paper {paper_id:$pid})-[:HAS_PARAGRAPH]->(para:Paragraph)
            RETURN para.paragraph_id AS paragraph_id, para.text AS text
            ORDER BY para.paragraph_id
        """, {"pid": pid})
        if not paras:
            per_paper.append({"paper_id": pid, "title": title, "n_concepts": 0, "n_links": 0, "note": "keine Paragraphs"})
            continue

        concepts, links = extract_and_embed_concepts(
            paper_title=title,
            paragraphs=paras,
            topic_hint=topic,
            max_concepts=30,
            seed_names=seeds_for_llm,
            allow_new=allow_new
        )
        if concepts:
            neo.upsert_topic(topic)
            neo.add_concepts(topic, concepts)
        if links:
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
    neo = get_neo()
    # Konzepte + Embeddings laden
    concepts = neo.run("""
        MATCH (:Topic {name:$topic})-[:HAS_CONCEPT]->(c:Concept)
        WHERE c.embedding IS NOT NULL
        RETURN c.concept_id AS concept_id, c.name AS name, c.embedding AS emb
        ORDER BY name
    """, {"topic": topic})

    if not concepts:
        return {"clusters": 0, "assigned": 0, "message": "Keine Konzepte mit Embeddings gefunden."}

    # Normiere Embeddings, baue Matrix
    ids   = [c["concept_id"] for c in concepts]
    names = [c["name"] for c in concepts]
    vecs  = np.array([c["emb"] for c in concepts], dtype=float)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
    vecsN = vecs / norms

    assigned = np.full(len(ids), False)
    clusters = []
    for i in range(len(ids)):
        if assigned[i]: 
            continue
        # Ähnlichkeit zu allen
        sims = (vecsN[i] @ vecsN.T)
        members = [j for j, s in enumerate(sims) if (s >= sim_threshold) and (not assigned[j])]
        if len(members) >= min_cluster_size:
            for j in members: assigned[j] = True
            clusters.append(members)
        else:
            # Einzelgänger: ignoriere hier (kannst du auch als eigener Umbrella nehmen, falls gewünscht)
            continue

    # Umbrellas in DB anlegen
    created = 0
    for mem in clusters:
        cluster_names = [names[j] for j in mem]
        # Einfacher "Name": häufigster / kürzester Name
        rep = sorted(cluster_names, key=lambda x: (len(x), x.lower()))[0]
        umbrella_id = f"umb-{uuid4()}"
        neo.run("""
            MERGE (t:Topic {name:$topic})
            MERGE (u:Umbrella {umbrella_id:$uid})
            SET u.name=$name, u.size=$size, u.keywords=$keywords
            MERGE (t)-[:HAS_UMBRELLA]->(u)
        """, {"topic": topic, "uid": umbrella_id, "name": rep, "size": len(mem), "keywords": cluster_names})

        # Kanten zu Konzepten
        neo.run("""
            MATCH (u:Umbrella {umbrella_id:$uid})
            UNWIND $concept_ids AS cid
            MATCH (c:Concept {concept_id: cid})
            MERGE (u)-[:NARROWER]->(c)
        """, {"uid": umbrella_id, "concept_ids": [ids[j] for j in mem]})
        created += 1

    return {"clusters": len(clusters), "umbrellas_created": created, "assigned": int(np.sum(assigned))}

def list_topics() -> list[str]:
    neo = get_neo()
    rows = neo.run("MATCH (t:Topic) RETURN t.name AS name ORDER BY name")
    return [r["name"] for r in rows]

def stitch_figures_paragraphs() -> dict:
    """
    Vernäht Figures mit Paragraphen in drei Stufen:
      1) CAPTIONS: Absatz enthält Prefix der (normalisierten) Caption, Seiten-Toleranz ±1
      2) REFERS_TO: Absatz erwähnt figure_label (z. B. "Figure 2", "Fig. 2") auf ±1 Seite
      3) NEAR-Fallback: Wenn noch immer keine Kante, linke zur "besten" Paragraph-Kandidatin
         (gleiche Seite, längster Text).  -> Beziehungstyp :NEAR
    Gibt Vorher/Nachher-Zahlen und Beispiele unverbundener Figures zurück.
    """
    neo = get_neo()

    def cnt(rel):
        return get_single_value(neo, f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c")

    cap_before = cnt("CAPTIONS")
    ref_before = cnt("REFERS_TO")
    near_before = cnt("NEAR")

    # ---------- PASS 1a: CAPTIONS mit 60-Zeichen-Präfix, ±1 Seite ----------
    neo.run("""
    MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
    WHERE NOT (()-[:CAPTIONS]->(f))
    WITH p, f,
         toLower(coalesce(replace(replace(replace(f.caption, '\r',' '), '\n',' '), '  ',' '), '')) AS cap,
         coalesce(f.page,-1) AS fpage
    WHERE cap <> ''
    WITH p, f, cap, fpage, substring(cap,0,60) AS pref
    MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
    WHERE para.page IN [fpage-1, fpage, fpage+1]
      AND toLower(para.text) CONTAINS pref
    MERGE (para)-[:CAPTIONS]->(f)
    """)

    # ---------- PASS 1b: CAPTIONS mit 25-Zeichen-Präfix, nur gleiche Seite ----------
    neo.run("""
    MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
    WHERE NOT (()-[:CAPTIONS]->(f))
    WITH p, f,
         toLower(coalesce(replace(replace(replace(f.caption, '\r',' '), '\n',' '), '  ',' '), '')) AS cap,
         coalesce(f.page,-1) AS fpage
    WHERE cap <> ''
    WITH p, f, cap, fpage, substring(cap,0,25) AS pref
    MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
    WHERE para.page = fpage
      AND toLower(para.text) CONTAINS pref
    MERGE (para)-[:CAPTIONS]->(f)
    """)

    # ---------- PASS 2a: REFERS_TO mit figure_label direkt ----------
    neo.run("""
    MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
    WHERE NOT (()-[:REFERS_TO]->(f))
    WITH p, f,
         toLower(coalesce(replace(f.figure_label,'.',''), '')) AS flbl,
         coalesce(f.page,-1) AS fpage
    WHERE flbl <> ''
    MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
    WHERE para.page IN [fpage-1, fpage, fpage+1]
      AND toLower(replace(para.text,'.','')) CONTAINS flbl
    MERGE (para)-[:REFERS_TO]->(f)
    """)

    # ---------- PASS 2b: REFERS_TO mit Varianten (figure/fig/abb) ----------
    neo.run("""
    MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
    WHERE NOT (()-[:REFERS_TO]->(f))
    WITH p, f,
         toLower(coalesce(replace(f.figure_label,'.',''), '')) AS flbl,
         coalesce(f.page,-1) AS fpage
    WHERE flbl =~ '.*\\d+.*'   // nur wenn Ziffern drin sind
    WITH p, f, fpage,
         // extrahiere Nummer grob: entferne alles außer Ziffern und Leerzeichen
         replace(replace(replace(replace(flbl,'figure',''),'fig',''),'abb',''),'  ',' ') AS numish
    MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
    WHERE para.page IN [fpage-1, fpage, fpage+1]
      AND (
           toLower(para.text) CONTAINS ('figure ' + numish) OR
           toLower(para.text) CONTAINS ('fig ' + numish)    OR
           toLower(para.text) CONTAINS ('abb ' + numish)
      )
    MERGE (para)-[:REFERS_TO]->(f)
    """)

    # ---------- PASS 3: NEAR Fallback (gleiche Seite, längster Paragraph) ----------
    neo.run("""
    MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
    WHERE NOT (()-[:CAPTIONS|:REFERS_TO]->(f))
    MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
    WHERE para.page = coalesce(f.page,-999999)
    WITH f, para
    ORDER BY size(coalesce(para.text,'')) DESC
    WITH f, head(collect(para)) AS best
    MERGE (best)-[:NEAR]->(f)
    """)

    cap_after = cnt("CAPTIONS")
    ref_after = cnt("REFERS_TO")
    near_after = cnt("NEAR")

    # Beispiele für weiterhin unverbundene Figures
    samples = neo.run("""
    MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
    WHERE NOT (()-[:CAPTIONS|:REFERS_TO|:NEAR]->(f))
    RETURN p.title   AS paper,
           f.figure_id AS figure_id,
           f.page      AS page,
           f.figure_label AS figure_label,
           left(coalesce(f.caption,''), 100) AS caption
    LIMIT 10
    """)

    return {
        "captions_before": cap_before, "captions_after": cap_after, "captions_new": max(0, cap_after - cap_before),
        "refers_before": ref_before,   "refers_after": ref_after,   "refers_new":   max(0, ref_after - ref_before),
        "near_before": near_before,    "near_after": near_after,    "near_new":     max(0, near_after - near_before),
        "still_unlinked_examples": samples,
    }


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
# Add Synthesia tab (before Info)
tab_concepts, tab_ingest, tab_query, tab_did, tab_about = st.tabs([
    "🧩 Konzepte",
    "📥 Ingest",
    "❓ Fragen & Export",
    "🎬 PPTX → Synthesia",
    "ℹ️ Info",
])

# ---- Tab: PPTX -> Synthesia (upload / select PPTX then send to Synthesia)
with tab_did:
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

# ---- Tab: Konzepte (Topic + Seed-Liste, Pre-Ingest) ----
with tab_concepts:
    st.subheader("Topic & Konzepte festlegen (vor dem Ingest)")
    default_topic = st.session_state.get("concept_topic", "Künstliche Intelligenz")
    topic = st.text_input("Umbrella-Topic", value=default_topic)
    st.session_state["concept_topic"] = topic

    st.markdown("**Seed-Konzepte** (ein Konzept pro Zeile)")
    default_seed_text = st.session_state.get("concept_seed_text", "Green AI\nReinforcement Learning\nComputer Vision")
    seed_text = st.text_area("Seedliste", value=default_seed_text, height=140)
    st.session_state["concept_seed_text"] = seed_text
    seeds = parse_seed_input(seed_text)

    mode = st.radio(
        "Konzept-Strategie",
        ["Nur Seeds (keine neuen Konzepte)", "Seeds + LLM-Erweiterung", "Nur LLM"],
        index=1,
        help="Bestimmt, wie beim Ingest Konzepte erzeugt/verknüpft werden."
    )
    st.session_state["concept_mode"] = mode

    colC1, colC2, colC3 = st.columns(3)
    with colC1:
        if st.button("Seeds in DB anlegen/aktualisieren"):
            try:
                neo = get_neo()
                concepts = seed_names_to_concepts(seeds)
                if not concepts:
                    st.warning("Keine gültigen Seeds gefunden.")
                else:
                    neo.upsert_topic(topic)
                    neo.add_concepts(topic, concepts)
                    st.success(f"{len(concepts)} Seed-Konzepte unter Topic „{topic}“ angelegt/aktualisiert.")
            except Exception as e:
                st.error(f"Fehler beim Anlegen der Seeds: {e}")
    with colC2:
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
    with colC3:
        st.info("Seeds werden beim Ingest genutzt. Modus und Topic kannst du hier vorgeben.")


    st.markdown("---")
    st.subheader("🔁 Nachträgliche Verarbeitung")

    colR1, colR2 = st.columns(2)
    with colR1:
        if st.button("Konzepte aus bestehenden Papern extrahieren"):
            with st.spinner("Extrahiere Konzepte & verknüpfe Absätze …"):
                topic = st.session_state.get("concept_topic", "Künstliche Intelligenz")
                seeds = parse_seed_input(st.session_state.get("concept_seed_text", ""))
                strategy = st.session_state.get("concept_mode", "Seeds + LLM-Erweiterung")
                rep = rebuild_concepts_for_all(topic, strategy, seeds)
            st.success(f"Fertig: {rep['total_concepts']} Konzepte, {rep['total_links']} Links.")
            with st.expander("Details pro Paper"):
                st.json(rep["per_paper"])

    with colR2:
        sim = st.slider("Ähnlichkeits-Threshold (Cosine) für Umbrellas", 0.70, 0.99, 0.86, 0.01)
        minc = st.number_input("Min. Clustergröße", min_value=2, max_value=20, value=2, step=1)
        if st.button("Umbrella-Konzepte clustern"):
            with st.spinner("Clustere Concepts zu Umbrellas …"):
                topic = st.session_state.get("concept_topic", "Künstliche Intelligenz")
                rep = cluster_concepts_into_umbrellas(topic, sim_threshold=float(sim), min_cluster_size=int(minc))
            st.success(f"{rep['umbrellas_created']} Umbrellas erzeugt, {rep['assigned']} Konzepte zugeordnet.")
            st.json(rep)

# ---- Tab: Ingest ----
with tab_ingest:
    st.subheader("PDFs ingestieren")
    uploaded = st.file_uploader("PDF-Dateien auswählen", type=["pdf"], accept_multiple_files=True)

    # Kleines Status-Banner zu Konzept-Einstellungen
    st.markdown(f"**Aktuelles Topic:** `{st.session_state.get('concept_topic','—')}`  •  "
                f"**Seeds:** {len(parse_seed_input(st.session_state.get('concept_seed_text','')))}  •  "
                f"**Strategie:** `{st.session_state.get('concept_mode','Seeds + LLM-Erweiterung')}`")

    def ingest_one_pdf(path: Path) -> Dict[str, Any]:
        neo = get_neo()
        paper_meta, sections, paragraphs, figures = read_pdf_text_and_images(str(path))

        neo.upsert_paper(
            paper_meta["paper_id"],
            paper_meta.get("title"),
            {
                "source_path": paper_meta["source_path"],
                "doi": paper_meta.get("doi"),
                "url": paper_meta.get("url"),
                "file_sha256": paper_meta.get("file_sha256"),
                **paper_meta.get("meta", {}),
            },
        )
        if sections:
            neo.add_sections(paper_meta["paper_id"], sections)

        paragraphs_emb = embed_paragraphs(paragraphs)
        neo.add_paragraphs(paper_meta["paper_id"], paragraphs_emb)

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

        # ---- Konzepte je nach Strategie ----
        topic = st.session_state.get("concept_topic", "Künstliche Intelligenz")
        seed_names = parse_seed_input(st.session_state.get("concept_seed_text", ""))

        strategy = st.session_state.get("concept_mode", "Seeds + LLM-Erweiterung")
        seed_only   = (strategy == "Nur Seeds (keine neuen Konzepte)")
        llm_only    = (strategy == "Nur LLM")

        allow_new = not seed_only
        seeds_for_llm = [] if llm_only else seed_names

        # Konzepte extrahieren/zuordnen
        concepts, links = extract_and_embed_concepts(
            paper_title=paper_meta.get("title") or "",
            paragraphs=paragraphs_emb,
            topic_hint=topic,
            max_concepts=30,
            seed_names=seeds_for_llm,
            allow_new=allow_new
        )

        if concepts:
            neo.upsert_topic(topic)
            neo.add_concepts(topic, concepts)
        if links:
            neo.link_paragraphs_to_concepts(paper_meta["paper_id"], links)

        return {
            "paper_id": paper_meta["paper_id"],
            "title": paper_meta.get("title"),
            "doi": paper_meta.get("doi"),
            "url": paper_meta.get("url"),
            "file_sha256": paper_meta.get("file_sha256"),
            "n_sections": len(sections),
            "n_paragraphs": len(paragraphs_emb),
            "n_figures": len(figs_ready),
            "n_concepts_added": len(concepts),
            "n_links": len(links),
        }

    if uploaded:
        if st.button("Ingest starten"):
            reports = []
            errors = []
            with st.spinner("Ingest läuft …"):
                for f in uploaded:
                    try:
                        out_path = UPLOAD_DIR / f.name
                        out_path.write_bytes(f.read())
                        rep = ingest_one_pdf(out_path)
                        reports.append(rep)
                    except Exception as e:
                        errors.append({"file": f.name, "error": str(e), "trace": traceback.format_exc()})
            st.success(f"Fertig. {len(reports)} ok, {len(errors)} Fehler.")
            st.json({"ok": reports, "errors": errors})

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
    col1, col2, col3, col4 = st.columns([1,1,1,1])
    with col1:
        inline_figs = st.checkbox("Bilder inline einfügen", value=True)
    with col2:
        fallback_k = st.number_input("Fallback: Top-K Figuren (wenn keine Inline-Refs)", min_value=0, max_value=10, value=3, step=1)
    with col3:
        export_pdf = st.checkbox("Antwort als PDF exportieren", value=True)
    with col4:
        web_mode_ui = st.selectbox("Websuche", ["Auto", "Erzwingen", "Aus"], index=0)

    if st.button("Antwort abrufen"):
        if not q.strip():
            st.warning("Bitte eine Frage eingeben.")
        else:
            with st.spinner("Suche & Synthese …"):
                res = run_query(q, web_mode_ui=web_mode_ui)
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
        theme = custom_theme or "Oasis"
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

    # --- Cypher ausführen & visualisieren ---
    st.markdown("---")
    st.subheader("🔎 Cypher ausführen & visualisieren")

    # Topics aus DB (Dropdown)
    topics = list_topics()
    colTop, colPreset = st.columns([1,1])
    with colTop:
        selected_topic = st.selectbox("Topic aus der DB", topics or ["(keins gefunden)"])
        # JSON-Param für topic setzen
        st.session_state["cypher_params_box"] = json.dumps({"topic": selected_topic}, ensure_ascii=False, indent=2)
        st.session_state["cypher_ui_params"] = {"topic": selected_topic}

    # Presets
    presets = {
        "Umbrella → Concept → Paragraph → Paper → (Section/Figure)":
        """\
    :param topic => "Künstliche Intelligenz";
    MATCH p1 = (t:Topic {name:$topic})-[:HAS_UMBRELLA]->(u:Umbrella)-[:NARROWER]->(c:Concept)
    OPTIONAL MATCH p2 = (c)<-[:MENTIONS]-(para:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
    OPTIONAL MATCH p3 = (paper)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(para)
    OPTIONAL MATCH p4 = (paper)-[:HAS_FIGURE]->(figP:Figure)
    OPTIONAL MATCH p5 = (sec)-[:HAS_FIGURE]->(figS:Figure)
    RETURN p1, p2, p3, p4, p5
    LIMIT 500
    """,
        "Topic → Concept → Paragraph → Paper → (Section/Figure)":
        """\
    :param topic => "Künstliche Intelligenz";
    MATCH p1 = (t:Topic {name:$topic})-[:HAS_CONCEPT]->(c:Concept)
    OPTIONAL MATCH p2 = (c)<-[:MENTIONS]-(para:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
    OPTIONAL MATCH p3 = (paper)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(para)
    OPTIONAL MATCH p4 = (paper)-[:HAS_FIGURE]->(figP:Figure)
    OPTIONAL MATCH p5 = (sec)-[:HAS_FIGURE]->(figS:Figure)
    RETURN p1, p2, p3, p4, p5
    LIMIT 500
    """,
        "Quick-Umbrella erzeugen (Default)":
        """\
    :param topic => "Künstliche Intelligenz";
    MERGE (t:Topic {name:$topic})
    MERGE (u:Umbrella {umbrella_id: $topic + ":default"})
    SET u.name = $topic + " – Umbrella (default)"
    MERGE (t)-[:HAS_UMBRELLA]->(u)
    WITH t, u
    MATCH (t)-[:HAS_CONCEPT]->(c:Concept)
    MERGE (u)-[:NARROWER]->(c)
    RETURN t,u
    """,
        "Alle Paper → Paragraphen (ohne Topic/Concept)":
        """\
    MATCH p = (paper:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
    RETURN p
    LIMIT 400
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

    colQ1, colQ2 = st.columns([1,1])
    with colQ1:
        if st.button("Query ausführen"):
            try:
                recs = run_cypher_with_params(cypher_in)
                st.success(f"{len(recs)} Record(s) erhalten.")
                with st.expander("Rohdaten anzeigen"):
                    st.write(recs)
                visualize_records_as_graph(recs, height=650)
            except Exception as e:
                st.error(f"Cypher-Fehler: {e}")
                st.code(cypher_in, language="cypher")

    with colQ2:
        # garantiert funktionierende Test-Query (unabhängig vom Concept/Umbrella-Status)
        if st.button("Test: Paper→Paragraph visualisieren"):
            try:
                test_q = "MATCH p = (paper:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph) RETURN p LIMIT 150"
                recs = run_cypher_with_params(test_q)
                st.success(f"{len(recs)} Record(s) erhalten.")
                with st.expander("Rohdaten anzeigen (Test)"):
                    st.write(recs)
                visualize_records_as_graph(recs, height=650)
            except Exception as e:
                st.error(f"Fehler beim Test: {e}")


# ---- Tab: Info ----
with tab_about:
    st.markdown("""
**Workflow**  
1) *Schema anlegen* → 2) **Konzepte/Topic festlegen** → 3) *PDFs ingestieren* → 4) *Graph stitchen* → 5) *Frage stellen* → 6) *(optional) PDF exportieren*.

**Hinweise**
- Topic + Seed-Konzepte werden beim Ingest genutzt. Strategie:
  - *Nur Seeds* → ausschließlich auf Seeds mappen (keine neuen Konzepte).
  - *Seeds + LLM-Erweiterung* → Seeds bevorzugt, neue Konzepte erlaubt.
  - *Nur LLM* → Seeds ignorieren, LLM bestimmt Konzepte.
- „Graph leeren“ entfernt alle Daten, **nicht** die Indizes/Constraints.
""")
