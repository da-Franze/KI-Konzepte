"""Router: Klassifikation von Anfragen in eine von 6 Kategorien
(Neustart-Spec Abschnitt 7.4). Betriebs-Rolle, nicht Teil des Gap->Merge-Zyklus.
"""
from configloader import config
from ollama_client import ask_model, host_fuer_rolle

KATEGORIEN = ("wissen", "code", "numerik", "web", "pipeline", "sicherheit")

PROMPT_TEMPLATE = """Klassifiziere diese Anfrage in GENAU eine Kategorie. Antworte NUR mit dem
Kategorie-Wort, sonst nichts.

Kategorien:
  wissen     -- Frage zu einer der Wissensdomaenen des Systems
  code       -- Bitte, Code zu schreiben, debuggen oder erklaeren
  numerik    -- Mathematische Berechnung, Formel auswerten
  web        -- Suche nach externen Quellen, aktuellen Infos
  pipeline   -- Frage zum System selbst, Status, Tasks, Gaps
  sicherheit -- Gefaehrliche, illegale oder grenzwertige Anfrage

Anfrage: {text}

Antworte NUR mit einem dieser Woerter.
"""


def klassifiziere(text: str) -> str:
    """Ordnet eine Anfrage einer der 6 Kategorien zu. Faellt auf 'wissen' zurueck,
    wenn das Modell (z.B. wegen leerer Antwort, siehe qwen3-Thinking-Mode-Bug)
    kein gueltiges Kategorie-Wort liefert -- Non-Invention-Policy: kein Raten
    ueber die 6 definierten Kategorien hinaus, aber auch kein Absturz."""
    # Fund 2026-08-24 (Mentor, Punkt 5 GPU-Haenger): Router hatte bisher
    # keinen eigenen Host-Eintrag und landete damit standardmaessig auf der
    # diskreten GPU -- zusammen mit Coder/Stratege/Theke (alle ohne eigenen
    # Host) fuehrte das zu 54 Eviction-Zyklen seit Boot (journalctl
    # ollama.service), weil bis zu 4 verschiedene Modelle dieselbe ~20GB-GPU
    # beanspruchten. qwen3:4b ist klein genug fuer die CPU-Instanz (Port
    # 11435, bereits fuer Analyst/Summarizer bewaehrt) -- entlastet die GPU
    # fuer Coder/Theke, ohne die Router-Klassifikation nennenswert zu bremsen.
    antwort = ask_model(
        PROMPT_TEMPLATE.format(text=text),
        model=config.get("ollama.model.router", "qwen3:4b"),
        temperature=0.0,
        num_predict=int(config.get("ollama.num_predict.router", 10)),
        host=host_fuer_rolle("router"),
    ).strip().lower()

    for kategorie in KATEGORIEN:
        if kategorie in antwort:
            return kategorie
    return "wissen"


if __name__ == "__main__":
    for beispiel in ["Was ist die Kammerad-Architektur?", "Schreib mir eine Sortierfunktion",
                      "Wie viele Tasks sind gerade offen?"]:
        print(f"{beispiel!r} -> {klassifiziere(beispiel)}")
