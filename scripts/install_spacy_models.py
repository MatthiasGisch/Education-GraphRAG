#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Installiert die spaCy- und SciSpacy-Modelle für die hybride Entitätsextraktion."""

import subprocess
import sys

def run_command(cmd, description):
    """Führt einen Shell-Befehl aus und gibt Status aus."""
    print(f"\n{'='*60}")
    print(f"{description}")
    print(f"{'='*60}")
    print(f"Command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("✓ Erfolgreich!")
        if result.stdout:
            print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Fehler: {e}")
        if e.stderr:
            print(f"Fehlerausgabe: {e.stderr}")
        return False

def main():
    """Installiert en_core_web_sm und en_core_sci_sm und meldet Erfolg oder Fehler."""
    print("\n" + "="*60)
    print("spaCy Modell-Installation für Hybrid Entity Extraction")
    print("="*60)
    
    # 1. Installiere standard englisches spaCy Modell
    success1 = run_command(
        [sys.executable, "-m", "spacy", "download", "en_core_web_sm"],
        "1. Installiere spaCy Basismodell (en_core_web_sm)"
    )
    
    # 2. Installiere SciSpacy Modell
    success2 = run_command(
        [sys.executable, "-m", "pip", "install", 
         "https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/releases/v0.5.4/en_core_sci_sm-0.5.4.tar.gz"],
        "2. Installiere SciSpacy Modell (en_core_sci_sm)"
    )
    
    print("\n" + "="*60)
    print("INSTALLATION ABGESCHLOSSEN")
    print("="*60)
    
    if success1 and success2:
        print("✓ Alle Modelle erfolgreich installiert!")
        print("\nDu kannst jetzt die Hybrid-Extraktion nutzen:")
        print("  - Im GUI: Wähle 'Hybrid (NER + LLM + Relationen)'")
        print("  - Im Code: Nutze extract_and_embed_concepts_hybrid()")
    else:
        print("✗ Einige Installationen sind fehlgeschlagen.")
        print("Bitte prüfe die Fehlermeldungen oben.")
        sys.exit(1)

if __name__ == "__main__":
    main()
