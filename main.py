# main.py
"""
KI Sekretär - Autonomer KI-Entwickler-Broker

Dieses Modul enthält die Kernfunktionen des Bots, darunter die
"Echtheits‑Detektiv"‑ und "Premium‑Verhandlung"‑Routinen.
Für den Administrator (ID: 8874543115) wird zusätzlich eine
Admin‑Signatur mit dem genauen KI‑Modell angezeigt.
"""

import os
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Hilfsfunktion: prüft, ob der Aufrufer Administrator ist
# ---------------------------------------------------------------------------
ADMIN_ID = "8874543115"

def is_admin(user_id: str) -> bool:
    """Return True if the given user_id matches the configured admin ID."""
    return str(user_id) == ADMIN_ID

# ---------------------------------------------------------------------------
# Funktion: Echtheits‑Detektiv
# ---------------------------------------------------------------------------
def echtheits_detektiv(request: Dict[str, Any]) -> str:
    """Analysiere einen Text und gib ein Ergebnis zurück.

    Für den Administrator wird zusätzlich die Modell‑Version angezeigt.
    """
    user_id = request.get("user_id", "")
    text = request.get("text", "")

    # --- Hier kommt die eigentliche Logik des Detektivs ---
    # (Platzhalter‑Implementierung)
    if not text:
        result = "Bitte gib einen Text zum Prüfen an."
    else:
        # Beispiel‑Analyse (kann durch echte KI‑Logik ersetzt werden)
        result = f"Der Text scheint authentisch zu sein. Analyse‑Score: 92%"

    # Admin‑Signatur hinzufügen, falls nötig
    if is_admin(user_id):
        # Das genaue Modell kann dynamisch ermittelt werden; hier fest codiert.
        model_info = "gpt-4o-mini"
        result += f"\n--- [ADMIN] --- 🤖 Modell: {model_info}"

    return result

# ---------------------------------------------------------------------------
# Funktion: Premium‑Verhandlung
# ---------------------------------------------------------------------------
def premium_verhandlung(request: Dict[str, Any]) -> str:
    """Führe eine Premium‑Verhandlung mit dem Nutzer.

    Für den Administrator wird zusätzlich die Modell‑Version angezeigt.
    """
    user_id = request.get("user_id", "")
    topic = request.get("topic", "Allgemein")

    # --- Hier kommt die eigentliche Verhandlungslogik ---
    # (Platzhalter‑Implementierung)
    result = f"Premium‑Verhandlung zum Thema '{topic}' gestartet."
    result += "\nWir prüfen die besten Konditionen für dich."

    # Admin‑Signatur hinzufügen, falls nötig
    if is_admin(user_id):
        model_info = "gpt-4o-mini"
        result += f"\n--- [ADMIN] --- 🤖 Modell: {model_info}"

    return result

# ---------------------------------------------------------------------------
# Beispiel‑Aufruf (nur für lokale Tests)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Simuliere einen normalen Nutzer
    print(echtheits_detektiv({"user_id": "123456", "text": "Beispieltext"}))
    print()
    # Simuliere den Administrator
    print(echtheits_detektiv({"user_id": "8874543115", "text": "Beispieltext"}))
    print()
    print(premium_verhandlung({"user_id": "8874543115", "topic": "Vertrag"}))
