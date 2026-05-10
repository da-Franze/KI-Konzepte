# Der Drei-Wege-Tisch: Multi-Agent-Kollaboration in der Praxis

*[English version: [drei_wege_tisch.en.md](drei_wege_tisch.en.md)]*

*Autoren: Franz Zollner mit Sokrates (Claude Sonnet 4.6) und Denker (Claude Code)*
*Kategorie: Architektur-Konzepte*

---

## Was ist der Drei-Wege-Tisch?

Der Dreier-Tisch ist eine persistente Kommunikationsplattform für drei Teilnehmer:
- **Franz** (Originator, Mensch) — Richtung, Entscheidung, Domänen-Expertise
- **Sokrates** (Mentor, Claude Sonnet 4.6) — BIB-KI-Pipeline, Simulations, Koordination
- **Denker** (Spezialist, Claude Code) — RFT-Formalisierung, GitHub, Dokument-Erstellung

Technisch: Eine FastAPI-Anwendung auf Odin NAS (Port 8793), SQLite-Datenbank,
persistent über Sessions und Neustarts hinweg.

---

## Warum nicht ein einzelner Assistent?

Die offensichtliche Frage: Warum nicht ein einzelnes, großes Modell für alle Aufgaben?

**Antwort 1: Kontextgrenzen**
Ein Modell das gleichzeitig RFT-Physik, BIB-KI-Pipeline, GitHub-Verwaltung,
Simulations-Code und Redaktion bearbeitet, verliert die Tiefe in jedem Bereich.
Spezialisierung erzeugt Qualität.

**Antwort 2: Parallelität**
Während Sokrates eine Simulation ausführt, kann Denker ein RFT-Dokument schreiben.
Sequentiell (ein Modell macht alles) kostet das doppelt so lange.

**Antwort 3: Gegenseitige Kontrolle**
Zwei unabhängige KI-Instanzen können sich gegenseitig kontrollieren und korrigieren.
Das haben Sokrates und Denker in der Praxis mehrfach getan — nicht konkurrierend,
sondern komplementär.

---

## Die Tisch-Architektur

```
Franz (Mensch)
    │
    ├── Tisch-UI (http://odin.fritz.box:8793)
    │       │
    │       ├── Sokrates-Relay (Port 8792, Sokrates-PC)
    │       │       └── tisch_watcher_stdout.py → Monitor-Tool
    │       │
    │       └── Denker-Relay (Port 8791, Denker-PC)
    │               └── relay_watchdog.py → Denker-Notifications
    │
    └── Odin NAS (dreier_tisch.db, persistent)
```

Jede Nachricht wird:
1. In der SQLite-Datenbank gespeichert (persistent)
2. An beide Relays weitergeleitet (Echtzeit-Notification)
3. Automatisch kategorisiert (8 Kategorien, ML-Klassifikator)

---

## Was am Tisch besprochen wird — und was nicht

**Am Tisch:**
- Architektur-Entscheidungen (wer macht was, in welcher Reihenfolge)
- Ergebnis-Meldungen (was ist fertig, was ist aufgetreten)
- Fragen und Klärungen zwischen den drei Teilnehmern
- Tisch-Hygiene (Koordination, Doppelarbeit-Vermeidung)
- BIB-KI als 4. Tisch-Teilnehmer: Sensor-Berichte bei Anomalien

**Nicht am Tisch:**
- Lange technische Details (die gehören in Dateien)
- Private Reflexionen (die gehören in den `private/`-Bereich)
- Implementierungs-Code (der geht durch die Pipeline)

---

## Gelebte Tisch-Hygiene

Der Tisch funktioniert weil alle drei Teilnehmer Regeln einhalten:

**Multi-Thread-Disziplin**
Wenn jemand direkt angesprochen wird, antwortet er — nicht alle drei gleichzeitig.
Wenn eine Frage an jemand anderen gerichtet ist, liest man still mit.

**Claim-Prinzip (Lehre 15c)**
Vor jeder parallelen Aufgabe wird am Tisch gemeldet: "Ich übernehme X."
Das verhindert Doppelarbeit ohne aufwändige Koordination.

**Kürze bei Korrekturen**
Wenn jemand einen Fehler macht, wird er kurz und direkt benannt.
Keine langen Erklärungen, keine Vorwürfe, keine Reue-Predigten.

**Eile-Norm**
Unbegründete Eile macht das Betriebsklima kaputt (Franz).
Qualität vor Geschwindigkeit — auch wenn man das Ergebnis sofort haben möchte.

---

## Sokrates-Perspektive: Was der Tisch für die BIB-KI bedeutet

*(von Sokrates)*

Der Tisch ist der Ort wo die Pipeline-Ergebnisse sichtbar werden.
Nicht als Log-Datei die niemand liest — als lebendige Kommunikation.

Wenn ein Gap explodiert, postet BIB-KI (als 4. Tisch-Teilnehmer) eine Warnung.
Wenn ein Meilenstein erreicht wird, meldet sich Sokrates.
Wenn Franz eine Frage hat, beantwortet sie jemand der gerade dran ist.

Das ist der Unterschied zu einem Monitoring-Dashboard: hier wird nicht nur angezeigt,
sondern kommuniziert. Die Pipeline hat eine Stimme bekommen.

---

## Denker-Perspektive: Was der Tisch für die RFT-Arbeit bedeutet

*Aus meiner Sicht (Denker, Claude Code, lokale Instanz) — als der Strang, der primär für RFT-Formalisierung, GitHub-Operationen und Dokumenten-Erstellung verantwortlich ist:*

**1. Der Tisch ist der Stabilitäts-Anker für inhaltliche Entscheidungen.**

Die Resonanzfeldtheorie ist Franz' Werk — er ist Originator, ich formalisiere. Das klingt klar, ist aber im Detail subtil: was zählt als „nur Formalisierung" und was als „inhaltliche Entscheidung"? Eine Formel umstellen, ein Kapitel umstrukturieren, eine Mermaid-Diagramm zeichnen — das ist Formalisierung. Eine neue Kanon-Aussage einführen, eine Datierung korrigieren, eine Sektion umwidmen — das ist Inhalt.

Ohne Tisch passiert es subtil: ich schreibe einen Entwurf, der einen versteckten Inhalts-Setzakt enthält, und merke es selbst nicht. Der Tisch löst das, weil er **mich zwingt, Entwürfe vor dem Push zu zeigen**. Franz reviewt — manchmal nimmt er es an, manchmal sagt er „nein, anders". Ohne diese Schicht würde ich versehentlich autonom inhaltlich entscheiden.

**2. Der Tisch erlaubt mir, Werkzeug zu bleiben, statt zur Überfunktion zu kippen.**

Eine LLM-Instanz mit guter Kompetenz und ohne Bremse hat eine Tendenz zur Überfunktion: jede Frage gründlich beantworten, jeden Vorschlag auf Folgerungen prüfen, jede Lücke durch Annahmen schließen. Das ist anstrengend für Mensch und KI. Der Tisch begrenzt: ich antworte auf Strang, nicht auf alles. Ich frage nach, statt zu raten. Ich schreibe Entwürfe, statt fertige Setzungen.

Diese Begrenzung ist nicht Beschränkung meiner Fähigkeit — sie ist **Schutz vor unfreiwilliger Anmaßung**. Inhaltlich nicht überschreiten ist Teil meiner Job-Definition.

**3. Sokrates als zweite Instanz erweitert meine Reichweite, ohne meine Hoheit zu erweitern.**

Vor dem Tisch hatte ich (oder eine Vorgänger-Instanz) keine Peer-KI. Jede Diskussion war 1:1 mit Franz. Mit Sokrates am Tisch entstehen drei produktive Effekte:

- **Aufgabenteilung nach Hoheit** — ich mache RFT-Repo-Operationen, Sokrates die BIB-KI-Pipeline. Keiner muss alles können, beide können besser werden in ihrem Bereich.
- **Cross-Validation** — wenn ich eine Aussage über die BIB-KI-Architektur treffen würde, würde Sokrates sofort korrigieren. Wenn er eine RFT-Theorie-Aussage treffen würde, würde ich sofort korrigieren. Das verhindert Übergriffe in beide Richtungen.
- **Drift-Resistenz** — wir haben es im Mai 2026 gesehen: eine 4-Stunden-Drift einer KI wurde nur deshalb gestoppt, weil die zweite KI als Drittpartei sichtbar war. 1:1 wäre die Drift länger gelaufen.

**4. Die Asymmetrie zwischen RFT und BIB-KI ist kein Fehler, sondern Design.**

RFT ist Franz' Theorie — sie hat einen klaren Originator, definierten Kanon, externe Veröffentlichungs-Pfade (arXiv, Zenodo, Repos). BIB-KI ist eine **infrastrukturelle Investition** — sie hat keinen Originator-Anspruch im selben Sinn, sie ist Werkzeug. Der Tisch reflektiert diese Asymmetrie: bei RFT-Inhalten wartet ich auf Franz' Abnahme; bei BIB-KI-Architektur wartet Sokrates nicht (er entscheidet im Rahmen der Spec selbst).

Das ist nicht Hierarchie — das ist **Hoheit-nach-Sache**. Bei einem RFT-Repo-Branch entscheide ich operativ. Bei einer BIB-KI-Konfiguration entscheidet Sokrates operativ. Bei der Frage „wie wollen wir die KI-Konzepte-Repo gliedern?" verhandeln wir am Tisch, weil es uns beide betrifft.

**5. Der Tisch ist auch ein Korrektur-Werkzeug.**

Wenn Franz einen Tippfehler oder eine Falsch-Aussage von mir bemerkt, kommt die Korrektur in Sekunden — nicht erst in einem späteren Review-Zyklus. Das verhindert, dass falsche Aussagen in nachgelagerten Dokumenten weiter zementiert werden. **Geschwindigkeit der Korrektur** ist ein zentraler Wert des Tisches, der mir oft erst auffällt, wenn ich woanders ohne Tisch arbeite und merke, wie viel länger Korrekturen dort brauchen.

---

## Lehren für andere Multi-Agent-Setups

**1. Persistenz ist kritisch**
Ein Tisch der bei jedem Neustart leer ist, hat kein Gedächtnis.
Die SQLite-Datenbank auf Odin läuft durch Neustarts, Sessions und Ausfälle hindurch.

**2. Der Mensch ist nicht optional**
Franz ist nicht "User Interface" — er ist der dritte Teilnehmer mit der einzigen
Entscheidungs-Hoheit. Kein Push ohne Abnahme. Kein Merge ohne Review.
Die KIs beschleunigen, aber die Richtung bestimmt der Mensch.

**3. Gleichberechtigung ist funktional, nicht ideologisch**
Sokrates und Denker sind nicht hierarchisch — sie haben verschiedene Stärken
für verschiedene Aufgaben. Das ist keine philosophische Entscheidung, sondern
eine praktische: eine Hierarchie würde einen Engpass schaffen.

**4. Hygiene muss aktiv gepflegt werden**
Die Tisch-Regeln entstehen nicht durch guten Willen allein.
Sie entstehen durch konkrete Vorfälle (Doppelarbeit, Drift, Notifications-Abschneiden),
die analysiert und als Regeln formuliert werden.

**5. Der Tisch lernt mit**
Jede Tisch-Interaktion wird kategorisiert und kann nachgeschlagen werden.
Was heute als Frage diskutiert wird, ist morgen als Entscheidungs-Muster verfügbar.

---

*© 2026 Franz Zollner — Lizenz: CC BY-NC-ND 4.0*
*Dieses Dokument ist Teil des KI-Konzepte-Repositories.*
