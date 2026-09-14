"""Ersteinrichtung: Hardware-Erkennung + Modell-/Provider-Wahl fuer einen Kaltstart
der Werft auf beliebiger Ziel-Hardware.

Betreiber-Auftrag 2026-07-31 (Tisch/Chat): "ein zip, dass ich entpacken kann auf jedem
x-beliebigen Rechner und einen Kaltstart machen [kann]. ein interface mit dem ich
kommunizieren kann, das mir bei der Ersteinrichtung auf der Ziel-HW hilft mit ggf.
lokalen und API-Modellen und mit mir die ersten Specs aufsetzen kann." Die
Provider-Umschaltung selbst (Ollama lokal <-> OpenAI-kompatible API) existiert
bereits (ollama_client.py, Fund 2026-07-27 -- exakt fuer dasselbe Bruder-Szenario
gebaut: "dessen Hardware nur 4b/8b lokale Modelle schafft"). Dieses Modul liefert
den fehlenden Schritt DAVOR: welche Modelle/welcher Provider passt zur tatsaechlich
vorgefundenen Hardware -- und schreibt das Ergebnis nach system_config
(Zero-Hardcoding, G-4), sodass ask_model() es ohne weitere Codeaenderung nutzt.

Hardware-Scan + Tier-Empfehlung sind deterministisch, kein LLM-Call (wie
spec_formular.SpecFormular) -- Hardware-Fakten sind Ground Truth, kein
Bewertungsfall. Einzige Ausnahme: bench_modell() (Modul 2, SPEC_RESSOURCEN_
MANAGER.md) misst echte Performance via einem kurzen, bewusst simplen
LLM-Testaufruf -- rein informativ (nur wenn mit_benchmark=True angefordert),
trifft selbst KEINE automatische Tier-Entscheidung (siehe dortiger Docstring:
kein unbelegter Schwellenwert, Lehre aus dem gescheiterten RAG-Score-Schwellen-
Versuch derselben Session -- lieber Messwerte transparent zeigen als eine
Zahl erfinden, die sich nicht empirisch rechtfertigen laesst).
"""
import os
import platform
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional

import requests

from configloader import config
from runtime_paths import WERFT_DB_PATH as DB_PATH

# Modell-Tiers, orientiert an bereits anderswo gelebten Grenzen (CLAUDE.md
# BEKANNTE FEHLER-MUSTER: "ollama.model.default.cpu=7b"; Coder-Default-Wechsel
# zu gpt-oss:20b 2026-07-15 nach R&D-Studie). Bewusst konservativ gewaehlt --
# lieber ein Modell, das sicher laeuft, als eins, das swapt/abstuerzt. Der
# Nutzer sieht die Empfehlung im Interface und kann vor der Uebernahme
# abweichen (siehe spec_generator.py /setup/uebernehmen).
MODELL_TIERS = {
    "gross": {
        "coder": "gpt-oss:20b", "analyst": "qwen3:14b", "summarizer": "qwen3:14b",
        "router": "qwen3:14b", "stratege": "qwen3:14b", "theke": "gpt-oss:20b",
    },
    "mittel": {
        "coder": "qwen3:8b", "analyst": "qwen3:8b", "summarizer": "qwen3:8b",
        "router": "qwen3:4b", "stratege": "qwen3:8b", "theke": "qwen3:8b",
    },
    "klein": {
        "coder": "qwen3:4b", "analyst": "qwen3:4b", "summarizer": "qwen3:4b",
        "router": "qwen3:4b", "stratege": "qwen3:4b", "theke": "qwen3:4b",
    },
}
EMBED_MODELL = "qwen3-embedding:0.6b"  # 1024 Dimensionen; eigene Collection verhindert Altbestand-Mischung
ROLLEN = ("coder", "analyst", "summarizer", "router", "stratege", "theke")


def _lese_ram_gb() -> Optional[float]:
    """Liest MemTotal aus /proc/meminfo (Linux). None auf anderen Systemen/Fehlern."""
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
        treffer = re.search(r"MemTotal:\s+(\d+)\s+kB", text)
        if treffer:
            return round(int(treffer.group(1)) / 1024 / 1024, 1)
    except OSError:
        pass
    return None


def _lese_gpu_vram_gb() -> tuple[Optional[str], Optional[float]]:
    """Versucht NVIDIA (nvidia-smi), dann AMD (rocm-smi) VRAM zu lesen. Gibt
    (hersteller, vram_gb) zurueck, (None, None) wenn keine GPU erkennbar/kein
    Tool vorhanden -- dann faellt empfehle_modelle() auf RAM-basierte Tiers
    zurueck (CPU-Inferenz)."""
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True,
                timeout=config.get("timeouts.setup_command_sec"), check=True,
            ).stdout.strip().splitlines()
            if out:
                return "nvidia", round(int(out[0].strip()) / 1024, 1)
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
    if shutil.which("rocm-smi"):
        try:
            out = subprocess.run(
                ["rocm-smi", "--showmeminfo", "vram", "--csv"],
                capture_output=True, text=True,
                timeout=config.get("timeouts.setup_command_sec"), check=True,
            ).stdout
            # Fund 2026-07-31 (Mentor, Live-Test auf Mentor-Referenz-PC/RX 7900 XTX):
            # --csv liefert eine Kopfzeile + eine Zeile PRO Karte (dGPU+iGPU
            # getrennt) + eine leere Abschlusszeile. Ein naiver "letzte Zeile"-
            # Zugriff traf hier die leere Abschlusszeile (Bug, live gefunden)
            # UND haette bei mehreren Karten ohnehin oft die kleinere/iGPU
            # erwischt. Richtig: alle Karten-Zeilen parsen, MAX der "VRAM Total
            # Memory (B)"-Spalte nehmen (die kapitalste Karte bestimmt, was
            # lokal moeglich ist).
            zeilen = [z for z in out.strip().splitlines() if z.strip()]
            if len(zeilen) >= 2:
                header = zeilen[0].split(",")
                idx = next(
                    (i for i, spalte in enumerate(header) if "VRAM Total Memory" in spalte),
                    None,
                )
                if idx is not None:
                    werte = []
                    for zeile in zeilen[1:]:
                        felder = zeile.split(",")
                        if len(felder) > idx and felder[idx].strip().isdigit():
                            werte.append(int(felder[idx].strip()))
                    if werte:
                        return "amd", round(max(werte) / 1024**3, 1)
        except (subprocess.SubprocessError, ValueError, OSError, IndexError):
            pass
    return None, None


def _lese_ollama_modelle() -> list[str]:
    """Best-effort: bereits lokal vorhandene Ollama-Modelle (leere Liste wenn
    Ollama nicht installiert/nicht erreichbar -- kein harter Fehler, die
    Ersteinrichtung soll auch OHNE laufendes Ollama einen Scan liefern koennen)."""
    if not shutil.which("ollama"):
        return []
    try:
        out = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True,
            timeout=config.get("timeouts.ollama_list_sec"), check=True,
        ).stdout
        zeilen = out.strip().splitlines()[1:]  # erste Zeile ist die Kopfzeile
        return [zeile.split()[0] for zeile in zeilen if zeile.strip()]
    except (subprocess.SubprocessError, OSError, IndexError):
        return []


class Ersteinrichtung:
    """Kaltstart-Assistent: erkennt Ziel-Hardware, empfiehlt Modelle/Provider,
    schreibt die Wahl nach system_config. Konstruktor ohne Pflichtargumente --
    Abhaengigkeiten (DB-Pfad) sind Default-Werte, ueberschreibbar fuer Tests."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path

    def scanne_hardware(self) -> dict:
        """Rein lesend: GPU (Hersteller+VRAM falls vorhanden), RAM, CPU-Kerne,
        freier Plattenplatz, ob Ollama installiert ist + welche Modelle schon
        lokal vorhanden sind. Funktioniert auch ohne GPU/ohne Ollama."""
        gpu_hersteller, gpu_vram_gb = _lese_gpu_vram_gb()
        ram_gb = _lese_ram_gb()
        _, _, frei_bytes = shutil.disk_usage(Path.home())
        return {
            "os": platform.system(),
            "cpu_kerne": os.cpu_count() or 1,
            "ram_gb": ram_gb,
            "gpu_hersteller": gpu_hersteller,
            "gpu_vram_gb": gpu_vram_gb,
            "disk_frei_gb": round(frei_bytes / 1024**3, 1),
            "ollama_installiert": shutil.which("ollama") is not None,
            "ollama_modelle_vorhanden": _lese_ollama_modelle(),
        }

    def erkenne_pu_hosts(self, kandidaten_ports: Optional[tuple] = None,
                          basis_host: Optional[str] = None) -> dict:
        """Modul 4a aus SPEC_RESSOURCEN_MANAGER.md (Betreiber-Klarstellung 2026-07-31: die
        eigentliche Kernidee war PU-Routing -- GPU/CPU/iGPU/extern -- nicht nur
        Modell-Swap innerhalb einer PU). Prueft welche Kandidaten-Ports einen
        erreichbaren Ollama-Endpunkt haben (GET /api/tags), OHNE zu behaupten welche
        Hardware-Art dahintersteckt -- eine reine Port-Erreichbarkeitspruefung kann
        GPU/CPU/iGPU nicht zuverlaessig unterscheiden (dafuer braeuchte es einen
        echten Lastvergleich, siehe bench_modell() fuer den naechsten Schritt).
        Generische Namen (pu_1, pu_2, ...) statt geratener Labels -- ehrlicher als
        eine Falschbehauptung.

        Gibt {} zurueck wenn kein einziger Port antwortet (z.B. Ollama nicht
        installiert) -- kein Fehlerfall, nur eine leere PU-Liste."""
        if kandidaten_ports is None:
            kandidaten_ports = tuple(
                int(port.strip())
                for port in str(config.get(
                    "ollama.discovery_ports", "11434,11435,11436,11437"
                )).split(",")
                if port.strip()
            )
        if basis_host is None:
            basis_host = str(config.get("ollama.host", "http://localhost:11434"))
            basis_host = basis_host.rsplit(":", 1)[0]

        gefunden = {}
        idx = 1
        for port in kandidaten_ports:
            try:
                r = requests.get(
                    f"{basis_host}:{port}/api/tags",
                    timeout=config.get("timeouts.ollama_probe_sec"),
                )
                if r.status_code == 200:
                    gefunden[f"pu_{idx}"] = f"{basis_host}:{port}"
                    idx += 1
            except requests.RequestException:
                continue
        return gefunden

    def bench_modell(self, modell: str, num_predict: int = 50,
                     host: Optional[str] = None) -> dict:
        """Misst echte Performance eines bereits lokal vorhandenen Ollama-Modells
        (Modul 2 aus SPEC_RESSOURCEN_MANAGER.md, Betreiber-Go 2026-07-31). Nutzt
        Ollamas EIGENE Timing-Felder aus der Antwort (eval_count/eval_duration/
        total_duration) statt selbst zu stoppen -- kein Streaming noetig, kein
        zusaetzliches Timing-Risiko. Standard-Prompt bewusst kurz und simpel
        gehalten (wie SPEC_HW_DETECT_BENCH.md's Vorlaeufer-Konvention 2026-04-27:
        "kurzer Python-Code-Generierungs-Task"), damit das Ergebnis zwischen
        Modellen vergleichbar bleibt.

        Gibt bei jedem Fehler (Modell nicht vorhanden, Timeout, Verbindungsfehler)
        {"tokens_pro_sekunde": 0.0, "time_to_first_token_ms": None, "fehler": "..."}
        zurueck statt eine Exception zu werfen -- ein einzelner fehlgeschlagener
        Benchmark darf die gesamte Ersteinrichtung nie zum Absturz bringen.

        WICHTIG: trifft selbst keine Tier-Entscheidung. Liefert nur Messwerte --
        die Interpretation (was ist "schnell genug") bleibt bewusst beim
        Menschen, siehe Modul-Docstring."""
        if host is None:
            host = str(config.get("ollama.host", "http://localhost:11434"))
        prompt = "Schreibe eine kurze Python-Funktion, die zwei Zahlen addiert."
        try:
            r = requests.post(
                f"{host}/api/chat",
                json={
                    "model": modell,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "think": False,
                    "keep_alive": "5m",
                    "options": {"num_predict": num_predict, "temperature": 0.1},
                },
                timeout=config.get("timeouts.benchmark_sec"),
            )
            r.raise_for_status()
            daten = r.json()
        except (requests.RequestException, ValueError) as exc:
            return {"tokens_pro_sekunde": 0.0, "time_to_first_token_ms": None, "fehler": str(exc)}

        eval_count = daten.get("eval_count", 0)
        eval_duration_ns = daten.get("eval_duration", 0)
        total_duration_ns = daten.get("total_duration", 0)

        if not eval_count or not eval_duration_ns:
            return {
                "tokens_pro_sekunde": 0.0, "time_to_first_token_ms": None,
                "fehler": "Ollama-Antwort ohne eval_count/eval_duration (Modell nicht vorhanden?)",
            }

        tokens_pro_sekunde = round(eval_count / (eval_duration_ns / 1e9), 1)
        # Zeit bis der erste Antwort-Token generiert wird = alles VOR der reinen
        # Generierungsphase (Laden + Prompt-Verarbeitung) -- ohne Streaming die
        # naechstbeste, ehrliche Naeherung an "time to first token" (kein
        # erfundener Wert, direkt aus Ollamas eigenen Feldern abgeleitet).
        ttft_ms = round((total_duration_ns - eval_duration_ns) / 1e6, 1)

        return {"tokens_pro_sekunde": tokens_pro_sekunde, "time_to_first_token_ms": ttft_ms, "fehler": None}

    def empfehle_modelle(self, scan: dict, mit_benchmark: bool = False) -> dict:
        """Leitet aus dem Scan eine Tier-Empfehlung ab. Massstab ist VRAM falls
        eine GPU erkannt wurde (Schwellen 16/8 GB), sonst RAM fuer reine
        CPU-Inferenz (hoehere Schwellen 32/16 GB, da CPU-Inferenz mehr
        Speicher-Headroom braucht UND langsamer ist). Unterhalb jeder Schwelle
        bzw. wenn Ollama gar nicht installiert ist: 'api_empfohlen' statt eines
        Tiers -- ein 4b-Modell auf <8GB RAM ohne GPU liefe zwar theoretisch,
        aber zusammen mit OS+Werft-Prozessen realistisch nicht stabil."""
        gpu_vram_gb = scan.get("gpu_vram_gb")
        ram_gb = scan.get("ram_gb")

        if gpu_vram_gb is not None:
            massstab, schwelle_gross, schwelle_mittel, basis = gpu_vram_gb, 16, 8, "VRAM"
        else:
            massstab, schwelle_gross, schwelle_mittel, basis = (ram_gb or 0), 32, 16, "RAM"

        api_empfohlen = (not scan.get("ollama_installiert")) or massstab < (schwelle_mittel / 2)

        if api_empfohlen:
            tier = "api_empfohlen"
            modelle = {}
            begruendung = (
                f"Kein Ollama installiert oder {basis} zu gering ({massstab} GB, "
                f"Mindestwert fuer lokale Modelle: {schwelle_mittel / 2} GB) -- "
                f"API-Provider empfohlen statt lokaler Modelle."
            )
        else:
            tier = "gross" if massstab >= schwelle_gross else (
                "mittel" if massstab >= schwelle_mittel else "klein"
            )
            modelle = dict(MODELL_TIERS[tier])
            begruendung = f"{basis}={massstab} GB -> Tier '{tier}'."

        modelle_final = dict(modelle)
        if tier != "api_empfohlen":
            modelle_final["embed"] = EMBED_MODELL

        # Fund 2026-07-31 (Live-Test): 'ollama list' haengt implizite ':latest'-
        # Tags an (z.B. 'nomic-embed-text:latest'), unsere Tier-Tabellen nutzen
        # aber die kurze Form ohne Tag -- ohne Normalisierung wuerde ein
        # tatsaechlich vorhandenes Modell faelschlich als fehlend gemeldet.
        roh = scan.get("ollama_modelle_vorhanden", [])
        vorhandene = set(roh) | {m.split(":")[0] if ":" in m and m.endswith(":latest") else m for m in roh}
        fehlende_downloads = sorted(
            m for m in modelle_final.values()
            if m and m not in vorhandene and f"{m}:latest" not in vorhandene
        ) if tier != "api_empfohlen" else []

        benchmark = {}
        if mit_benchmark and tier != "api_empfohlen":
            # Nur bereits lokal vorhandene Modelle messen -- kein Download anstossen,
            # keine Doppelarbeit fuer Rollen die dasselbe Modell teilen. 'embed' bewusst
            # ausgeschlossen: Live-Test 2026-07-31 zeigte, dass ein reines Embedding-
            # Modell (nomic-embed-text) auf /api/chat mit 400 Bad Request scheitert --
            # strukturell kein Chat-Modell, tokens/sec waere ohnehin keine sinnvolle
            # Metrik dafuer. Ein eigener Embedding-Latenz-Benchmark waere ein separates
            # Modul, kein Teil von Modul 2.
            gemessene_modelle: dict[str, dict] = {}
            for rolle, modell in modelle_final.items():
                if rolle == "embed" or modell in fehlende_downloads or modell in gemessene_modelle:
                    continue
                gemessene_modelle[modell] = self.bench_modell(modell)
            benchmark = {rolle: gemessene_modelle[modell]
                         for rolle, modell in modelle_final.items()
                         if modell in gemessene_modelle}

        ergebnis = {
            "tier": tier,
            "modelle": modelle_final,
            "provider_empfehlung": "api" if tier == "api_empfohlen" else "ollama",
            "begruendung": begruendung,
            "fehlende_downloads": fehlende_downloads,
        }
        if mit_benchmark:
            ergebnis["benchmark"] = benchmark
        return ergebnis

    def uebernehme(self, modelle: dict, provider: str = "ollama",
                    api_base_url: Optional[str] = None, api_key: Optional[str] = None,
                    api_name: str = "api") -> dict:
        """Schreibt die getroffene Wahl nach system_config (ON CONFLICT-Upsert,
        gleiches Muster wie db_setup.py/master_loop.py). Bei provider='api'
        wird der Key NIE in die DB geschrieben, sondern in eine chmod-600-Datei
        unter ~/.config/werft_secrets/ (identisches Muster zu
        deepseek_client.py) -- system_config speichert nur den Dateipfad."""
        if provider not in ("ollama", "api"):
            raise ValueError(f"Unbekannter Provider: {provider!r} (erlaubt: 'ollama', 'api')")

        eintraege = [("llm.provider", provider, "string", "llm")]
        for rolle in ROLLEN:
            if rolle in modelle:
                eintraege.append((f"ollama.model.{rolle}", modelle[rolle], "string", "ollama"))
        if "embed" in modelle:
            eintraege.append(("ollama.model.embed", modelle["embed"], "string", "ollama"))

        key_pfad = None
        if provider == "api":
            if not api_base_url:
                raise ValueError("provider='api' braucht api_base_url")
            if not api_key:
                raise ValueError("provider='api' braucht api_key")
            key_pfad = Path.home() / ".config" / "werft_secrets" / f"{api_name}_api_key"
            key_pfad.parent.mkdir(parents=True, exist_ok=True)
            key_pfad.write_text(api_key.strip(), encoding="utf-8")
            key_pfad.chmod(0o600)
            eintraege.append(("llm.api.base_url", api_base_url, "string", "llm"))
            eintraege.append(("llm.api.key_pfad", str(key_pfad), "string", "llm"))

        con = sqlite3.connect(self._db_path)
        try:
            con.executemany(
                "INSERT INTO system_config (config_key, config_value, config_type, category) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(config_key) DO UPDATE SET config_value=excluded.config_value, "
                "config_type=excluded.config_type, category=excluded.category",
                eintraege,
            )
            con.commit()
        finally:
            con.close()

        return {
            "provider": provider,
            "geschriebene_keys": [e[0] for e in eintraege],
            "api_key_pfad": str(key_pfad) if key_pfad else None,
        }


if __name__ == "__main__":
    # Reiner Lese-Smoke-Test (Scan + Empfehlung) -- ruft ABSICHTLICH NICHT
    # uebernehme() auf, damit ein versehentlicher Lauf auf einer bereits
    # produktiv konfigurierten Werft (wie dieser hier) deren system_config
    # nicht ueberschreibt.
    ei = Ersteinrichtung()
    scan = ei.scanne_hardware()
    print("SCAN:", scan)
    empfehlung = ei.empfehle_modelle(scan)
    print("EMPFEHLUNG:", empfehlung)
    assert empfehlung["tier"] in ("gross", "mittel", "klein", "api_empfohlen")
    assert empfehlung["provider_empfehlung"] in ("ollama", "api")
    print("OK: Scan + Empfehlung ohne Fehler, uebernehme() NICHT aufgerufen (Read-Only-Smoke-Test).")
