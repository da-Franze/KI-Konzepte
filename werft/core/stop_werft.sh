#!/bin/bash
# Betreiber-Vorschlag 2026-08-24: sauberes Gegenstueck zu start_werft.sh, um vor
# geplanten Eingriffen an Abhaengigkeiten (z.B. Ollama-Neustart/-Upgrade)
# einen definierten, initialisierten Zustand herzustellen -- Anlass war ein
# Live-Fund desselben Tages: ein Ollama-Neustart unter einem laufenden
# master_loop liess eine tote TCP-Verbindung zurueck, die den Coder-Schritt
# ueber 40 Minuten unbemerkt haengen liess. Erst die Werft geordnet stoppen,
# dann Abhaengigkeiten neu starten, dann die Werft wieder hochfahren.
echo "Stoppe Stagnations-Watchdog..."
pkill -f "werft_stagnation_watchdog.sh" 2>/dev/null

echo "Stoppe Werft-Wrapper (start_werft.sh)..."
pkill -f "start_werft.sh" 2>/dev/null

echo "Stoppe master_loop..."
pkill -f "from master_loop import main" 2>/dev/null

sleep 2
if pgrep -f "from master_loop import main" > /dev/null; then
    echo "WARNUNG: master_loop laesst sich nicht sauber beenden -- manuell pruefen (ps aux | grep master_loop)."
    exit 1
fi
echo "Werft sauber gestoppt. Neustart mit: ./start_werft.sh"
