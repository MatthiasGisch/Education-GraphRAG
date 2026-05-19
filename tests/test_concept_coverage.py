"""Testet die Concept-Extraction-Coverage auf einem Machine-Learning-Beispieltext."""
import sys
import os
from dotenv import load_dotenv

# Lade Umgebungsvariablen VOR dem Import von concept_extract
load_dotenv()

if not os.getenv("OPENAI_API_KEY"):
    print("❌ FEHLER: OPENAI_API_KEY nicht gesetzt in .env Datei!")
    sys.exit(1)

from src import concept_extract as ce

# Beispieltext über Machine Learning
sample_text = """
Machine Learning ist ein Teilbereich der künstlichen Intelligenz (KI), der es Computersystemen ermöglicht, 
aus Erfahrungen zu lernen und sich zu verbessern, ohne explizit programmiert zu werden. Der Kern des 
maschinellen Lernens liegt in der Entwicklung von Algorithmen, die Muster in Daten erkennen und daraus 
Vorhersagen treffen können.

Es gibt verschiedene Arten von Machine Learning: Supervised Learning (überwachtes Lernen), bei dem ein 
Modell mit gelabelten Trainingsdaten trainiert wird; Unsupervised Learning (unüberwachtes Lernen), bei 
dem Muster in nicht-gelabelten Daten gefunden werden; und Reinforcement Learning (verstärkendes Lernen), 
bei dem ein Agent durch Trial-and-Error lernt, optimale Entscheidungen zu treffen.

Neuronale Netze sind eine wichtige Architektur im Machine Learning, die von der Struktur des menschlichen 
Gehirns inspiriert ist. Deep Learning nutzt mehrschichtige neuronale Netze, um komplexe Muster zu erkennen. 
Convolutional Neural Networks (CNNs) werden häufig für Bildverarbeitung verwendet, während Recurrent Neural 
Networks (RNNs) für Sequenzdaten wie Text oder Zeitreihen eingesetzt werden.

Ein zentrales Konzept im Machine Learning ist das Training eines Modells. Dabei wird die Loss Function 
(Verlustfunktion) minimiert, um die Modellparameter zu optimieren. Gradient Descent ist ein häufig verwendeter 
Optimierungsalgorithmus. Overfitting tritt auf, wenn ein Modell zu gut auf Trainingsdaten passt und schlecht 
auf neuen Daten generalisiert. Regularisierungstechniken wie L1 und L2 Regularization helfen, Overfitting zu 
vermeiden.

Die Evaluierung von Machine Learning Modellen erfolgt typischerweise durch Metriken wie Accuracy (Genauigkeit), 
Precision (Präzision), Recall (Trefferquote) und F1-Score. Cross-Validation wird verwendet, um die 
Modellleistung robuster zu bewerten. Feature Engineering, also die Auswahl und Transformation von 
Eingabevariablen, ist oft entscheidend für den Erfolg eines ML-Projekts.
"""

def test_concept_coverage():
    """Testet wie viele der erwarteten Schlüsselkonzepte aus dem Beispieltext extrahiert werden."""
    print("=" * 80)
    print("CONCEPT EXTRACTION COVERAGE TEST")
    print("=" * 80)
    print(f"\nTesttext Länge: {len(sample_text)} Zeichen")
    print(f"Wörter: ~{len(sample_text.split())} Wörter\n")
    
    # Simuliere Paragraphen
    paragraphs = []
    for i, para in enumerate(sample_text.split('\n\n')):
        if para.strip():
            paragraphs.append({
                "paragraph_id": f"p{i}",
                "text": para.strip()
            })
    
    print(f"Paragraphen: {len(paragraphs)}\n")
    
    # Extrahiere Konzepte (ohne Neo4j für einfachen Test)
    print("Starte Concept Extraction...")
    print("-" * 80)
    
    try:
        concepts, links = ce.extract_and_embed_concepts(
            paper_title="Machine Learning Grundlagen",
            paragraphs=paragraphs,
            topic_hint="Machine Learning, Deep Learning, Neural Networks",
            max_concepts=30,  # Erhöht für bessere Coverage
            seed_names=[],
            allow_new=True,
            neo_client=None,  # Kein Dedupe
            dedupe_threshold=0.9,
            min_confidence=0.4,  # Mindest-Confidence 0.4 (nicht zu restriktiv)
            persist_to_topic=False  # Kein DB-Zugriff
        )
        
        print(f"\n✅ Extraction erfolgreich!")
        print(f"\n📊 ERGEBNISSE:")
        print(f"  • Extrahierte Konzepte: {len(concepts)}")
        print(f"  • Paragraph-Concept Links: {len(links)}")
        print(f"  • Durchschnittliche Links pro Konzept: {len(links) / len(concepts) if concepts else 0:.1f}")
        
        # Zeige extrahierte Konzepte
        print(f"\n📝 EXTRAHIERTE KONZEPTE:")
        print("-" * 80)
        for i, concept in enumerate(concepts, 1):
            name = concept.get('name', 'UNNAMED')
            desc = concept.get('description', '')[:80]
            alt_labels = concept.get('alt_labels', [])
            
            # Zähle Links für dieses Konzept
            concept_links = [l for l in links if l.get('concept_name') == name]
            avg_conf = sum(l.get('confidence', 0) for l in concept_links) / len(concept_links) if concept_links else 0
            
            print(f"\n{i}. {name}")
            if alt_labels:
                print(f"   Alt. Bezeichnungen: {', '.join(alt_labels)}")
            print(f"   Beschreibung: {desc}...")
            print(f"   Links: {len(concept_links)} | Avg. Confidence: {avg_conf:.2f}")
        
        # Zeige Link-Verteilung
        print(f"\n📈 LINK-VERTEILUNG:")
        print("-" * 80)
        
        # Debug: Zeige Link-Struktur
        if links:
            print(f"DEBUG: Erstes Link-Objekt: {links[0]}")
        
        # Erstelle Mapping concept_id -> name
        concept_id_to_name = {c.get('concept_id', ''): c.get('name', 'UNNAMED') for c in concepts}
        
        for i, para in enumerate(paragraphs):
            para_links = [l for l in links if l.get('paragraph_id') == f"p{i}"]
            print(f"Paragraph {i+1}: {len(para_links)} Konzepte erkannt")
            if para_links:
                for link in sorted(para_links, key=lambda x: x.get('confidence', 0), reverse=True)[:3]:
                    # Nutze concept_id statt concept_name falls vorhanden
                    concept_id = link.get('concept_id', '')
                    concept_name = concept_id_to_name.get(concept_id, link.get('concept_name', 'UNKNOWN'))
                    print(f"  • {concept_name} (Conf: {link.get('confidence', 0):.2f})")
        
        # Coverage-Analyse
        print(f"\n🎯 COVERAGE-ANALYSE:")
        print("-" * 80)
        
        # Erwartete Schlüsselkonzepte (manuell identifiziert)
        expected_concepts = [
            "Machine Learning", "Künstliche Intelligenz", "Supervised Learning", 
            "Unsupervised Learning", "Reinforcement Learning", "Neural Networks",
            "Deep Learning", "CNN", "RNN", "Training", "Loss Function",
            "Gradient Descent", "Overfitting", "Regularization", "Accuracy",
            "Precision", "Recall", "F1-Score", "Cross-Validation", "Feature Engineering"
        ]
        
        extracted_names = [c['name'].lower() for c in concepts]
        found_count = 0
        missing = []
        
        for expected in expected_concepts:
            # Prüfe ob Konzept (oder Variante) gefunden wurde
            if any(expected.lower() in name or name in expected.lower() for name in extracted_names):
                found_count += 1
            else:
                missing.append(expected)
        
        coverage = (found_count / len(expected_concepts)) * 100
        print(f"Erwartete Schlüsselkonzepte: {len(expected_concepts)}")
        print(f"Gefundene Schlüsselkonzepte: {found_count}")
        print(f"Coverage: {coverage:.1f}%")
        
        if missing:
            print(f"\n❌ FEHLENDE KONZEPTE:")
            for m in missing:
                print(f"  • {m}")
        
        # Qualitätsbewertung
        print(f"\n⭐ QUALITÄTSBEWERTUNG:")
        print("-" * 80)
        if coverage >= 80:
            print("✅ EXZELLENT - Sehr gute Coverage der wichtigsten Konzepte")
        elif coverage >= 60:
            print("✅ GUT - Solide Coverage, einige Details fehlen")
        elif coverage >= 40:
            print("⚠️  BEFRIEDIGEND - Wichtige Konzepte werden erkannt, aber Verbesserungspotenzial")
        else:
            print("❌ UNZUREICHEND - Viele wichtige Konzepte fehlen")
        
        return concepts, links, coverage
        
    except Exception as e:
        print(f"\n❌ FEHLER bei der Extraction: {e}")
        import traceback
        traceback.print_exc()
        return None, None, 0

if __name__ == "__main__":
    concepts, links, coverage = test_concept_coverage()
    print("\n" + "=" * 80)
    print(f"TEST ABGESCHLOSSEN - Coverage: {coverage:.1f}%")
    print("=" * 80)
