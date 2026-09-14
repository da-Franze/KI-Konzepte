# Die Werft — Bedienanleitung

Was sie kann, wie man ihr einen Auftrag gibt, wie man den Bau verfolgt und was
zu tun ist, wenn etwas hängt. Alle Zahlen und Abläufe sind aus dem laufenden
Code ausgelesen, nicht geschätzt — die Beispielwerte in dieser Anleitung
stammen aus einem länger laufenden Referenzsystem, in einer frisch
aufgesetzten Werft sehen sie anfangs anders aus.

Zum Aufbau auf leerer Hardware: [INSTALL.md](INSTALL.md). Diese Anleitung
setzt voraus, dass das erledigt ist — insbesondere, dass `runtime/werft.db`
bereits existiert und die Werft aus `werft/` heraus gestartet wird (Code und
Skripte liegen dabei unter `werft/core/`).

---

# Teil 1 — Was die Werft kann

## In einem Satz

**Man bestellt ein Stück Software in Prosa, und die Werft baut es** — sie
schreibt das Lastenheft, erzeugt den Code, prüft ihn gegen die Spezifikation,
findet und behebt Fehler und liefert die fertige Datei in einen Zielordner.

Kein Mensch schreibt dabei Code. Die Arbeit machen lokale Sprachmodelle in
festen Rollen.

## Der Weg einer Bestellung

```
  Bestellung in Prosa
        │  status = eingegangen
        ▼
  Coordinator ──► legt den Einstiegs-Task an          status = in_bau
        │
        ▼
  Planer      ──► reichert an: welcher Code ist betroffen?
  Summarizer  ──► macht aus der Beschreibung ein konkretes Lastenheft
  Coder       ──► schreibt Python-Code
  Debugger    ──► statische Prüfung VOR der Abnahme, findet Fehler
  Analyst     ──► Abnahme gegen die Spezifikation  ("Taufe")
        │
        ├── bestanden ──► merged ──► Auslieferung in den Zielordner
        │                                          status = geliefert
        └── gescheitert ──► zurück zum Coder, bis zur Wiederholungsgrenze
                            danach ──► review_pending  (wartet auf den Menschen)
```

Zwei Rollen laufen nebenher: der **Stratege** plant über einzelne Bestellungen
hinaus, der **Judge** bewertet die Wichtigkeit offener Lücken. Eine
**Mentor-Autokorrektur** räumt bekannte Blockaden selbstständig weg.

## Beispiel: die Bilanz eines länger laufenden Referenzsystems

```
Bestellungen        124 geliefert · 5 in Bau · 3 archiviert · 2 eingegangen
Tasks               128 merged · 3 archiviert · 3 archived · 2 review_pending
```

Das sind Zahlen aus einem Werft-Exemplar nach mehreren Wochen Betrieb, nicht
der Zustand nach dem Kaltstart. Was daran wichtig bleibt: die 2 auf
`review_pending` sind der Normalfall, nicht ein Fehler — dort **wartet die
Werft auf eine menschliche Entscheidung**. Wer sie übersieht, lässt die Werft
stillstehen — in einem Referenzsystem ist das schon einmal zwei Tage lang
passiert.

## Was sie sich selbst merkt

`runtime/werft.db` hat über 30 Tabellen. Die im Alltag wichtigen:

| Tabelle | Inhalt |
|---|---|
| `bestellungen` | die Aufträge samt Spezifikation und Zielordner |
| `task_pipeline` | die einzelnen Bauschritte und ihr Zustand |
| `debugger_findings` | gefundene Fehler mit Wiederholungszähler |
| `systemmap`, `call_graph_kanten` | welcher Code ruft welchen — die Landkarte |
| `coder_dna_regeln` | Regeln, die der Coder einhalten muss |
| `projekt_verbotene_muster` | was nie im erzeugten Code stehen darf |
| `mentor_actions_log` | was die Werft selbst repariert hat, und was sie eskaliert |
| `spezialist_charakter`, `charakter_leistung` | Charaktere und ihre gemessene Güte |
| `judge_log` | Bewertungen der Wichtigkeit offener Lücken |
| `strategie_planung` | die längerfristige Planung des Strategen |

Die Werft **behält ihre Fehlhistorie**. Das ist Absicht: `debugger_findings`
zählt, wie oft derselbe Befund wiederkam, und daraus lernt sie.

---

# Teil 2 — Bedienung

## Starten und Stoppen

Die Werft besteht aus **zwei Prozessen**:

```bash
cd werft

# 1. Die Bau-Pipeline (hält sich selbst am Leben)
nohup bash core/start_werft.sh > /tmp/master_loop_persistent.log 2>&1 &

# 2. Die Weboberfläche zum Bestellen (Port 8891)
nohup ./core/venv/bin/python3 core/spec_generator.py >> /tmp/spec_generator.log 2>&1 &
```

Unter Windows entsprechend über die `.ps1`-Skripte (Details und
Hintergrund-Varianten: [INSTALL.md](INSTALL.md) Schritt 6):

```powershell
Set-Location werft
Start-Process powershell -ArgumentList "-File core\start_werft.ps1"
Start-Process -FilePath ".\core\venv\Scripts\python.exe" -ArgumentList "core\spec_generator.py"
```

`start_werft.sh`/`start_werft.ps1` sind Wrapper mit Absicht: `master_loop.py`
**beendet sich selbst**, sobald sich der eigene Code ändert — die
Laufzeitumgebung würde importierte Module sonst dauerhaft zwischenspeichern
und eine Korrektur bliebe wirkungslos. Der Wrapper startet nach jedem Ende
neu, mit frisch geladenem Code. Ein Exit ist hier also normal und kein
Absturz.

**Stoppen (Linux):**

```bash
pkill -f start_werft.sh          # zuerst den Wrapper, sonst startet er neu
pkill -f "from master_loop"
pkill -f spec_generator.py
```

**Stoppen (Windows):** `core/stop_werft.ps1` beendet den `master_loop`-Prozess
sauber (sucht per `Win32_Process`-Filter nach der Kommandozeile, analog zu
`pkill -f`):

```powershell
.\core\stop_werft.ps1
Get-Process python* | Where-Object { $_.MainWindowTitle -match "spec_generator" } | Stop-Process
```

**Läuft sie?**

```bash
ps aux | grep -E "master_loop|spec_generator" | grep -v grep
ss -lptn 'sport = :8891'
tail -f /tmp/master_loop_persistent.log
```
```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'master_loop|spec_generator' }
Get-NetTCPConnection -LocalPort 8891 -ErrorAction SilentlyContinue
Get-Content werft\runtime\master_loop_windows.log -Wait
```

## Eine Bestellung aufgeben

### Weg 1 — im Browser (der übliche)

`http://localhost:8891`

Die Seite hat zwei Hälften:

**Oben, „Werft Spec-Generator":** ein Chat. Man beschreibt in normaler Sprache,
was gebaut werden soll, und bekommt Rückfragen, bis die Beschreibung
vollständig ist. Wer das nicht braucht, drückt **„Standard-Vorlage laden (statt
Chat)"** und füllt das Formular direkt aus.

**Unten, „Einreichen":** Knopf **„An die Werft einreichen"**. Damit steht die
Bestellung auf `eingegangen`, und der nächste Zyklus greift sie auf.

### Weg 2 — direkt in die Datenbank

Für Skripte und Wiederholungen:

```bash
sqlite3 werft/runtime/werft.db "INSERT INTO bestellungen
  (bestellung_id, titel, spec_inhalt, quelle, ziel_ordner, status, created_at)
  VALUES ('meine_bestellung_01', 'Kurzer Titel',
          '## Aufgabe
Beschreibung in Prosa.

## Abnahmekriterium
\`\`\`python
assert meine_funktion(2) == 4
\`\`\`',
          'manuell', '/pfad/zum/zielprojekt', 'eingegangen', datetime('now'))"
```

(Unter Windows identisch, sofern `sqlite3.exe` installiert ist — sonst tut es
auch ein kurzes Python-Skript mit dem eingebauten `sqlite3`-Modul gegen
`werft\runtime\werft.db`.)

## Wie eine gute Bestellung aussieht

Das entscheidet über Erfolg oder Fehlschlag, mehr als jede Einstellung.

**Eine Spezifikation beschreibt das Konzept, nicht die Lösung.** Wer den Code
schon vorgibt, bekommt eine schlechtere Umsetzung als wer das Ziel beschreibt.

**Das Abnahmekriterium ist Pflicht** und muss ausführbarer Python-Code in einem
```` ```python ````-Block sein. Er entscheidet über bestanden oder nicht.

Vier Fallen, jede im Referenzsystem mindestens einmal live erlebt:

1. **Nur EIN Abschnitt `## Abnahmekriterium` pro Bestellung.** Bei mehreren
   nimmt die Prüfung den ersten Codeblock nach der ersten Überschrift — und
   testet damit womöglich etwas völlig Fremdes. Eine fertige Bestellung blieb so
   dauerhaft blockiert.

2. **Keine destruktiven Muster im Abnahmekriterium** — kein `DELETE FROM`, kein
   `DROP`. Die Werft blockiert sie, und zwar zu Recht: ein gespeichertes
   Testkriterium hat in einem Referenzsystem einmal eine produktive Datenbank
   geleert, ohne Backup. Wer eine Datenbank testen muss, legt eine zweite
   Test-Datenbank an, statt in der echten zu löschen.

3. **Beschreibungen nicht in Code-Schreibweise.** Erwähnt der Fließtext
   beiläufig `meinefunktion()` und existiert diese Funktion in der Zieldatei,
   hält die Werft das für eine Änderungsanweisung. Das ist in einem
   Referenzsystem dreimal hintereinander passiert — ausgerechnet beim
   Formulieren einer Korrektur, die genau dieses Fehlverhalten beschreiben
   sollte. Also: *„die Methode meinefunktion"* statt `meinefunktion()`.

4. **`spec_inhalt` und `knowledge_links` müssen zusammenpassen.** Der Coder
   liest beides. Wer nur eins ändert, bekommt einen Bau aus zwei
   widersprüchlichen Ständen.

## Den Bau verfolgen

**Auf der Kommandozeile** (Pfad zur Datenbank: `werft/runtime/werft.db`):

```bash
# Wo steht welche Bestellung?
sqlite3 werft/runtime/werft.db "SELECT bestellung_id, status FROM bestellungen
                  WHERE status != 'geliefert'"

# Was tun die Tasks?
sqlite3 werft/runtime/werft.db "SELECT task_id, status, assigned_agent, coder_fehlversuche
                  FROM task_pipeline WHERE status NOT IN ('merged','archiviert')"

# Was hat der Debugger gefunden?
sqlite3 werft/runtime/werft.db "SELECT * FROM debugger_findings ORDER BY id DESC LIMIT 10"

# Was hat die Werft selbst repariert oder eskaliert?
sqlite3 werft/runtime/werft.db "SELECT * FROM mentor_actions_log ORDER BY id DESC LIMIT 10"

# Live mitlesen
tail -f /tmp/master_loop_persistent.log
```

## Eine laufende Bestellung korrigieren

Wenn der Bau in die falsche Richtung läuft, wird nicht abgebrochen, sondern
nachgebessert. Der Befehl muss aus `werft/core` heraus laufen, weil
`bestellungen.py` seine Geschwistermodule über kurze Namen importiert:

```bash
cd werft/core
./venv/bin/python3 -c "
import bestellungen
print(bestellungen.bestellung_korrigieren('meine_bestellung_01', '''
## Aufgabe
Nachgebesserte Beschreibung.

## Abnahmekriterium
\`\`\`python
assert True
\`\`\`
'''))"
```

> **Nach einer Korrektur ist der erste Fehlschlag oft nur ein Nachhall.** Der
> Coder hatte womöglich schon einen Kandidaten aus dem alten Stand in Arbeit.
> Einmal abwarten, bevor man die Korrektur für gescheitert erklärt.

## `review_pending` — wenn die Werft auf dich wartet

Erreicht ein Task die Wiederholungsgrenze, geht er auf `review_pending` und
**bleibt dort liegen, bis ein Mensch entscheidet**. Die Werft macht in der
Zwischenzeit an dieser Bestellung nichts mehr.

```bash
sqlite3 werft/runtime/werft.db "SELECT task_id, status FROM task_pipeline WHERE status='review_pending'"
```

Dann prüfen, warum es scheiterte — meist ist es eine der vier Fallen oben — und
entweder die Spezifikation korrigieren (dann läuft es weiter) oder den Task
archivieren.

Das gehört in die tägliche Runde. In einem Referenzsystem standen einmal zwei
Tasks zwei Tage lang, weil niemand nachsah.

---

# Teil 3 — Wenn etwas hängt

## Die Reihenfolge

```bash
# 1. Läuft überhaupt jemand?
ps aux | grep -E "master_loop|spec_generator" | grep -v grep

# 2. Was macht die Schleife?
tail -30 /tmp/master_loop_persistent.log

# 3. Sind die Modelle erreichbar?
curl -s http://localhost:11434/api/tags | grep -c '"name"'

# 4. Wartet die Werft auf mich?
sqlite3 werft/runtime/werft.db "SELECT count(*) FROM task_pipeline WHERE status='review_pending'"
```

Unter Windows entsprechen sich: Prozessliste über `Get-CimInstance
Win32_Process`, Log über `Get-Content ... -Wait`, Modell-Erreichbarkeit über
`Invoke-RestMethod http://localhost:11434/api/tags`, Rest identisch (sqlite3
CLI-Syntax ist plattformunabhängig).

## Häufige Fälle

| Symptom | Ursache | Was tun |
|---|---|---|
| nichts bewegt sich, keine Fehler | Tasks auf `review_pending` | Schritt 4, entscheiden |
| Bestellung bleibt auf `eingegangen` | keine verwertbare Spezifikation — die Werft rät bewusst nicht | Abnahmekriterium ergänzen |
| Task scheitert alle paar Minuten neu | Abnahmekriterium unerfüllbar oder falsch | `debugger_findings` lesen |
| Code wird gebaut, aber nicht ausgeliefert | nicht alle Tasks der Bestellung sind `merged` | Task-Liste der Bestellung prüfen |
| Korrektur wirkt nicht | Kandidat aus dem alten Stand | einen Zyklus abwarten |
| Prozess beendet sich ständig | **normal**, wenn der Werft-Code geändert wurde | Log liest sich als „Neustart in 3s" |
| Modelle konkurrieren, alles zäh | mehrere Rollen auf demselben Ollama-Host | Rollen-Hosts prüfen |

## Wiederholungszähler richtig lesen

Der Debugger nennt in Eskalationsmeldungen manchmal eine irreführende Zahl
(„nach 11 Wiederholungen"). Die Zählung hängt an der Task-Nummer, **nicht am
Fehlertyp** — ein längst erledigter alter Befund zählt für einen völlig neuen
Fehler am selben Task weiter. Also: die Zahl als Hinweis nehmen, den
**Fehlertext** als Wahrheit.

---

# Teil 4 — Grenzen und Umgangsregeln

**Die Werft baut nur, was prüfbar ist.** Ohne ausführbares Abnahmekriterium gibt
es keine Abnahme. Vage Wünsche („Fehlerbehandlung verbessern") führen zu
generischem Code, der scheitert, und der Task wird immer wieder neu erzeugt.
Wenn sich aus einer Beschreibung kein konkretes Prüfkriterium formulieren lässt,
ist sie noch keine Bestellung.

**Sie ändert fremden Code nur, wenn sie ihn kennt.** Vor einer Bestellung gegen
unbekannten Code muss die Landkarte stimmen — `systemmap` und
`call_graph_kanten`. Sonst rät der Planer die betroffenen Stellen.

**Sie ist langsam, und das ist der Preis.** Ein Durchlauf befragt mehrere lokale
Modelle nacheinander. Eine Bestellung braucht Minuten bis Stunden, nicht
Sekunden. Dafür ist jeder Schritt nachvollziehbar und in der Datenbank belegt.

**Was der Werft gehört, und was nicht.** Der von ihr gebaute Code wird nicht von
Hand nachgebessert — dafür gibt es die Korrektur-Bestellung. Ein schneller
Handgriff am erzeugten Code umgeht genau die Prüfung, für die die Werft gebaut
wurde. Änderungen an der Werft selbst — Pipeline, Prompts, Werkzeuge — sind
davon nicht betroffen.

---

# Teil 5 — Spickzettel

```bash
# Starten
cd werft
nohup bash core/start_werft.sh > /tmp/master_loop_persistent.log 2>&1 &
nohup ./core/venv/bin/python3 core/spec_generator.py >> /tmp/spec_generator.log 2>&1 &

# Stoppen (Wrapper zuerst!)
pkill -f start_werft.sh && pkill -f "from master_loop" && pkill -f spec_generator.py

# Bestellen
xdg-open http://localhost:8891

# Zustand
sqlite3 werft/runtime/werft.db "SELECT status, count(*) FROM bestellungen GROUP BY status"
sqlite3 werft/runtime/werft.db "SELECT status, count(*) FROM task_pipeline GROUP BY status"
sqlite3 werft/runtime/werft.db "SELECT count(*) FROM task_pipeline WHERE status='review_pending'"
tail -f /tmp/master_loop_persistent.log

# Diagnose
sqlite3 werft/runtime/werft.db "SELECT * FROM debugger_findings ORDER BY id DESC LIMIT 10"
sqlite3 werft/runtime/werft.db "SELECT * FROM mentor_actions_log ORDER BY id DESC LIMIT 10"
```

**Ports:** Spec-Generator 8891 · Qdrant 6333 · Ollama 11434 (Standard, ggf.
weitere Instanzen auf anderen Ports)
**Datenbank:** `werft/runtime/werft.db` — über 30 Tabellen
**Wissens-Schicht:** Qdrant-Collection `werft_wissen_1024`
**Rollen je Zyklus:** Bestellungs-Eingang → Judge → Coordinator → Requeue →
Planer → Summarizer/Coder → Debugger → Analyst → Auslieferung → Stratege →
Mentor-Autokorrektur
