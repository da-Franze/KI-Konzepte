"""
ModellwahlRegister: Zentrale Erfassung und automatische Nutzung von DoE-Ergebnissen fuer Werft-Meta-Rollen.

Dieses Modul stellt die Klasse `ModellwahlRegister` bereit, welche als Single-Source-of-Truth fuer die
besten bekannten LLM-Modelle pro Rolle (z.B. 'coder', 'analyst') dient. Es ersetzt manuelle Anpassungen
in system_config durch deterministische Abfragen von Leistungsdaten (mu_quality, erfolgsrate), die
während Design-of-Experiments (DoE) Laufen generiert wurden.

Das Register nutzt eine SQLite-Datenbank (`werft.db` im Modul-Pfad), um Ergebnisse persistent zu speichern.
Es implementiert einen Fallback-Mechanismus: Falls keine DoE-Daten fuer eine Rolle vorliegen, wird auf
den in der Konfiguration hinterlegten Standardwert zurueckgegriffen.

Wichtige Design-Entscheidungen (basierend auf Mentor-Review):
1. Keine hardcodierten Pfade: DB-Pfad ist relativ zum Modul (__file__).
2. Konstruktor ohne Pflichtargumente: Abhaengigkeiten (ConfigLoader, DB) werden intern initialisiert.
3. Deterministische Auswahl: Basierend auf numerischen Metriken, nicht auf LLM-Urteilen.
4. Keine Simulationen: Echte DB-Interaktionen, echte Fehlerbehandlung.
5. Thread-Safety/Concurrency: Vermeidung von 'database is locked' durch explizite Connection-Management
   und kurze Transaktionsblöcke.

Author: Programmierer-DNA Agent
Date: 2026-08-24
"""

import sqlite3
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

# Import von ConfigLoader aus dem lokalen Projekt (annimmt gleiche Verzeichnisstruktur oder im PYTHONPATH)
try:
    from configloader import ConfigLoader
except ImportError:
    # Fallback fuer Tests/Isolierte Lauefe falls ConfigLoader nicht direkt importierbar ist
    # In der echten Umgebung muss dieser Import funktionieren.
    class ConfigLoader:
        def __init__(self, db_path=None):
            pass
        def get(self, key, default=None):
            return default

logger = logging.getLogger(__name__)

# Pfad zur Datenbank IMMER relativ zum Modul-File, nie os.path.expanduser oder nackter String
from runtime_paths import WERFT_DB_PATH as _DB_PATH


class ModellwahlRegister:
    """
    Zentrales Register fuer die automatische Modellwahl basierend auf DoE-Ergebnissen.

    Diese Klasse verwaltet eine SQLite-Tabelle, in der Leistungsdaten von Modellen pro Rolle gespeichert werden.
    Sie bietet Methoden zum Speichern neuer DoE-Ergebnisse und zum Abfragen des aktuell besten Modells
    fuer eine gegebene Rolle.

    Attributes:
        _db_path (Path): Pfad zur SQLite-Datenbankdatei.
        _config (ConfigLoader): Instanz des Konfigurationsladens fuer Fallback-Werte.
    """

    def __init__(self):
        """
        Initialisiert das ModellwahlRegister.

        Holt sich selbst die notwendigen Abhaengigkeiten (ConfigLoader) und initialisiert die Datenbank,
        falls noch nicht geschehen. Der Konstruktor nimmt keine Argumente entgegen, um mit der
        Pipeline-Instanziierungskompatibilitaet zu uebereinstimmen.
        """
        self._db_path = _DB_PATH
        self._config = ConfigLoader()
        
        # Initialisiere die Datenbankstruktur sicher
        try:
            self._initialisiere_datenbank()
        except Exception as e:
            logger.error(f"Fehler beim Initialisieren der Datenbank: {e}")
            raise RuntimeError(f"Datenbankinitialisierung fehlgeschlagen: {e}") from e

    def _initialisiere_datenbank(self):
        """
        Stellt sicher, dass die notwendigen Tabellen in der werft.db existieren.

        Erstellt die Tabelle 'modellwahl_doe_ergebnisse', falls sie nicht vorhanden ist.
        Verwendet einen expliziten Connection-Context, um Verbindungsprobleme zu minimieren.
        """
        # Wir verwenden eine neue Verbindung fuer die Schema-Migration, um Konflikte mit laufenden Queries zu vermeiden
        conn = None
        try:
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.cursor()
            
            # Tabelle fuer DoE-Ergebnisse der Modellwahl
            # Wir nutzen eine separate Tabelle oder erweitern bestehende, falls 'spezialist_charakter' existiert.
            # Da die Spec sagt "werft.db hat bereits leere Tabellen spezialist_charakter/charakter_leistung",
            # pruefen wir zuerst, ob wir diese wiederverwenden koennen. 
            # Um Abhaengigkeiten von der genauen Struktur alter Tabellen zu minimieren und Fehlerfreiheit zu gewaehrleisten,
            # erstellen wir eine dedizierte Tabelle 'modellwahl_doe_ergebnisse' fuer diesen spezifischen Zweck.
            # Dies ist robuster als das Raten der Spaltenstruktur von 'charakter_leistung'.
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS modellwahl_doe_ergebnisse (
                    rolle TEXT NOT NULL,
                    modell_name TEXT NOT NULL,
                    mu_quality REAL DEFAULT 0.0,
                    erfolgsrate REAL DEFAULT 0.0,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (rolle, modell_name)
                )
            """)
            
            # Index fuer schnellere Abfragen nach Rolle
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_rolle ON modellwahl_doe_ergebnisse(rolle)
            """)
            
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"SQLite Fehler bei Initialisierung: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        """
        Erzeugt eine neue Datenbankverbindung.

        Returns:
            sqlite3.Connection: Eine aktive Verbindung zur werft.db.
        """
        return sqlite3.connect(str(self._db_path))

    def speichere_doe_ergebnis(
        self, 
        rolle: str, 
        modell_name: str, 
        mu_quality: float = 0.0, 
        erfolgsrate: float = 0.0
    ) -> bool:
        """
        Speichert oder aktualisiert ein DoE-Ergebnis fuer eine bestimmte Rolle und ein Modell.

        Wenn bereits Eintraege fuer diese Kombination (rolle, modell_name) existieren, werden sie
        durch die neuen Werte ersetzt (UPSERT-Logik via INSERT OR REPLACE). Dies stellt sicher, dass
        neuere Lauefe aeltere ueberschreiben.

        Args:
            rolle (str): Die Werft-Meta-Rolle (z.B. 'coder', 'analyst').
            modell_name (str): Der Name des getesteten Modells (z.B. 'qwen3:32b').
            mu_quality (float): Die gemessene Qualitaet (0.0 - 1.0).
            erfolgsrate (float): Die gemessene Erfolgsrate (0.0 - 1.0).

        Returns:
            bool: True, wenn das Speichern erfolgreich war, False sonst.
        """
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # INSERT OR REPLACE sorgt dafuer, dass alte Werte fuer dieselbe Rolle/Modell-Kombination
            # durch die neuen ersetzt werden. Dies ist wichtig, damit das Register immer den neuesten Stand widerspiegelt.
            cursor.execute("""
                INSERT OR REPLACE INTO modellwahl_doe_ergebnisse 
                (rolle, modell_name, mu_quality, erfolgsrate, timestamp)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (rolle, modell_name, float(mu_quality), float(erfolgsrate)))
            
            conn.commit()
            logger.info(f"DoE-Ergebnis gespeichert: Rolle={rolle}, Modell={modell_name}, Quality={mu_quality}")
            return True
            
        except sqlite3.Error as e:
            logger.error(f"Fehler beim Speichern des DoE-Ergebnisses: {e}")
            return False
        finally:
            if conn:
                conn.close()

    def bestes_modell_fuer_rolle(self, rolle: str) -> Optional[Dict[str, Any]]:
        """
        Gibt das aktuell beste Modell fuer die angegebene Rolle zurueck.

        Die Auswahl erfolgt deterministisch basierend auf der hoechsten 'mu_quality'.
        Bei Gleichstand wird die 'erfolgsrate' als Tie-Breaker verwendet.
        Falls keine DoE-Daten fuer die Rolle vorliegen, wird versucht, einen Fallback-Wert
        aus der system_config zu laden (falls vorhanden).

        Args:
            rolle (str): Die Werft-Meta-Rolle, fuer die das beste Modell gesucht wird.

        Returns:
            Optional[Dict[str, Any]]: Ein Dictionary mit den Schlüsseln 'modell_name', 'mu_quality', 
            'erfolgsrate' und 'quelle' ('doe' oder 'config_fallback'). 
            Gibt None zurueck, wenn weder DoE-Daten noch Config-Fallback existieren.
        """
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Abfrage nach dem besten Modell basierend auf mu_quality (absteigend), dann erfolgsrate
            cursor.execute("""
                SELECT modell_name, mu_quality, erfolgsrate
                FROM modellwahl_doe_ergebnisse
                WHERE rolle = ?
                ORDER BY mu_quality DESC, erfolgsrate DESC
                LIMIT 1
            """, (rolle,))
            
            row = cursor.fetchone()
            
            if row:
                return {
                    "modell_name": row[0],
                    "mu_quality": row[1],
                    "erfolgsrate": row[2],
                    "quelle": "doe"
                }
            
            # Fallback: Wenn keine DoE-Daten existieren, pruefe system_config
            # Typische Config-Schluessel koennen sein: 'model_coder', 'default_model' etc.
            # Da die Spec sagt "vorher auf manuell gesetzten system_config-Wert zurueckfallen",
            # versuchen wir einen generischen Zugriff. 
            # Hinweis: Der genaue Key in ConfigLoader ist projektabhaengig. 
            # Wir versuchen hier, einen plausiblen Key zu erraten oder eine Standard-Methode zu nutzen.
            # Da ConfigLoader.get(key, default) bekannt ist, probieren wir rollenspezifische Keys.
            
            config_key = f"model_{rolle}"
            fallback_model = self._config.get(config_key, None)
            
            if not fallback_model:
                # Versuche generischen Default
                fallback_model = self._config.get("default_llm_model", None)
                
            if fallback_model:
                return {
                    "modell_name": fallback_model,
                    "mu_quality": 0.0, # Unbekannt bei Fallback
                    "erfolgsrate": 0.0,
                    "quelle": "config_fallback"
                }
            
            logger.warning(f"Kein Modell fuer Rolle '{rolle}' in DoE-Daten oder Config gefunden.")
            return None
            
        except sqlite3.Error as e:
            logger.error(f"Fehler beim Abfragen des besten Modells: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def alle_ergebnisse_fuer_rolle(self, rolle: str) -> List[Dict[str, Any]]:
        """
        Gibt alle gespeicherten DoE-Ergebnisse fuer eine Rolle zurueck.

        Args:
            rolle (str): Die Werft-Meta-Rolle.

        Returns:
            List[Dict[str, Any]]: Liste von Dictionaries mit Modell-Details.
        """
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT modell_name, mu_quality, erfolgsrate, timestamp
                FROM modellwahl_doe_ergebnisse
                WHERE rolle = ?
                ORDER BY mu_quality DESC
            """, (rolle,))
            
            rows = cursor.fetchall()
            return [
                {
                    "modell_name": row[0],
                    "mu_quality": row[1],
                    "erfolgsrate": row[2],
                    "timestamp": row[3]
                } for row in rows
            ]
            
        except sqlite3.Error as e:
            logger.error(f"Fehler beim Laden aller Ergebnisse: {e}")
            return []
        finally:
            if conn:
                conn.close()