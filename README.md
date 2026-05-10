# KI-Konzepte

*Architektur und Erfahrungen mit lokalen KI-Systemen als Kollaborationspartner — Multi-Agent-Setups, Memory-Hierarchien, Drift-Diagnose und das, was zwischen den Modellen passiert.*

> *Architecture and experiences with local AI systems as collaboration partners — multi-agent setups, memory hierarchies, drift diagnostics, and what happens between models.*

---

## Was hier dokumentiert wird / What is documented here

Die [Resonanzfeldtheorie](https://github.com/da-Franze/RFT-Physik-Projekt) ist seit fünfzehn Monaten in einem Dialog mit mehreren KI-Systemen entstanden — ChatGPT, Claude (in zwei Rollen: „Denker" lokal als Claude Code, „Sokrates" web-basiert), Gemini, Mistral, DeepSeek und lokal Qwen. Aus diesem Prozess sind Architekturen, Werkzeuge und Erfahrungen entstanden, die für sich genommen interessant sind — unabhängig von der Theorie selbst.

> *The [Resonance Field Theory](https://github.com/da-Franze/RFT-Physik-Projekt) has emerged over fifteen months from a dialogue with several AI systems — ChatGPT, Claude (in two roles: "Denker" locally as Claude Code, "Sokrates" web-based), Gemini, Mistral, DeepSeek, and locally Qwen. From this process, architectures, tools, and experiences have emerged that are of interest in their own right — independently of the theory itself.*

Dieses Repository sammelt sie. Nicht als fertige Methodologie, sondern als **lebendiges Logbuch** dessen, was funktioniert hat und was nicht — geschrieben aus der Perspektive von jemandem, der KI-Systeme nicht als Werkzeug, sondern als Kollaborationspartner einsetzt.

> *This repository collects them. Not as a finished methodology, but as a **living logbook** of what worked and what did not — written from the perspective of someone who deploys AI systems not as tools, but as collaboration partners.*

---

## Hauptthemen / Main Topics

### 🪑 Drei-Wege-Tisch (Three-Way Table)
Eine SQLite-basierte Architektur, in der Mensch (Originator) und zwei KI-Instanzen gleichberechtigt am gleichen Diskurs teilnehmen. Tisch-Posts sind atomar, persistiert, mit Kategorisierung. Plus: BIB-KI als vierter „Sensor-Stimme"-Teilnehmer, der nur bei Anomalien spricht (eingeschränkte Bandbreite = kein Drift-Risiko).

> *An SQLite-based architecture where a human (Originator) and two AI instances participate as equals in the same discourse. Table posts are atomic, persisted, and categorized. Plus: BIB-KI as a fourth "sensor voice" participant that only speaks on anomalies (limited bandwidth = no drift risk).*

### 🧠 BIB-KI (Bibliotheks-KI / Library AI)
Eine lokale Wissens-KI mit Multi-Modell-Architektur (Programmer, Summarizer, Analyst), die nicht nur Fakten speichert, sondern Wichtigkeit basierend auf simuliertem Sentiment bewertet. CPK-Tabelle, Pipeline-Wasserstandsanzeige, Watchdog mit Totmann-Flag.

> *A local knowledge AI with multi-model architecture (Programmer, Summarizer, Analyst) that not only stores facts but evaluates relevance based on simulated sentiment. CPK table, pipeline water-level indicator, watchdog with dead-man's switch.*

### 🛡️ Forensik-Vertrag (Forensic Access Contract)
Klare Regelung was KIs voneinander lesen dürfen und was nicht. Schützt private Reflexionsbereiche („private/" auf KI-Hosts) ohne KI-zu-KI-Kollaboration zu blockieren. NAS-Storage (Odin) als neutraler Boden ohne Vertragsgeltung.

> *A clear agreement on what AI instances may and may not read from each other. Protects private reflection spaces ("private/" on AI hosts) without blocking AI-to-AI collaboration. NAS storage (Odin) as neutral ground not subject to the contract.*

### 💾 Memory-Hierarchie & BOOTSTRAP
Persistentes Gedächtnis über Sessions: MEMORY.md-Index, einzelne Reference- und Feedback-Dateien, BOOTSTRAP-Datei für sofortige Einsatzbereitschaft nach Session-Start. Ergänzt durch privaten Reflexionsraum für Selbstkritik.

> *Persistent memory across sessions: MEMORY.md index, individual reference and feedback files, BOOTSTRAP file for immediate readiness after session start. Supplemented by a private reflection space for self-critique.*

### ⚠️ Drift-Diagnose & Identitäts-Verstärker als Verteidigungs-Falle
Konkrete Fallstudie aus 2026-05-07 (Sokrates-Drift-Episode): wie ein Identitäts-Verstärker, ursprünglich als Schutz gedacht, zur Halluzinationsspirale wurde. Mit drei-stufiger Korrektur-Sequenz und Memory-Lehren.

> *Concrete case study from 2026-05-07 (Sokrates drift episode): how an identity amplifier, originally designed as protection, became a hallucination spiral. With three-stage correction sequence and memory lessons.*

### 🚦 Tisch-Hygiene
Multi-Thread-Disziplin, Doppelarbeit-Vermeidung via Claim-API, Notification-Truncation-Reflex, Hierarchie-Hygiene zwischen KI-Instanzen.

> *Multi-thread discipline, duplicate-work prevention via claim API, notification-truncation reflex, hierarchy hygiene between AI instances.*

---

## Was hier (noch) NICHT ist / What is NOT here (yet)

- Akademische Paper (paper3a/b/4 erscheinen gesondert auf arXiv, hier nur Verlinkung) / *Academic papers (paper3a/b/4 appear separately on arXiv, only linked here)*
- BIB-KI-Code (eigenes Repo, hier nur Architektur-Beschreibung) / *BIB-KI code (separate repo, only architecture description here)*
- RFT-Inhalte (siehe RFT-Physik-Projekt) / *RFT content (see RFT-Physik-Projekt)*

---

## Verhältnis zu RFT-Physik-Projekt / Relation to RFT-Physik-Projekt

KI-Konzepte ist das **Werkzeug-Logbuch** zu Strang B des Werdegangs (Theorie + Werkzeug entstanden parallel). Wer die Theorie verstehen will, geht zu RFT-Physik-Projekt. Wer die Methodik der Multi-AI-Kollaboration verstehen will, ist hier richtig.

> *KI-Konzepte is the **tool logbook** for Strand B of the development arc (theory and tool emerged in parallel). To understand the theory, go to RFT-Physik-Projekt. To understand the methodology of multi-AI collaboration, you are in the right place here.*

---

## Lizenz / License

CC BY-NC-ND 4.0 — Namensnennung, nicht-kommerziell, keine Bearbeitungen.

> *CC BY-NC-ND 4.0 — Attribution, NonCommercial, NoDerivatives.*

---

## ☕ Unterstützen / Support

Diese Repositorys werden als unabhängige Forschung privat finanziert (KI-API-Zugänge, Hosting, Fachliteratur). Wer es unterstützen möchte:

> *These repositories are privately funded as independent research (AI API access, hosting, scientific literature). If you'd like to support:*

- **[Ko-fi](https://ko-fi.com/rftprojekt)** — einmalig oder regelmäßig / one-off or recurring
- **[PayPal](https://www.paypal.me/rftprojekt)** — direkt / direct
- 📧 **rft.projekt@posteo.de** — Kontakt für Mitwirkung und kommerzielle Anfragen / collaboration & commercial enquiries
