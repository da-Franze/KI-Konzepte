"""End-to-end Selbsttest der Werft gegen ihren eigenen Kern.

Ohne --live wird nur der deterministische Vertrag geprüft. Mit --live werden
die echten Summarizer-, Coder- und Analyst-Provider aufgerufen.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path


def _statuses(db_path: Path, order_id: str) -> dict:
    with sqlite3.connect(db_path) as con:
        order = con.execute(
            "SELECT status, kanonischer_name FROM bestellungen WHERE bestellung_id=?",
            (order_id,),
        ).fetchone()
        task = con.execute(
            "SELECT status FROM task_pipeline WHERE task_id=?",
            (f"task_bestellung_{order_id}",),
        ).fetchone()
        version = con.execute(
            "SELECT product_name, version, status FROM product_versions "
            "WHERE bestellung_id=? ORDER BY id DESC LIMIT 1",
            (order_id,),
        ).fetchone()
    return {
        "order": order,
        "task": task[0] if task else None,
        "product_version": version,
    }


def run(live: bool = False, keep: bool = False) -> dict:
    from code_crawler import crawl_project, write_analysis_bundle
    from werft_map_pipeline import WerftMapPipeline

    core_dir = Path(__file__).resolve().parent
    project_map = crawl_project(core_dir)
    if project_map["parse_errors"]:
        raise RuntimeError(f"Werft-Kern enthält Syntaxfehler: {project_map['parse_errors']}")

    if keep:
        workspace = Path(tempfile.mkdtemp(prefix="werft-self-test-"))
        temp_context = None
    else:
        temp_context = tempfile.TemporaryDirectory(prefix="werft-self-test-")
        workspace = Path(temp_context.name)
    runtime_dir = workspace / "runtime"
    generated_dir = workspace / "generiert"
    delivery_dir = workspace / "auslieferung"
    analysis_dir = workspace / "analysepaket"
    specification_path = core_dir.parent / "docs" / "werft_soll_spezifikation.md"
    specification_text = specification_path.read_text(encoding="utf-8")
    map_pipeline = WerftMapPipeline(runtime_db_path=runtime_dir / "werft.db")
    map_analysis = map_pipeline.analysiere_projekt(
        specification_text, core_dir, max_nodes=12
    )
    analysis_dir.mkdir(parents=True, exist_ok=True)
    (analysis_dir / "self_analysis.json").write_text(
        json.dumps(map_analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (analysis_dir / "task_regions.json").write_text(
        json.dumps(map_analysis["task_regions"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.environ["WERFT_RUNTIME_DIR"] = str(runtime_dir)
    os.environ["WERFT_DB_PATH"] = str(runtime_dir / "werft.db")
    os.environ["WERFT_PRODUCT_ARCHIVE_DIR"] = str(runtime_dir / "product_archive")
    os.environ["WERFT_GENERATED_DIR"] = str(generated_dir)

    import runtime_paths
    runtime_paths.RUNTIME_DIR = runtime_dir
    runtime_paths.WERFT_DB_PATH = runtime_dir / "werft.db"
    runtime_paths.PRODUCT_ARCHIVE_DIR = runtime_dir / "product_archive"
    runtime_paths.GENERATED_DIR = generated_dir

    import db_setup
    db_setup.DB_PATH = runtime_dir / "werft.db"
    db_setup.main()

    # Modules imported before the temporary runtime was selected may retain a
    # ConfigLoader bound to the production database. Rebind it before loading
    # any live role so the acceptance run remains isolated and deterministic.
    import configloader
    production_db = core_dir.parent / "runtime" / "werft.db"
    production_config = configloader.ConfigLoader(production_db)
    embedding_host = production_config.get("ollama.host", "http://localhost:11434")
    production_host = production_config.get(
        "ollama.host.summarizer",
        production_config.get("ollama.host", "http://localhost:11434"),
    )
    production_host = os.environ.get("WERFT_SELF_TEST_OLLAMA_HOST", production_host)
    configloader.config = configloader.ConfigLoader(runtime_dir / "werft.db")
    import sys
    for module_name in ("ollama_client", "vdb_client"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "config"):
            module.config = configloader.config
    vdb_module = sys.modules.get("vdb_client")
    if vdb_module is not None and hasattr(vdb_module, "vdb"):
        vdb_module.vdb._ollama_host = embedding_host

    if live:
        # The live acceptance run is deliberately bounded independently from
        # the production model budget so a slow local model cannot leave the
        # test process waiting for several roles indefinitely.
        live_timeout = float(os.environ.get("WERFT_LIVE_TIMEOUT", "90"))
        # Der globale Host bleibt der Fallback. Ein Rollenhost wie 11435 darf
        # ausfallen, ohne den zweiten Versuch auf denselben Host umzubiegen.
        host_overrides = {"ollama.host": embedding_host}
        host_overrides.update({f"ollama.host.{role}": production_host for role in
                               ("summarizer", "coder", "analyst", "router", "stratege", "theke")})
        configloader.config.set_runtime_overrides({
            **host_overrides,
            "ollama.gesamt_timeout_sec": live_timeout,
            "ollama.connect_timeout_sec": min(live_timeout, 10.0),
            "ollama.read_timeout_sec": live_timeout,
        })
        coder_num_predict = os.environ.get("WERFT_SELF_TEST_CODER_NUM_PREDICT")
        if coder_num_predict:
            configloader.config.set_runtime_overrides({
                "coder.num_predict_max": int(coder_num_predict),
            })
        if os.environ.get("WERFT_SELF_TEST_DEBUG"):
            configloader.config.set_runtime_overrides({"werft.debug": "true"})

    live_model = os.environ.get("WERFT_SELF_TEST_MODEL")
    if live_model:
        configloader.config.set_runtime_overrides({
            f"ollama.model.{role}": live_model
            for role in ("summarizer", "coder", "analyst", "router", "stratege", "theke")
        })
        con = sqlite3.connect(db_setup.DB_PATH)
        try:
            con.executemany(
                "UPDATE system_config SET config_value=? WHERE config_key=?",
                [(live_model, f"ollama.model.{role}") for role in
                 ("summarizer", "coder", "analyst", "router", "stratege", "theke")],
            )
            con.commit()
        finally:
            con.close()

    order_id = "self-spec-werft"
    bundle = write_analysis_bundle(
        core_dir,
        analysis_dir,
        project_name="WerftSelbstpruefung",
        target_name="selftest_report.py",
        target_dir=delivery_dir,
    )
    live_specification_path = analysis_dir / "live_order.md"
    live_specification_path.write_text(
        """## Ziel
Erzeuge ein kleines, eigenstaendiges Python-Modul fuer den Werft-Selbsttest. """
        "Das Modul soll einen strukturierten Statusbericht als Dictionary liefern.\n\n"
        "## Kanonischer Name\n"
        "Datei: selftest_report.py\n"
        "Klasse: SelfTestReport\n\n"
        "## Methoden\n"
        "- pruefe() -> dict: liefert den Statusbericht.\n"
        "- als_text() -> str: liefert den Bericht als Text.\n\n"
        "## Abnahmekriterium (Pflicht)\n"
        "Aufruf: SelfTestReport().pruefe()\n"
        "Erwartet: ein Dictionary mit dem Schluessel 'status' und dem Wert 'ok'.\n",
        encoding="utf-8",
    )
    con = sqlite3.connect(db_setup.DB_PATH)
    try:
        con.execute(
            "INSERT INTO bestellungen "
            "(bestellung_id, titel, spec_pfad, quelle, ziel_ordner, status, kanonischer_name) "
            "VALUES (?, ?, ?, 'self_test', ?, 'eingegangen', ?)",
            (order_id, "Werft spezifiziert ihren eigenen Kern", str(live_specification_path),
             str(delivery_dir), bundle["target_name"]),
        )
        con.commit()
    finally:
        con.close()

    import bestellungen
    intake_count = bestellungen.eingang_schritt()
    result = {
        "mode": "live" if live else "dry-run",
        "project_map_modules": len(project_map["modules"]),
        "analysis_bundle": bundle,
        "self_analysis": {
            "soll_map_id": map_analysis["soll_map"].get("map_id"),
            "ist_map_id": map_analysis["ist_map"].get("map_id"),
            "diff_status": map_analysis["diff"]["provenance"]["status"],
            "task_regions": len(map_analysis["task_regions"]),
            "refinement_status": map_analysis["refinement"]["provenance"]["status"],
            "analysis_path": str(analysis_dir / "self_analysis.json"),
        },
        "intake_tasks": intake_count,
        "db_path": str(db_setup.DB_PATH),
        "workspace": str(workspace),
        "live_model": live_model,
    }
    if live:
        import analyst
        import coder
        import summarizer
        import ollama_client
        import vdb_client
        vdb_client.vdb._ollama_host = embedding_host
        print(
            f"LIVE ollama host: {ollama_client.host_fuer_rolle('summarizer')}",
            flush=True,
        )

        result["live_status"] = "running"
        for stage_name, stage_fn in (
            ("summarizer", summarizer.run_once),
            ("coder", coder.run_once),
            ("analyst", analyst.run_once),
        ):
            try:
                print(f"LIVE stage START: {stage_name}", flush=True)
                result[stage_name] = stage_fn()
                print(f"LIVE stage DONE: {stage_name} -> {result[stage_name]!r}", flush=True)
                if stage_name == "coder" and result[stage_name]:
                    with sqlite3.connect(db_setup.DB_PATH) as connection:
                        task_status = connection.execute(
                            "SELECT status FROM task_pipeline WHERE task_id=?",
                            (f"task_{order_id}",),
                        ).fetchone()
                    if task_status and task_status[0] != "code_ready":
                        raise RuntimeError(
                            f"coder stage ended with task status {task_status[0]!r}"
                        )
            except Exception as exc:
                print(f"LIVE stage FAILED: {stage_name}: {type(exc).__name__}: {exc}", flush=True)
                result["live_status"] = "failed"
                result["live_error"] = {
                    "stage": stage_name,
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
                break
        if result["live_status"] == "running":
            try:
                print("LIVE stage START: delivery", flush=True)
                result["delivery"] = bestellungen.auslieferung_schritt()
                print(f"LIVE stage DONE: delivery -> {result['delivery']!r}", flush=True)
            except Exception as exc:
                print(f"LIVE stage FAILED: delivery: {type(exc).__name__}: {exc}", flush=True)
                result["live_status"] = "failed"
                result["live_error"] = {
                    "stage": "delivery",
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
        result["statuses"] = _statuses(db_setup.DB_PATH, order_id)
        result["generated_files"] = [str(p) for p in generated_dir.glob("*")]
        result["delivered_files"] = [str(p) for p in delivery_dir.glob("*")]
        if result["live_status"] == "running":
            result["live_status"] = (
                "completed"
                if (
                    result.get("delivery", 0) > 0
                    and result["statuses"]["order"][0] == "geliefert"
                    and result["statuses"]["product_version"] is not None
                )
                else "failed"
            )
    else:
        result["statuses"] = _statuses(db_setup.DB_PATH, order_id)
        result["next_live_stages"] = ["summarizer", "coder", "analyst", "delivery"]

    if keep:
        result["kept_workspace"] = str(workspace)
    if temp_context is not None:
        temp_context.cleanup()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Provider und echte Pipeline-Stufen aufrufen")
    parser.add_argument("--keep", action="store_true", help="Temporäre Testdaten behalten")
    args = parser.parse_args()
    print(json.dumps(run(live=args.live, keep=args.keep), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()