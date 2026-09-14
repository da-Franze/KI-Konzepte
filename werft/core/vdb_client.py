"""Minimaler Qdrant-Zugriff fuer die Werft-Wissens-Schicht (Schicht 1).

Bewusst ueber `requests` statt `qdrant_client` (BATCH_wiederaufbau.md,
Schritt 1: "requests (kein qdrant_client)"). Embeddings via Ollama
`nomic-embed-text` (768 Dimensionen, bereits lokal vorhanden).

Collection `werft_wissen` (Name aus ConfigLoader, nie hardcoded) ist neu und
getrennt von den bestehenden alten Collections eines Vorgaengerprojekts
(u.a. ki_konzepte) -- die Werft startet wissensmaessig bei Null, aber der
Mechanismus existiert ab Tag 1 (Betreiber-Korrektur, siehe Plan-Kontext).

Die VDB ist dabei nur eine Schicht des Wissensspeichers. Gerichtete, typisierte
Graphbeziehungen (z.B. Eltern-Kind) sind kein Ersatz fuer Vektor-Aehnlichkeit
und werden in einer separaten Graph-/Beziehungsschicht verwaltet. Ein
Hypothesengenerator kann beide Quellen kombinieren: VDB fuer parallele Muster,
Graph fuer gerichtete und ablaufbare Beziehungen.
"""
import uuid
from typing import Optional

import requests

from configloader import config

_DEFAULT_VECTOR_SIZE = 1024
VDB_SCOPES = {"private", "team", "system", "product_archive"}


class VDBClient:
    def __init__(self):
        self._host = config.get("qdrant.host", "http://localhost:6333")
        self._collection = config.get("qdrant.collection", "werft_wissen_1024")
        self._vector_size = int(config.get("qdrant.vector_size", _DEFAULT_VECTOR_SIZE))
        self._ollama_host = config.get("ollama.host", "http://localhost:11434")
        self._embed_model = config.get("ollama.model.embed", "qwen3-embedding:0.6b")
        self._ensured = False

    def _embed(self, text: str) -> list[float]:
        """Fund 2026-07-19: nomic-embed-text hat ein begrenztes Kontextfenster --
        laengere Anfragen (z.B. coder.py durchsucht die VDB mit dem KOMPLETTEN
        knowledge_links-Text einer Bestellung, inkl. voller Spezifikation) liefern
        einen 500er ("the input length exceeds the context length"). Reproduziert
        mit 6186 Zeichen. Fix: Text auf ein konfigurierbares Zeichen-Limit kappen,
        statt an jeder Aufrufstelle einzeln zu kuerzen (Zero-Hardcoding: Limit aus
        system_config, kein Magic Number im Code).

        Fund 2026-07-27 (Mentor, live reproduziert an einer Forex-Spec-Bestellung
        mit 10090 Zeichen dichtem Markdown -- Tabellen/Formeln/Code-Bloecke):
        der 07-19-Fix nimmt an, ein FESTES Zeichen-Limit (system_config,
        damals 6000) sei aequivalent zum Token-Kontextfenster des Modells --
        das stimmt nicht, das Verhaeltnis Zeichen/Token haengt von der
        Text-DICHTE ab (Tabellen/Formeln tokenisieren dichter als Fliesstext).
        Live reproduziert: bei genau dieser Spec lag die reale Grenze zwischen
        4000 (200 OK) und 5000 Zeichen (500 "the input length exceeds the
        context length") -- der konfigurierte max_chars-Wert (5000) lag also
        SELBST ueber der realen Grenze und loeste denselben Bug erneut aus,
        nur mit anderem Inhalt. Blockierte dadurch eine Bestellung dauerhaft
        im 'audit'-Status (summarizer.py faengt die Exception zwar ab, der
        Task kommt aber nie in 'spec' weiter -- stiller Pipeline-Stillstand,
        keine Fehlermeldung im Bestellungs-Status selbst).
        Permanenter Fix statt erneuter Zahlenraterei: bei genau diesem
        Fehlertext progressiv halbieren und erneut versuchen (bis zu einer
        Untergrenze), statt sich auf ein einziges statisches Zeichen-Limit
        zu verlassen -- robust gegen jede zukuenftige Text-Dichte."""
        max_chars = config.get("qdrant.embed.max_chars", 6000)
        if len(text) > max_chars:
            text = text[:max_chars]
        untergrenze = 300
        while True:
            r = requests.post(
                f"{self._ollama_host}/api/embeddings",
                json={"model": self._embed_model, "prompt": text},
                timeout=config.get("timeouts.vdb_embed_sec"),
            )
            if r.status_code == 500 and "context length" in r.text.lower() and len(text) > untergrenze:
                text = text[: len(text) // 2]
                continue
            r.raise_for_status()
            return r.json()["embedding"]

    def _ensure_collection(self) -> None:
        if self._ensured:
            return
        r = requests.get(
            f"{self._host}/collections/{self._collection}",
            timeout=config.get("timeouts.vdb_read_sec"),
        )
        if r.status_code == 404:
            requests.put(
                f"{self._host}/collections/{self._collection}",
                json={"vectors": {"size": self._vector_size, "distance": "Cosine"}},
                timeout=config.get("timeouts.vdb_read_sec"),
            ).raise_for_status()
        else:
            r.raise_for_status()
        self._ensured = True

    def upsert(
        self, text: str, payload: Optional[dict] = None, scope: str = "system"
    ) -> str:
        """Speichert einen Wissens-Eintrag (Fehlhistorie, Erfolg/Fehlschlag, Muster).

        Gibt die Punkt-ID zurueck. payload sollte mindestens 'owner' und
        'memory_type' enthalten (Parallele zu werft_memory in werft.db).
        """
        if scope not in VDB_SCOPES:
            raise ValueError(f"Unbekannter VDB-Scope: {scope}")
        self._ensure_collection()
        point_id = str(uuid.uuid4())
        vector = self._embed(text)
        if len(vector) != self._vector_size:
            raise ValueError(
                f"Embedding-Dimension {len(vector)} passt nicht zu Qdrant "
                f"{self._collection} ({self._vector_size})"
            )
        point_payload = {"text": text, **(payload or {})}
        point_payload["scope"] = scope
        body = {"points": [{"id": point_id, "vector": vector, "payload": point_payload}]}
        r = requests.put(
            f"{self._host}/collections/{self._collection}/points",
            json=body,
            timeout=config.get("timeouts.vdb_write_sec"),
        )
        r.raise_for_status()
        return point_id

    def delete(self, point_id: str) -> None:
        """Loescht einen einzelnen Punkt (Fund 2026-07-15, beispielserie_teil5):
        ein Funktions-Registry-Eintrag, der bei einem SPAETER als fehlerhaft
        erkannten Merge geschrieben wurde, blieb sonst dauerhaft als "aehnliche
        fruehere Loesung" mit hohem Score abrufbar -- der Coder bekam bei jedem
        Korrektur-Retry sein EIGENES, laengst ueberholtes (fehlerhaftes) Muster
        zurueckgespiegelt und wiederholte es trotz gegenteiliger Fehlhistorie."""
        self._ensure_collection()
        requests.post(
            f"{self._host}/collections/{self._collection}/points/delete",
            json={"points": [point_id]},
            timeout=config.get("timeouts.vdb_read_sec"),
        ).raise_for_status()

    def punkte_count(self, collection: str = None) -> int:
        """Anzahl Punkte einer Collection (debugger.py Balance-Waechter, 2.4).
        Collection-Parameter optional -- Default ist die eigene Werft-Collection,
        aber der Waechter vergleicht ueber mehrere Collections hinweg."""
        ziel = collection or self._collection
        r = requests.get(
            f"{self._host}/collections/{ziel}",
            timeout=config.get("timeouts.vdb_read_sec"),
        )
        if r.status_code == 404:
            return 0
        r.raise_for_status()
        return r.json()["result"]["points_count"]

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_text_len: int = 100,
        scope: Optional[str] = None,
    ) -> list[dict]:
        """Semantische Suche gegen die Wissens-Schicht. Leere Liste wenn nichts gefunden
        (KEIN Erfinden, Non-Invention-Policy -- Aufrufer muss leere Ergebnisse selbst behandeln).

        min_text_len: filtert sehr kurze generische Chunks, die bei nomic-embed-text
        als "Embedding-Attraktoren" bei fast jeder Anfrage hoch scoren (bekanntes,
        systemisches Problem der migrierten ki_konzepte-Daten, Befund 2026-07-06/10).
        MILDERUNG, keine Loesung -- die eigentliche Bereinigung der Attraktor-Chunks
        ist ein separater, von Betreiber freizugebender Dateneingriff.
        """
        if scope is not None and scope not in VDB_SCOPES:
            raise ValueError(f"Unbekannter VDB-Scope: {scope}")
        self._ensure_collection()
        vector = self._embed(query)
        if len(vector) != self._vector_size:
            raise ValueError(
                f"Embedding-Dimension {len(vector)} passt nicht zu Qdrant "
                f"{self._collection} ({self._vector_size})"
            )
        r = requests.post(
            f"{self._host}/collections/{self._collection}/points/search",
            # Ueberholen und filtern: genug Kandidaten holen, damit nach dem
            # Laengenfilter noch top_k uebrig bleiben.
            json={
                "vector": vector,
                "limit": top_k * 4,
                "with_payload": True,
                **({"filter": {"must": [{"key": "scope", "match": {"value": scope}}]}}
                   if scope else {}),
            },
            timeout=config.get("timeouts.vdb_write_sec"),
        )
        r.raise_for_status()
        treffer = []
        for p in r.json()["result"]:
            # eigene Eintraege nutzen 'text', migrierte Alt-Punkte aus
            # ki_konzepte nutzen 'content' -- beide Feldnamen abdecken.
            text = p["payload"].get("text") or p["payload"].get("content", "")
            if len(text.strip()) < min_text_len:
                continue
            treffer.append({"score": p["score"], "text": text, "payload": p["payload"]})
            if len(treffer) >= top_k:
                break
        return treffer

    def muster_suche(
        self,
        problem: str,
        top_k: int = 5,
        scopes: Optional[list[str]] = None,
    ) -> list[dict]:
        """Findet aehnliche Erfahrungs- und Problemuster fuer Hypothesen.

        Mehrere getrennte Scope-Suchen werden bewusst einzeln ausgefuehrt und
        danach deterministisch nach Score sortiert. Private Eintraege werden
        nur aufgenommen, wenn der aufrufende Mitarbeiter den Scope explizit
        freigegeben hat.
        """
        requested = scopes or ["system", "team"]
        unbekannt = set(requested) - VDB_SCOPES
        if unbekannt:
            raise ValueError(f"Unbekannte VDB-Scopes: {sorted(unbekannt)}")
        treffer: list[dict] = []
        for scope in requested:
            treffer.extend(self.search(problem, top_k=top_k, scope=scope))
        treffer.sort(
            key=lambda item: (-item["score"], item["payload"].get("event_hash", ""))
        )
        return treffer[:top_k]


vdb = VDBClient()

if __name__ == "__main__":
    pid = vdb.upsert(
        "Smoke-Test: Werft-Wissens-Schicht angelegt.",
        {"owner": "db_setup", "memory_type": "system", "tags": "smoke_test"},
    )
    print("upsert ok, id =", pid)
    hits = vdb.search("Werft Wissens-Schicht Test", top_k=3)
    print("search ok, hits =", len(hits))
    for h in hits:
        print(f"  score={h['score']:.3f} text={h['text']!r}")
