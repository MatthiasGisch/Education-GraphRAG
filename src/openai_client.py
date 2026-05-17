from __future__ import annotations
import base64, json, re, time
from typing import List, Dict, Any
from openai import OpenAI, RateLimitError
from . import config as cfg


# ---------------------------------------------------------------------------
# Interne Client-Factories (lesen cfg zur Laufzeit → GUI-Änderungen wirken)
# ---------------------------------------------------------------------------

def _make_openai_client() -> OpenAI:
    if not cfg.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY fehlt. Bitte in der Sidebar oder .env eintragen.")
    return OpenAI(api_key=cfg.OPENAI_API_KEY, max_retries=6)


def _make_lmstudio_client() -> OpenAI:
    return OpenAI(base_url=cfg.LMSTUDIO_BASE_URL, api_key="lm-studio")


def _chat_client() -> OpenAI:
    return _make_lmstudio_client() if cfg.LLM_MODE == "local" else _make_openai_client()


def _embed_client() -> OpenAI:
    return _make_lmstudio_client() if cfg.LLM_MODE == "local" else _make_openai_client()


# Modell-Auswahl zur Laufzeit
def _chat_model() -> str:
    return cfg.LMSTUDIO_CHAT_MODEL if cfg.LLM_MODE == "local" else "gpt-4o-mini"


def _vision_model() -> str:
    return cfg.LMSTUDIO_VISION_MODEL if cfg.LLM_MODE == "local" else "gpt-4o-mini"


def _embed_model() -> str:
    return cfg.LMSTUDIO_EMBED_MODEL if cfg.LLM_MODE == "local" else "text-embedding-3-large"


# Rückwärtskompatibilität: client() wird von altem Code noch erwartet
def client() -> OpenAI:
    return _chat_client()


# ---- Embeddings ----
def embed_text(text: str, model: str | None = None) -> List[float]:
    m = model or _embed_model()
    for attempt in range(6):
        try:
            resp = _embed_client().embeddings.create(model=m, input=text)
            return resp.data[0].embedding  # type: ignore
        except RateLimitError:
            if attempt == 5:
                raise
            time.sleep(min(0.5 * 2 ** attempt, 16))
    raise RuntimeError("embed_text: unreachable")


def embed_texts_batch(texts: List[str], model: str | None = None, batch_size: int = 50) -> List[List[float]]:
    """Sendet Embeddings in Batches und wiederholt bei Rate-Limit-Fehlern."""
    m = model or _embed_model()
    results: List[List[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        for attempt in range(6):
            try:
                resp = _embed_client().embeddings.create(model=m, input=batch)
                ordered = sorted(resp.data, key=lambda d: d.index)
                results.extend(d.embedding for d in ordered)
                break
            except RateLimitError:
                if attempt == 5:
                    raise
                time.sleep(min(0.5 * 2 ** attempt, 16))
    return results


# ---- Vision: Bild beschreiben ----
def _image_to_data_url(path: str) -> str:
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    return f"data:{mime};base64,{b64}"


def describe_image(path: str) -> Dict[str, Any]:
    data_url = _image_to_data_url(path)
    system = (
        "Du bist ein wissenschaftlicher Bild-Analyst für akademische Publikationen. "
        "Antworte strikt als kompaktes JSON. "
        "WICHTIG: Verwende KEINE Emojis, keine dekorativen Zeichen, nur sachliche Beschreibungen."
    )
    user_parts = [
        {"type": "text", "text": (
            "Beschreibe das Bild präzise und wissenschaftlich. "
            "Ermittle figure_type (chart|diagram|photo|table-scan|other). "
            "Gib entities (Schlüsselbegriffe) als Liste an. "
            "Wenn erkennbar, extrahiere kurze ocr_hints (max 5). "
            "KEINE Emojis verwenden! Nur sachliche, wissenschaftliche Sprache. "
            "Antworte als JSON mit Schlüsseln: caption, figure_type, entities, ocr_hints."
        )},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    for attempt in range(6):
        try:
            resp = _chat_client().chat.completions.create(
                model=_vision_model(),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_parts},
                ],
                temperature=0.2,
            )
            break
        except RateLimitError:
            if attempt == 5:
                raise
            time.sleep(min(2 * 2 ** attempt, 60))
    text = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(text)
    except Exception:
        data = {"caption": text, "figure_type": "other", "entities": [], "ocr_hints": []}

    # Post-processing: Emojis entfernen
    caption = data.get("caption", "")
    if caption:
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251"
            "]+", flags=re.UNICODE
        )
        data["caption"] = emoji_pattern.sub("", caption).strip()

    return data


def grounded_answer(query: str, supports: List[Dict[str, Any]], max_supports: int = 20) -> str:
    # Lokales Modell: Kontextfenster schonen (typisch 4096 Tokens)
    _local = cfg.LLM_MODE == "local"
    if _local:
        max_supports = min(max_supports, 6)
    _snippet_chars = 400 if _local else 800

    sys = (
        "Du bist ein erfahrener Dozent und Lehrer, der Lernmaterial aus wissenschaftlichen Quellen erstellt. "
        "Erkläre die Inhalte didaktisch aufbereitet, strukturiert und verständlich für Studierende. "
        "Antworte NUR auf Basis der gelieferten Belege. Erfinde nichts. "
        "Nutze eine klare, lehrende Sprache mit Beispielen wo möglich. "
        "Strukturiere deine Antwort logisch (z.B. Definition → Erklärung → Anwendung → Zusammenfassung). "
        "WICHTIG: Wenn Abbildungen ([F...]) verfügbar sind, verweise DIREKT im Fließtext darauf, "
        "z.B. 'Wie Abbildung [F12345] zeigt...' oder 'In [F67890] ist dargestellt...'. "
        "Die Bilder werden dann automatisch an dieser Stelle im PDF eingefügt."
    )
    bib = {}
    ctx_lines = []
    for s in supports:
        if s.get("type") == "paragraph":
            ref_id = f"P{s['paragraph_id']}"
            bib[ref_id] = {
                "paper": s.get("paper_title"),
                "doi": s.get("doi"),
                "url": s.get("url"),
                "page": s.get("page"),
                "section": s.get("section_title"),
            }
            ctx_lines.append(f"[{ref_id}] {s.get('paper_title', '?')} • S.{s.get('page')} • {s.get('section_title') or '—'} :: {s['text'][:_snippet_chars]}")
        else:
            ref_id = f"F{s['figure_id']}"
            bib[ref_id] = {
                "paper": s.get("paper_title"),
                "doi": s.get("doi"),
                "url": s.get("url"),
                "page": s.get("page"),
                "section": s.get("section_title"),
            }
            figure_type = s.get("figure_type", "")
            entities    = s.get("entities") or []
            ocr_hints   = s.get("ocr_hints") or []
            meta_parts  = []
            if figure_type and figure_type != "other":
                meta_parts.append(f"Typ: {figure_type}")
            if entities:
                meta_parts.append(f"Schlüsselbegriffe: {', '.join(entities[:6])}")
            if ocr_hints:
                meta_parts.append(f"Erkennbarer Text: {', '.join(ocr_hints[:3])}")
            meta_str = (" [" + " | ".join(meta_parts) + "]") if meta_parts else ""
            ctx_lines.append(
                f"[{ref_id}] {s.get('paper_title', '?')} • Abb. • S.{s.get('page')} :: "
                f"{s.get('caption','')[:min(300, _snippet_chars)]}{meta_str}"
            )

    bib_json = json.dumps(bib, ensure_ascii=False)

    user = f"""
Frage: {query}

Belege (nicht erfinden, nur daraus arbeiten):
{chr(10).join(ctx_lines[:max_supports])}

Anweisung als Dozent:
- Erkläre das Thema didaktisch verständlich auf Deutsch, wie in einer Vorlesung oder einem Lehrbuch.
- Strukturiere die Antwort logisch (z.B. Definition → Erklärung → Beispiele → Zusammenhänge).
- Nutze eine klare, lehrende Sprache: Führe Studierende schrittweise durch das Thema.
- Jede Kernaussage mit [Pxxx] / [Fxxx] belegen (Quellenangabe in eckigen Klammern).
  WICHTIG: Zitationen enthalten NUR die ID in Klammern, KEINE Seitenzahlen oder weitere Metadaten.
  Beispiele: [P12345], [F67890], nicht [P12345 S.5] oder [F67890 (Abb. 3)]
- **WICHTIG für Abbildungen [F...]:** Wenn eine Abbildung relevant ist, verweise DIREKT im Fließtext darauf!
  Beispiel: "Abbildung [F12345] zeigt den Aufbau..." oder "Wie in [F67890] dargestellt..."
  Die Abbildungen werden dann automatisch unter dem Text eingefügt.
- Wenn möglich, verdeutliche Zusammenhänge und Anwendungsbereiche.
- Wenn Belege widersprüchlich oder zu dünn sind, sage das klar und erkläre was fehlt.
- FÜGE KEINEN separaten "Quellen"-Block am Ende hinzu - Quellenangaben werden automatisch erstellt.

Zitierformat:
- Verwende die ID-Kürzel [Pxxx] oder [Fxxx] direkt im Text um auf Belege zu verweisen.
- KEINE Seitenzahlen, Jahreszahlen oder andere Metadaten in den Klammern.

Bibliographie-Map (nur zur Information, nicht im Text verwenden):
{bib_json}
"""
    resp = _chat_client().chat.completions.create(
        model=_chat_model(),
        messages=[
            {"role": "system", "content": sys},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
    )
    raw = resp.choices[0].message.content or ""

    try:
        lines = raw.split("\n")
        fixed_lines = []
        for ln in lines:
            while True:
                m = re.match(r"^\s*(?P<enum>\d+(?:[\.)\:]?\s*))\[(?P<cid>[PF][^\]]+)\]", ln)
                if not m:
                    break
                enum = m.group("enum")
                cid = m.group("cid")
                ln = enum + ln[m.end():]
                end_punct = re.match(r"^(?P<body>.*?)(?P<punct>[\.!?])\s*$", ln)
                if end_punct:
                    ln = end_punct.group("body") + f" [{cid}]" + end_punct.group("punct")
                else:
                    ln = ln.rstrip() + f" [{cid}]"
            fixed_lines.append(ln)
        raw = "\n".join(fixed_lines)
    except Exception:
        pass

    return raw
