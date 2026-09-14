"""Legt werft.db an: Schema fuer die Werft (WERFT_NEUSTART_SPEZIFIKATION.md Abschnitt 6+12.1).

Aufruf: python3 db_setup.py
Idempotent: CREATE TABLE IF NOT EXISTS, Bootstrap-Inserts nur falls Tabelle leer.
"""
import sqlite3
from runtime_paths import WERFT_DB_PATH as DB_PATH


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS task_pipeline (
    task_id         TEXT PRIMARY KEY,
    status          TEXT DEFAULT 'pending',
    assigned_agent  TEXT,
    knowledge_links TEXT,
    priority        INTEGER DEFAULT 1,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS systemmap (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ziel_ordner      TEXT NOT NULL,
    kanonischer_name TEXT NOT NULL,
    klasse           TEXT,
    methoden         TEXT,
    dict_schluessel  TEXT,
    ruft_auf         TEXT,
    bestellung_id    TEXT,
    aktualisiert_am  DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ziel_ordner, kanonischer_name)
);

CREATE TABLE IF NOT EXISTS call_graph_kanten (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    von_datei        TEXT NOT NULL,
    von_methode      TEXT NOT NULL,
    nach_datei       TEXT NOT NULL,
    nach_methode     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'unsicher',
    quelle           TEXT,
    task_id          TEXT,
    aktualisiert_am  DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(von_datei, von_methode, nach_datei, nach_methode)
);

CREATE TABLE IF NOT EXISTS system_gaps (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    gap_key      TEXT UNIQUE,
    description  TEXT,
    severity     INTEGER,
    detected_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    resolved     BOOLEAN DEFAULT 0
);

CREATE TABLE IF NOT EXISTS specialists_v8 (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT UNIQUE,
    role_description TEXT,
    module_path      TEXT,
    active           BOOLEAN DEFAULT 1,
    last_seen        DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS system_config (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    config_key     TEXT UNIQUE,
    config_value   TEXT,
    config_type    TEXT,
    category       TEXT,
    description    TEXT,
    last_modified  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS werft_memory (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    owner        TEXT,
    memory_type  TEXT,
    content      TEXT,
    tags         TEXT,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Abschnitt 12.1: Bestellungs-Eingang (jetzt schon angelegt, damit ein
-- laufendes Zielprojekt spaeter ohne Migration als Bestellung reinkommen
-- kann; wird in Phase A nicht befuellt)
CREATE TABLE IF NOT EXISTS bestellungen (
    bestellung_id   TEXT PRIMARY KEY,
    titel           TEXT NOT NULL,
    spec_pfad       TEXT,
    spec_inhalt     TEXT,
    quelle          TEXT,
    ziel_ordner     TEXT NOT NULL,
    status          TEXT DEFAULT 'eingegangen',
    kanonischer_name TEXT,  -- z.B. "router_agent.py" -- Fund 2026-07-15 (Betreiber):
                            -- ohne festen Zielnamen heissen ausgelieferte Module
                            -- immer "bestellung_<id>.py", was saubere Imports
                            -- zwischen Teilen der Spec-Serie verhindert (siehe
                            -- Systemmap-Konzept, spezifikation/serie_kern/00_systemmap.md)
    formular_vorschlag TEXT,  -- Planer-Rueckfrage bei struktureller Unvollstaendigkeit
                            -- (spec_formular.py, Betreiber-Auftrag 2026-07-27 Tisch 15:10)
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- WERFT_V8_3_SPEZIFIKATION.md Abschnitt 3.2/7: Mentor-Freigabeprozess.
-- Jede architekturrelevante Aenderung (neue Rolle, neue Tabelle, geaenderte
-- Pipeline-Logik) wird hier eingetragen und wirkt erst nach approved_by='Betreiber'.
-- Reine Bugfixes an bereits Freigegebenem brauchen keinen neuen Eintrag.
CREATE TABLE IF NOT EXISTS mentor_actions_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type       TEXT,
    beschreibung      TEXT,
    vorgeschlagen_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    status            TEXT DEFAULT 'pending_review',
    approved_by       TEXT,
    approved_at       DATETIME
);

-- ERWEITERUNG_5_ROLLE_DEBUGGER.md Abschnitt 2.3: Lebenszyklus fuer wiederholte
-- Audit-/Learning-Befunde -- verhindert das 04-11-Muster (7x identischer
-- Befund am selben Tag, nie umgesetzt).
CREATE TABLE IF NOT EXISTS debugger_findings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    quelle         TEXT,
    befund_key     TEXT,
    beschreibung   TEXT,
    erste_meldung  DATETIME DEFAULT CURRENT_TIMESTAMP,
    letzte_meldung DATETIME DEFAULT CURRENT_TIMESTAMP,
    wiederholungen INTEGER DEFAULT 1,
    status         TEXT DEFAULT 'offen',
    eskaliert_an   TEXT
);

-- WERFT_NEUSTART_SPEZIFIKATION.md Abschnitt 13: LLM-as-Judge-Wichtigkeits-
-- bewertung JEDES offenen Deltas, mit Pflicht-Begruendung (Erfolgskriterium §18).
CREATE TABLE IF NOT EXISTS judge_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    gap_key      TEXT,
    wichtigkeit  INTEGER,
    warum        TEXT NOT NULL,
    bewertet_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Abschnitt 14: Charakter-System, EIN Schema (additive Ergaenzung -- specialists_v8
-- bleibt als bereits DoE-verifizierte Betriebstabelle unangetastet, siehe STATUS.md
-- "Bekannte Abweichung"; volle Konsolidierung ist als Folgeschritt dokumentiert,
-- keine blinde Migration der getesteten Kern-Rollen in dieser Session).
CREATE TABLE IF NOT EXISTS spezialist_charakter (
    charakter_id      TEXT PRIMARY KEY,
    rolle             TEXT NOT NULL,
    dna_basis         TEXT NOT NULL,
    koennen           TEXT,
    llm_params        TEXT,
    private_vdb_zone  TEXT,
    aktives_modell    TEXT,
    updated_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS charakter_leistung (
    charakter_id       TEXT NOT NULL,
    modell             TEXT NOT NULL,
    n_samples          INTEGER DEFAULT 0,
    mu_quality         REAL,
    sigma_quality      REAL,
    cpk_quality        REAL,
    cpk_status         TEXT DEFAULT 'insufficient_data',
    updated_at         TEXT DEFAULT CURRENT_TIMESTAMP,
    n_aufrufe          INTEGER DEFAULT 0,
    avg_dauer_sekunden REAL,
    kumulierte_kosten  REAL DEFAULT 0,
    PRIMARY KEY (charakter_id, modell)
);

-- ERWEITERUNG_6_ROLLE_STRATEGE.md: Arbeitsstrukturierung + Reissbrett als
-- Datenstruktur (statt nur Markdown), R&D-Versuchs-Protokoll.
CREATE TABLE IF NOT EXISTS strategie_planung (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ueber_bestellung  TEXT,
    teil_nr           INTEGER,
    kanonischer_name  TEXT NOT NULL,
    geplante_klasse   TEXT,
    geplante_methoden TEXT,
    abhaengigkeiten   TEXT,
    status            TEXT DEFAULT 'geplant',
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rnd_versuche (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id              TEXT,
    charakter_standard   TEXT,
    charakter_experiment TEXT,
    ergebnis             TEXT,
    gemessener_vorteil   TEXT,
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Fund 2026-07-15 (beispielserie_teil5): ein Funktions-Registry-Eintrag aus
-- einem SPAETER als fehlerhaft erkannten Merge blieb sonst dauerhaft als
-- "aehnliche fruehere Loesung" abrufbar und widersprach der Fehlhistorie-Korrektur.
-- Trackt den letzten VDB-Punkt pro Ziel, damit er vor einem neuen Eintrag
-- geloescht (invalidiert) werden kann.
CREATE TABLE IF NOT EXISTS funktions_registry_punkte (
    target_name TEXT PRIMARY KEY,
    point_id    TEXT NOT NULL,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS product_versions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name      TEXT NOT NULL,
    version           TEXT NOT NULL,
    bestellung_id     TEXT,
    specification     TEXT NOT NULL,
    code_hash         TEXT NOT NULL,
    code_path         TEXT NOT NULL,
    project_map_path  TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'verified',
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_name, version)
);

CREATE TABLE IF NOT EXISTS coder_dna_regeln (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    regel_text    TEXT NOT NULL,
    ausgeloest_von TEXT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_product_versions_name
    ON product_versions(product_name, created_at DESC);

CREATE TABLE IF NOT EXISTS project_map_versions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name      TEXT NOT NULL,
    cycle             INTEGER NOT NULL,
    expected_map_id   TEXT,
    actual_map_id     TEXT,
    expected_map      TEXT NOT NULL,
    actual_map        TEXT NOT NULL,
    map_diff          TEXT NOT NULL,
    task_regions      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'beobachtet',
    approval          TEXT NOT NULL DEFAULT 'freigabe_noetig',
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_project_map_versions_project
    ON project_map_versions(project_name, created_at DESC);
"""

BOOTSTRAP_CONFIG = [
    ("ollama.host", "http://localhost:11434", "string", "ollama"),
    ("ollama.host.fallback", "http://localhost:11434", "string", "ollama"),
    ("ollama.discovery_ports", "11434,11435,11436,11437", "string", "ollama"),
    ("ollama.model.coder", "qwen3:32b", "string", "ollama"),
    ("ollama.model.analyst", "qwen3:14b", "string", "ollama"),
    ("ollama.model.summarizer", "qwen3:14b", "string", "ollama"),
    # qwen3:4b/8b liefern mit 'think:false' weiterhin leere/abgebrochene Antworten
    # (reproduziert 2026-07-09, deckt sich mit 687 historischen DoE-Experimenten:
    # score_total=0.0 durchgehend fuer beide Modelle). qwen2.5:1.5b bestand einen
    # isolierten Kurztest, versagte aber am tatsaechlichen (laengeren) Router-Prompt
    # (klassifizierte alles als 'pipeline') -- End-to-End-Test statt Kurztest zaehlt.
    # qwen3:14b bestand den vollen Prompt korrekt fuer alle 3 Testfaelle.
    ("ollama.model.router", "qwen3:14b", "string", "ollama"),
    ("ollama.model.embed", "qwen3-embedding:0.6b", "string", "ollama"),
    ("ollama.num_ctx", "32768", "int", "ollama"),
    ("ollama.num_predict.router", "10", "int", "ollama"),
    ("ollama.num_predict.analyst", "900", "int", "ollama"),
    ("ollama.num_predict.coder", "6144", "int", "ollama"),
    ("ollama.num_predict.stratege", "800", "int", "ollama"),
    ("pipeline.max_retry_spec_failed", "8", "int", "pipeline"),
    # v8.3 Abschnitt 6.2: zweite Sicherung gegen Task-Explosion (Coordinator-Bug,
    # 587 Tasks fuer einen Gap in 2h) -- unabhaengig von der Redundanz-Logik selbst.
    ("pipeline.max_tasks_per_gap", "3", "int", "pipeline"),
    # Qdrant-Anbindung (Wissens-Schicht, Betreiber-Korrektur -- siehe Plan-Kontext).
    ("qdrant.host", "http://localhost:6333", "string", "qdrant"),
    ("qdrant.collection", "werft_wissen_1024", "string", "qdrant"),
    ("qdrant.vector_size", "1024", "int", "qdrant"),
    # Registrierte, VERFUEGBARE Fine-Tunes (bestehende Lernzyklen) fuer die
    # Bestellungs-Ebene (Phase B) -- NICHT fuer die 4 Werft-Kernrollen selbst,
    # siehe Ablaufdiagramm-Begruendung im Plan (Domaenen-Neutralitaet, Erfolgs-
    # kriterium #6). Rein informativ in Phase A, noch nicht von Code gelesen.
    ("charakter.available.mentor-14b", "trainiert auf der Mentor-Rolle des Zielprojekts", "string", "charakter"),
    ("charakter.available.mentor-gemma", "trainiert auf der Mentor-Rolle des Zielprojekts (gemma-Basis)", "string", "charakter"),
    ("charakter.available.qwen2.5-projekt-v3", "trainiert auf dem Betrieb eines konkreten Zielprojekts", "string", "charakter"),
    # ERWEITERUNG_5_ROLLE_DEBUGGER.md Abschnitt 4
    ("ollama.model.debugger", "qwen3:14b", "string", "ollama"),
    ("debugger.eskalations_schwelle", "3", "int", "debugger"),
    ("debugger.stall_schwelle_minuten", "60", "int", "debugger"),
    ("debugger.ausfuehrungs_timeout_sekunden", "15", "int", "debugger"),
    # WERFT_NEUSTART_SPEZIFIKATION.md Abschnitt 13
    ("ollama.model.judge", "qwen3:14b", "string", "ollama"),
    # ERWEITERUNG_6_ROLLE_STRATEGE.md
    ("ollama.model.stratege", "qwen3:14b", "string", "ollama"),
    ("stratege.rnd_pareto_schnitt_prozent", "20", "int", "stratege"),
    ("stratege.rnd_mindest_stichprobe", "10", "int", "stratege"),
    ("stratege.max_features_pro_teil", "6", "int", "stratege"),
]

BOOTSTRAP_SPECIALISTS = [
    ("Analyst", "Erkennt Luecken (Soll/Ist) und prueft fertigen Code gegen die Spec (Taufe)", None),
    ("Summarizer", "Verwandelt eine Gap-Beschreibung in ein konkretes Lastenheft", None),
    ("Coder", "Schreibt den tatsaechlichen Python-Code aus der Spec", None),
    ("Router", "Waehlt pro Task/Anfrage den richtigen Spezialisten", None),
]


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript(SCHEMA)

        con.execute(
            "INSERT OR IGNORE INTO system_config "
            "(config_key, config_value, config_type, category, description) "
            "VALUES ('ollama.host.fallback', 'http://localhost:11434', "
            "'string', 'ollama', 'Zentraler Fallback fuer nicht erreichbare Rollenhosts')"
        )
        con.execute(
            "INSERT OR IGNORE INTO system_config "
            "(config_key, config_value, config_type, category, description) "
            "VALUES ('ollama.discovery_ports', '11434,11435,11436,11437', "
            "'string', 'ollama', 'Kommaseparierte Ports fuer die Ollama-Erkennung')"
        )

        cur = con.execute("SELECT COUNT(*) FROM system_config")
        if cur.fetchone()[0] == 0:
            con.executemany(
                "INSERT INTO system_config (config_key, config_value, config_type, category) "
                "VALUES (?, ?, ?, ?)",
                BOOTSTRAP_CONFIG,
            )

        cur = con.execute("SELECT COUNT(*) FROM specialists_v8")
        if cur.fetchone()[0] == 0:
            con.executemany(
                "INSERT INTO specialists_v8 (name, role_description, module_path) VALUES (?, ?, ?)",
                BOOTSTRAP_SPECIALISTS,
            )

        cur = con.execute("SELECT COUNT(*) FROM mentor_actions_log")
        if cur.fetchone()[0] == 0:
            con.execute(
                "INSERT INTO mentor_actions_log (action_type, beschreibung, status, approved_by, approved_at) "
                "VALUES (?, ?, 'approved', 'Betreiber', CURRENT_TIMESTAMP)",
                (
                    "neue_rolle",
                    "Phase-A-Werft-Bau (4 Kernrollen + Wissens-Schicht) laut Plan "
                    "swift-puzzling-feigenbaum.md, per ExitPlanMode freigegeben",
                ),
            )

        # Die separierte Werft verwendet qwen3-embedding (1024 Dimensionen).
        # Alte Werft-Defaults zeigen auf nomic/768 und bleiben als alte
        # Collection erhalten, werden aber nicht weiter als aktive Defaults
        # verwendet.
        con.execute(
            "UPDATE system_config SET config_value='qwen3-embedding:0.6b' "
            "WHERE config_key='ollama.model.embed' AND config_value='nomic-embed-text'"
        )
        con.execute(
            "UPDATE system_config SET config_value='werft_wissen_1024' "
            "WHERE config_key='qdrant.collection' AND config_value='werft_wissen'"
        )
        con.execute(
            "INSERT OR IGNORE INTO system_config "
            "(config_key, config_value, config_type, category) "
            "VALUES ('qdrant.vector_size', '1024', 'int', 'qdrant')"
        )

        # Auch bestehende Runtime-Datenbanken erhalten nachtraeglich neue
        # zentrale Defaults, ohne bereits gesetzte Werte zu ueberschreiben.
        con.executemany(
            "INSERT OR IGNORE INTO system_config "
            "(config_key, config_value, config_type, category) VALUES (?, ?, ?, ?)",
            BOOTSTRAP_CONFIG,
        )

        con.commit()
        print(f"werft.db bereit: {DB_PATH}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
