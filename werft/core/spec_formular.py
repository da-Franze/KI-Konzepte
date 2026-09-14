from __future__ import annotations

"""Spec-Formular: standardisierte Vorlage + deterministische Vollstaendigkeits-
Pruefung fuer Bestellungen (Betreiber-Auftrag 2026-07-27, Tisch 15:10 "zuerst b
umgesetzt haben und dann a" -- Teil b der beiden am selben Tag diskutierten
Spec-Builder-Optionen: siehe project_planer_rolle_gebaut_2026-07-27 sowie
Tisch-Nachricht 8253. Ziel: "Zugang zur Werft vereinfachen", konkret fuer
Betreibers Bruder (ungeuebter Nutzer, kein Werft-Hintergrundwissen) -- ein
standardisiertes Formular statt eines mehrstufigen LLM-Chat-Coachings ist
guenstiger und robuster (funktioniert auch wenn der Coach ueber ein
schwaches lokales Modell laeuft), und nutzt genau die heute schon
bestehende Infrastruktur (bestellungen-Tabelle, remember_system,
spec_generator.py) statt ein neues Subsystem zu bauen.

Bewusst NICHT inhaltlich (G-10/Abschnitt 12.1: "die Werft prueft nicht
inhaltlich WAS bestellt wird") -- geprueft wird nur STRUKTUR: sind die
Elemente da, die der Coder/Planer/Stratege heute schon brauchen (kanonischer
Name, Abnahmekriterium), nicht ob die Idee dahinter gut ist.

Dieselben 4 Prinzipien wie in spec_generator.py::COACH_PROMPT (bewusst
konsistent gehalten -- ein Nutzer soll nicht zwei widerspruechliche
Anleitungen sehen, je nachdem ob er ueber den Chat-Coach oder das Formular
geht)."""
import re

STANDARD_VORLAGE = """## Titel
<kurzer, sprechender Name der Bestellung>

## Ziel
<ein bis zwei Saetze: was soll das fertige Modul tun>

## Kanonischer Name
Datei: <name>.py
Klasse: <Name> (Konstruktor OHNE Pflichtargumente -- Abhaengigkeiten holt
sich die Klasse selbst, z.B. ueber ConfigLoader)

## Methoden
- <methode1>(...) -> ...: <was sie tut>
- <methode2>(...) -> ...: <was sie tut>
(2-5 Methoden, nicht mehr -- ein groesserer Auftrag gehoert in mehrere
Bestellungen aufgeteilt)

## Abnahmekriterium (Pflicht)
Konkretes Beispiel, an dem sich objektiv pruefen laesst ob der gebaute Code
funktioniert:
Aufruf: <methode>(<Beispiel-Eingabe>)
Erwartet: <Beispiel-Ausgabe>

## Zielordner
<wohin das fertige Modul geliefert werden soll>
"""

_ZIEL_MUSTER = re.compile(r"^#{1,4}\s*Ziel\b", re.IGNORECASE | re.MULTILINE)
_DATEI_MUSTER = re.compile(r"(?:Datei:\s*)?\b[\w-]+\.py\b", re.IGNORECASE)
_KLASSE_MUSTER = re.compile(r"Klasse:\s*\w+|KLASSE:\s*\w+", re.IGNORECASE)
_ABNAHME_MUSTER = re.compile(
    r"^#{1,4}\s*(Abnahmekriterium|Akzeptanzkriterium)\b|Aufruf:.{0,200}Erwartet:",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)

_KRITERIEN_LABELS = {
    "ziel_beschreibung": "Ziel-Beschreibung (## Ziel -- was soll das Modul tun)",
    "kanonischer_name": "Kanonischer Dateiname (z.B. 'Datei: mein_modul.py')",
    "klasse": "Klassenname (z.B. 'Klasse: MeinModul')",
    "abnahmekriterium": "Maschinenpruefbares Abnahmekriterium (Aufruf + erwartetes Ergebnis)",
    "mindestlaenge": "Ausfuehrlichere Beschreibung (aktuell zu kurz fuer eine verwertbare Spezifikation)",
}


class SpecFormular:
    """Deterministische Vollstaendigkeits-Pruefung einer Bestellung gegen die
    4 Werft-Grundprinzipien (siehe spec_generator.py::COACH_PROMPT). Kein
    LLM-Call -- Ground Truth per Regex, wie planer.py's Widerspruchs-/
    Zerlegungs-Checks."""

    def __init__(self, min_laenge: int = 200):
        self._min_laenge = min_laenge

    def pruefe(self, spec_inhalt: str, kanonischer_name: str | None = None) -> dict:
        text = spec_inhalt or ""
        fehlend = []

        if not _ZIEL_MUSTER.search(text) and len(text.strip()) < self._min_laenge:
            fehlend.append("ziel_beschreibung")
        if not (kanonischer_name or _DATEI_MUSTER.search(text)):
            fehlend.append("kanonischer_name")
        if not _KLASSE_MUSTER.search(text):
            fehlend.append("klasse")
        if not _ABNAHME_MUSTER.search(text):
            fehlend.append("abnahmekriterium")
        if len(text.strip()) < self._min_laenge:
            fehlend.append("mindestlaenge")

        return {
            "vollstaendig": not fehlend,
            "fehlend": fehlend,
            "fehlend_beschriftet": [_KRITERIEN_LABELS[k] for k in fehlend],
        }

    def formular_vorschlag(self, spec_inhalt: str, fehlend: list[str]) -> str:
        """Baut eine an den vorhandenen Text angehaengte Rueckfrage: was ist
        schon da (woertlich zitiert), was fehlt noch (mit Vorlagen-Ausschnitt
        aus STANDARD_VORLAGE fuer genau die fehlenden Punkte)."""
        bloecke = []
        for key in fehlend:
            for abschnitt in STANDARD_VORLAGE.split("\n\n"):
                if key == "ziel_beschreibung" and abschnitt.lstrip().startswith("## Ziel\n"):
                    bloecke.append(abschnitt.strip())
                elif key in ("kanonischer_name", "klasse") and abschnitt.lstrip().startswith("## Kanonischer Name"):
                    if abschnitt.strip() not in bloecke:
                        bloecke.append(abschnitt.strip())
                elif key == "abnahmekriterium" and abschnitt.lstrip().startswith("## Abnahmekriterium"):
                    bloecke.append(abschnitt.strip())

        rueckfrage = (
            "PLANER: Diese Bestellung ist noch nicht eindeutig genug (G-10, "
            "Abschnitt 12.1) -- die Werft bewertet NICHT den Inhalt, nur ob "
            "die noetige Struktur da ist. Es fehlt:\n"
            + "\n".join(f"- {_KRITERIEN_LABELS[k]}" for k in fehlend)
            + "\n\nZur Ergaenzung, diese Abschnitte aus der Standard-Vorlage "
              "ausfuellen und an den bisherigen Text anhaengen:\n\n"
            + "\n\n".join(bloecke)
        )
        return rueckfrage


if __name__ == "__main__":
    formular = SpecFormular()
    ergebnis = formular.pruefe("Baue mir was Nettes.")
    print(ergebnis)
    print(formular.formular_vorschlag("Baue mir was Nettes.", ergebnis["fehlend"]))
