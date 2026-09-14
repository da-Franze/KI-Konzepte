# Die Werft — Konzept und Architektur

Dieses Dokument erklärt, warum die Werft so gebaut ist, wie sie
funktioniert, und welche Entscheidungen bewusst getroffen wurden. Für die
reine Bedienung siehe [USER_GUIDE.md](USER_GUIDE.md), für die
Installation [INSTALL.md](INSTALL.md). Alle Aussagen hier sind aus dem
Code selbst abgeleitet (Docstrings, Kommentare, Tabellenschema) — wo etwas
unklar oder unvollständig ist, wird das ausdrücklich so benannt statt
glattgezogen.

## 1. Überblick

Die Werft ist eine Baupipeline: Man beschreibt in Prosa, was gebaut werden
soll — eine „Bestellung" — und ein Team lokaler Sprachmodelle in festen
Rollen (Coordinator, Planer, Summarizer, Coder, Debugger, Analyst, Stratege,
Judge) erzeugt daraus lauffähigen Python-Code, prüft ihn und liefert ihn in
ein Zielverzeichnis aus. Kein Mensch schreibt dabei Code — die Rolle des
Menschen ist, Bestellungen zu formulieren und an einer begrenzten Zahl
definierter Stellen zu entscheiden, wenn die Werft selbst nicht
weiterkommt.

Die Werft ist kein Chatbot mit Code-Ausgabe. Sie ist eine Pipeline mit
persistentem Zustand (`werft.db`, über 30 Tabellen), die einen Auftrag über
mehrere Sprachmodell-Aufrufe, Prüfstufen und — bei Bedarf — mehrere
Korrekturrunden hinweg verfolgt, bis er entweder ausgeliefert oder
menschlicher Entscheidung vorgelegt wird. Jeder Schritt hinterlässt eine
Spur in der Datenbank; nichts läuft nur „im Kontext eines Chats" und ist
danach verloren.

## 2. Architekturprinzip

### 2.1 Die Spezifikation beschreibt das Konzept, nicht die Lösung

Eine Bestellung besteht aus einer Beschreibung in Prosa und einem
verpflichtenden **Abnahmekriterium** — ausführbarem Python-Code, der über
Erfolg oder Misserfolg entscheidet (`## Abnahmekriterium` mit einem
` ```python ` -Block, geprüft von `debugger.ausfuehrungs_check()` und der
Analyst-Taufe). Der Rest der Spezifikation soll das GEWÜNSCHTE VERHALTEN
beschreiben, nicht den Code selbst vorwegnehmen — wer die Lösung schon
mitliefert, bekommt nach der gelebten Erfahrung hinter diesem Projekt eine
schlechtere Umsetzung als wer das Ziel beschreibt und dem Coder die
Freiheit lässt, es zu implementieren.

### 2.2 Deterministisch vor LLM

Ein wiederkehrendes Muster im Code: Wo eine Prüfung **objektiv** entschieden
werden kann (ist eine Datei syntaktisch gültiges Python? enthält der Code
ein bekanntes Stub-Muster? importiert er ein nicht existierendes Modul?),
wird sie per Regex/AST/`compile()` entschieden — nicht per LLM-Urteil.
`code_crawler.py` ist ein „Deterministischer Python-Code-Crawler" (eigener
Docstring), `debugger.py`s Vor-Merge-Statik prüft über feste Muster-Listen
(`STUB_PATTERNS`, `SIMULATIONS_PATTERNS`, `OLLAMA_THINK_MISPLACED_PATTERN`),
`planer.py` erkennt Widersprüche in einer Spezifikation „deterministisch
per Regex (Ground Truth statt Raten)". Sprachmodelle kommen dort zum
Einsatz, wo eine Bewertung tatsächlich Urteilsvermögen braucht: ob Code
inhaltlich der Spezifikation entspricht (Analyst), wie eine vage
Gap-Beschreibung in ein konkretes Lastenheft übersetzt wird (Summarizer),
wie wichtig eine offene Lücke ist (Judge).

### 2.3 Non-Invention-Policy

Mehrere Module tragen denselben Grundsatz explizit im Docstring: kein
Erfinden von Ergebnissen, wo keine belegten Daten vorliegen. `vdb_client.py`
gibt bei einer erfolglosen Suche eine leere Liste zurück, „KEIN Erfinden".
`judge.py` verweigert eine Bewertung ohne mindestens einen Satz konkreter
Begründung (`memory.MIN_GRUND_LAENGE`, historisch aus einem 5-Why-Fund: eine
frühere Fehlhistorie-Implementierung erlaubte Einträge ohne brauchbare
Begründung, „jeder Versuch war ein blinder Neuanfang"). `router.py` fällt
bei einer nicht eindeutig klassifizierbaren Anfrage auf eine feste
Default-Kategorie zurück, statt eine der sechs definierten Kategorien zu
erraten. Dieselbe Haltung erzwingt auch, dass der Coder bei echter
Unsicherheit ein Fehler-Dict zurückgeben MUSS statt einen erfundenen Erfolg
vorzutäuschen (Coder-DNA-Regel, siehe Abschnitt 4).

### 2.4 Zero-Hardcoding

`configloader.py` liest sämtliche Betriebsparameter (Modellnamen, Hosts,
Zeitüberschreitungen, Schwellwerte) aus der Tabelle `system_config` in
`werft.db`, nicht aus Konstanten im Code. Der eigene Docstring nennt das
Prinzip „Zero-Hardcoding (G-4)": „Modellnamen, Hosts, Schwellwerte werden
NIE im Code hinterlegt, sondern immer über diese Klasse aus system_config
gelesen." Das erlaubt, Modellwahl und Zeitverhalten ohne Codeänderung an
sehr unterschiedliche Hardware anzupassen — von einer Maschine mit einer
24-GB-GPU bis zu einer reinen CPU-Installation, die stattdessen einen
API-Provider nutzt (siehe Abschnitt 7).

## 3. Die Rollen

Jede Bestellung durchläuft — vereinfacht — diesen Lebenszyklus (Status in
`task_pipeline.status`):

```
eingegangen → audit → spec → code_ready → merged → geliefert
                 │         │                   │
                 └── (Fehlschlag, Retry) ───────┘
                                │
                    review_pending / archived
                    (Wiederholungsgrenze erreicht, wartet auf einen Menschen)
```

| Rolle | Modul | Aufgabe |
|---|---|---|
| **Coordinator** | `master_loop.coordinator_schritt()` | Erzeugt aus jeder offenen, unresolved Lücke (`system_gaps`) einen Task — sortiert nach der vom Judge vergebenen Wichtigkeit (`severity DESC`). Begrenzt über `pipeline.max_tasks_per_gap`, um eine Task-Explosion pro Lücke zu verhindern (historischer Fund: 587 Tasks für eine einzige Lücke in zwei Stunden, weil eine Positiv-Liste „relevanter" Status einen neuen Status vergaß). |
| **Planer** | `planer.py` | Vorgelagerte Prüfung VOR dem Summarizer: erkennt, ob eine Spezifikation in „verdauliche Häppchen" zerlegt werden muss, und ob sie innere Widersprüche enthält (z.B. ein Changelog, das einen Wert ändert, während der Regelwerk-Text weiter unten den alten Wert unverändert stehen lässt). Rein deterministisch per Regex, kein LLM-Aufruf für die Erkennung selbst. |
| **Summarizer** | `summarizer.py` | Übersetzt eine Gap-Beschreibung oder Bestellung in ein konkretes, strukturiertes Lastenheft (JSON). Erhält Zahlen/Formeln/exakte Relationen wörtlich, statt sie zu destillieren — ein dokumentierter Fund zeigt, dass ein zu freier Summarizer Details erfindet, die dann fälschlich in die Spezifikation einfließen. |
| **Coder** | `coder.py` | Schreibt den eigentlichen Python-Code, gegen die Spezifikation UND gegen die tatsächliche Schnittstelle bereits ausgelieferter Abhängigkeiten (nicht gegen eine geratene Ähnlichkeit aus der Wissens-Schicht). Trägt die DNA-Regeln (Abschnitt 4) und liest bei jedem Aufruf zusätzlich `coder_dna_regeln` — die einzige Rolle mit einem sich selbst erweiternden Regelwerk. |
| **Debugger** | `debugger.py` | Statische Prüfung VOR der Analyst-Abnahme: Stub-/Simulations-Muster, ein isolierter Ausführungs-Check (Import + Instanziierung im Subprozess, mit Timeout), Muster-Verbote pro Zielordner (`projekt_verbotene_muster`), destruktive-Muster-Scan im Abnahmekriterium selbst. Lernt bei bestimmten deterministischen Funden automatisch neue Coder-DNA-Regeln (Abschnitt 6.2). |
| **Analyst** | `analyst.py` | Die „Taufe": prüft generierten Code inhaltlich gegen die Spezifikation UND registriert ihn bei Erfolg in `specialists_v8` sowie als durchsuchbaren Eintrag in der Wissens-Schicht. Lebenszyklus laut eigenem Kommentar: „Embryo -> Audit -> Taufe -> Registrierung." |
| **Stratege** | `strategie.py` | Plant über einzelne Bestellungen hinaus: zerlegt große Aufträge in Teile (`zerlege_bestellung`), fordert gezielte Korrekturen an bereits laufenden Bestellungen an (`fordere_korrektur`), sucht per Pareto-Kostenschnitt nach Modellen/Charakteren, die überproportional viele Fehlschläge/Kosten verursachen (`bottleneck_scan` — liefert nur Kandidaten, ein tatsächlicher Modellwechsel bleibt eine Mentor-Entscheidung). |
| **Judge** | `judge.py` | Bewertet JEDE offene Lücke auf einer Skala 1–10 mit Pflicht-Begründung und schreibt das Ergebnis nach `system_gaps.severity` — das bestimmt die Baureihenfolge des Coordinators. |
| **Router** | `router.py` | Betriebs-Rolle außerhalb des Gap→Merge-Zyklus: klassifiziert eine externe Anfrage in eine von sechs Kategorien (wissen, code, numerik, web, pipeline, sicherheit). |

Ein vollständiger Zyklus (`master_loop.zyklus()`) läuft in dieser
Reihenfolge: Externe Bestellungen einlesen → Bestellungs-Eingang → Judge →
Coordinator → Requeue → Planer → Summarizer/Coder (swap-bewusst sortiert,
siehe Abschnitt 7) → Debugger → Analyst → Auslieferung → Stratege →
Mentor-Autokorrektur → Log-Pflege. Jeder einzelne Schritt läuft
fehlertolerant über einen Wrapper (`_sicher()`), der eine Exception in
genau einer Rolle nicht den gesamten Zyklus abreißen lässt.

## 4. Die Charakter-/DNA-Schicht

Jede Rolle hat eine „DNA" — einen Satz verbindlicher Verhaltensregeln, die
in jeden Prompt eingebaut werden. Diese DNA ist bewusst **nicht** fest im
Python-Code verankert, sondern liegt portabel in
`config/personas/<rolle>.ini` (`role_dna.load_role_dna()`, eigener
Docstring: „Lädt externe Rollen-DNA statt sie als Betriebslogik fest
einzubauen"). Jedes Modul trägt zusätzlich eine ausführlichere,
historisch gewachsene Fallback-DNA im eigenen Quelltext (mit eingebetteten
Begründungen aus echten Fehlern) — sie greift nur, wenn die zugehörige
Persona-Datei fehlt oder leer ist. In der Praxis überschreibt die knappere,
persona-basierte Version die ausführliche Code-Fallback-DNA, sobald eine
Persona-Datei vorhanden ist: die INI-Dateien sind der tatsächliche
Betriebsstand, der Python-Text ist Dokumentation der ursprünglichen
Absicht und Rückfallebene.

Der Coder geht darüber hinaus: `coder._vollstaendige_dna()` hängt an die
sieben fest verankerten Regeln zusätzlich jede Zeile aus der Tabelle
`coder_dna_regeln` an, ab Nummer 8. Diese Tabelle wird nicht nur von einem
Menschen gepflegt, sondern auch **automatisch vom Debugger** beschrieben,
sobald er ein neues, deterministisch erkennbares Fehlermuster findet
(Abschnitt 6.2) — die DNA einer Rolle kann also zur Laufzeit wachsen, ohne
Codeänderung.

Ergänzend dazu gibt es `projekt_verbotene_muster`: pro Zielordner eine
Liste **verbotener** (statt empfohlener) Regex-Muster mit Begründung — die
negative Entsprechung zur positiven DNA. Ein Zielprojekt kann so eigene,
projektspezifische Einschränkungen hinterlegen, die über die generische
Stub-/Simulations-Erkennung hinausgehen (z.B. ein noch nicht freigegebenes
internes Fachvokabular, das im ausgelieferten Code nicht auftauchen soll).

Eine dritte, lose verwandte Schicht ist die **Charakter-Verwaltung**
(`spezialist_charakter`, `charakter_leistung` in `werft.db`,
`modellwahl_register.py`): Fine-Tunes oder benannte Modell-Varianten für
bestimmte Rollen werden über gemessene Kennzahlen (`mu_quality`,
Erfolgsrate) statt über manuelle Konfiguration ausgewählt, mit Fallback auf
den Konfigurationsstandard, falls keine Messdaten vorliegen. Das Register
speichert dabei ausdrücklich „keine Simulationen: echte
DB-Interaktionen, echte Fehlerbehandlung."

## 5. Die Code-Landkarte

`code_crawler.py` und `werft_map_pipeline.py` sind der Dreh- und Angelpunkt
dafür, dass die Werft Code **kennt**, bevor sie ihn ändert. Der Crawler
liest ein Python-Modul deterministisch per `ast`/`symtable` aus und
erzeugt eine „Projektmap": Klassen, Funktionen, Variablen, Aufrufkanten
zwischen ihnen. `WerftMapPipeline` verbindet diese deterministische
Kartenbildung mit dem Freigabeprozess: sie persistiert Soll-Karte,
Ist-Karte, Diff und daraus abgeleitete Teilaufträge in der Runtime-Tabelle
`project_map_versions`, und eine Teilauftragsfreigabe aktualisiert
denselben Datensatz erst nach bestandenem Prüfstand.

Diese Landkarte ist die Voraussetzung für eine der zentralen
Design-Entscheidungen der Werft: **sie ändert fremden Code nur, wenn sie
ihn kennt.** Ohne eine aktuelle Karte (Tabellen `systemmap`,
`call_graph_kanten`) muss der Planer die betroffenen Stellen einer
Bestellung gegen unbekannten Code RATEN — genau das Risiko, das die
Landkarte eliminieren soll. `werft_diagnose_fremdcode.py` nutzt denselben
Mechanismus für einen verwandten, aber unterschiedlichen Zweck: es lässt
die Werft (über dieselbe Analyst-Infrastruktur) fremden, unbekannten Code
rein lesend untersuchen und diagnostizieren, ohne ihn zu bauen oder
auszuliefern — ein Diagnose-Werkzeug, kein Lieferweg.

`product_archive.py` schließt den Kreis für ausgelieferten Code: jede
verifizierte Lieferung wird mit Code, Projektmap und einem SHA-256-Hash des
Inhalts versioniert abgelegt (`PRODUCT_ARCHIVE_DIR/<produkt>/<zeitstempel>-
<hash-prefix>/`), sodass eine spätere Bestellung auf eine konkrete, geprüfte
Version zurückgreifen kann statt erneut zu raten, was „die aktuelle
Version" war.

## 6. Selbstheilung

Die Werft enthält mehrere unabhängige Mechanismen, die Blockaden ohne
menschliches Eingreifen auflösen — bewusst nur dort, wo das Risiko einer
autonomen Aktion strukturell begrenzt ist (rein additiv, reversibel, oder
auf deterministischer statt urteilsbasierter Erkennung beruhend).

### 6.1 Code-Hash-Neustart

`werft_startup.werft_code_hash()` bildet einen kombinierten
mtime-Fingerabdruck aller Top-Level-`.py`-Dateien im `core/`-Ordner (nicht
`generiert/` — das ändert sich durch normale Coder-Arbeit ständig und ist
kein Infrastruktur-Code). `master_loop.main()` vergleicht diesen
Fingerabdruck bei jedem Zyklus gegen den Stand beim eigenen Start; bei
einer Abweichung beendet sich der Prozess sauber (`sys.exit(0)`). Ein
äußerer Wrapper (`start_werft.sh`/`start_werft.ps1`) hält den Prozess
dauerhaft am Leben und startet ihn nach jedem Exit automatisch mit frisch
importiertem Code neu. Grund für diese Trennung: Python cached importierte
Module dauerhaft im laufenden Prozess — ohne diesen Mechanismus würde eine
Korrektur an der Werft selbst erst nach einem manuellen Neustart wirken,
was laut Code-Kommentar mehrfach live zu unbemerkt wirkungslosem Warten
geführt hat.

### 6.2 Autonom lernende DNA-Regeln

Wenn der Debugger ein bestimmtes, deterministisch erkennbares Fehlermuster
zum ersten Mal findet (aktuell: ein falsch platzierter `"think"`-Parameter
in einem Ollama-API-Aufruf, oder eine selbst eingestandene
Simulation/Attrappe im generierten Code), schreibt er automatisch eine neue
Zeile in `coder_dna_regeln` — ohne Freigabe-Gate. Der Code-Kommentar
begründet das explizit: „ein Freigabe-Gate-VORSCHLAG löst das Kernproblem
nicht — die Lehre erreicht die DNA erst, wenn ein Mensch sie manuell
abnickt" (Betreiber-Zitat im Quelltext: „wenn ein Mentor danebenstehen muss und
sie immer ermahnen, ist das keine Autonomie!"). Die Sicherheitsbegründung
ist strukturell, nicht nur behauptet: die Regel ist rein additiv (macht nie
schlechteren Code möglich, nur eine zusätzliche Einschränkung) und beruht
auf Regex-Erkennung statt auf einem LLM-Urteil — kein
Halluzinations-Risiko in der Erkennung selbst. Jede autonome Änderung wird
dennoch transparent in `mentor_actions_log` protokolliert
(`status='auto_angewendet'`), nicht versteckt.

### 6.3 Mentor-Autokorrektur

`master_loop.mentor_autokorrektur_schritt()` gibt liegengebliebenen Tasks
(`review_pending` oder `archived`) einmal täglich pro Task automatisch eine
frische Chance durch Debugger und Analyst — OHNE Neugenerierung durch den
Coder, um das dokumentierte Regressionsrisiko einer kompletten
Coder-Neuschreibung an möglicherweise fast fertigem Code zu vermeiden. Eine
Gesamt-Obergrenze (`mentor_autokorrektur.max_versuche`, Standard 5)
verhindert, dass ein strukturell festsitzender Fall über viele Tage hinweg
endlos neu versucht wird, ohne dass je ein Mensch die eigentliche Ursache
klärt — nach Erreichen der Grenze wird eine echte Eskalation geloggt statt
eines weiteren automatischen Versuchs.

### 6.4 Startup-Sweep

`werft_startup.sweep()` läuft einmalig bei jedem `master_loop`-Start (nicht
pro Zyklus) und räumt strukturell auf: doppelte `master_loop`-Prozesse
killen, Datenbank-Integrität prüfen, verwaiste Funktions-Registry-Punkte
löschen (ein Fund zeigte, dass ein Registry-Eintrag den Coder wiederholt an
seinem eigenen, längst überholten Muster „vergiften" konnte), veraltete
Debugger-Findings schließen, Systemmap-Konsistenz gegen die reale Datei
prüfen. Bewusst additiv/reversibel und ohne Freigabe-Gate — dieselbe
Risikokategorie wie die autonomen Coder-DNA-Regeln.

### 6.5 Log-Pflege

`mentor_log_pflege.py` schließt automatisch Eskalationen in
`mentor_actions_log`, die sich von selbst erledigt haben (die zugehörige
Bestellung ist inzwischen `geliefert`) oder byte-identische Duplikate
einer wiederkehrenden Meldung sind — nur die jüngste Instanz bleibt offen.
Ausdrücklich nicht destruktiv: es wird nur der Status gesetzt, keine Zeile
gelöscht. Grund laut Code-Kommentar: ohne diese Pflege ertrinken echte
offene Fälle zwischen Hunderten längst erledigten Einträgen
(„Alarm-Ermüdung").

## 7. Ressourcen-Management

Die Werft ist für heterogene, oft bescheidene lokale Hardware gebaut, nicht
für einen dedizierten Cluster. Drei Bausteine tragen das:

**Ersteinrichtung** (`ersteinrichtung.py`): ein deterministischer
Hardware-Scan (RAM, GPU-Hersteller/VRAM über `nvidia-smi`/`rocm-smi`,
vorhandene Ollama-Modelle) leitet eine Tier-Empfehlung ab (groß/mittel/
klein, oder „API empfohlen" bei zu schwacher Hardware) — reine
Ground-Truth-Fakten, kein Sprachmodell-Urteil. Ein optionaler Benchmark
misst echte Tokens/Sekunde eines bereits vorhandenen Modells über Ollamas
eigene Timing-Felder, trifft aber selbst keine automatische Entscheidung:
„die Interpretation, was 'schnell genug' ist, bleibt bewusst beim
Menschen." Diese Empfehlung deckt allerdings nur sechs Rollen ab (Coder,
Analyst, Summarizer, Router, Stratege sowie ein `theke`-Eintrag für ein
verwandtes Projekt) — Debugger und Judge bekommen ihren Modell-Default
unabhängig davon direkt beim Anlegen der Datenbank gesetzt (siehe
Abschnitt 10, bekannte Lücken).

**Ressourcen-Beobachtung** (`ressourcen_status.py`): liest Ollamas
`/api/ps`-Endpunkt aus, um zu sehen, welche Modelle gerade in welchem
Prozessor geladen sind — reine Beobachtung, keine Entscheidung, nach
eigenem Docstring „wirkungslos (nicht fehlerhaft)" bei einem reinen
API-Provider-Betrieb ohne lokales Ollama.

**Swap-bewusste Rollenreihenfolge** (`master_loop._sortiere_rollen_swap_
bewusst()`, `_beobachte_modell_swap()`): bevor eine Rolle ein Modell
aufruft, prüft die Werft, ob dafür ein bereits geladenes anderes Modell aus
demselben Prozessor verdrängt („evicted") werden müsste, und ordnet die
Ausführungsreihenfolge innerhalb eines Zyklus so um, dass unnötiges
wiederholtes Nachladen vermieden wird — ein dokumentierter historischer
Fund zeigt bis zu 54 Eviction-Zyklen seit dem letzten Neustart, weil
mehrere Rollen ohne eigene Host-Zuweisung standardmäßig auf derselben GPU
landeten.

## 8. Grenzen und bewusste Designentscheidungen

**Langsam mit Absicht.** Ein Durchlauf befragt mehrere lokale Modelle
nacheinander; eine Bestellung braucht Minuten bis Stunden, nicht Sekunden.
Der Ausgleich: jeder Schritt ist nachvollziehbar und in der Datenbank
belegt, nicht nur im flüchtigen Kontext eines Chats.

**Die Werft prüft nicht inhaltlich, WAS bestellt wird** (`bestellungen.py`,
Abschnitt 12.1 der internen Spezifikation) — nur, ob die Bestellung
eindeutig genug formuliert ist, um gebaut zu werden. Ob die Idee dahinter
sinnvoll ist, entscheidet der Mensch, der sie aufgibt.

**Menschliche Entscheidung ist ein fester Bestandteil des Zyklus, kein
Fehlerfall.** `review_pending` ist ein bewusstes Sicherheitsventil gegen
Endlosschleifen, kein Bug — die Kehrseite ist, dass ein übersehener
`review_pending`-Task die Werft an genau dieser Bestellung stillstehen
lässt, dokumentiert bis zu mehreren Tagen in einem Referenzsystem.

**Bekannte, im Code selbst dokumentierte Lücken** (nicht behoben, weil sie
über den Rahmen einer reinen Fehlerbehebung hinausgehen würden):

- Der Status-Wert für „archiviert" wird in `task_pipeline` inkonsistent
  geschrieben — teils als `'archived'` (englisch, z.B. in
  `master_loop.requeue_schritt()`), teils als `'archiviert'` (deutsch, z.B.
  in `debugger.py`, das dies selbst im Kommentar als Migrationsschritt
  vermerkt, aber nicht überall vollzogen hat).
- `ersteinrichtung.py`s Hardware-Tier-Empfehlung deckt Debugger und Judge
  nicht ab (siehe Abschnitt 7) — auf sehr schwacher Hardware kann die
  Ersteinrichtung „API empfohlen" für die Kernrollen vorschlagen, während
  Debugger/Judge/Stratege trotzdem den Datenbank-Default `qwen3:14b`
  behalten.
- Das in `master_loop.py` und `bestellungen.py` importierte Modul
  `external_orders` (für den Import externer Bestellungen und das
  Zurückschreiben von Ergebnissen) ist in diesem Auslieferungsstand nicht
  im `core/`-Ordner enthalten — ohne es startet `master_loop.py` nicht.
  Siehe [INSTALL.md](INSTALL.md) für die beiden möglichen Übergangslösungen.

Diese Lücken werden hier bewusst benannt statt stillschweigend
„repariert" zu präsentieren — die Vollständigkeit dieses Dokuments hängt
davon ab, tatsächlich nur zu behaupten, was im Code nachprüfbar ist.
