"""Ollama-Zugriff fuer die 4 Werft-Kernrollen (WERFT_NEUSTART_SPEZIFIKATION.md Abschnitt 5).

Host/Modell/Temperature kommen ausschliesslich aus ConfigLoader -- Zero-Hardcoding
(G-4). Jede Rolle uebergibt ihren eigenen system_config-Modell-Key.

Fund 2026-07-27 (Betreiber, Tisch: will die Werft an seinen Bruder weitergeben,
dessen Hardware nur 4b/8b lokale Modelle schafft -- "koennte man die lokalen
LLM auch zentral konfigurierbar machen, um ggf. auf API-Modelle zu wechseln?"):
Host/Modellname waren schon zentral in system_config, aber ask_model() selbst
war hart auf Ollamas eigenes /api/chat-Format verdrahtet. deepseek_client.py
hatte bereits einen funktionierenden, OpenAI-kompatiblen Aufruf mit identischer
Signatur (nur fuer den R&D-Vergleichsharness genutzt, nicht in die Pipeline
eingebunden) -- das war der Beweis, dass ein zweites Protokoll grundsaetzlich
funktioniert. Jetzt generalisiert UND in ask_model() selbst eingebaut, damit
KEIN Aufrufer (coder.py/analyst.py/summarizer.py/judge.py/strategie.py, alle
nutzen denselben Funktionsnamen) angefasst werden muss: system_config-Key
'llm.provider' (Default 'ollama') schaltet zentral um. Bei 'api' werden
Base-URL/Key-Pfad ebenfalls aus system_config gelesen (llm.api.base_url,
llm.api.key_pfad) -- nicht auf einen bestimmten Anbieter (DeepSeek/OpenAI/...)
festgelegt, jeder OpenAI-kompatible Endpunkt funktioniert."""
import concurrent.futures
import time
from pathlib import Path

import requests

from configloader import config

# Fund 2026-07-17 (Betreiber-Auftrag): zentraler Debug-Schalter -- EIN Ort, an dem
# ALLE LLM-Aufrufe der Werft durchlaufen, statt Debug-Logik in jeder Rolle
# einzeln. Ueber system_config (Key "werft.debug", Werte "true"/"false")
# EIN/AUS schaltbar, kein Hardcoding, kein Neustart der Werft noetig (wird
# bei jedem Aufruf frisch gelesen). Bei AN: Modell, Prompt-/Antwort-Laenge,
# Dauer und die ersten 200 Zeichen von Prompt+Antwort auf stdout -- genau die
# Sichtbarkeit, die beim RouterAgent-Haenger (2026-07-17) gefehlt hat: ohne
# Debug-Ausgabe war nicht unterscheidbar, ob ein Zyklus haengt oder nur lange
# rechnet.
def _debug_aktiv() -> bool:
    return str(config.get("werft.debug", "false")).lower() in ("true", "1", "yes")


def _ask_api(prompt: str, model: str, temperature: float, num_predict: int = None) -> str:
    """Generalisierter OpenAI-kompatibler API-Aufruf (Base-URL/Key-Pfad aus
    system_config, kein fest verdrahteter Anbieter -- siehe Modul-Docstring).
    Gleiches Verhalten wie ask_model() bei Fehlern: Exception propagiert,
    Aufrufer behandeln das identisch zu einem Ollama-Fehlschlag."""
    base_url = config.get("llm.api.base_url", "")
    if not base_url:
        raise RuntimeError(
            "llm.provider=api gesetzt, aber system_config 'llm.api.base_url' fehlt."
        )
    key_pfad = Path(str(config.get("llm.api.key_pfad", "")))
    if not key_pfad.exists():
        raise RuntimeError(
            f"llm.provider=api gesetzt, aber Key-Datei {key_pfad} (system_config "
            "'llm.api.key_pfad') fehlt -- Zero-Hardcoding, Key liegt nie im Code."
        )
    api_key = key_pfad.read_text(encoding="utf-8").strip()

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "stream": False,
    }
    if num_predict is not None:
        payload["max_tokens"] = num_predict

    r = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=float(config.get("ollama.api_timeout_sec")),
    )
    if r.status_code == 404:
        print(f"[ollama_client] 404 body: {r.text[:500]}", flush=True)
    if r.status_code == 404:
        print(f"[ollama_client] 404 body: {r.text[:500]}", flush=True)
    r.raise_for_status()
    data = r.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


def host_fuer_rolle(rolle: str) -> str:
    """Modul 5 (SPEC_RESSOURCEN_MANAGER.md): loest den Ollama-Host fuer eine
    Rolle auf. Liest 'ollama.host.<rolle>' aus system_config, faellt bei
    fehlendem Key auf den globalen 'ollama.host'-Default zurueck -- KEIN
    Verhaltensbruch fuer Rollen, die (noch) keinen eigenen Host-Eintrag haben.
    Bewusst getrennt von ask_model()s eigener host=None-Aufloesung, damit
    Aufrufer den Host VOR dem eigentlichen Aufruf kennen (z.B. fuer
    RessourcenStatus-Beobachtung desselben Hosts, siehe master_loop.py)."""
    return config.get(f"ollama.host.{rolle}", config.get("ollama.host", "http://localhost:11434"))


def _fallback_host() -> str:
    """Liest den zentralen Ollama-Fallback fuer ausgefallene Rollenhosts."""
    return config.get(
        "ollama.host.fallback",
        config.get("ollama.host", "http://localhost:11434"),
    )


def _mit_gesamt_timeout(fn, gesamt_timeout: float):
    """Erzwingt ein echtes Gesamt-Zeitlimit fuer einen blockierenden Aufruf,
    unabhaengig von requests' per-Read-Timeout (der bei gelegentlich
    eintrudelnden Daten nie feuert, selbst wenn die Gesamtdauer laengst ueber
    dem gewollten Budget liegt -- live reproduziert 2026-08-26, master_loop
    haengte >30 Minuten ohne jede Exception).

    Der Hintergrund-Thread wird bei Ueberschreitung NICHT hart abgebrochen
    (in Python nicht sicher moeglich) -- shutdown(wait=False) sorgt aber
    dafuer, dass die TimeoutError SOFORT beim Aufrufer ankommt, statt bis
    zum Ende des haengenden Threads zu warten (das war ein Bug im ersten
    Entwurf: "with ThreadPoolExecutor(...) as ex:" blockiert beim Verlassen
    auf shutdown(wait=True))."""
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = ex.submit(fn)
    try:
        ergebnis = future.result(timeout=gesamt_timeout)
        ex.shutdown(wait=False)
        return ergebnis
    except concurrent.futures.TimeoutError:
        ex.shutdown(wait=False)
        raise TimeoutError(
            f"ask_model: Gesamt-Zeitlimit {gesamt_timeout}s ueberschritten -- "
            f"moeglicherweise ein trickelnder Stream ohne harten Read-Timeout."
        )


def ask_model(prompt: str, model: str, temperature: float = 0.1,
              host: str = None, num_predict: int = None) -> str:
    """Ruft ein LLM auf und gibt den Antworttext zurueck. Provider (Ollama
    lokal ODER eine OpenAI-kompatible API) wird zentral ueber system_config
    ('llm.provider', Default 'ollama') gesteuert -- kein Aufrufer muss dafuer
    angepasst werden (siehe Modul-Docstring, Fund 2026-07-27).

    host: falls None, aus system_config (ollama.host) gelesen. Wird bei
    llm.provider=api ignoriert (dort zaehlt llm.api.base_url).

    Nutzt /api/chat mit think=False (nicht /api/generate) -- bekannter, bereits
    im alten System verifizierter Fix (doe_worker.py): qwen3-Modelle erschoepfen
    sonst num_predict im Thinking-Mode und liefern eine leere Antwort. Reproduziert
    bei mir selbst (Smoke-Test) und in 687 historischen DoE-Experimenten (qwen3:4b/8b
    score_total=0.0 durchgehend), solange think nicht explizit deaktiviert wird.
    """
    if str(config.get("llm.provider", "ollama")).lower() == "api":
        return _ask_api(prompt, model, temperature, num_predict)

    if host is None:
        host = config.get("ollama.host", "http://localhost:11434")

    # Fund 2026-07-20 (Scout-Portierung): 8192 hartcodiert reichte fuer
    # KORREKTUR_TEMPLATE-Antworten (volle Datei rein+raus) schon bei ~5KB
    # Dateien nicht mehr -- konfigurierbar gemacht (G-4), Default angehoben.
    num_ctx = int(config.get("ollama.num_ctx", 16384))
    options = {"temperature": temperature, "num_ctx": num_ctx}
    if num_predict is not None:
        options["num_predict"] = num_predict

    debug = _debug_aktiv()
    if debug:
        start = time.perf_counter()
        print(f"[werft.debug] ask_model START modell={model} temp={temperature} "
              f"num_predict={num_predict} prompt_len={len(prompt)}")
        print(f"[werft.debug] PROMPT[:200]={prompt[:200]!r}")

    gesamt_timeout = float(config.get("ollama.gesamt_timeout_sec"))
    connect_timeout = float(config.get("ollama.connect_timeout_sec"))
    read_timeout = float(config.get("ollama.read_timeout_sec"))
    
    # Fund 2026-08-26 (Betreiber-Auftrag): Zweitsystem-Fallback.
    # Wenn der primäre Host nicht erreichbar ist (ConnectionError/ConnectTimeout),
    # versuchen wir es genau EINMAL mit dem lokalen Default-Host.
    # Dies entlastet die lokale GPU, wenn der Remote-Host (Zweitsystem) ausfällt,
    # ohne dass Aufrufer angepasst werden müssen.
    
    def _do_request(target_host: str, timeout_val):
        return requests.post(
            f"{target_host}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
                "keep_alive": "5m",
                "options": options,
            },
            timeout=timeout_val,
        )

    try:
        # 1. Erster Versuch gegen den uebergebenen `host` mit konfiguriertem Timeout
        r = _mit_gesamt_timeout(
            lambda: _do_request(host, (connect_timeout, read_timeout)),
            gesamt_timeout,
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout) as exc:
        # 2. Fallback-Logik
        lokalen_fallback_host = _fallback_host()
        
        # Wenn der Fallback-Host identisch mit dem fehlgeschlagenen Host ist,
        # keinen sinnlosen zweiten Versuch starten.
        if lokalen_fallback_host == host:
            raise exc

        # 3. Loggen des Fallbacks (Betriebszustand, immer sichtbar)
        print(f"[ollama_client] {host} nicht erreichbar ({exc.__class__.__name__}), Fallback auf {lokalen_fallback_host}")

        # 4. Genau EINEN weiteren Versuch gegen lokalen_fallback_host
        #    mit demselben konfigurierten Timeout.
        #    Dieser Fehler wird NICHT abgefangen, er propagiert normal.
        r = _mit_gesamt_timeout(
            lambda: _do_request(lokalen_fallback_host, (connect_timeout, read_timeout)),
            gesamt_timeout,
        )

    if r.status_code == 404:
        print(f"[ollama_client] 404 body: {r.text[:500]}", flush=True)
    r.raise_for_status()
    data = r.json()
    antwort = (data.get("message", {}).get("content", "") or "").strip()

    if debug:
        dauer = round(time.perf_counter() - start, 2)
        print(f"[werft.debug] ask_model ENDE modell={model} dauer={dauer}s "
              f"antwort_len={len(antwort)}")
        print(f"[werft.debug] ANTWORT[:200]={antwort[:200]!r}")

    return antwort


if __name__ == "__main__":
    antwort = ask_model(
        "Antworte nur mit dem Wort: OK",
        model=config.get("ollama.model.router", "qwen3:4b"),
        temperature=0.0,
        num_predict=10,
    )
    print("ask_model smoke-test:", repr(antwort))