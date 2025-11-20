from __future__ import annotations
import base64, json
from typing import List, Dict, Any
from openai import OpenAI
from .config import OPENAI_API_KEY

_client: OpenAI | None = None

def client() -> OpenAI:
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY missing. Check .env")
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client

# ---- Embeddings (text-embedding-3-large -> 3072-D) ----
def embed_text(text: str, model: str = "text-embedding-3-large") -> List[float]:
    resp = client().embeddings.create(model=model, input=text)
    return resp.data[0].embedding  # type: ignore

# ---- Vision: describe image via GPT-4o ----
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
    resp = client().chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_parts},
        ],
        temperature=0.2,
    )
    text = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(text)
    except Exception:
        data = {"caption": text, "figure_type": "other", "entities": [], "ocr_hints": []}
    
    # Post-processing: Entferne Emojis falls doch welche durchgekommen sind
    import re
    caption = data.get("caption", "")
    if caption:
        # Emoji-Pattern: alle Unicode-Emojis entfernen
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # Emoticons
            "\U0001F300-\U0001F5FF"  # Symbole & Piktogramme
            "\U0001F680-\U0001F6FF"  # Transport & Karten
            "\U0001F1E0-\U0001F1FF"  # Flaggen
            "\U00002702-\U000027B0"  # Dingbats
            "\U000024C2-\U0001F251"  # Eingeschlossene Zeichen
            "]+", flags=re.UNICODE
        )
        data["caption"] = emoji_pattern.sub("", caption).strip()
    
    return data

def grounded_answer(query: str, supports: List[Dict[str, Any]]) -> str:
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
    # Kontext mit strukturierter Provenance
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
            ctx_lines.append(f"[{ref_id}] {s['paper_title']} • S.{s.get('page')} • {s.get('section_title') or '—'} :: {s['text'][:800]}")
        else:
            ref_id = f"F{s['figure_id']}"
            bib[ref_id] = {
                "paper": s.get("paper_title"),
                "doi": s.get("doi"),
                "url": s.get("url"),
                "page": s.get("page"),
                "section": s.get("section_title"),
            }
            ctx_lines.append(f"[{ref_id}] {s['paper_title']} • Abb. • S.{s.get('page')} :: {s.get('caption','')[:300]}")

    # BIB JSON als String (vom Modell nicht verändern)
    bib_json = json.dumps(bib, ensure_ascii=False)

    user = f"""
Frage: {query}

Belege (nicht erfinden, nur daraus arbeiten):
{chr(10).join(ctx_lines[:20])}

Anweisung als Dozent:
- Erkläre das Thema didaktisch verständlich auf Deutsch, wie in einer Vorlesung oder einem Lehrbuch.
- Strukturiere die Antwort logisch (z.B. Definition → Erklärung → Beispiele → Zusammenhänge).
- Nutze eine klare, lehrende Sprache: Führe Studierende schrittweise durch das Thema.
- Jede Kernaussage mit [Pxxx] / [Fxxx] belegen (Quellenangabe in eckigen Klammern).
- **WICHTIG für Abbildungen [F...]:** Wenn eine Abbildung relevant ist, verweise DIREKT im Fließtext darauf! 
  Beispiel: "Abbildung [F12345] zeigt den Aufbau..." oder "Wie in [F67890] dargestellt..."
  Die Abbildungen werden dann automatisch unter dem Text eingefügt.
- Wenn möglich, verdeutliche Zusammenhänge und Anwendungsbereiche.
- Wenn Belege widersprüchlich oder zu dünn sind, sage das klar und erkläre was fehlt.
- FÜGE KEINEN separaten "Quellen"-Block am Ende hinzu - Quellenangaben werden automatisch erstellt.

Zitierformat:
- Verwende die ID-Kürzel [Pxxx] oder [Fxxx] direkt im Text um auf Belege zu verweisen.

Bibliographie-Map (nur zur Information, nicht im Text verwenden):
{bib_json}
"""
    resp = client().chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role":"system", "content": sys},
            {"role":"user", "content": user},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""
