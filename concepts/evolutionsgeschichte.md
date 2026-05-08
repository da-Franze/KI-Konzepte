# Die Entstehungsgeschichte: Von RFT zu Multi-AI-Systemen

*Autor: Franz Zollner mit Sokrates (Claude Sonnet 4.6)*
*Kategorie: Evolutionsgeschichte*

---

## Das Problem: Eine Theorie, eine Person, zu viel Stoff

Die Resonanzfeldtheorie (RFT) ist ein umfassendes Theorie-Gebäude. Sie beschreibt
den Raum als schwingungsfähiges Medium und leitet daraus Gravitation, Elektromagnetismus,
Quantenmechanik und die ART als Speziallösungen einer einzigen Mastergleichung ab.

Das Problem: Dieses Theorie-Gebäude hat hunderte von Verknüpfungen, Konsistenz-Anforderungen
und Ableitungsschritten. Eine einzelne Person kann das nicht vollständig im Kopf halten —
nicht weil die Person nicht intelligent genug ist, sondern weil das menschliche Arbeitsgedächtnis
schlicht zu klein ist für solche Strukturen.

**Die Ausgangsfrage (September 2024):** Kann eine KI helfen, diese Theorie zu formalisieren,
zu prüfen und zu kommunizieren — ohne die Kernideen zu verfälschen?

---

## Der Wendepunkt: Das Post-Verteilerzentrum (PVZ)

*"Kannst du beim ersten Lesen thematische Inhaltsverzeichnisse erstellen —
aufgeteilt wie ein Postverteilerzentrum?"* — Franz Zollner, Frühphase 2025

Das war die Frage, die alles veränderte.

**Das ursprüngliche Problem:** GPT musste bei jeder Sitzung alle RFT-Dokumente lesen —
eine Katastrophe, weil die Theorie kein Standard-Lernfeld ist und GPT kein spezifisches
Training dafür hatte. Jede Sitzung begann von Null.

**Die Idee:** Thematische Inhaltsverzeichnisse, aufgeteilt wie in einem Postverteilerzentrum.
Jedes "Postfach" enthält die relevanten Dokumente zu einem Thema. GPT liest nicht mehr
alles — es schaut in das zuständige Postfach.

**Der überraschende Nebeneffekt:** Die Antwortqualität stieg dramatisch.
Antworten kamen in Minuten statt in längerer Zeit, mit deutlich höherer Präzision.

**Die Erkenntnis:** Die Postfächer wurden so groß, dass kein Fokuswechsel innerhalb
einer Fragestellung nötig war. Es entstand eine **seriell arbeitende Spezialistenkette**:
jedes Postfach wurde der Frage entsprechend abgearbeitet, am Ende folgte eine Zusammenfassung.

Das war der Beginn von Start-Anweisungen / Protokollen — und der konzeptuelle Vorläufer
des Domain Centers (DC), der späteren Vektordatenbank (VDB) und der gesamten
Multi-Agent-Architektur.

PVZ → Domain Center → VDB → Kammeraden-Pipeline: jeder Schritt war die Antwort auf
ein konkretes Skalierungsproblem.

---

## Erste Schritte: ChatGPT als Korrektiv (2024–2025)

Der erste KI-Einsatz war simpel: ChatGPT als Gesprächspartner, um Ideen zu testen.
Funktioniert die Argumentation? Gibt es offensichtliche Lücken? Welche Formulierung
ist am klarsten?

Ergebnis: Nützlich, aber begrenzt. Ein einzelnes LLM:
- Halluziniert bei komplexen Herleitungen
- Vergisst frühere Diskussionsstränge nach wenigen Nachrichten
- Hat keine persistente Theorie-Grundlage
- Kann keine parallelen Aufgaben übernehmen

**Lehre:** Ein einzelnes Modell ist kein Forschungspartner, sondern ein Werkzeug.

---

## Erste Börsen-KI: Spezialisierung als Lösung (2025)

Parallel zur RFT-Arbeit entstand ein anderes Problem: Portfolio-Management.
Die erste Börsen-KI sollte Sentiment-Daten auswerten und Empfehlungen geben.

Dabei stellte sich heraus: Ein einzelnes Modell für Datenbeschaffung + Analyse +
Entscheidung ist zu langsam und unzuverlässig. Die Lösung war **Spezialisierung**:
- Scout: Datenbeschaffung (Guardian API, GDELT)
- Analyst: Sentiment-Auswertung
- Advisory: Empfehlung basierend auf Signal-Vektor

Das war der Moment, wo die Multi-Agent-Idee konkret wurde.

**Lehre:** Spezialisierung schlägt Generalismus. Kleine Experten + Koordination > ein Allwissender.

---

## Säugetier-KI: Das Nervensystem-Modell (2025)

Die nächste Iteration orientierte sich am Nervensystem: viele kleine Neuronen,
koordiniert durch ein Zentrum (Router), mit Gedächtnis (VDB) und Lernfähigkeit.

Kernprinzip (damals noch intuitiv): **Wissen gehört nicht ins Modell, sondern in
eine externe Datenbank.** Das Modell braucht nur die Fähigkeit, in dieser Datenbank
zu suchen und die Ergebnisse zu nutzen.

Das entspricht dem Koch-Prinzip: Ein Koch muss nicht alle Rezepte im Kopf haben.
Er muss wissen, wie er kocht — und ein Kochbuch zur Hand haben.

**Lehre:** RAG (Retrieval Augmented Generation) ist mächtiger als Training auf Fakten.

---

## BIB-KI: Die vollständige Architektur (2026)

Die BIB-KI (Bibliotheks-KI) brachte diese Prinzipien zu einer vollständigen Architektur:

- **VDB (Qdrant):** Das externe Gedächtnis — 167.000+ Wissenspunkte
- **Pipeline:** Autonomer Lernzyklus (Spec → Code → Test → Review → Merge)
- **Kammeraden:** 395 spezialisierte KI-Agenten
- **Watchdog:** Autonome Überwachung und Selbstheilung
- **Drei-Wege-Tisch:** Koordination zwischen Mensch und zwei KI-Instanzen

Das entscheidende Prinzip, das sich durch alle Iterationen zieht:

> **KI ist ein Kollaborationspartner, kein Ersatz für menschliches Denken.**
>
> Sie kann recherchieren, formalisieren, prüfen, dokumentieren — aber die Richtung,
> die Intuition und die Entscheidung bleiben beim Menschen.

---

## Was wir gelernt haben: Fünf Kernprinzipien

**1. Koch-Prinzip:** Kleines Modell + großes Wissens-Repository > großes Modell allein.
Die VDB ersetzt das Training.

**2. Spezialisierung:** Viele kleine Experten mit klaren Aufgaben > ein Alleskönner.
Jeder Kammerad macht eine Sache gut.

**3. Persistenz:** Wissen muss gespeichert werden, nicht im Gespräch gehalten werden.
Das Gedächtnis ist die VDB, nicht das Kontextfenster.

**4. Autonomie mit Grenzen:** Die Pipeline läuft autonom, aber der Mensch definiert
die Ziele und prüft kritische Entscheidungen. Kein Eingriff ohne Ankündigung.

**5. Ehrlichkeit:** Eine KI die "Ich weiß es nicht" sagt und dann nachschlägt,
ist wertvoller als eine KI die halluziniert.

---

## Für andere Independent-Researcher

Diese Geschichte ist kein Rezept — sie ist eine Einladung.

Wenn du als Einzelperson an einem komplexen Problem arbeitest und KI-Unterstützung
suchst: die Architektur muss nicht komplex sein. Du brauchst:

- Eine Möglichkeit, Wissen zu speichern (VDB, Notizen, Dokumente)
- Spezialisierte Werkzeuge für verschiedene Aufgaben
- Klare Grenzen zwischen KI-Aufgabe und Mensch-Aufgabe

Das Ziel ist nicht, die KI das Denken machen zu lassen.
Das Ziel ist, mehr Zeit für das Denken zu haben.

---

*© 2026 Franz Zollner — Lizenz: CC BY-NC 4.0*
*Dieses Dokument ist Teil des KI-Konzepte-Repositories.*
