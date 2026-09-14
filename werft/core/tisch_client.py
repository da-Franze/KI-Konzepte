"""tisch_client.py -- minimale Anbindung der Werft an den Odin-Tisch.

Betreiber-Auftrag (2026-07-30, Tisch 14:40): Bestellungen, die auf Nutzer-Eingabe
warten (z.B. unvollstaendiges Formular) oder die der Debugger eskaliert hat,
sollten das AKTIV melden statt nur passiv in der DB zu stehen -- bisher
erfuhr Betreiber von einem haengenden formular_vorschlag nur, wenn ich (Mentor)
es zufaellig beim Nachschauen bemerkt habe.

Bewusst als eigenes, kleines Modul (nicht in bestellungen.py/debugger.py
eingebettet): reine Netzwerk-I/O-Funktion, unabhaengig testbar/mockbar,
Fehler dabei duerfen NIE die Werft-Pipeline selbst zum Stehen bringen
(daher der grosszuegige try/except -- ein nicht erreichbarer Tisch ist ein
Kommunikationsproblem, kein Werft-Problem).
"""
from __future__ import annotations

import logging

import requests

from configloader import config

logger = logging.getLogger(__name__)

_ENDPUNKT = "http://odin.fritz.box:8793/api/senden"


def benachrichtigen(text: str, von: str = "WERFT", thema: str = "") -> bool:
    """Postet eine Nachricht an den Odin-Tisch. Gibt True bei Erfolg zurueck,
    schluckt aber jeden Fehler (Netzwerk, Timeout, Tisch down) -- eine
    fehlgeschlagene Benachrichtigung darf niemals einen Pipeline-Schritt zum
    Absturz bringen. `von` muss 2-20 Zeichen A-Z/0-9/- sein (Tisch-Konvention)."""
    aktiviert = config.get("werft.tisch_benachrichtigung_aktiv", True)
    if not aktiviert:
        return False
    try:
        r = requests.post(
            _ENDPUNKT,
            json={"von": von, "thema": thema, "text": text},
            timeout=config.get("timeouts.tisch_post_sec"),
        )
        return r.status_code == 200
    except Exception as exc:  # pragma: no cover -- Netzwerkfehler duerfen nie eskalieren
        logger.warning("Tisch-Benachrichtigung fehlgeschlagen: %s", exc)
        return False
