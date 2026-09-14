#!/bin/bash
# Betreiber-Auftrag 2026-07-21 (Punkt 1 der Automatisierungs-Liste): master_loop.py
# beendet sich seit heute selbst sauber (sys.exit(0)), sobald werft_startup.py
# eine Aenderung an den eigenen .py-Dateien erkennt (Python cached importierte
# Module sonst dauerhaft, Code-Fixes wirken erst nach manuellem Neustart --
# heute mehrfach live erlebt). Dieser Wrapper haelt den Prozess dauerhaft am
# Laufen: bei jedem Exit (egal ob wegen Code-Aenderung oder Absturz) kurze
# Pause, dann automatischer Neustart mit frisch importiertem Code.
cd "$(dirname "$0")"
while true; do
    ~/ki_venv/bin/python3 -c "from master_loop import main; main(pause_s=15)" >> /tmp/master_loop_persistent.log 2>&1
    echo "$(date '+%Y-%m-%d %H:%M:%S') master_loop beendet (Exit-Code $?) -- Neustart in 3s" >> /tmp/master_loop_persistent.log
    sleep 3
done
