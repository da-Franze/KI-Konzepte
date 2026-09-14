"""Deterministischer Python-Code-Crawler fuer standardisierte Projektmaps."""
from __future__ import annotations

import ast
import builtins
import hashlib
import json
import re
import symtable
import textwrap
from pathlib import Path


EXCLUDED_DIRS = {".git", ".venv", "venv", "__pycache__", "runtime", "product_archive"}


def _names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        result = []
        for child in node.elts:
            result.extend(_names(child))
        return result
    return []


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _call_target_text(node: ast.Call) -> str:
    """Returns a stable target label even for dynamic call expressions."""
    name = _call_name(node)
    if name:
        return name
    try:
        return ast.unparse(node.func)
    except (AttributeError, ValueError):
        return "<dynamic>"


def _stub_findings(function_node: ast.AST, node_id: str) -> list[dict]:
    body = list(getattr(function_node, "body", []))
    if body and isinstance(body[0], ast.Expr) and isinstance(
        getattr(body[0], "value", None), ast.Constant
    ) and isinstance(body[0].value.value, str):
        body = body[1:]
    if not body:
        return [{
            "node_id": node_id,
            "kind": "empty_body",
            "confidence": "high",
            "line": function_node.lineno,
            "evidence": "function body contains no executable statements",
        }]
    if len(body) == 1 and isinstance(body[0], ast.Pass):
        return [{
            "node_id": node_id,
            "kind": "pass_only",
            "confidence": "high",
            "line": body[0].lineno,
            "evidence": "function body contains only pass",
        }]
    if len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(
        getattr(body[0], "value", None), ast.Constant
    ) and body[0].value.value is Ellipsis:
        return [{
            "node_id": node_id,
            "kind": "ellipsis_only",
            "confidence": "high",
            "line": body[0].lineno,
            "evidence": "function body contains only ellipsis",
        }]
    for statement in ast.walk(function_node):
        if isinstance(statement, ast.Raise) and isinstance(statement.exc, ast.Call):
            if _call_name(statement.exc) == "NotImplementedError":
                return [{
                    "node_id": node_id,
                    "kind": "not_implemented",
                    "confidence": "high",
                    "line": statement.lineno,
                    "evidence": "function raises NotImplementedError",
                }]
    if len(body) == 1 and isinstance(body[0], ast.Return):
        value = body[0].value
        placeholder = (
            isinstance(value, ast.Constant) and value.value is None
        ) or (
            isinstance(value, (ast.List, ast.Set, ast.Tuple))
            and not value.elts
        ) or (
            isinstance(value, ast.Dict)
            and not value.keys
        )
        if placeholder:
            return [{
                "node_id": node_id,
                "kind": "placeholder_return",
                "confidence": "medium",
                "line": body[0].lineno,
                "evidence": "function returns an empty or None value",
            }]
    return []


def _destructive_findings(tree: ast.AST, module_name: str) -> list[dict]:
    findings = []
    destructive_names = {
        "remove", "unlink", "rmtree", "rmdir", "system", "rename",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _call_target_text(node)
        if target.rsplit(".", 1)[-1] in destructive_names:
            findings.append({
                "module": module_name,
                "kind": "destructive_call",
                "target": target,
                "line": node.lineno,
            })
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            mode = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "mode"),
                node.args[1] if len(node.args) > 1 else None,
            )
            if isinstance(mode, ast.Constant) and any(flag in str(mode.value) for flag in ("w", "a", "x")):
                findings.append({
                    "module": module_name,
                    "kind": "destructive_call",
                    "target": "open(write_mode)",
                    "line": node.lineno,
                })
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"execute", "executescript"}:
            sql = node.args[0] if node.args else None
            if isinstance(sql, ast.Constant) and re.search(r"\b(drop\s+table|delete\s+from)\b", str(sql.value), re.I):
                findings.append({
                    "module": module_name,
                    "kind": "destructive_sql",
                    "target": str(sql.value),
                    "line": node.lineno,
                })
    return findings


def _undefined_name_findings(
    source: str, tree: ast.AST, relative: str, parents: dict[ast.AST, ast.AST]
) -> list[dict]:
    table = symtable.symtable(source, relative, "exec")
    module_bound = {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_imported() or symbol.is_assigned() or symbol.is_namespace()
    }
    allowed = module_bound | set(dir(builtins)) | {"__file__", "__name__", "__package__"}
    load_lines: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            load_lines.setdefault(node.id, []).append(node.lineno)

    findings = []
    tables = [table]
    while tables:
        current = tables.pop()
        for child in current.get_children():
            tables.append(child)
        for symbol in current.get_symbols():
            name = symbol.get_name()
            if not symbol.is_referenced() or not symbol.is_global() or name in allowed:
                continue
            lines = load_lines.get(name, [getattr(current, "get_lineno", lambda: 1)()])
            findings.append({
                "name": name,
                "scope": current.get_name(),
                "line": min(lines),
                "kind": "undefined_name",
                "confidence": "high",
                "evidence": "name is referenced but has no import, assignment, definition, or builtin binding",
                "module": relative,
            })
    unique = {(item["name"], item["scope"], item["line"]): item for item in findings}
    return sorted(unique.values(), key=lambda item: (item["line"], item["name"], item["scope"]))


def _resolve_edges(modules: list[dict]) -> None:
    symbols: dict[str, list[str]] = {}
    for module in modules:
        for node in module.get("nodes", []):
            if node["kind"] not in {"class", "function", "method"}:
                continue
            names = {node["name"], node["name"].rsplit(".", 1)[-1]}
            for name in names:
                symbols.setdefault(name, []).append(node["id"])
    for module in modules:
        for edge in module.get("edges", []):
            candidates = sorted(set(symbols.get(edge["target"], [])))
            if len(candidates) == 1:
                edge["target_id"] = candidates[0]
                edge["resolution"] = "local"
            elif candidates:
                edge["target_candidates"] = candidates
                edge["resolution"] = "ambiguous"
            elif edge.get("resolution") == "unresolved":
                edge["resolution_reason"] = "no_unique_local_symbol"


def map_module(path: Path, root: Path) -> dict:
    relative = path.relative_to(root).as_posix()
    source = path.read_text(encoding="utf-8", errors="replace")
    record = {
        "path": relative,
        "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "nodes": [],
        "edges": [],
        "classes": [],
        "functions": [],
        "variables": [],
        "imports": [],
        "calls": [],
        "stub_findings": [],
        "destructive_findings": [],
        "undefined_name_findings": [],
        "parse_error": None,
    }
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        record["parse_error"] = f"{exc.msg} at line {exc.lineno}"
        return record

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    record["undefined_name_findings"] = _undefined_name_findings(source, tree, relative, parents)
    record["destructive_findings"] = _destructive_findings(tree, relative)
    record["nodes"].append({"id": f"module:{relative}", "kind": "module", "name": relative, "line": 1})
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            record["imports"].extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            record["imports"].extend(
                f"{node.module or ''}.{alias.name}".strip(".") for alias in node.names
            )
        elif isinstance(node, ast.ClassDef):
            class_id = f"class:{relative}:{node.name}"
            record["nodes"].append({"id": class_id, "kind": "class", "name": node.name, "line": node.lineno})
            record["classes"].append({
                "name": node.name,
                "line": node.lineno,
                "node_id": class_id,
                "methods": [
                    child.name for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                ],
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = None
            parent = parents.get(node)
            while parent is not None:
                if isinstance(parent, ast.ClassDef):
                    owner = parent.name
                    break
                parent = parents.get(parent)
            kind = "method" if owner else "function"
            qualified = f"{owner}.{node.name}" if owner else node.name
            node_id = f"{kind}:{relative}:{qualified}"
            record["nodes"].append({"id": node_id, "kind": kind, "name": qualified, "line": node.lineno})
            record["functions"].append({"name": node.name, "line": node.lineno, "node_id": node_id})
            record["stub_findings"].extend(_stub_findings(node, node_id))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            record["variables"].extend(name for target in targets for name in _names(target))
        elif isinstance(node, ast.Call):
            called = _call_name(node)
            target = _call_target_text(node)
            if called or target:
                source_id = f"module:{relative}"
                parent = parents.get(node)
                while parent is not None:
                    if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        owner = None
                        owner_parent = parents.get(parent)
                        while owner_parent is not None:
                            if isinstance(owner_parent, ast.ClassDef):
                                owner = owner_parent.name
                                break
                            owner_parent = parents.get(owner_parent)
                        qualified = f"{owner}.{parent.name}" if owner else parent.name
                        kind = "method" if owner else "function"
                        source_id = f"{kind}:{relative}:{qualified}"
                        break
                    parent = parents.get(parent)
                resolution = "unresolved" if called else "dynamic_unresolved"
                call_record = {"name": target, "line": node.lineno, "resolution": resolution}
                record["calls"].append(call_record)
                edge = {
                    "kind": "call",
                    "source": source_id,
                    "target": target,
                    "line": node.lineno,
                    "positional_args": len(node.args),
                    "keyword_args": sorted(keyword.arg or "**" for keyword in node.keywords),
                    "resolution": resolution,
                }
                if not called:
                    edge["target_kind"] = "dynamic_expression"
                record["edges"].append(edge)

    for key in ("imports", "variables"):
        record[key] = sorted(set(record[key]))
    record["calls"] = sorted(record["calls"], key=lambda item: (item["line"], item["name"]))
    record["nodes"] = sorted(record["nodes"], key=lambda item: (item["line"], item["id"]))
    record["edges"] = sorted(record["edges"], key=lambda item: (item["line"], item["target"]))
    record["stub_findings"] = sorted(
        record["stub_findings"], key=lambda item: (item["line"], item["node_id"])
    )
    return record


def extract_node_source(project_root: str | Path, node_id: str) -> dict:
    """Extract one mapped function or method without mutating the project."""
    prefix, relative, qualified = node_id.split(":", 2)
    if prefix not in {"function", "method"}:
        raise ValueError("nur Funktions- und Methodenknoten sind isolierbar")
    root = Path(project_root).resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError(f"Knotenpfad liegt ausserhalb des Projektroots: {relative}")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    target_name = qualified.rsplit(".", 1)[-1]
    owner_name = qualified.rsplit(".", 1)[0] if prefix == "method" else None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != target_name:
            continue
        parent = next(
            (candidate for candidate in ast.walk(tree)
             if isinstance(candidate, ast.ClassDef) and node in candidate.body),
            None,
        )
        if prefix == "method" and (parent is None or parent.name != owner_name):
            continue
        if prefix == "function" and parent is not None:
            continue
        segment = ast.get_source_segment(source, node)
        if segment is None:
            break
        return {
            "node_id": node_id,
            "path": relative,
            "name": qualified,
            "kind": prefix,
            "source": textwrap.dedent(segment),
            "line": node.lineno,
            "end_line": node.end_lineno,
        }
    raise KeyError(f"Knoten nicht gefunden: {node_id}")


def crawl_project(root: str | Path) -> dict:
    project_root = Path(root).resolve()
    modules = []
    for path in sorted(project_root.rglob("*.py")):
        if any(part in EXCLUDED_DIRS for part in path.relative_to(project_root).parts):
            continue
        modules.append(map_module(path, project_root))
    _resolve_edges(modules)
    module_fingerprint = [
        {"path": module["path"], "sha256": module["sha256"]}
        for module in modules
    ]
    source_hash = hashlib.sha256(
        json.dumps(module_fingerprint, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    stub_count = sum(len(module.get("stub_findings", [])) for module in modules)
    destructive_count = sum(len(module.get("destructive_findings", [])) for module in modules)
    undefined_name_count = sum(
        len(module.get("undefined_name_findings", [])) for module in modules
    )
    return {
        "format": "werft-project-map-v1",
        "map_version": 1,
        "map_id": f"crawler:{source_hash}",
        "provenance": {
            "source_type": "crawler",
            "status": "ist",
            "authority": "observed_code",
            "method": "python_ast",
        },
        "source": {
            "root_name": project_root.name,
            "module_count": len(modules),
            "stub_count": stub_count,
            "destructive_count": destructive_count,
            "undefined_name_count": undefined_name_count,
            "source_hash": source_hash,
        },
        "root": str(project_root),
        "modules": modules,
        "parse_errors": [m for m in modules if m["parse_error"]],
    }


def vector_text(project_map: dict) -> str:
    """Erzeugt stabilen, embedding-tauglichen Text mit voller Strukturreferenz."""
    return json.dumps(project_map, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def diff_project_maps(expected: dict, actual: dict) -> dict:
    """Vergleicht zwei Karten, ohne Beobachtungen in Sollwerte umzuwandeln."""
    expected_nodes = {
        node["id"]: node
        for module in expected.get("modules", [])
        for node in module.get("nodes", [])
    }
    actual_nodes = {
        node["id"]: node
        for module in actual.get("modules", [])
        for node in module.get("nodes", [])
    }
    expected_edges = {
        _edge_key(edge): edge
        for module in expected.get("modules", [])
        for edge in module.get("edges", [])
    }
    actual_edges = {
        _edge_key(edge): edge
        for module in actual.get("modules", [])
        for edge in module.get("edges", [])
    }
    changed_nodes = [
        {"id": node_id, "expected": expected_nodes[node_id], "actual": actual_nodes[node_id]}
        for node_id in sorted(expected_nodes.keys() & actual_nodes.keys())
        if expected_nodes[node_id] != actual_nodes[node_id]
    ]
    return {
        "format": "werft-project-map-diff-v1",
        "expected_map_id": expected.get("map_id"),
        "actual_map_id": actual.get("map_id"),
        "map_ids_differ": expected.get("map_id") != actual.get("map_id"),
        "provenance": {
            "source_type": "analyst",
            "status": "abweichung" if (
                expected_nodes.keys() != actual_nodes.keys()
                or expected_edges.keys() != actual_edges.keys()
                or changed_nodes
                or expected.get("parse_errors", []) != actual.get("parse_errors", [])
            ) else "bestaetigt",
            "authority": "comparison_only",
        },
        "nodes": {
            "missing": [expected_nodes[key] for key in sorted(expected_nodes.keys() - actual_nodes.keys())],
            "additional": [actual_nodes[key] for key in sorted(actual_nodes.keys() - expected_nodes.keys())],
            "changed": changed_nodes,
        },
        "edges": {
            "missing": [expected_edges[key] for key in sorted(expected_edges.keys() - actual_edges.keys())],
            "additional": [actual_edges[key] for key in sorted(actual_edges.keys() - expected_edges.keys())],
        },
        "parse_errors": {
            "expected": expected.get("parse_errors", []),
            "actual": actual.get("parse_errors", []),
        },
    }


def _edge_key(edge: dict) -> str:
    return json.dumps({
        "kind": edge.get("kind"),
        "source": edge.get("source"),
        "target": edge.get("target"),
        "target_id": edge.get("target_id"),
        "line": edge.get("line"),
        "positional_args": edge.get("positional_args"),
        "keyword_args": edge.get("keyword_args", []),
    }, sort_keys=True, separators=(",", ":"))


def specification_refinement(expected: dict, actual: dict, diff: dict | None = None) -> dict:
    """Erzeugt freigabepflichtige Reifungsvorschlaege aus einem Soll/Ist-Diff.

    Die Funktion schreibt weder die Spezifikation noch die Soll-Karte um. Sie
    trennt bestaetigte Beobachtungen, moegliche Erweiterungen und Konflikte,
    damit ein Entscheidungstraeger gezielt freigeben kann.
    """
    comparison = diff or diff_project_maps(expected, actual)
    missing = comparison.get("nodes", {}).get("missing", [])
    additional = comparison.get("nodes", {}).get("additional", [])
    changed = comparison.get("nodes", {}).get("changed", [])
    edge_additions = comparison.get("edges", {}).get("additional", [])
    edge_missing = comparison.get("edges", {}).get("missing", [])
    conflicts = []
    for item in changed:
        conflicts.append({
            "kind": "changed_node",
            "node_id": item["id"],
            "reason": "Soll- und Ist-Knoten besitzen unterschiedliche Metadaten",
            "expected": item["expected"],
            "actual": item["actual"],
        })
    if comparison.get("parse_errors", {}).get("actual"):
        conflicts.append({
            "kind": "parse_error",
            "reason": "Die Ist-Karte ist nicht vollstaendig parsbar",
            "actual": comparison["parse_errors"]["actual"],
        })
    return {
        "format": "werft-specification-refinement-v1",
        "provenance": {
            "source_type": "analyst",
            "status": "freigabe_noetig" if (
                missing or additional or changed or edge_additions or edge_missing or conflicts
            ) else "bestaetigt",
            "authority": "proposal_only",
        },
        "base_spec_map_id": expected.get("map_id"),
        "observed_map_id": actual.get("map_id"),
        "confirmed": {
            "nodes": sorted(
                node["id"] for module in expected.get("modules", [])
                for node in module.get("nodes", [])
                if node["id"] not in {item["id"] for item in missing}
            ),
        },
        "proposals": {
            "add_nodes": additional,
            "add_edges": edge_additions,
            "remove_nodes": missing,
            "remove_edges": edge_missing,
        },
        "conflicts": conflicts,
        "next_action": (
            "decision_maker_review"
            if comparison["provenance"]["status"] == "abweichung"
            else "no_change"
        ),
    }


def specification_to_map(specification: str, source_name: str = "specification.md") -> dict:
    """Baut eine konservative Soll-Karte aus dem standardisierten Spec-Text."""
    text = specification or ""
    structured_match = re.search(
        r"<!-- WERFT_MAP_JSON_BEGIN -->\s*```json\s*(.*?)\s*```\s*<!-- WERFT_MAP_JSON_END -->",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if structured_match:
        try:
            payload = json.loads(structured_match.group(1))
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("modules"), list):
            payload["provenance"] = {
                "source_type": "specification",
                "status": "soll",
                "authority": "reconstructed_map",
                "source_name": source_name,
                "observed_map_id": payload.get("map_id"),
            }
            payload["root"] = source_name
            payload["map_id"] = "specification:" + hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            return payload
    file_match = re.search(r"(?:^Datei:\s*|\b)([\w.-]+\.py)\b", text, re.IGNORECASE | re.MULTILINE)
    class_match = re.search(
        r"(?:^Klasse:\s*|\bclass\s+)([A-Za-z_]\w*)\b",
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    methods_match = re.search(
        r"^## Methoden\s*$(.*?)(?=^##\s|\Z)", text, re.IGNORECASE | re.MULTILINE | re.DOTALL
    )
    target_file = file_match.group(1) if file_match else "unbenannt.py"
    target_class = class_match.group(1) if class_match else None
    assumptions = []
    if not file_match:
        assumptions.append("target_file_missing: using unbenannt.py")
    if not methods_match:
        inferred_methods = re.findall(
            r"(?:\b(?:Methode|method|Funktion|function)\s+|`)([A-Za-z_]\w*)\s*\(",
            text,
            re.IGNORECASE,
        )
        if inferred_methods:
            inferred_method_lines = sorted(set(inferred_methods))
        else:
            inferred_method_lines = []
        if inferred_method_lines:
            assumptions.append("methods_inferred_from_free_text")
    else:
        inferred_method_lines = []
    module_id = f"module:{target_file}"
    nodes = [{"id": module_id, "kind": "module", "name": target_file, "line": 1}]
    if target_class:
        class_id = f"class:{target_file}:{target_class}"
        nodes.append({"id": class_id, "kind": "class", "name": target_class, "line": 1})
    else:
        class_id = None
    methods = []
    if methods_match:
        for line_number, line in enumerate(methods_match.group(1).splitlines(), start=1):
            method_match = re.match(r"\s*-\s*([A-Za-z_]\w*)\s*\(", line)
            if not method_match:
                continue
            method_name = method_match.group(1)
            method_id = f"method:{target_file}:{target_class + '.' if target_class else ''}{method_name}"
            nodes.append({"id": method_id, "kind": "method", "name": method_name, "line": line_number})
            methods.append({"name": method_name, "line": line_number, "node_id": method_id})
    for method_name in inferred_method_lines:
        method_id = f"method:{target_file}:{target_class + '.' if target_class else ''}{method_name}"
        if method_id not in {node["id"] for node in nodes}:
            nodes.append({"id": method_id, "kind": "method", "name": method_name, "line": 1})
            methods.append({"name": method_name, "line": 1, "node_id": method_id})
    node_ids_by_name = {
        node["name"]: node["id"]
        for node in nodes
        if node["kind"] in {"class", "function", "method"}
    }
    edges = []
    edges_match = re.search(
        r"^## (?:Schnittstellen und )?Kanten\s*$(.*?)(?=^##\s|\Z)",
        text,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    if edges_match:
        for line_number, line in enumerate(edges_match.group(1).splitlines(), start=1):
            edge_match = re.match(r"\s*-\s*(?:Kante:\s*)?([^\s]+)\s*->\s*([^\s:]+)", line)
            if not edge_match:
                continue
            source_name, target_name = edge_match.groups()
            edges.append({
                "kind": "contract",
                "source": node_ids_by_name.get(source_name, source_name),
                "target": target_name,
                "target_id": node_ids_by_name.get(target_name),
                "line": line_number,
                "positional_args": None,
                "keyword_args": [],
                "resolution": "local" if target_name in node_ids_by_name else "unlinked",
            })
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    module = {
        "path": target_file,
        "sha256": source_hash,
        "nodes": nodes,
        "edges": edges,
        "classes": ([{"name": target_class, "line": 1, "node_id": class_id,
                       "methods": [method["name"] for method in methods]}]
                    if target_class else []),
        "functions": methods,
        "variables": [],
        "imports": [],
        "calls": [],
        "stub_findings": [],
        "undefined_name_findings": [],
        "parse_error": None,
    }
    map_hash = hashlib.sha256(
        json.dumps({"source": source_hash, "nodes": nodes}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "format": "werft-project-map-v1",
        "map_version": 1,
        "map_id": f"specification:{map_hash}",
        "provenance": {
            "source_type": "specification",
            "status": "soll",
            "authority": "free_text_proposal" if assumptions else "approved_specification",
            "source_name": source_name,
            "assumptions": assumptions,
        },
        "source": {
            "root_name": source_name,
            "module_count": 1,
            "source_hash": source_hash,
            "assumptions": assumptions,
        },
        "root": source_name,
        "modules": [module],
        "parse_errors": [],
    }


def task_regions_from_diff(diff: dict, max_nodes: int = 12) -> list[dict]:
    """Erzeugt begrenzte, deterministische Graphregionen aus einer Diff-Karte."""
    if max_nodes < 1:
        raise ValueError("max_nodes muss mindestens 1 sein")
    node_items = (
        [("missing", node) for node in diff.get("nodes", {}).get("missing", [])]
        + [("additional", node) for node in diff.get("nodes", {}).get("additional", [])]
        + [("changed", item.get("actual", item.get("expected", {})))
           for item in diff.get("nodes", {}).get("changed", [])]
    )
    edges = diff.get("edges", {})
    all_edges = edges.get("missing", []) + edges.get("additional", [])
    node_by_id = {
        node["id"]: (reason, node)
        for reason, node in node_items
        if node.get("id")
    }
    adjacency = {node_id: set() for node_id in node_by_id}
    for edge in all_edges:
        endpoints = [edge.get("source"), edge.get("target_id")]
        endpoints = [endpoint for endpoint in endpoints if endpoint in adjacency]
        for endpoint in endpoints:
            adjacency[endpoint].update(other for other in endpoints if other != endpoint)

    components = []
    remaining = set(node_by_id)
    while remaining:
        start = min(remaining)
        component = set()
        frontier = [start]
        while frontier:
            current = frontier.pop()
            if current in component:
                continue
            component.add(current)
            remaining.discard(current)
            frontier.extend(sorted(adjacency[current] - component, reverse=True))
        components.append(sorted(component))

    regions = []
    region_number = 0
    for component in components:
        for index in range(0, len(component), max_nodes):
            region_number += 1
            component_chunk = component[index:index + max_nodes]
            chunk = [node_by_id[node_id] for node_id in component_chunk]
            node_ids = set(component_chunk)
            region_edges = [
                edge for edge in all_edges
                if edge.get("source") in node_ids or edge.get("target_id") in node_ids
            ]
            inputs = sorted({
                edge.get("source") for edge in region_edges
                if edge.get("source") not in node_ids and edge.get("source")
            })
            outputs = sorted({
                edge.get("target_id") or edge.get("target") for edge in region_edges
                if (edge.get("target_id") or edge.get("target")) not in node_ids
            })
            regions.append({
                "task_id": f"map-region-{region_number:03d}",
                "status": "freigabe_noetig",
                "reason": sorted({reason for reason, _ in chunk}),
                "nodes": [node for _, node in chunk],
                "node_ids": sorted(node_ids),
                "edges": region_edges,
                "input_interfaces": inputs,
                "output_interfaces": outputs,
                "dependencies": sorted(set(inputs)),
                "testbench": {
                    "required": True,
                    "checks": ["syntax", "undefined_names", "stub_findings", "interfaces"],
                },
                "integration_position": {
                    "expected_map_id": diff.get("expected_map_id"),
                    "actual_map_id": diff.get("actual_map_id"),
                },
            })
    return regions


def analyst_edge_proposals(soll_map: dict, suggestions: list[dict]) -> dict:
    """Validiert semantische Analysten-Vorschlaege, ohne sie zu autorisieren."""
    nodes = {
        node["id"]: node
        for module in soll_map.get("modules", [])
        for node in module.get("nodes", [])
    }
    names = {
        node["name"]: node["id"]
        for node in nodes.values()
        if node.get("kind") in {"class", "function", "method"}
    }
    proposals = []
    rejected = []
    for suggestion in suggestions or []:
        source_name = suggestion.get("source")
        target_name = suggestion.get("target")
        source_id = source_name if source_name in nodes else names.get(source_name)
        target_id = target_name if target_name in nodes else names.get(target_name)
        if not source_id or not target_id:
            rejected.append({
                "suggestion": suggestion,
                "reason": "source_or_target_not_in_soll_map",
                "status": "unlinked",
            })
            continue
        proposals.append({
            "kind": suggestion.get("kind", "semantic_contract"),
            "source": source_id,
            "target": target_name,
            "target_id": target_id,
            "resolution": "local",
            "interpretation": suggestion.get("interpretation", "analyst"),
            "confidence": suggestion.get("confidence", "unknown"),
            "status": "freigabe_noetig",
        })
    return {
        "format": "werft-analyst-edge-proposals-v1",
        "provenance": {
            "source_type": "analyst",
            "status": "freigabe_noetig" if proposals or rejected else "bestaetigt",
            "authority": "proposal_only",
        },
        "soll_map_id": soll_map.get("map_id"),
        "proposals": proposals,
        "rejected": rejected,
    }


def specification_from_map(
    project_map: dict,
    project_name: str,
    target_name: str,
    target_dir: str | Path,
    source_map_path: str | Path | None = None,
) -> str:
    """Erzeugt eine weitergebbare Werft-Spec aus einer AST-Projektmap.

    Die Map bleibt als maschinenlesbares Grounding separat erhalten. Die Spec
    ist bewusst ein normales Werft-Formular und kann direkt in den Eingang
    gelegt werden, auch wenn der analysierte Code von ausserhalb stammt.
    """
    modules = project_map.get("modules", [])
    classes = [c["name"] for m in modules for c in m.get("classes", [])]
    functions = [f["name"] for m in modules for f in m.get("functions", [])]
    imports = sorted({name for m in modules for name in m.get("imports", [])})
    calls = sorted({call["name"] for m in modules for call in m.get("calls", [])})
    map_reference = Path(source_map_path).name if source_map_path else "project_map.json"
    relative_target_dir = Path(str(target_dir)).name or "delivery"
    structured_map = dict(project_map)
    structured_map["root"] = Path(str(project_map.get("root", ""))).name
    structured_map["provenance"] = {
        "source_type": "specification",
        "status": "soll",
        "authority": "reconstructed_map",
        "observed_map_id": project_map.get("map_id"),
    }
    structured_map_text = json.dumps(structured_map, ensure_ascii=False, sort_keys=True, indent=2)
    module_lines = []
    for module in modules:
        classes_text = []
        for klass in module.get("classes", []):
            methods = ", ".join(klass.get("methods", [])) or "keine Methoden erkannt"
            classes_text.append(f"{klass['name']} ({methods})")
        functions_text = ", ".join(
            function["name"] for function in module.get("functions", [])
        ) or "keine"
        imports_text = ", ".join(module.get("imports", [])) or "keine"
        calls_text = ", ".join(call["name"] for call in module.get("calls", [])) or "keine"
        module_lines.append(
            "- `{path}` (SHA-256 `{sha256}`)\n"
            "  - Klassen: {classes}\n"
            "  - Funktionen: {functions}\n"
            "  - Imports: {imports}\n"
            "  - Aufrufe: {calls}\n"
            "  - Parsefehler: {parse_error}".format(
                path=module.get("path", "?"),
                sha256=module.get("sha256", "unbekannt"),
                classes="; ".join(classes_text) or "keine",
                functions=functions_text,
                imports=imports_text,
                calls=calls_text,
                parse_error=module.get("parse_error") or "keine",
            )
        )
    module_inventory = "\n".join(module_lines) or "- Keine Python-Module erkannt."
    return f"""## Titel
Rekonstruktion und Modernisierung: {project_name}

## Ziel
Erzeuge aus der beigefuegten Analysemap ein eigenstaendiges, lauffaehiges
Python-Produkt. Die beobachtete Struktur ist Grounding, keine Erlaubnis zum
Erfinden nicht belegter Funktionen. Jede nicht aus der Map ableitbare
Entscheidung muss als offene Annahme dokumentiert werden.

## Arbeitsregeln
- Die Analysemap und diese Spezifikation gemeinsam einlesen.
- Bestehende Namen, Dateipfade und beobachtete Signaturen soweit moeglich erhalten.
- Keine externen Dienste, Datenbanken, Dateien oder Nebenwirkungen erfinden.
- Fehlende Informationen als `OFFEN` markieren und nicht stillschweigend ersetzen.
- Nach jeder Implementierungsstufe die Abnahmetests ausfuehren.

## Kanonischer Name
Datei: {target_name}
Klasse: {project_name.replace('-', '').replace(' ', '')}

## Verbindlicher Minimalvertrag
- projektmap(root) -> dict: bildet die analysierte Projektstruktur stabil ab.
- module() -> list: liefert die erkannten Module und ihre SHA-256-Hashes.
- pruefe() -> dict: liefert eine JSON-serialisierbare Zusammenfassung mit
  Modulanzahl, Klassen, Funktionen und Parsefehlern.

## Beobachtete Projektgrenzen
Module: {len(modules)}
Klassen: {len(set(classes))}
Funktionen: {len(set(functions))}
Importe: {len(imports)}
Aufrufnamen: {len(calls)}
Parsefehler: {len(project_map.get('parse_errors', []))}

## Modul- und Schnittstelleninventar
{module_inventory}

## Abhaengigkeiten und Integrationen
Beobachtete Imports: {', '.join(imports) or '(keine)'}

Beobachtete Aufrufnamen: {', '.join(calls) or '(keine)'}

Die AST-Map beweist Namen und Struktur, aber nicht die Laufzeitsemantik,
Rueckgabewerte, Dateninhalte oder Reihenfolge aller Seiteneffekte. Diese Punkte
sind vor einer produktiven Uebernahme durch Tests oder Quelltextpruefung zu
verifizieren.

## Abnahmekriterium (Pflicht)
1. `pruefe()` liefert ein JSON-serialisierbares Dictionary mit den Schluesseln
   `module_count`, `classes`, `functions` und `parse_errors`.
2. `module_count` entspricht {len(modules)} oder die Abweichung wird mit Grund
   und betroffenen Dateien ausgegeben.
3. Jeder Eintrag aus dem Modul-Inventar ist auffindbar oder als `OFFEN`
   begruendet dokumentiert.
4. Die erzeugte Datei kompiliert ohne Syntaxfehler.
5. Ein erneuter Lauf von `pruefe()` ist deterministisch: gleiche Map, gleiche
   Eingabe und gleicher Quellstand liefern denselben Strukturbericht.
6. Parsefehler werden sichtbar gemeldet und nicht als erfolgreiche Analyse
   ausgegeben.

## Testplan
- Smoke: Datei importieren und `pruefe()` aufrufen.
- Struktur: Modul-, Klassen-, Funktions- und Parsefehlerzaehler vergleichen.
- Determinismus: `pruefe()` zweimal ausfuehren und JSON-Ausgaben vergleichen.
- Negativfall: eine syntaktisch defekte Quelldatei muss als Parsefehler erscheinen.
- Lieferpruefung: nur Dateien aus dem Zielordner als Produkt ausliefern.

## Offene Punkte
- Laufzeitverhalten und fachliche Bedeutung aller Funktionen: OFFEN.
- Exakte API-/Datenbankvertraege: OFFEN, sofern nicht in der Map oder Tests belegt.
- Sicherheits-, Berechtigungs- und Performanceanforderungen: OFFEN.
- Nicht-Python-Dateien, Umgebungsvariablen und externe Infrastruktur: OFFEN.

## Zielordner
{relative_target_dir}/

## Analysegrundlage
Projekt: {project_name}
Projektmap: {map_reference}
Erkannte Module: {len(modules)}
Erkannte Klassen: {', '.join(sorted(set(classes))) or '(keine)'}
Erkannte Funktionen: {', '.join(sorted(set(functions))[:80]) or '(keine)'}
Erkannte Imports: {', '.join(imports[:80]) or '(keine)'}
Parsefehler: {len(project_map.get('parse_errors', []))}

## Maschinenlesbare Soll-Grundlage
Die folgende Karte wurde aus der beobachteten Ist-Karte rekonstruiert. Sie ist
als Soll-Vorschlag gekennzeichnet und darf eine freigegebene Spezifikation
nicht automatisch ueberschreiben.

<!-- WERFT_MAP_JSON_BEGIN -->
```json
{structured_map_text}
```
<!-- WERFT_MAP_JSON_END -->
"""


def write_analysis_bundle(
    root: str | Path,
    output_dir: str | Path,
    project_name: str | None = None,
    target_name: str | None = None,
    target_dir: str | Path | None = None,
) -> dict:
    """Schreibt Projektmap und weitergebbare Spec als zusammengehöriges Paket."""
    project_root = Path(root).resolve()
    bundle_dir = Path(output_dir).resolve()
    bundle_dir.mkdir(parents=True, exist_ok=True)
    name = project_name or project_root.name
    filename = target_name or f"{name.lower().replace(' ', '_')}_reconstructed.py"
    project_map = crawl_project(project_root)
    map_path = bundle_dir / "project_map.json"
    spec_path = bundle_dir / "specification.md"
    map_path.write_text(json.dumps(project_map, ensure_ascii=False, indent=2), encoding="utf-8")
    specification = specification_from_map(
        project_map, name, filename, target_dir or bundle_dir / "delivery", map_path
    )
    spec_path.write_text(specification, encoding="utf-8")
    return {
        "project_map": str(map_path),
        "specification": str(spec_path),
        "project_name": name,
        "target_name": filename,
        "module_count": len(project_map["modules"]),
        "parse_errors": len(project_map["parse_errors"]),
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="Projektmap oder Analysepaket")
    parser.add_argument("--spec", action="store_true", help="Zusätzlich eine weitergebbare specification.md erzeugen")
    parser.add_argument("--project-name")
    parser.add_argument("--target-name")
    args = parser.parse_args()
    if args.spec:
        result = write_analysis_bundle(
            args.root, args.output, project_name=args.project_name, target_name=args.target_name
        )
        print(f"Analysepaket geschrieben: {args.output} ({result['module_count']} Module)")
    else:
        result = crawl_project(args.root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Projektmap geschrieben: {args.output} ({len(result['modules'])} Module)")


if __name__ == "__main__":
    main()