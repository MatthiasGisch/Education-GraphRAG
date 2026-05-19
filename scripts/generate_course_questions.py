"""Generiert kursspezifische RAGAS-Testfragen mit Ground Truths aus den Kursinhaltsverzeichnissen und Neo4j."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI

from src import config as cfg
from src.neo import Neo4jClient
from src.retriever import concept_based_retrieve

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

OUTPUT_DIR = ROOT / "data" / "eval"

# ---------------------------------------------------------------------------
# Kursdefinitionen
# ---------------------------------------------------------------------------

KURSE: dict[str, dict] = {

    "kurs1_ai_literacy_kmu": {
        "name": "AI Literacy für KMU – Level 0 (Schnupperkurs)",
        "fragen_pro_abschnitt": 2,
        "abschnitte": [
            {"id": "1.1", "kapitel": "Kapitel 1 – Was ist KI und wo begegnet sie uns?",
             "titel": "Was ist 'Künstliche Intelligenz'?",
             "themen": ["Alltagsdefinition von KI",
                        "Unterschied zu klassischer Wenn-dann-Software",
                        "Teilbereiche: Machine Learning, generative KI"]},
            {"id": "1.2", "kapitel": "Kapitel 1 – Was ist KI und wo begegnet sie uns?",
             "titel": "Typische KI-Anwendungen im Büro, Vertrieb, Service und Produktion",
             "themen": ["Beispiele aus Büro und Verwaltung (Textbearbeitung, E-Mails, Dokumente)",
                        "Beispiele aus Vertrieb und Service (Kundendialog, FAQ, Chatbots)",
                        "Beispiele aus Produktion und Logistik (Planung, Prognosen, Automatisierung)"]},
            {"id": "1.3", "kapitel": "Kapitel 1 – Was ist KI und wo begegnet sie uns?",
             "titel": "Was können heutige KI-Systeme gut – und was (noch) nicht?",
             "themen": ["Stärken: Mustererkennung, Sprachverarbeitung, Automatisierung repetitiver Aufgaben",
                        "Schwächen: fehlendes Weltverständnis, fehlerhafte Inhalte, mangelnde Kontexttiefe",
                        "Warum KI immer Kontrolle durch Menschen braucht"]},
            {"id": "1.4", "kapitel": "Kapitel 1 – Was ist KI und wo begegnet sie uns?",
             "titel": "Generative KI im Überblick (Texte, Bilder, Tabellen, Code)",
             "themen": ["Generative KI als spezielle Form von KI",
                        "Typische Formen: Textgeneratoren, Bildgeneratoren, Code-Assistenten",
                        "Einfache Praxisbeispiele aus dem Arbeitsalltag von KMU"]},
            {"id": "2.1", "kapitel": "Kapitel 2 – Wie KI grob funktioniert (ohne Mathe)",
             "titel": "Daten, Modelle, Training, Vorhersage – in einfachen Bildern",
             "themen": ["Daten als Beispiele, aus denen KI lernt",
                        "Modell als Muster-Finder",
                        "Unterschied zwischen Trainingsphase und Nutzung im Alltag"]},
            {"id": "2.2", "kapitel": "Kapitel 2 – Wie KI grob funktioniert (ohne Mathe)",
             "titel": "Klassische Software vs. lernende Systeme",
             "themen": ["Feste Regeln vs. datengetriebene Muster",
                        "Warum sich lernende Systeme manchmal unerwartet verhalten",
                        "Konsequenzen für Verständnis, Kontrolle und Erklärbarkeit"]},
            {"id": "2.3", "kapitel": "Kapitel 2 – Wie KI grob funktioniert (ohne Mathe)",
             "titel": "Was ist ein Sprachmodell? (z.B. ChatGPT)",
             "themen": ["Sprachmodell als System, das Wahrscheinlichkeiten für Wörter berechnet",
                        "Warum Texte oft flüssig, aber nicht immer korrekt sind",
                        "Rolle von Prompts für die Qualität der Ergebnisse"]},
            {"id": "2.4", "kapitel": "Kapitel 2 – Wie KI grob funktioniert (ohne Mathe)",
             "titel": "Warum KI halluziniert und wie man damit umgeht",
             "themen": ["Erklärung des Phänomens Halluzination",
                        "Typische Warnsignale für fehlerhafte Antworten",
                        "Einfache Strategien zur Gegenprüfung von KI-Ergebnissen"]},
            {"id": "3.1", "kapitel": "Kapitel 3 – Chancen und Risiken von KI für KMU",
             "titel": "Produktivität im Büro, Verwaltung und Vertrieb",
             "themen": ["Zeitersparnis bei Routineaufgaben (E-Mails, Schriftsätze, Protokolle)",
                        "Unterstützung bei Angeboten, Präsentationen, Marketingtexten",
                        "Verbesserte Strukturierung von Informationen und Dokumenten"]},
            {"id": "3.2", "kapitel": "Kapitel 3 – Chancen und Risiken von KI für KMU",
             "titel": "Unterstützung in Produktion, Wartung, Qualitätssicherung",
             "themen": ["Predictive Maintenance: vorausschauende Wartung",
                        "KI für Qualitätskontrollen (z.B. Bildauswertung)",
                        "Optimierung in Produktion und Logistik"]},
            {"id": "3.3", "kapitel": "Kapitel 3 – Chancen und Risiken von KI für KMU",
             "titel": "Typische Hürden: Wissen, Kosten, Infrastruktur, Kompetenzen",
             "themen": ["Fehlendes Know-how im Unternehmen",
                        "Kosten- und Ressourcenfragen bei Einführung und Betrieb",
                        "Abhängigkeit von Dienstleistern, interne Akzeptanz"]},
            {"id": "3.4", "kapitel": "Kapitel 3 – Chancen und Risiken von KI für KMU",
             "titel": "Risikoaspekte: Datenschutz, Informationssicherheit, Geschäftsgeheimnisse",
             "themen": ["Gefahr, vertrauliche Daten in externe KI-Systeme einzugeben",
                        "Relevanz von Datenschutz und IT-Sicherheit bei Cloud-Diensten",
                        "Umgang mit Geschäftsgeheimnissen und sensiblen Kundendaten"]},
            {"id": "3.5", "kapitel": "Kapitel 3 – Chancen und Risiken von KI für KMU",
             "titel": "Gesellschaftliche und arbeitsbezogene Auswirkungen (Bias, Fairness, Beschäftigung)",
             "themen": ["Verzerrungen in Trainingsdaten und ihre Folgen",
                        "Fairer Umgang mit KI in Personalprozessen",
                        "Veränderung von Aufgabenprofilen und Bedarf an Weiterbildung"]},
            {"id": "4.1", "kapitel": "Kapitel 4 – Sicher und verantwortungsvoll mit KI umgehen",
             "titel": "Grundregeln: Do's & Don'ts für Mitarbeitende",
             "themen": ["Was Mitarbeitende mit KI tun dürfen und was nicht",
                        "Grundregeln: keine sensiblen Daten, Ergebnisse prüfen, Transparenz",
                        "Verantwortung bei der Nutzung von KI-gestützten Ergebnissen"]},
            {"id": "4.2", "kapitel": "Kapitel 4 – Sicher und verantwortungsvoll mit KI umgehen",
             "titel": "Beispiele für gute KI-Nutzungsrichtlinien im Organisationskontext",
             "themen": ["Bausteine einer einfachen KI-Richtlinie für KMU",
                        "Erlaubte und verbotene Anwendungen, Graubereiche",
                        "Transparenz- und Dokumentationspflichten bei KI-Einsatz"]},
            {"id": "4.3", "kapitel": "Kapitel 4 – Sicher und verantwortungsvoll mit KI umgehen",
             "titel": "Einfache Checkliste: Darf ich diese Aufgabe einer KI überlassen?",
             "themen": ["Leitfragen zur Einschätzung von Risiko und Eignung",
                        "Beispiele: unkritische Aufgaben vs. Aufgaben mit hohen Risiken",
                        "Rolle von Gegenprüfung und Vier-Augen-Prinzip"]},
            {"id": "4.4", "kapitel": "Kapitel 4 – Sicher und verantwortungsvoll mit KI umgehen",
             "titel": "Kurzüberblick: EU AI Act – warum betrifft mich das als KMU?",
             "themen": ["Grundidee des risikobasierten Ansatzes (niedriges, hohes, verbotenes Risiko)",
                        "Relevanz für Nutzer von KI-Systemen im Unternehmen",
                        "Hinweis auf Vertiefungskurs für Details"]},
        ],
    },

    "kurs2_ki_strategie_governance": {
        "name": "AI-Strategie & KI-Governance für Führungskräfte in KMU – Level 0",
        "fragen_pro_abschnitt": 2,
        "abschnitte": [
            {"id": "1.1", "kapitel": "Kapitel 1 – Warum KI für die Unternehmensführung relevant ist",
             "titel": "Was KI für die Geschäftsentwicklung bedeutet",
             "themen": ["Wie KI Wert schaffen kann: Zeitersparnis, bessere Entscheidungen, neue Angebote",
                        "Unterschied zwischen Einzelprojekten und strategischer Ausrichtung"]},
            {"id": "1.2", "kapitel": "Kapitel 1 – Warum KI für die Unternehmensführung relevant ist",
             "titel": "Wo KI in KMU typischerweise hilft",
             "themen": ["Beispiele aus Vertrieb, Verwaltung und operativen Bereichen",
                        "Fokus auf kleine, überschaubare Anwendungsfälle mit klar erkennbarem Nutzen"]},
            {"id": "1.3", "kapitel": "Kapitel 1 – Warum KI für die Unternehmensführung relevant ist",
             "titel": "Welche Voraussetzungen Führungskräfte schaffen müssen",
             "themen": ["Klarheit über Ziele und Erwartungen",
                        "Erste organisatorische Prioritäten: Zuständigkeiten, Budget, Kommunikation"]},
            {"id": "2.1", "kapitel": "Kapitel 2 – Grundlagen einer KI-fähigen Organisation",
             "titel": "Rollen und Verantwortlichkeiten",
             "themen": ["Warum Führungskräfte bei KI eine aktive Rolle spielen müssen",
                        "Übersicht einfacher Rollen: Ansprechpersonen für Daten, Prozesse, KI-Werkzeuge"]},
            {"id": "2.2", "kapitel": "Kapitel 2 – Grundlagen einer KI-fähigen Organisation",
             "titel": "Daten als Basis für KI",
             "themen": ["Warum Datenqualität und Zugänglichkeit entscheidend sind",
                        "Welche Daten für einfache KI-Anwendungen sinnvoll sind"]},
            {"id": "2.3", "kapitel": "Kapitel 2 – Grundlagen einer KI-fähigen Organisation",
             "titel": "Kompetenzen und Lernwege im Unternehmen",
             "themen": ["Erste Kompetenzen für Mitarbeitende: KI-Verständnis, Umgang mit Werkzeugen",
                        "Wie Führungskräfte Lernangebote fördern und Barrieren abbauen können"]},
            {"id": "3.1", "kapitel": "Kapitel 3 – Grundlagen der KI-Governance",
             "titel": "Warum Unternehmen Regeln für KI benötigen",
             "themen": ["Risiken wie Fehler, Verzerrungen, falsche Entscheidungen, Datenschutzprobleme",
                        "Ziel: Orientierung und Sicherheit für Mitarbeitende schaffen"]},
            {"id": "3.2", "kapitel": "Kapitel 3 – Grundlagen der KI-Governance",
             "titel": "Einfache Prinzipien guter KI-Governance",
             "themen": ["Transparenz: klare Information darüber, wo KI eingesetzt wird",
                        "Verantwortlichkeit: wer prüft, wer freigibt, wer reagiert bei Problemen",
                        "Sicherheit: wie Ergebnisse kontrolliert und Fehler minimiert werden"]},
            {"id": "3.3", "kapitel": "Kapitel 3 – Grundlagen der KI-Governance",
             "titel": "Erste interne Leitlinien",
             "themen": ["Grundideen für einfache Regeln zum Umgang mit KI-Werkzeugen",
                        "Wann Mitarbeitende Rücksprache mit Führungskräften halten sollten"]},
            {"id": "4.1", "kapitel": "Kapitel 4 – Von der Idee zur ersten KI-Roadmap",
             "titel": "Nützliche Startpunkte für KI im KMU",
             "themen": ["Identifikation kleiner, risikoarmer Möglichkeiten zur Automatisierung",
                        "Beispiele für erste Pilotanwendungen"]},
            {"id": "4.2", "kapitel": "Kapitel 4 – Von der Idee zur ersten KI-Roadmap",
             "titel": "Auswahl und Priorisierung",
             "themen": ["Kriterien: Nutzen, Umsetzbarkeit, Datenverfügbarkeit, Risiko",
                        "Wie Führungskräfte Prioritäten setzen und Ressourcen bündeln"]},
            {"id": "4.3", "kapitel": "Kapitel 4 – Von der Idee zur ersten KI-Roadmap",
             "titel": "Erfolg beurteilen und weiterlernen",
             "themen": ["Einfache Kennzahlen, um Fortschritte sichtbar zu machen",
                        "Bedeutung von Austausch, Feedback und schrittweiser Verbesserung"]},
            {"id": "4.4", "kapitel": "Kapitel 4 – Von der Idee zur ersten KI-Roadmap",
             "titel": "Kommunikation und Veränderungsmanagement",
             "themen": ["Warum Mitarbeitende früh eingebunden werden sollten",
                        "Grundlagen für eine klare Kommunikation zu Chancen und Grenzen von KI"]},
        ],
    },

    "kurs3_ki_recht_eu_ai_act": {
        "name": "KI & Recht – Der EU AI Act in der Unternehmenspraxis – Level 0",
        "fragen_pro_abschnitt": 2,
        "abschnitte": [
            {"id": "1.1", "kapitel": "Kapitel 1 – Warum wird KI reguliert?",
             "titel": "Digitale Transformation, Chancen und Risiken von KI",
             "themen": ["Rolle von KI in Vertrieb, Verwaltung, Produktion und Service",
                        "Typische Risiken: Diskriminierung, Intransparenz, Sicherheitsrisiken, Grundrechtsbeeinträchtigungen",
                        "Politische und wirtschaftliche Gründe für Regulierung"]},
            {"id": "1.2", "kapitel": "Kapitel 1 – Warum wird KI reguliert?",
             "titel": "Überblick über den europäischen Rechtsrahmen",
             "themen": ["Zusammenspiel von AI Act, DSGVO, Digital Services Act",
                        "Grundidee der Abgrenzung zwischen einzelnen Rechtsakten"]},
            {"id": "1.3", "kapitel": "Kapitel 1 – Warum wird KI reguliert?",
             "titel": "Ziele des EU AI Act",
             "themen": ["Schutz von Gesundheit, Sicherheit und Grundrechten",
                        "Gewährleistung eines funktionierenden Binnenmarktes",
                        "Förderung von Innovation durch klare Regeln"]},
            {"id": "2.1", "kapitel": "Kapitel 2 – Aufbau und Systematik des EU AI Act",
             "titel": "Anwendungsbereich und zentrale Begriffe",
             "themen": ["Definition von KI-Systemen im Sinne des Gesetzes",
                        "Rollen: Anbieter, Nutzer, Importeure, Distributoren",
                        "Besonderheiten bei Forschung, Open Source, nicht-professioneller Nutzung"]},
            {"id": "2.2", "kapitel": "Kapitel 2 – Aufbau und Systematik des EU AI Act",
             "titel": "Der risikobasierte Ansatz – vier Risikoklassen",
             "themen": ["Verbotene KI-Praktiken (unacceptable risk)",
                        "Hochrisiko-Systeme (high-risk)",
                        "Systeme mit begrenztem Risiko (limited risk)",
                        "Niedrigrisiko-Systeme (minimal risk)"]},
            {"id": "2.3", "kapitel": "Kapitel 2 – Aufbau und Systematik des EU AI Act",
             "titel": "Rollen und Verantwortlichkeiten in der KI-Wertschöpfungskette",
             "themen": ["Pflichten von Anbietern (Provider)",
                        "Pflichten von Nutzern (Deployern)",
                        "Besonderheiten bei allgemeinen KI-Modellen (General Purpose AI)"]},
            {"id": "2.4", "kapitel": "Kapitel 2 – Aufbau und Systematik des EU AI Act",
             "titel": "Zeitplan und Übergangsfristen",
             "themen": ["Grobe Einordnung der zeitlichen Umsetzungsschritte",
                        "Bedeutung für Unternehmen in der Anfangsphase"]},
            {"id": "3.1", "kapitel": "Kapitel 3 – Pflichten für Unternehmen (insbesondere KMU)",
             "titel": "Typische KI-Anwendungen in Unternehmen und ihre Risikoprofile",
             "themen": ["Beispiele aus HR, Vertrieb, Produktion und Finanzbereich",
                        "Erste Orientierung zur Einordnung der Risikokategorie"]},
            {"id": "3.2", "kapitel": "Kapitel 3 – Pflichten für Unternehmen (insbesondere KMU)",
             "titel": "Grundpflichten für Anbieter und Nutzer",
             "themen": ["Anforderungen für Anbieter: Risikomanagement, Datenqualität, Dokumentation, Transparenz, menschliche Aufsicht",
                        "Anforderungen für Nutzer: zweckgebundene Nutzung, Monitoring, Protokollierung"]},
            {"id": "3.3", "kapitel": "Kapitel 3 – Pflichten für Unternehmen (insbesondere KMU)",
             "titel": "Konformitätsbewertung und CE-Kennzeichnung",
             "themen": ["Grundprinzipien der Konformitätsbewertung",
                        "Rolle von Prüfstellen und Marktüberwachung"]},
            {"id": "3.4", "kapitel": "Kapitel 3 – Pflichten für Unternehmen (insbesondere KMU)",
             "titel": "Sanktionen und Haftungsschnittstellen",
             "themen": ["Grundidee des Sanktionsrahmens",
                        "Bezug zu Produkthaftung und nationalem Haftungsrecht"]},
            {"id": "3.5", "kapitel": "Kapitel 3 – Pflichten für Unternehmen (insbesondere KMU)",
             "titel": "Besondere Herausforderungen für KMU",
             "themen": ["Ressourcen- und Kompetenzfragen",
                        "Bedeutung von Standards, Vorlagen und unabhängigen Prüfinstanzen",
                        "Chancen durch frühzeitige Compliance"]},
            {"id": "4.1", "kapitel": "Kapitel 4 – Umsetzung im Unternehmen: vom Gesetz zur Praxis",
             "titel": "Bestandsaufnahme",
             "themen": ["Analyse bestehender KI- oder Automationssysteme",
                        "Erste Risikoeinordnung anhand gesetzlicher Kriterien"]},
            {"id": "4.2", "kapitel": "Kapitel 4 – Umsetzung im Unternehmen: vom Gesetz zur Praxis",
             "titel": "Governance-Strukturen und Verantwortlichkeiten",
             "themen": ["Einbettung von KI in bestehende Organisationsstrukturen",
                        "Rollenverteilung zwischen Technik, Fachbereichen, Recht und Management"]},
            {"id": "4.3", "kapitel": "Kapitel 4 – Umsetzung im Unternehmen: vom Gesetz zur Praxis",
             "titel": "Prozesse, Richtlinien und Dokumentation",
             "themen": ["Bausteine einer internen KI-Richtlinie",
                        "Dokumentationsanforderungen und Monitoringprozesse",
                        "Umgang mit Vorfällen und Fehlern"]},
            {"id": "4.4", "kapitel": "Kapitel 4 – Umsetzung im Unternehmen: vom Gesetz zur Praxis",
             "titel": "Schulung und Sensibilisierung",
             "themen": ["Kompetenzen für Management, Fachabteilungen und Technikteams",
                        "Konkrete Szenarien, in denen Mitarbeitende Compliance beachten müssen"]},
            {"id": "4.5", "kapitel": "Kapitel 4 – Umsetzung im Unternehmen: vom Gesetz zur Praxis",
             "titel": "Blick nach vorn",
             "themen": ["Bedeutung zukünftiger Leitlinien und Standards",
                        "Entwicklung von Best Practices auf EU- und Branchenebene"]},
        ],
    },
}


# ---------------------------------------------------------------------------
# Phase 1: Fragen generieren
# ---------------------------------------------------------------------------

_FRAGEN_SYSTEM = (
    "Du bist ein didaktischer Experte und erstellst Prüfungsfragen für Unternehmenskurse. "
    "Du antwortest ausschließlich als kompaktes JSON-Array."
)

_FRAGEN_USER = """\
Kurs: {kurs_name}
Kapitel: {kapitel}
Abschnitt: {abschnitt_titel}

Lernziele / Themen:
{themen}

Erstelle genau {n} unterschiedliche Testfragen auf Deutsch.
Regeln:
- Fragen sollen direkt aus den Lernzielen ableitbar und faktisch beantwortbar sein
- Niveau Level 0 (Einsteiger, keine Fachkenntnisse vorausgesetzt)
- Keine Ja/Nein-Fragen, keine Meinungsfragen
- Jede Frage deckt einen anderen Aspekt ab
- Kurze, klare Formulierung

Antworte NUR als JSON-Array:
[{{"frage": "...", "typ": "factual"}}, ...]
"""


def _generate_questions_for_section(
    client: OpenAI,
    kurs_name: str,
    abschnitt: dict,
    n: int = 2,
) -> list[dict]:
    """Generiert n Testfragen für einen Kursabschnitt via GPT-4o-mini und gibt sie als Liste von Dicts zurück."""
    themen_str = "\n".join(f"- {t}" for t in abschnitt["themen"])
    prompt = _FRAGEN_USER.format(
        kurs_name=kurs_name,
        kapitel=abschnitt["kapitel"],
        abschnitt_titel=abschnitt["titel"],
        themen=themen_str,
        n=n,
    )
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _FRAGEN_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "[]"
    # JSON-Object-Wrapper entfernen falls nötig
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            # GPT gibt manchmal {"questions": [...]} zurück
            for v in parsed.values():
                if isinstance(v, list):
                    parsed = v
                    break
        return parsed if isinstance(parsed, list) else []
    except Exception as e:
        log.warning("JSON-Parse fehlgeschlagen (%s): %s", e, raw[:200])
        return []


# ---------------------------------------------------------------------------
# Phase 2: Ground Truths generieren
# ---------------------------------------------------------------------------

_GT_SYSTEM = (
    "Du bist ein sachkundiger Experte. "
    "Beantworte Fragen präzise und korrekt auf Deutsch. "
    "Maximal 3 prägnante Sätze."
)

_GT_USER_NEO4J = """\
Frage: {frage}

Quelltexte aus dem Wissensgraphen:
{kontext}

Synthetisiere eine knappe, korrekte Antwort NUR auf Basis der Quelltexte.
Wenn die Quelltexte nicht ausreichen, ergänze mit Allgemeinwissen und markiere diesen Teil mit [Allgemeinwissen].
"""

_GT_USER_SYNTHETIC = """\
Frage: {frage}

Kurskontext:
Kurs: {kurs_name}
Abschnitt: {abschnitt_titel}
Themen: {themen}

Gib eine knappe, sachlich korrekte Antwort auf Deutsch (max. 3 Sätze).
Markiere die gesamte Antwort mit [Allgemeinwissen].
"""


def _generate_ground_truth(
    client: OpenAI,
    frage: str,
    neo: Neo4jClient,
    kurs_name: str,
    abschnitt: dict,
) -> tuple[str, str]:
    """Generiert per Neo4j-Retrieval oder GPT-Allgemeinwissen eine Ground Truth und gibt (text, source) zurück."""
    # Paragraphen aus Neo4j holen
    try:
        result = concept_based_retrieve(neo, frage)
        supports = result.get("supports", [])
        paragraphs = [
            s["text"] for s in supports
            if s.get("type") == "paragraph" and s.get("text")
        ][:6]
    except Exception as e:
        log.warning("Retrieval fehlgeschlagen für '%s': %s", frage[:60], e)
        paragraphs = []

    if paragraphs:
        kontext = "\n\n".join(f"[{i+1}] {p[:600]}" for i, p in enumerate(paragraphs))
        prompt = _GT_USER_NEO4J.format(frage=frage, kontext=kontext)
        source = "neo4j"
    else:
        log.info("Kein Kontext in Neo4j – nutze Allgemeinwissen für: %s", frage[:60])
        themen_str = ", ".join(abschnitt["themen"])
        prompt = _GT_USER_SYNTHETIC.format(
            frage=frage,
            kurs_name=kurs_name,
            abschnitt_titel=abschnitt["titel"],
            themen=themen_str,
        )
        source = "synthetic"

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _GT_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    ground_truth = (resp.choices[0].message.content or "").strip()
    return ground_truth, source


# ---------------------------------------------------------------------------
# Kurs verarbeiten
# ---------------------------------------------------------------------------

def process_kurs(
    kurs_id: str,
    nur_fragen: bool = False,
    neo: Neo4jClient | None = None,
) -> list[dict]:
    """Generiert Fragen und optional Ground Truths für einen Kurs, speichert die Ergebnisse als JSON und gibt sie zurück."""
    kurs = KURSE[kurs_id]
    client = OpenAI(api_key=cfg.OPENAI_API_KEY)
    n = kurs["fragen_pro_abschnitt"]
    alle_fragen: list[dict] = []

    log.info("=== %s (%d Abschnitte × %d Fragen) ===", kurs["name"],
             len(kurs["abschnitte"]), n)

    for abschnitt in kurs["abschnitte"]:
        log.info("Abschnitt %s: %s", abschnitt["id"], abschnitt["titel"])

        # Phase 1: Fragen generieren
        rohe_fragen = _generate_questions_for_section(
            client, kurs["name"], abschnitt, n
        )
        if not rohe_fragen:
            log.warning("Keine Fragen generiert für %s – übersprungen", abschnitt["id"])
            continue

        for fq in rohe_fragen:
            frage_text = fq.get("frage", "").strip()
            if not frage_text:
                continue

            eintrag: dict = {
                "question": frage_text,
                "ground_truth": "",
                "ground_truth_source": "pending",
                "question_type": fq.get("typ", "factual"),
                "kurs_id": kurs_id,
                "kurs_name": kurs["name"],
                "kapitel": abschnitt["kapitel"],
                "abschnitt_id": abschnitt["id"],
                "abschnitt_titel": abschnitt["titel"],
            }

            # Phase 2: Ground Truth anreichern
            if not nur_fragen and neo is not None:
                gt, source = _generate_ground_truth(
                    client, frage_text, neo, kurs["name"], abschnitt
                )
                eintrag["ground_truth"] = gt
                eintrag["ground_truth_source"] = source
                time.sleep(0.3)  # Rate-Limit schonen

            alle_fragen.append(eintrag)

    # Speichern
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"questions_{kurs_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(alle_fragen, f, ensure_ascii=False, indent=2)

    neo4j_count = sum(1 for q in alle_fragen if q["ground_truth_source"] == "neo4j")
    synth_count = sum(1 for q in alle_fragen if q["ground_truth_source"] == "synthetic")
    log.info(
        "Gespeichert: %s | %d Fragen (Neo4j: %d, Synthetic: %d, Pending: %d)",
        out_path, len(alle_fragen), neo4j_count, synth_count,
        len(alle_fragen) - neo4j_count - synth_count,
    )
    return alle_fragen


# ---------------------------------------------------------------------------
# Laden für RAGAS
# ---------------------------------------------------------------------------

def load_course_questions(kurs_id: str) -> list[dict]:
    """Lädt gespeicherte Fragen aus data/eval/questions_{kurs_id}.json als Liste von Dicts."""
    path = OUTPUT_DIR / f"questions_{kurs_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Fragen nicht gefunden: {path}\n"
            f"Bitte zuerst ausführen: python scripts/generate_course_questions.py --kurs {kurs_id}"
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_as_test_questions(kurs_id: str):
    """Lädt Fragen aus der JSON-Datei und konvertiert sie in TestQuestion-Objekte für ragas_eval.py."""
    from scripts.ragas_eval import TestQuestion
    raw = load_course_questions(kurs_id)
    return [
        TestQuestion(
            question=q["question"],
            ground_truth=q["ground_truth"] or q["abschnitt_titel"],
            question_type=q.get("question_type", "factual"),
        )
        for q in raw
        if q.get("question")
    ]


# ---------------------------------------------------------------------------
# Übersicht ausgeben
# ---------------------------------------------------------------------------

def print_summary(kurs_id: str) -> None:
    """Gibt eine Übersicht aller generierten Fragen eines Kurses mit Ground-Truth-Quelle auf stdout aus."""
    fragen = load_course_questions(kurs_id)
    kurs_name = KURSE[kurs_id]["name"]
    print(f"\n{'='*70}")
    print(f"  {kurs_name}")
    print(f"  {len(fragen)} Fragen | Datei: data/eval/questions_{kurs_id}.json")
    print(f"{'='*70}")
    for i, q in enumerate(fragen, 1):
        src = q.get("ground_truth_source", "?")
        src_mark = "✓" if src == "neo4j" else "~" if src == "synthetic" else "?"
        print(f"  [{i:02d}] [{src_mark}] ({q['abschnitt_id']}) {q['question']}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Kursspezifische RAGAS-Testfragen generieren"
    )
    parser.add_argument(
        "--kurs", choices=list(KURSE.keys()), default=None,
        help="Nur einen bestimmten Kurs generieren (Standard: alle drei)"
    )
    parser.add_argument(
        "--nur-fragen", action="store_true",
        help="Nur Fragen generieren, Ground Truths überspringen (kein Neo4j nötig)"
    )
    parser.add_argument(
        "--zusammenfassung", action="store_true",
        help="Nur Zusammenfassung bereits generierter Fragen anzeigen"
    )
    args = parser.parse_args()

    ziel_kurse = [args.kurs] if args.kurs else list(KURSE.keys())

    # Nur Zusammenfassung
    if args.zusammenfassung:
        for kid in ziel_kurse:
            try:
                print_summary(kid)
            except FileNotFoundError as e:
                print(f"  {e}")
        sys.exit(0)

    # Neo4j nur wenn Ground Truths gewünscht
    neo = None
    if not args.nur_fragen:
        try:
            neo = Neo4jClient()
            log.info("Neo4j verbunden.")
        except Exception as e:
            log.warning("Neo4j nicht erreichbar (%s) – nur Fragen werden generiert.", e)
            args.nur_fragen = True

    try:
        for kid in ziel_kurse:
            process_kurs(kid, nur_fragen=args.nur_fragen, neo=neo)

        log.info("Fertig. Dateien in: %s", OUTPUT_DIR)

        # Zusammenfassung anzeigen
        for kid in ziel_kurse:
            print_summary(kid)

    finally:
        if neo:
            neo.close()
