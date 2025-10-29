# src/agent.py
from __future__ import annotations
from typing import List, Dict, Any
import os, re, json

from .neo import Neo4jClient
from .retriever import hybrid_retrieve
from .openai_client import grounded_answer

# ---------------- Config / Defaults ----------------
USE_SERP = bool(os.getenv("SERPAPI_API_KEY", ""))
WEB_SEARCH_PROVIDER = os.getenv("WEB_SEARCH_PROVIDER", "openai").lower()
OPENAI_WEB_MODEL   = os.getenv("OPENAI_WEB_MODEL", "gpt-4o-mini")

MIN_SUPPORTS_COUNT_DEFAULT = int(os.getenv("MIN_SUPPORTS_COUNT", "3"))
MIN_SUPPORTS_SCORE_DEFAULT = float(os.getenv("MIN_SUPPORTS_SCORE", "0.0"))  # 0.0 = keine Score-Hürde

# ---------------- OpenAI Web Search ----------------
try:
    from openai import OpenAI
    _oai_client = OpenAI()
except Exception:
    _oai_client = None

_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

def _extract_sources_json(answer_text: str) -> tuple[list[dict], str]:
    m = _JSON_BLOCK_RE.search(answer_text or "")
    if not m:
        return [], (answer_text or "")
    try:
        data = json.loads(m.group(1))
        sources = data.get("sources", []) if isinstance(data, dict) else []
    except Exception:
        sources = []
    cleaned = (answer_text[:m.start()] + answer_text[m.end():]).strip()
    return sources, cleaned

def answer_via_openai_web(query: str, *, lang: str = "de", force_tool: bool = False) -> Dict[str, Any]:
    if _oai_client is None:
        return {"mode": "web_error", "answer": "OpenAI Client nicht initialisierbar.", "supports": [], "debug": {"web_call": "client_none"}}

    sys_prompt = (
        "Du bist ein gewissenhafter Research-Assistent. Wenn du das Web nutzt, "
        "antworte präzise, nenne Belege im Fließtext (mit URL) und füge am Ende "
        "EXAKT einen JSON-Block an:\n"
        "```json {\"sources\":[{\"url\":\"...\",\"title\":\"...\"}]}```\n"
        "Nimm nur Quellen in den JSON-Block auf, die du auch genutzt/zitiert hast."
    )

    tool_choice = {"type": "web_search"} if force_tool else "auto"
    debug = {"web_model": OPENAI_WEB_MODEL, "tool_choice": ("force" if force_tool else "auto")}

    def _call(model: str):
        return _oai_client.responses.create(
            model=model,
            tools=[{"type": "web_search"}],
            tool_choice=tool_choice,
            input=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": f"Sprache: {lang}\nFrage: {query}"},
            ],
        )

    try:
        resp = _call(OPENAI_WEB_MODEL)
    except Exception as e1:
        # Fallback auf gpt-4o (falls search-preview nicht freigeschaltet)
        debug["retry_on_gpt4o"] = str(e1)
        try:
            resp = _call("gpt-4o")
            debug["web_model"] = "gpt-4o"
        except Exception as e2:
            return {
                "mode": "web_error",
                "answer": f"Websuche fehlgeschlagen: {e2}",
                "supports": [],
                "debug": {**debug, "exception": str(e2)},
            }

    answer_text = getattr(resp, "output_text", "") or ""
    sources, cleaned_text = _extract_sources_json(answer_text)
    supports: List[Dict[str, Any]] = [
        {"type": "web", "paper_title": s.get("title"), "url": s.get("url"),
         "doi": None, "page": None, "section_title": None, "score": None}
        for s in sources
    ]
    return {"mode": "web", "answer": cleaned_text, "supports": supports, "debug": debug}

# ---------------- (Optional) Serp-Stub ----------------
def answer_via_serp(query: str) -> Dict[str, Any]:
    if not USE_SERP:
        return {
            "mode": "web_fallback_disabled",
            "answer": "SERPAPI_API_KEY nicht gesetzt. Nutze den OpenAI-Web-Modus.",
            "supports": [],
            "debug": {"provider": "serp", "reason": "no_key"}
        }
    return {"mode": "web_fallback", "answer": "Websuche via Serp ist nicht implementiert.", "supports": [], "debug": {"provider": "serp"}}

# ---------------- Helpers ----------------
def _effective_supports(supports: List[Dict[str, Any]], min_score: float) -> int:
    """Zählt nur echte Graph-Belege: Paragraph/Figure mit score >= min_score."""
    n = 0
    for s in supports or []:
        if s.get("type") in {"paragraph", "figure"}:
            sc = s.get("score", 0.0) or 0.0
            if sc >= min_score:
                n += 1
    return n

# ---------------- Orchestrierung ----------------
def answer_query(
    query: str,
    neo: Neo4jClient,
    min_supports: int = MIN_SUPPORTS_COUNT_DEFAULT,
    min_supports_score: float = MIN_SUPPORTS_SCORE_DEFAULT,
    web_mode: str | None = None,   # "auto" | "force" | "off"
) -> Dict[str, Any]:
    """
    web_mode:
      - "force": immer Websuche (Graph wird übersprungen)
      - "off"  : niemals Websuche (nur Graph)
      - "auto" : erst Graph; wenn zu wenig valide Belege -> Web
      - None   : wie "auto" (Provider aus .env bestimmt openai/serp)
    """
    debug: Dict[str, Any] = {
        "web_mode": (web_mode or "auto"),
        "provider": WEB_SEARCH_PROVIDER,
        "min_supports": min_supports,
        "min_supports_score": min_supports_score,
    }

    # 1) FORCE → sofort Web
    if (web_mode or "").lower() == "force":
        debug["decision"] = "force_web"
        if WEB_SEARCH_PROVIDER == "serp":
            out = answer_via_serp(query)
        else:
            out = answer_via_openai_web(query, lang="de", force_tool=True)
        out.setdefault("debug", {}).update(debug)
        return out

    # 2) OFF → nur Graph
    if (web_mode or "").lower() in {"off", "none", "disabled"}:
        ret = hybrid_retrieve(neo, query)
        supports = (ret.get("supports") or [])[:12]
        eff = _effective_supports(supports, min_supports_score)
        debug.update({"graph_supports_total": len(supports), "graph_supports_effective": eff, "decision": "graph_only"})
        answer = grounded_answer(query, supports)
        mode = "graph" if eff >= min_supports else "graph_low_coverage"
        return {"mode": mode, "answer": answer, "supports": supports, "debug": debug}

    # 3) AUTO → erst Graph, dann ggf. Web
    ret = hybrid_retrieve(neo, query)
    supports = (ret.get("supports") or [])[:12]
    eff = _effective_supports(supports, min_supports_score)
    debug.update({"graph_supports_total": len(supports), "graph_supports_effective": eff})

    if eff >= min_supports:
        debug["decision"] = "graph_ok"
        answer = grounded_answer(query, supports)
        return {"mode": "graph", "answer": answer, "supports": supports, "debug": debug}

    # zu wenig valide Belege → Web-Fallback
    # Wenn GAR KEINE Belege: Tool *erzwingen*, sonst (bei wenigen) "auto"
    force_tool = (eff == 0)
    debug["decision"] = f"web_fallback({'force' if force_tool else 'auto'})"

    if WEB_SEARCH_PROVIDER == "serp":
        out = answer_via_serp(query)
    else:
        out = answer_via_openai_web(query, lang="de", force_tool=force_tool)

    out.setdefault("debug", {}).update(debug)
    return out
