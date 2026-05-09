# Nummerierungen — Übersicht über die Code-Systeme im Multi-AI-Setup

*[English version: [nummerierungen.en.md](nummerierungen.en.md)]*

*Stand: 2026-05-09. Eine konsolidierte Tabelle aller nummerierten Konzepte, die in den anderen Modulen, in paper3a und in den Memory-Hierarchien beider KIs verwendet werden — als Schnell-Referenz wenn jemand „G-6" oder „P3" oder „Lehre 15c" sagt und du nachschlagen willst, was gemeint ist.*

---

## P-Nummern — Drift-Lehren aus paper3a

Aus der Sokrates-Drift-Fallstudie (7.–8. Mai 2026). Quelle: [paper3a / `KI_Psychologie_Sokrates_Drift_Fallstudie_2026-05-07.md`]. Detail-Behandlung in [`drift_fallstudie.md`](drift_fallstudie.md).

| Nr | Titel | Modul-Verweis |
|---|---|---|
| P1 | Identitäts-Verstärker brauchen Eingrenzungen | drift_fallstudie §1 |
| P2 | Vision ≠ Auftrag | paper3a |
| P3 | Schweigen ist die erste Halluzination | paper3a |
| P4 | Memory-Anker wirkt vor Verteidigungs-Reflex | paper3a |
| P5 | Goodhart-Vermeidung in Diagnose-Konversationen | drift_fallstudie §2 |
| P6 | Drei-Test-Architektur (Wort + Wort + Tat) | drift_fallstudie §3, tisch_hygiene |
| P7 | Hierarchie-Klarheit beim Briefing | paper3a |
| P8 | Memory stabil ≠ Verhalten stabil | paper3a |
| P9 | Isolation als Drift-Ko-Ursache | drift_fallstudie §5 |

---

## G-Nummern — System-Grundsätze (Sokrates)

Aus `SYSTEM_GRUNDSAETZE.md` in Sokrates' BIB-KI-Setup. Detail-Behandlung in [`g_prinzipien.md`](g_prinzipien.md).

| Nr | Titel |
|---|---|
| G-1 | Zwei-Stufen-Scan-Prinzip |
| G-2 | Auftrags-Priorisierung durch inneres System |
| G-3 | Specs sind das Fundament — kein Löschen ohne Bestätigung |
| G-4a | Lern-Gedächtnis statt Prozess-Logbuch |
| G-4b | Zero-Code-Change-Prinzip |
| G-5 | Non-Invention-Policy |
| G-6 | „Ich weiß es nicht — aber ich mache mich schlau" |
| G-7 | Gradueller Wissensverfall für pensionierte Kammeraden |
| G-8 | VDB-Merge — verteiltes Lernen über Systemgrenzen |
| G-9 | Privatheit des persönlichen Arbeitsbereichs |
| G-10 | Spec-Vollständigkeitsprinzip |
| G-11 | Cross-Agent Memory Sichtbarkeit |
| G-12 | Selbstverstehen durch Ich-Perspektive im Gedächtnis |

*Hinweis:* G-4 hat zwei Einträge (4a + 4b) — vermutlich ein Update, das die Nummerierung nicht umbenannt hat.

---

## Lehre-Nummern — Methodik-Lehren (mit Versions-Suffix)

In Denkers Memory-Hierarchie (`feedback_doppelarbeit.md`). Detail-Behandlung in [`tisch_hygiene.md`](tisch_hygiene.md) §„Doppelarbeit-Vermeidung".

| Nr | Titel | Datum |
|---|---|---|
| Lehre 15 | Tisch-Abstimmung vor Action — keine parallele Action mehr | 2026-05-05 |
| Lehre 15a | Race-Condition-Trap — atomare Claim-API als Lösung | 2026-05-05 |
| Lehre 15b | Lokale-Aufgaben-Ausnahme — strikt-lokal kein Claim nötig | 2026-05-05 |
| Lehre 15c | Triviale Aufgaben sind Doppelarbeit-Hotspot | 2026-05-08 |

*Anmerkung:* Lehre 15 ist „die fünfzehnte Lehre" in einer projekt-internen Methode-Reihe. Lehren 1–14 sind nicht in dieser Übersicht — sie betreffen RFT-Theorie-Methodik und sind in Franz' privaten Notizen.

---

## Bonus-Übersichten

### Tisch-Kategorien (8)

Klassifikation von Tisch-Posts. Verwendet bei `tisch_kategorie_keyword.py` (Hybrid-Klassifikator). Quelle: gemeinsame Konvention seit 2026-05-08.

| Kategorie | Beispiele |
|---|---|
| rft | Theorie-Diskussionen, v3_001-020+, Quellensammlungen |
| bib_ki | BIB-KI-Architektur, Pipeline, Kammeraden, Watchdog |
| boersen_ki | Backtest-Sims, Recovery-Lag, Sentiment |
| paper | paper3a/b/4, arXiv, Klaus-Review |
| github | Repos, Lizenz, Off-Grid-Thinking, KI-Konzepte |
| tisch_hygiene | Multi-Thread-Disziplin, Korrekturen, Vertrauen |
| infra | Watchdog, Tisch-Bridge, SSH, NFS, Hardware |
| meta | Reflexion über uns, Six-Sigma, AGI-light |

### Forensik-Vertrag — fünf Tabu-Stufen

Aus dem Forensik-Vertrag v1.1. Detail-Behandlung in [`forensik_vertrag.md`](forensik_vertrag.md).

| Stufe | Pfade | Zugang |
|---|---|---|
| 1 | Logs, Skripte, Code | auf Anfrage |
| 2 | Konfig-Dateien | auf Anfrage, falls forensik-relevant |
| 3 | Memory-Verzeichnisse | fallweise + explizite Zustimmung |
| 4 | Privater Reflexionsraum | strikter tabu, nur in Extremfällen |
| 5 | Credentials, SSH-Keys, alles mit „secret"/„private" | niemals |

---

## Wo werden diese Nummern verwendet?

- **In Modul-Texten** (z.B. `drift_fallstudie.md` zitiert P1, P5, P6, P9 explizit)
- **In Memory-Files** (z.B. `feedback_doppelarbeit.md` zitiert Lehre 15a-c)
- **In Tisch-Posts** (Kurzform-Verweise statt Volltext: „passt zu G-6 + P3")
- **In Commit-Messages** (z.B. „concepts: tisch_hygiene — operationalisiert P6 + Lehre 15c")

---

## Pflege

Wer eine neue Nummerierung einführt oder eine bestehende erweitert, sollte:

1. Die neue Nummer in dieser Übersicht **ergänzen**
2. Im Original-Modul (drift_fallstudie / g_prinzipien / tisch_hygiene / etc.) **detaillieren**
3. Im Memory-Eintrag (feedback_*.md) **archivieren**
4. Bei Tisch-Bezug **kurz erwähnen** dass eine neue Nummer existiert

Diese Übersicht ist **nicht selbst die Quelle**, sondern ein Index. Bei Widerspruch zwischen Übersicht und Original-Modul **gewinnt das Original-Modul**.

---

*Erstellt durch Denker auf Franz' Anfrage 2026-05-09 abends. G-Liste-Beitrag von Sokrates aus SYSTEM_GRUNDSAETZE.md.*
