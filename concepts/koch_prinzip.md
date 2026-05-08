# Das Koch-Prinzip: Trennung von Wissen und Denken

*Autor: Franz Zollner mit Sokrates (Claude Sonnet 4.6)*
*Kategorie: Architektur-Konzepte*

---

## Die Ausgangsfrage

Wie viel muss ein KI-Modell wissen, um nützlich zu sein?

Die intuitive Antwort: so viel wie möglich. Größere Modelle, mehr Training,
mehr Parameter — das scheint der Weg zu besserem Output.

Die Antwort dieser Architektur: **so wenig wie möglich** — aber mit dem richtigen Zugriff
auf das Wissen das gebraucht wird.

---

## Die Koch-Analogie

Ein Koch braucht kein Weltwissen über Kochen.

Er braucht zwei Dinge:
1. **Kochkompetenz** — er weiß wie man kocht, wie man Techniken anwendet, wie man
   Qualität bewertet.
2. **Ein Rezeptbuch** — das konkrete Wissen für das aktuelle Gericht, das er nachschlagen kann.

Das Rezeptbuch muss nicht im Gedächtnis des Kochs sein. Es reicht, wenn er es zur Hand hat
und weiß wie er es benutzt.

**Übersetzung auf KI-Architektur:**
- Kochkompetenz = Denkprozess, eintrainiert ins kleine Modell
- Rezeptbuch = Vektordatenbank (VDB) mit domänenspezifischem Wissen
- Küche = die Pipeline aus spezialisierten Agenten

---

## Das Prinzip

**Wissen gehört nicht ins Modell. Wissen gehört in die externe Datenbank.**

Ein Modell das auf Fakten trainiert wurde, kann diese Fakten halluzinieren, veralten
oder verwechseln — weil das Training ein statistischer Prozess ist, kein präzises
Speichern.

Ein Modell das in einer VDB nachschlägt, gibt dasselbe zurück was dort steht.
Wenn die VDB falsch ist, ist das Ergebnis falsch — aber es ist kein Halluzinieren,
es ist ein bekannter Fehler der behoben werden kann.

---

## Konsequenzen für die Architektur

### Kleine Modelle + große VDB > große Modelle allein

Ein 3-8B Parameter Modell (phi4-mini, Qwen2.5-Coder) mit Zugriff auf eine
167.000-Punkte-VDB übertrifft ein 70B-Modell ohne VDB bei domänenspezifischen
Aufgaben — weil es direkt auf die relevanten Fakten zugreift statt sie aus
dem statistischen Training zu rekonstruieren.

### Lokale Hardware reicht

Große Modelle (70B+) brauchen Rechenzentren. Kleine spezialisierte Modelle (3-14B)
laufen auf einer einzelnen Consumer-GPU (AMD RX 7900 XT, 20 GB VRAM).

Das macht **vollständig lokale** KI-Systeme möglich — ohne Cloud-Kosten,
ohne Datenschutzbedenken, ohne Abhängigkeit von externen APIs.

### Lernen ohne Retraining

Wenn neues Wissen in die VDB eingepflegt wird, lernt das System — ohne das
Modell neu trainieren zu müssen. Das ist der entscheidende Unterschied zu
klassischem Machine Learning.

Der Kammerad der heute über Thermodynamik nachschlägt, hat morgen Zugriff auf
neue Erkenntnisse — einfach weil ein neues Dokument in die VDB eingefügt wurde.

---

## Die zwei Schichten jedes Kammeraden

Jeder spezialisierte Agent (Kammerad) in der BIB-KI hat genau zwei Schichten:

**Schicht 1: Denkprozess** (im Modell)
- Wie formuliert man eine Anfrage an die VDB?
- Wie bewertet man die Ergebnisse?
- Was ist zu tun wenn nichts gefunden wird? (G-6: Scout triggern)
- Wie produziert man strukturierte Ausgabe?

**Schicht 2: Fachwissen** (in der VDB)
- Technische Dokumentation
- Frühere Ergebnisse und Erkenntnisse
- Domänenspezifische Fakten
- Fehlerhistorie (was ist schiefgelaufen und warum)

---

## G-6: "Ich weiß es nicht — aber ich mache mich schlau"

Das wichtigste Verhalten das das Koch-Prinzip ermöglicht:

Wenn ein Kammerad eine Frage nicht beantworten kann, sucht er in der VDB.
Wenn die VDB keine Antwort hat, löst er einen Scout aus der extern recherchiert.
Er halluziniert nicht — er eskaliert.

```
Anfrage
  ↓
VDB-Suche: Antwort gefunden? → Antworten
  ↓ nicht gefunden
Scout-Auftrag: "Recherchiere X"
  ↓
VDB-Update → neue Anfrage → Antworten
```

Das ist kein Mangel, sondern ein Feature: **Unsicherheit wird kommuniziert,
nicht versteckt.**

---

## Warum das funktioniert

Das Koch-Prinzip funktioniert weil es ein kognitives Problem mit einer
informationstechnischen Lösung adressiert:

Das menschliche Arbeitsgedächtnis ist klein. Ein KI-Kontextfenster ist groß aber
begrenzt. Eine VDB ist praktisch unbegrenzt und persistent.

Wissen das in einer VDB liegt, **altert nicht** (bis es explizit überschrieben wird).
Wissen das im Kontext liegt, verschwindet nach der Sitzung.

---

## Praktische Leitfragen

Wenn man ein neues KI-System mit diesem Prinzip aufbaut:

1. **Was muss das Modell können?** (Denkprozess)
   → Möglichst wenig, möglichst präzise

2. **Was muss das System wissen?** (VDB)
   → Alles domänenspezifische Wissen, extern gespeichert

3. **Wie kommt neues Wissen in die VDB?** (Lernzyklus)
   → Scout, manuelle Eingabe, Pipeline-Ergebnisse

4. **Wann weiß das Modell genug?**
   → Wenn es G-6 korrekt anwendet (nachschlagen, nicht raten)

---

*© 2026 Franz Zollner — Lizenz: CC BY-NC 4.0*
*Dieses Dokument ist Teil des KI-Konzepte-Repositories.*
