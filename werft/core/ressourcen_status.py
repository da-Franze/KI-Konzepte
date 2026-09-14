"""RessourcenStatus: Live-PU-Belegung der Werft im Blick (Modul 1 von
SPEC_RESSOURCEN_MANAGER.md).

Betreibers Kernsatz (2026-04-20, seither nie gebaut): "Instanz die Modell-Belegung
der 3 PUs im Auge hat." Dieses Modul setzt genau das um -- reine Beobachtung,
keine Entscheidung. Liest Ollamas eigenen `/api/ps`-Endpunkt (listet aktuell
geladene Modelle + expires_at), live verifiziert 2026-07-31 gegen diese Maschine.

Wirkungslos (nicht fehlerhaft) bei `llm.provider=api` -- dort gibt es kein
Ollama-`/api/ps`, geladene_modelle() liefert dann einfach eine leere Liste.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import requests

from configloader import config

from runtime_paths import WERFT_DB_PATH as DB_PATH


class RessourcenStatus:
    """Beobachtet, welche Ollama-Modelle gerade geladen sind. Konstruktor ohne
    Pflichtargumente -- liest Host selbst aus system_config (ConfigLoader)."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._host = config.get("ollama.host", "http://localhost:11434")
        self._db_path = db_path

    def geladene_modelle(self, host: str = None) -> list[dict]:
        """GET {host oder ollama.host}/api/ps -> Liste von {"name": ..., "expires_at": ...}.

        `host` optional (Modul 4b, SPEC_RESSOURCEN_MANAGER.md) -- Default bleibt
        `self._host` wie bisher, kein Bruch der bestehenden Modul-1-Nutzung.
        Erlaubt pu_uebersicht() dieselbe Methode fuer mehrere PU-Hosts wiederzuverwenden.

        Liefert bei jedem Fehler (Verbindung, Timeout, unerwartetes Format,
        llm.provider=api ohne Ollama) eine leere Liste statt eine Exception zu
        werfen -- reine Beobachtung darf den Aufrufer nie blockieren oder zum
        Absturz bringen."""
        ziel_host = host or self._host
        try:
            r = requests.get(
                f"{ziel_host}/api/ps",
                timeout=config.get("timeouts.debugger_host_sec"),
            )
            r.raise_for_status()
            modelle = r.json().get("models", [])
        except (requests.RequestException, ValueError):
            return []
        return [
            {"name": m.get("name", ""), "expires_at": m.get("expires_at")}
            for m in modelle
            if isinstance(m, dict)
        ]

    def pu_uebersicht(self) -> dict[str, list[dict]]:
        """Modul 4b (Betreiber-Klarstellung 2026-07-31): liest 'ollama.hosts.verfuegbar'
        aus system_config (von Ersteinrichtung.erkenne_pu_hosts() geschrieben) und
        fragt JEDEN PU-Host einzeln ab -- {"http://localhost:11434": [...], ...}.
        Host der nicht antwortet: leere Liste (gleiches Fehlerverhalten wie
        geladene_modelle()), kein Crash fuer die anderen Hosts. Leeres Dict wenn
        'ollama.hosts.verfuegbar' noch nie geschrieben wurde (Ersteinrichtung noch
        nicht gelaufen) -- kein Fehlerfall."""
        roh = config.get("ollama.hosts.verfuegbar", "")
        try:
            pu_hosts = json.loads(roh) if roh else {}
        except (ValueError, TypeError):
            return {}
        return {host: self.geladene_modelle(host=host) for host in pu_hosts.values()}

    def ist_geladen(self, modell: str, host: str = None) -> bool:
        """Kurzform: True wenn `modell` aktuell unter den geladenen Modellen ist.

        `host` optional (Modul 5, SPEC_RESSOURCEN_MANAGER.md) -- Default bleibt
        `self._host` wie bisher (kein Bruch bestehender Nutzung), erlaubt aber
        die Pruefung gegen die tatsaechliche PU einer Rolle, sobald diese per
        ollama_client.host_fuer_rolle() auf eine andere PU als die globale
        `ollama.host` geroutet ist (z.B. summarizer/analyst -> pu_2/CPU).

        Vergleicht sowohl mit als auch ohne ':latest'-Tag (Ollama haengt das
        implizit an, siehe gleiches Muster in ersteinrichtung.py, dort live
        2026-07-31 gefunden -- ein Modellname ohne Tag muss ein getaggtes
        Ergebnis trotzdem erkennen)."""
        if not modell:
            return False
        namen = {m["name"] for m in self.geladene_modelle(host=host)}
        return modell in namen or f"{modell}:latest" in namen

    def protokolliere_check(self, rolle: str, modell: str, host: str = None) -> bool:
        """Betreiber-Auftrag 2026-07-31 ("Modul 1 live in master_loop einbauen und
        beobachten"): prueft ob `modell` fuer `rolle` schon geladen ist (=kein
        Swap noetig) UND schreibt das Ergebnis in eine eigene Tabelle
        (ressourcen_beobachtung) -- Rohdaten fuer die in der Spec vorgesehene
        Frage, bevor Modul 3 (Swap-bewusste Umsortierung) gebaut wird: "kommen
        Swaps ueberhaupt haeufig genug vor, um die zusaetzliche Komplexitaet zu
        rechtfertigen?" Reine Beobachtung -- greift NICHT in den Ablauf ein,
        aendert nichts an der Rollen-Reihenfolge (das waere Modul 3).

        `host` optional (Modul 5) -- MUSS ab dem Zeitpunkt, an dem eine Rolle per
        ollama_client.host_fuer_rolle() auf eine eigene PU geroutet wird, mit
        angegeben werden, sonst misst diese Funktion am falschen Host vorbei
        und meldet faelschlich "Swap noetig", obwohl das Modell auf der
        tatsaechlich genutzten PU laengst geladen ist.

        Gibt bool zurueck (war_geladen) fuer optionales Debug-Logging durch den
        Aufrufer -- die eigentliche Aufzeichnung passiert bereits hier,
        Rueckgabewert ist ein Komfort-Nebenprodukt, kein Vertrag."""
        war_geladen = self.ist_geladen(modell, host=host)
        try:
            con = sqlite3.connect(
                self._db_path, timeout=config.get("timeouts.sqlite_lock_sec")
            )
            try:
                con.execute(
                    "CREATE TABLE IF NOT EXISTS ressourcen_beobachtung ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "rolle TEXT NOT NULL, modell TEXT NOT NULL, "
                    "war_geladen INTEGER NOT NULL, "
                    "zeitpunkt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
                # Migration (Modul 5, 2026-08-01): 'host'-Spalte nachgeruestet, damit
                # sich Beobachtungen von VOR und NACH der PU-Aufteilung unterscheiden
                # lassen -- ohne das waeren summarizer/analyst-Swap-Raten nach dem
                # Split nicht mehr ehrlich gegen die alte 98%-Baseline vergleichbar.
                try:
                    con.execute("ALTER TABLE ressourcen_beobachtung ADD COLUMN host TEXT")
                except sqlite3.OperationalError:
                    pass  # Spalte existiert schon (Migration bereits gelaufen)
                con.execute(
                    "INSERT INTO ressourcen_beobachtung (rolle, modell, war_geladen, host) VALUES (?, ?, ?, ?)",
                    (rolle, modell, int(war_geladen), host),
                )
                con.commit()
            finally:
                con.close()
        except sqlite3.Error:
            # Protokollierung darf den Aufrufer nie blockieren -- ein DB-Lock
            # o.ae. verhindert nicht den eigentlichen Rollen-Aufruf.
            pass
        return war_geladen


if __name__ == "__main__":
    # Reiner Lese-Smoke-Test -- kein Seiteneffekt, kein LLM-Call.
    rs = RessourcenStatus()
    geladen = rs.geladene_modelle()
    print("GELADENE MODELLE:", geladen)
    assert isinstance(geladen, list)
    if geladen:
        name = geladen[0]["name"]
        assert rs.ist_geladen(name), f"{name!r} sollte als geladen erkannt werden"
        assert not rs.ist_geladen("dieses-modell-gibt-es-sicher-nicht:0b")
        print(f"OK: {name!r} korrekt als geladen erkannt, Negativ-Fall korrekt verworfen.")
    else:
        print("OK: keine Modelle aktuell geladen (leere Liste, kein Fehler).")
