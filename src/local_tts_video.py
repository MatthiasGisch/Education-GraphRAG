"""
Lokale Video-Generierung aus PPTX.

Pipeline:
  PPTX → Folienbilder (PIL) + Narrationstexte (optional LLM) → TTS-Audio → MP4 (MoviePy)

Benötigte Pakete (einmalig installieren):
  pip install moviepy edge-tts pyttsx3

TTS-Backends (Priorität):
  1. edge-tts   – Microsoft Edge TTS, kostenlos, sehr gute Qualität, pip install edge-tts
  2. pyttsx3    – vollständig offline, Windows SAPI / Linux eSpeak, pip install pyttsx3
"""
from __future__ import annotations

import asyncio
import logging
import textwrap
import uuid
from pathlib import Path
from typing import List, Optional

from pptx import Presentation
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

DEFAULT_W = 1280
DEFAULT_H = 720

# Deutsche Stimme für edge-tts (sehr gute Qualität)
DEFAULT_EDGE_VOICE_DE = "de-DE-KatjaNeural"
DEFAULT_EDGE_VOICE_EN = "en-US-AriaNeural"


# ---------------------------------------------------------------------------
# Folientexte extrahieren
# ---------------------------------------------------------------------------

def extract_slide_texts(pptx_path: str) -> List[str]:
    prs = Presentation(pptx_path)
    result = []
    for slide in prs.slides:
        parts = []
        for shape in slide.shapes:
            try:
                if hasattr(shape, "text"):
                    t = shape.text.strip()
                    if t:
                        parts.append(t)
            except Exception:
                continue
        result.append("\n\n".join(parts)[:5000])
    return result


# ---------------------------------------------------------------------------
# Folienbilder rendern
# ---------------------------------------------------------------------------

def _render_slide_image(text: str, out_path: Path, width: int, height: int) -> str:
    img = Image.new("RGB", (width, height), color="#1e1e2e")
    draw = ImageDraw.Draw(img)

    try:
        title_font = ImageFont.truetype("arial.ttf", 40)
        body_font  = ImageFont.truetype("arial.ttf", 26)
    except Exception:
        title_font = ImageFont.load_default()
        body_font  = ImageFont.load_default()

    lines = text.splitlines()
    title = lines[0][:120] if lines else ""
    body  = "\n".join(lines[1:]) if len(lines) > 1 else ""

    # Titel
    draw.text((60, 50), title, fill="#cdd6f4", font=title_font)
    # Trennlinie
    draw.line([(60, 110), (width - 60, 110)], fill="#6c7086", width=2)

    y = 140
    for line in textwrap.wrap(body, width=90):
        draw.text((60, y), line, fill="#bac2de", font=body_font)
        y += 34
        if y > height - 60:
            break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    return str(out_path)


def render_slide_images(pptx_path: str, out_dir: str,
                        width: int = DEFAULT_W, height: int = DEFAULT_H) -> List[str]:
    slides = extract_slide_texts(pptx_path)
    out = []
    for i, text in enumerate(slides, start=1):
        p = Path(out_dir) / f"slide_{i:03d}.png"
        _render_slide_image(text, p, width, height)
        out.append(str(p))
    return out


# ---------------------------------------------------------------------------
# LLM-Narration (optional, nutzt konfigurierten Chat-Client)
# ---------------------------------------------------------------------------

def generate_narration_with_llm(slide_texts: List[str], language: str = "de") -> List[str]:
    """
    Lässt das konfigurierte Sprachmodell (Cloud oder LM Studio) einen
    natürlichen Vortragstext pro Folie erzeugen.
    """
    from .openai_client import _chat_client, _chat_model

    lang_name = "Deutsch" if language == "de" else "Englisch"
    narrations: List[str] = []

    for text in slide_texts:
        if not text.strip():
            narrations.append("")
            continue
        prompt = (
            f"Du bist ein Dozent, der vor Studierenden spricht. "
            f"Schreibe einen natürlich klingenden Vortragstext für diese Folie. "
            f"Sprache: {lang_name}. Länge: 3–5 Sätze. "
            f"Schreibe NUR den gesprochenen Text, ohne Überschriften oder Metadaten.\n\n"
            f"Folieninhalt:\n{text}"
        )
        try:
            resp = _chat_client().chat.completions.create(
                model=_chat_model(),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            narrations.append(resp.choices[0].message.content.strip())
        except Exception as e:
            log.warning("LLM-Narration fehlgeschlagen für Folie, verwende Rohtext: %s", e)
            narrations.append(text)

    return narrations


# ---------------------------------------------------------------------------
# TTS-Backends
# ---------------------------------------------------------------------------

def _tts_edge(texts: List[str], out_dir: Path, voice: str) -> List[str]:
    """edge-tts: Microsoft Edge TTS (kostenlos, sehr gute Qualität)."""
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError(
            "edge-tts nicht installiert. Bitte ausführen:\n  pip install edge-tts"
        )

    async def _synthesize_all() -> List[str]:
        paths = []
        for i, text in enumerate(texts):
            out_p = out_dir / f"audio_{i:03d}.mp3"
            if text.strip():
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(str(out_p))
            else:
                # Stille für leere Folien: 1 Sekunde Stille erzeugen
                _write_silence_mp3(out_p, duration_sec=1)
            paths.append(str(out_p))
        return paths

    # asyncio.run() funktioniert auch in Streamlit (synchron aufgerufen)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Streamlit läuft in einem eigenen Loop → neuen Thread nutzen
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _synthesize_all())
                return future.result()
        else:
            return loop.run_until_complete(_synthesize_all())
    except RuntimeError:
        return asyncio.run(_synthesize_all())


def _tts_pyttsx3(texts: List[str], out_dir: Path, voice: str) -> List[str]:
    """pyttsx3: vollständig offline via Windows SAPI / eSpeak."""
    try:
        import pyttsx3
    except ImportError:
        raise RuntimeError(
            "pyttsx3 nicht installiert. Bitte ausführen:\n  pip install pyttsx3"
        )

    engine = pyttsx3.init()
    # Stimme wählen falls angegeben
    if voice:
        for v in engine.getProperty("voices"):
            if voice.lower() in v.name.lower() or voice.lower() in (v.id or "").lower():
                engine.setProperty("voice", v.id)
                break

    paths: List[str] = []
    for i, text in enumerate(texts):
        out_p = out_dir / f"audio_{i:03d}.wav"
        engine.save_to_file(text or ".", str(out_p))
        paths.append(str(out_p))
    engine.runAndWait()
    return paths


def _write_silence_mp3(out_path: Path, duration_sec: float = 1.0):
    """Minimale MP3-Stille (falls kein Text vorhanden)."""
    try:
        import struct, wave
        wav_path = out_path.with_suffix(".wav")
        sample_rate = 22050
        n_samples = int(sample_rate * duration_sec)
        with wave.open(str(wav_path), "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(struct.pack("<" + "h" * n_samples, *([0] * n_samples)))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        wav_path.rename(out_path.with_suffix(".wav"))
        # Benenne zu .mp3 um (moviepy akzeptiert auch .wav)
        out_path.with_suffix(".wav").rename(out_path)
    except Exception:
        out_path.touch()


def generate_audio(texts: List[str], out_dir: Path,
                   backend: str = "edge-tts",
                   voice: str = "") -> List[str]:
    """
    Erzeugt Audiodateien für alle Folien.

    backend: "edge-tts" | "pyttsx3"
    voice:   Stimmname, leer = Standardstimme
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    if backend == "edge-tts":
        effective_voice = voice or DEFAULT_EDGE_VOICE_DE
        return _tts_edge(texts, out_dir, effective_voice)
    elif backend == "pyttsx3":
        return _tts_pyttsx3(texts, out_dir, voice)
    else:
        raise ValueError(f"Unbekanntes TTS-Backend: {backend!r}. Verwende 'edge-tts' oder 'pyttsx3'.")


# ---------------------------------------------------------------------------
# Video zusammensetzen
# ---------------------------------------------------------------------------

def assemble_video(image_paths: List[str], audio_paths: List[str],
                   out_path: str, fallback_duration: float = 5.0,
                   fps: int = 24) -> str:
    """
    Kombiniert Folienbilder + Audiodateien zu einem MP4-Video.
    Jede Folie dauert so lange wie die zugehörige Audiodatei.
    """
    try:
        from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
    except ImportError:
        raise RuntimeError(
            "moviepy nicht installiert. Bitte ausführen:\n  pip install moviepy"
        )

    clips = []
    for img_p, aud_p in zip(image_paths, audio_paths):
        aud_path = Path(aud_p)
        if aud_path.exists() and aud_path.stat().st_size > 100:
            try:
                audio = AudioFileClip(str(aud_path))
                duration = audio.duration
            except Exception:
                audio = None
                duration = fallback_duration
        else:
            audio = None
            duration = fallback_duration

        clip = ImageClip(img_p).set_duration(duration)
        if audio:
            clip = clip.set_audio(audio)
        clips.append(clip)

    if not clips:
        raise RuntimeError("Keine Folien zum Kombinieren gefunden.")

    video = concatenate_videoclips(clips, method="compose")
    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    video.write_videofile(str(out_p), fps=fps, codec="libx264", audio_codec="aac",
                          logger=None)
    return str(out_p)


# ---------------------------------------------------------------------------
# Haupt-Einstiegspunkt
# ---------------------------------------------------------------------------

def generate_local_video(
    pptx_path: str,
    out_dir: str,
    tts_backend: str = "edge-tts",
    tts_voice: str = "",
    use_llm_narration: bool = False,
    narration_language: str = "de",
    fallback_duration: float = 5.0,
    width: int = DEFAULT_W,
    height: int = DEFAULT_H,
    progress_callback=None,
) -> str:
    """
    Vollständige lokale Video-Pipeline.

    Args:
        pptx_path:          Pfad zur PPTX-Datei
        out_dir:            Ausgabeverzeichnis
        tts_backend:        "edge-tts" (empfohlen) oder "pyttsx3" (offline)
        tts_voice:          Stimmname (leer = Standardstimme)
        use_llm_narration:  True → LLM generiert natürlichen Vortragstext
        narration_language: "de" oder "en"
        fallback_duration:  Foliendauer in Sekunden falls kein Audio
        width / height:     Videoauflösung

    Returns:
        Pfad zur fertigen MP4-Datei
    """
    out_dir_p = Path(out_dir)
    tmp_dir = out_dir_p / f"_tmp_{uuid.uuid4().hex[:8]}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    def _progress(msg: str):
        log.info(msg)
        if progress_callback:
            progress_callback(msg)

    _progress("Schritt 1/4: Folieninhalt extrahieren...")
    slide_texts = extract_slide_texts(pptx_path)
    if not slide_texts:
        raise RuntimeError("Keine Folien in der PPTX-Datei gefunden.")

    if use_llm_narration:
        _progress("Schritt 2/4: LLM generiert Vortragstexte...")
        narration_texts = generate_narration_with_llm(slide_texts, language=narration_language)
    else:
        _progress("Schritt 2/4: Verwende Folientexte direkt...")
        narration_texts = slide_texts

    _progress("Schritt 3/4: Folienbilder rendern...")
    image_paths = render_slide_images(pptx_path, str(tmp_dir / "images"), width, height)

    _progress(f"Schritt 4a/4: Audio erzeugen ({tts_backend})...")
    audio_paths = generate_audio(narration_texts, tmp_dir / "audio",
                                 backend=tts_backend, voice=tts_voice)

    _progress("Schritt 4b/4: Video zusammensetzen...")
    stem = Path(pptx_path).stem
    out_mp4 = out_dir_p / f"{stem}_local_video.mp4"
    result = assemble_video(image_paths, audio_paths, str(out_mp4),
                            fallback_duration=fallback_duration)

    _progress(f"Fertig: {Path(result).name}")
    return result
