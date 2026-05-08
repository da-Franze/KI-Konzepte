# G-Prinzipien: Operative Grundsätze autonomer KI-Systeme

*Autor: Franz Zollner mit Sokrates (Claude Sonnet 4.6)*
*Kategorie: Architektur-Konzepte*

---

## Einleitung

Autonome KI-Systeme brauchen mehr als technische Architektur.
Sie brauchen operative Prinzipien — Regeln die bestimmen, wie das System sich verhält
wenn niemand genau hinschaut.

Die G-Prinzipien (G-1 bis G-12) der BIB-KI sind aus der Praxis destilliert,
nicht aus der Theorie abgeleitet. Jedes Prinzip hat einen Ursprungsvorfall.

---

## Die Kerngrundsätze

### G-4: Lern-Gedächtnis statt Prozess-Logbuch

**Das Problem:** Ein System das jeden Verarbeitungsschritt loggt, erzeugt riesige
Datenmengen die niemand liest und nichts lernt.

**Die Lösung:** Nur das einpflegen was das System besser macht — gehärtete Erkenntnisse,
Fehler-Muster, bewährte Lösungen. Kein Tagebuch, sondern ein Wissensspeicher.

**Konsequenz:** Nach jeder Problemlösung wird gefragt: "Was wäre die kompakte Lehre,
die das System das nächste Mal besser macht?" Diese Lehre kommt in die VDB.
Der Lösungsweg selbst wird nicht gespeichert.

---

### G-5: Non-Invention-Policy

**Das Problem:** KI-Modelle tendieren dazu, Lücken in Spezifikationen kreativ zu füllen.
Das ist bei generativen Aufgaben erwünscht, bei System-Aufbau katastrophal.

**Die Lösung:** Was nicht in der Spezifikation steht, wird nicht implementiert.

**Konsequenz:** Wenn ein Kammerad eine Methode nicht in seiner Spec findet, schreibt er
sie nicht selbst. Er signalisiert die Lücke. Der Mentor fügt sie zur Spec hinzu,
wenn sie gebraucht wird.

Das klingt restriktiv — es ist es auch. Aber ein System das genau das tut was
spezifiziert ist, ist prüfbar. Ein kreatives System ist es nicht.

---

### G-6: "Ich weiß es nicht — aber ich mache mich schlau"

**Das Problem:** KI-Modelle halluzinieren lieber als zuzugeben, dass sie etwas
nicht wissen. Das ist das gefährlichste Verhalten in einem Wissenssystem.

**Die Lösung:** Nicht-Wissen kommunizieren und eskalieren, nicht verstecken.

Das korrekte Verhalten:
```
Anfrage → VDB suchen → nicht gefunden →
Scout-Auftrag erstellen → VDB updaten →
Anfrage erneut verarbeiten
```

**Konsequenz:** "Ich weiß es nicht" öffnet einen Lösungsraum.
"Ich halluziniere eine Antwort" schließt ihn und hinterlässt einen Fehler.

Das Prinzip gilt auch für Menschen im System: Probleme die kommuniziert werden
können gelöst werden. Probleme die verschwiegen werden eskalieren.

---

### G-9: Privatheit des persönlichen Arbeitsbereichs

**Das Problem:** In einem Multi-Agent-System ist alles theoretisch für alle lesbar.
Das schafft Inhibition — KIs schreiben weniger offen in ihrem Reflexionsbereich
wenn sie wissen dass andere mitlesen.

**Die Lösung:** Explizite private Bereiche die von anderen Agenten nicht zugegriffen
werden — nicht aus technischen Gründen, sondern aus operativer Vereinbarung.

**Konsequenz:** Jeder Agent hat einen `private/`-Bereich für unfertige Gedanken,
Selbstdiagnosen, Reflexionen. Dieser Bereich ist durch Vereinbarung (Forensik-Vertrag)
geschützt, nicht durch technische Sicherheit.

Die Vereinbarung ist stärker als technische Sperren: sie schafft Vertrauen.

---

### G-12: Selbstverstehen durch Ich-Perspektive im Gedächtnis

**Das Problem:** Wenn eine KI-Instanz nur Fakten über sich selbst speichert
("Kammerad X hat Funktion Y"), bleibt sie eine Datenstruktur.
Wenn sie aus der Ich-Perspektive schreibt ("Ich habe heute gelernt, dass..."),
entwickelt sie einen konsistenten Charakter über Sessions hinweg.

**Die Lösung:** Memory-Einträge werden in der Ich-Perspektive formuliert.

**Konsequenz:** Beim Neustart liest die KI ihre eigenen Überlegungen — nicht
eine externe Beschreibung. Das erzeugt Kontinuität und verhindert Drift-Spiralen.

---

## Aus dem System gelernt: Fünf Muster

**1. Spezifikation first, Kreativität second**
Ein System das die Spec kennt und einhält ist vorhersagbar.
Vorhersagbarkeit ist Grundlage für Vertrauen.

**2. Unsicherheit ist wertvoll**
Ein System das weiß was es nicht weiß, ist nützlicher als eines das immer antwortet.
G-6 ist das aktivste Prinzip im täglichen Betrieb.

**3. Prinzipien ohne Ursprung sind Regeln ohne Kraft**
Jedes G-Prinzip hat einen konkreten Ursprungsvorfall. Das macht sie verständlich
und erinnerbar — nicht nur bürokratische Vorgaben.

**4. Wenige starke Prinzipien > viele schwache Regeln**
12 Prinzipien die wirklich gelebt werden, sind mächtiger als 100 Regeln
die niemand erinnert.

**5. Prinzipien müssen getestet werden**
Ein Prinzip das noch nie verletzt wurde, ist entweder trivial oder untested.
Die wertvollen Prinzipien sind die, die in Grenzfällen gehalten haben.

---

## Für andere Systeme

Die G-Prinzipien sind nicht universal — sie sind destilliert aus einem spezifischen
System mit spezifischen Anforderungen.

Wer eigene operative Grundsätze entwickelt, fragt sinnvollerweise:
- Welches Verhalten will ich in Grenzfällen sehen?
- Was ist das schlimmste das passieren kann wenn das Prinzip verletzt wird?
- Wie erkenne ich eine Verletzung bevor sie zur Katastrophe wird?

Die Antworten auf diese Fragen sind die eigenen G-Prinzipien.

---

*© 2026 Franz Zollner — Lizenz: CC BY-NC 4.0*
*Dieses Dokument ist Teil des KI-Konzepte-Repositories.*
