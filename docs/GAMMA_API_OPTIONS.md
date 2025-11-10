# Gamma API Optionen & Anpassungsmöglichkeiten

## Übersicht

Die Gamma API bietet verschiedene Anpassungsmöglichkeiten für die Präsentationserstellung. **Wichtig:** Schriftgrößen und Schriftarten werden NICHT direkt von der API unterstützt - diese werden vom gewählten Theme gesteuert.

## Verfügbare API-Parameter

### 1. Basis-Parameter

```python
{
    "inputText": str,           # Markdown-Text mit --- als Folien-Trenner
    "textMode": str,            # "preserve" oder "optimize"
    "format": str,              # "presentation", "document", "webpage"
    "exportAs": str,            # "pptx", "pdf", oder None
}
```

### 2. Theme & Styling

```python
{
    "themeName": str,           # Name des Gamma-Themes (z.B. "Oasis", "Minimal")
                               # ⚠️ Themes sind account-spezifisch!
}
```

**Wichtig:** 
- Themes müssen in deinem Gamma-Account existieren
- Schriftgrößen werden vom Theme gesteuert
- Nutze "Refresh themes from Gamma" Button in der GUI

### 3. Text-Optionen

```python
{
    "textOptions": {
        "language": str,        # "de", "en", "fr", "es", "it", etc.
        "amount": str          # "brief", "medium", "detailed", "extensive"
                               # ⚠️ Nicht "small"! (wird von API abgelehnt)
    }
}
```

### 4. Bild-Optionen

```python
{
    "imageOptions": {
        "source": str          # "noImages", "aiGenerated", "unsplash", 
                              # "webFreeToUse", "placeholder"
    }
}
```

### 5. Folien-Optionen

```python
{
    "cardSplit": str,          # "inputTextBreaks" (nutze ---) oder "auto"
    "numCards": int,           # Ziel-Anzahl Folien (bei "auto")
    "cardOptions": {
        "dimensions": str      # "16x9" oder "4x3"
    }
}
```

### 6. Zugriffs-Optionen

```python
{
    "sharingOptions": {
        "externalAccess": str, # "noAccess", "view", "comment", "edit"
        "workspaceAccess": str # "noAccess", "view", "comment", "edit"
    }
}
```

## Vollständiges Beispiel

```python
from src.gamma import GammaClient

gamma = GammaClient()

body = {
    "inputText": """# Titel
* Bulletpoint 1
* Bulletpoint 2

---

# Folie 2
* Mehr Inhalt
""",
    "textMode": "preserve",
    "format": "presentation",
    "themeName": "Oasis",
    "cardSplit": "inputTextBreaks",
    "numCards": 5,
    "exportAs": "pptx",
    "textOptions": {
        "language": "de",
        "amount": "medium"
    },
    "imageOptions": {
        "source": "noImages"
    },
    "cardOptions": {
        "dimensions": "16x9"
    },
    "sharingOptions": {
        "externalAccess": "noAccess",
        "workspaceAccess": "view"
    }
}

generation_id = gamma.generate(body)
result = gamma.poll(generation_id)
```

## Lokales PPTX-Fallback (python-pptx)

Wenn die Gamma API nicht verfügbar ist oder fehlschlägt, nutzt das System ein lokales python-pptx Fallback mit **erweiterten Anpassungsmöglichkeiten**:

### Schriftgrößen anpassen

```python
from src.gamma_client import generate_presentation

result = generate_presentation(
    title="Meine Präsentation",
    answer_text="Haupttext...",
    supports=[...],
    use_gamma=False,
    template_path="path/to/template.pptx",  # Optional: PPTX-Template
    title_font_size=32,      # Folientitel (Standard: 32pt)
    body_font_size=18,       # Erste Zeile Body (Standard: 18pt)
    bullet_font_size=14      # Bulletpoints (Standard: 14pt)
)
```

### GUI-Integration

In der Streamlit-GUI findest du unter **"📝 Schriftgrößen (nur lokales PPTX)"** drei Regler:

1. **Folientitel (pt)**: 16-72pt, Standard: 32pt
2. **Body-Text (pt)**: 12-48pt, Standard: 18pt
3. **Bulletpoints (pt)**: 10-36pt, Standard: 14pt

Diese Einstellungen werden in `st.session_state` gespeichert und bei jeder lokalen PPTX-Erstellung verwendet.

## Unterschiede: Gamma API vs. Lokales PPTX

| Feature | Gamma API | Lokales PPTX |
|---------|-----------|--------------|
| Schriftgröße | ❌ Theme-gesteuert | ✅ Anpassbar |
| Schriftart | ❌ Theme-gesteuert | ✅ Template-basiert |
| AI-generierte Bilder | ✅ Ja | ❌ Nein |
| Web-Bilder (Unsplash) | ✅ Ja | ❌ Nein |
| Lokale Bilder | ✅ Via URL | ✅ Via file:// oder Pfad |
| Templates | ❌ Nur Gamma-Themes | ✅ Eigene PPTX-Templates |
| Offline-Nutzung | ❌ Nein | ✅ Ja |

## Best Practices

### 1. Theme-Auswahl

```python
# Verfügbare Themes abrufen
themes = gamma.list_themes()
print(themes)

# Theme testen vor Verwendung
test_body = {
    "inputText": "# Test\n* Validation",
    "themeName": "MeinTheme",
    # ... weitere Optionen
}
try:
    gen_id = gamma.generate(test_body)
    result = gamma.poll(gen_id)
    print("Theme funktioniert!")
except Exception as e:
    print(f"Theme nicht verfügbar: {e}")
```

### 2. Text-Menge optimieren

- `"brief"`: Sehr kurz, ~2-3 Sätze pro Folie
- `"medium"`: Ausgewogen, ~4-6 Sätze pro Folie
- `"detailed"`: Ausführlich, ~8-10 Sätze pro Folie
- `"extensive"`: Sehr detailliert, voller Text

### 3. Folien-Aufteilung

**inputTextBreaks**: Nutze `---` als explizite Folien-Trenner:
```markdown
# Folie 1
Inhalt

---

# Folie 2
Mehr Inhalt
```

**auto**: Lasse Gamma automatisch aufteilen, steuere mit `numCards`:
```python
{
    "cardSplit": "auto",
    "numCards": 10  # Ziel: 10 Folien
}
```

### 4. Fehlerbehandlung

```python
try:
    gen_id = gamma.generate(body)
    result = gamma.poll(gen_id, timeout_sec=300)
except Exception as e:
    # Fallback zu lokalem PPTX
    result = generate_presentation(
        title="Fallback",
        answer_text=text,
        supports=[],
        use_gamma=False
    )
```

## Häufige Fehler

### 1. Theme nicht gefunden (400)
```json
{"error": "Theme with name 'MeinTheme' not found"}
```
**Lösung**: Theme existiert nicht in deinem Account. Nutze `list_themes()` oder wähle "Oasis".

### 2. Ungültiger amount-Wert
```json
{"error": "textOptions.amount must be one of: brief, medium, detailed, extensive"}
```
**Lösung**: Ändere `"amount": "small"` zu `"amount": "brief"`.

### 3. API Key ungültig
```python
GammaError: POST .../generations failed [401]
```
**Lösung**: Prüfe `GAMMA_API_KEY` in .env Datei.

## Weitere Informationen

- Gamma API Dokumentation: https://gamma.app/docs/api
- GUI-Bereich: "🎨 Präsentation erstellen (Gamma)"
- Export-Verzeichnis: `exports/`
- Error-Logs: `exports/gamma_last_error.json`
