#!/usr/bin/env python3
"""Snapshot, plan, restore, and verify Codex desktop workspace metadata.

The native rollout files remain the source of truth for conversation history.
This helper handles only the small amount of desktop metadata that cct does not
carry: display titles, local project definitions, project assignments, and cwd
index values.  Writes are guarded, backed up, and require the desktop app to be
closed by the calling PowerShell wrapper.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from workspace_index import load_context, display_title, selected_project, metadata, native_projects, table_columns, validate_tree
from contextlib import closing


WORKSPACE_SCHEMA = "codex-chat-transfer/workspace-v1"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object: {path}")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def strip_extended_prefix(value: str) -> str:
    return value[4:] if value.startswith("\\\\?\\") else value


def comparable_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(strip_extended_prefix(value)))


def parse_path_maps(items: list[str]) -> list[tuple[str, str]]:
    mappings: list[tuple[str, str]] = []
    for item in items:
        if "=" not in item:
            raise RuntimeError(f"Path map must use OLD=NEW: {item}")
        old, new = item.split("=", 1)
        old = strip_extended_prefix(old.strip()).rstrip("\\/")
        new = strip_extended_prefix(new.strip()).rstrip("\\/")
        if not old or not new or not os.path.isabs(old) or not os.path.isabs(new):
            raise RuntimeError(f"Path map must contain two absolute paths: {item}")
        mappings.append((old, new))
    mappings.sort(key=lambda pair: len(comparable_path(pair[0])), reverse=True)
    return mappings


def map_path(value: str, mappings: list[tuple[str, str]]) -> str:
    plain = strip_extended_prefix(value)
    candidate = comparable_path(plain)
    for old, new in mappings:
        old_cmp = comparable_path(old)
        if candidate == old_cmp:
            return os.path.normpath(new)
        prefix = old_cmp.rstrip("\\/") + os.sep
        if candidate.startswith(prefix):
            relative = os.path.relpath(plain, old)
            return os.path.normpath(os.path.join(new, relative))
    return os.path.normpath(plain)


def database_path(codex_home: Path) -> Path:
    candidates = sorted(
        codex_home.glob("state_*.sqlite"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"No Codex state_*.sqlite database found under {codex_home}")
    return candidates[0]


def global_state_path(codex_home: Path) -> Path:
    path = codex_home / ".codex-global-state.json"
    if not path.is_file():
        raise RuntimeError(f"Codex global state is missing: {path}")
    return path


def require_thread_columns(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
    required = {"id", "title", "cwd"}
    missing = sorted(required - columns)
    if missing:
        raise RuntimeError("Unsupported Codex thread schema; missing: " + ", ".join(missing))


def snapshot(codex_home: Path, thread_ids: list[str], output: Path) -> dict[str, Any]:
    if not thread_ids:
        raise RuntimeError("Snapshot requires at least one thread id")
    context = load_context(codex_home, thread_ids)
    missing = [thread_id for thread_id in thread_ids if thread_id not in context["rows"]]
    if missing:
        raise RuntimeError("Selected threads are missing from the Codex index: " + ", ".join(missing))
    projects, threads = {}, []
    for thread_id in dict.fromkeys(thread_ids):
        row = context["rows"][thread_id]
        project_id, project, basis = selected_project(context, thread_id)
        if project is not None:
            projects[project_id] = {
                "SourceProjectId": project_id, "Name": str(project.get("name") or ""),
                "RootPaths": [strip_extended_prefix(str(root)) for root in project.get("rootPaths", [])],
            }
        threads.append({
            "ThreadId": thread_id, "Title": display_title(context, thread_id),
            "Cwd": strip_extended_prefix(str(row["cwd"])), "SourceProjectId": project_id,
            "ProjectAssociationBasis": basis,
        })
    result = {"Schema": WORKSPACE_SCHEMA, "CapturedUtc": utc_now(), "Threads": threads, "Projects": list(projects.values())}
    write_json_atomic(output, result)
    return result


def build_plan(workspace: dict[str, Any], path_maps: list[str]) -> dict[str, Any]:
    if workspace.get("Schema") != WORKSPACE_SCHEMA:
        raise RuntimeError(f"Unsupported workspace schema: {workspace.get('Schema')}")
    mappings = parse_path_maps(path_maps)
    projects: list[dict[str, Any]] = []
    missing_roots: list[str] = []
    for project in workspace.get("Projects") or []:
        roots = [map_path(str(root), mappings) for root in project.get("RootPaths") or []]
        for root in roots:
            if not os.path.isdir(root):
                missing_roots.append(root)
        projects.append(
            {
                "SourceProjectId": str(project.get("SourceProjectId") or ""),
                "Name": str(project.get("Name") or ""),
                "RootPaths": roots,
            }
        )
    threads = [
        {
            "ThreadId": str(thread.get("ThreadId") or ""),
            "Title": str(thread.get("Title") or ""),
            "Cwd": map_path(str(thread.get("Cwd") or ""), mappings),
            "SourceProjectId": thread.get("SourceProjectId"),
        }
        for thread in workspace.get("Threads") or []
    ]
    for thread in threads:
        if not os.path.isdir(thread["Cwd"]):
            missing_roots.append(thread["Cwd"])
    return {
        "Schema": WORKSPACE_SCHEMA,
        "Projects": projects,
        "Threads": threads,
        "MissingProjectRoots": sorted(set(missing_roots), key=str.casefold),
        "Ready": not missing_roots,
    }


def create_backups(codex_home: Path, db_path: Path, state_path: Path, backup_root: Path | None) -> tuple[Path, Path]:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    root = backup_root or (codex_home / "chat-transfer-backups" / (stamp + "-" + uuid.uuid4().hex[:8]))
    root.mkdir(parents=True, exist_ok=False)
    db_backup = root / db_path.name
    state_backup = root / state_path.name
    source = sqlite3.connect(db_path, timeout=30)
    destination = sqlite3.connect(db_backup)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    shutil.copy2(state_path, state_backup)
    index_path = codex_home / "session_index.jsonl"
    if index_path.exists():
        shutil.copy2(index_path, root / "session_index.jsonl")
    return db_backup, state_backup


def find_project_by_roots(local_projects: dict[str, Any], roots: list[str]) -> str | None:
    wanted = {comparable_path(root) for root in roots}
    for project_id, project in local_projects.items():
        if not isinstance(project, dict):
            continue
        existing = {comparable_path(str(root)) for root in project.get("rootPaths") or []}
        if existing == wanted:
            return str(project_id)
    return None


def restore(codex_home: Path, workspace: dict[str, Any], path_maps: list[str], backup_root: Path | None) -> dict[str, Any]:
    plan = build_plan(workspace, path_maps)
    if not plan["Ready"]:
        raise RuntimeError("Mapped project roots do not exist: " + ", ".join(plan["MissingProjectRoots"]))

    db_path = database_path(codex_home)
    state_path = global_state_path(codex_home)
    state = read_json(state_path)
    local_projects = state.get("local-projects") or {}
    assignments = state.get("thread-project-assignments") or {}
    root_hints = state.get("thread-workspace-root-hints") or {}
    if not isinstance(local_projects, dict) or not isinstance(assignments, dict) or not isinstance(root_hints, dict):
        raise RuntimeError("Unsupported Codex global project state")

    connection = sqlite3.connect(db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        require_thread_columns(connection)
        ids = [thread["ThreadId"] for thread in plan["Threads"]]
        placeholders = ",".join("?" for _ in ids)
        present = {
            row[0] for row in connection.execute(f"SELECT id FROM threads WHERE id IN ({placeholders})", ids)
        }
        missing = [thread_id for thread_id in ids if thread_id not in present]
        if missing:
            raise RuntimeError(
                "Imported threads are not indexed yet; restart Codex once, close it, and retry: " + ", ".join(missing)
            )
    finally:
        connection.close()

    db_backup, state_backup = create_backups(codex_home, db_path, state_path, backup_root)
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    source_to_local: dict[str, str] = {}
    for project in plan["Projects"]:
        roots = project["RootPaths"]
        project_id = find_project_by_roots(local_projects, roots)
        if project_id is None:
            project_id = str(uuid.uuid4())
        local_projects[project_id] = {
            "id": project_id,
            "name": project["Name"] or Path(roots[0]).name,
            "rootPaths": roots,
            "createdAt": int(local_projects.get(project_id, {}).get("createdAt") or now_ms),
            "updatedAt": now_ms,
        }
        source_to_local[project["SourceProjectId"]] = project_id

    moved_ids: list[str] = []
    for thread in plan["Threads"]:
        thread_id = thread["ThreadId"]
        source_project_id = thread.get("SourceProjectId")
        if source_project_id in source_to_local:
            project_id = source_to_local[source_project_id]
            assignments[thread_id] = {"projectKind": "local", "projectId": project_id}
            root_hints[thread_id] = thread["Cwd"]
            moved_ids.append(thread_id)

    state["local-projects"] = local_projects
    state["thread-project-assignments"] = assignments
    state["thread-workspace-root-hints"] = root_hints
    existing_order = [item for item in (state.get("project-order") or []) if isinstance(item, str) and item]
    restored_order = list(source_to_local.values())
    state["project-order"] = restored_order + [item for item in existing_order if item not in restored_order]
    state["projectless-thread-ids"] = [
        item for item in (state.get("projectless-thread-ids") or []) if item not in moved_ids
    ]

    temp_state = state_path.with_name(state_path.name + ".restore-" + uuid.uuid4().hex + ".tmp")
    with temp_state.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(state, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())

    connection = sqlite3.connect(db_path, timeout=30)
    state_replaced = False
    try:
        require_thread_columns(connection)
        connection.execute("BEGIN IMMEDIATE")
        columns = table_columns(connection, "threads")
        native = native_projects(connection)
        native_map = {}
        if native is not None:
            if "project_id" not in columns:
                raise RuntimeError("Native projects exist but threads.project_id is missing")
            for project in plan["Projects"]:
                project_id = find_project_by_roots(native, project["RootPaths"])
                if project_id is None:
                    project_id = str(uuid.uuid4())
                    position = connection.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM projects").fetchone()[0]
                    connection.execute(
                        "INSERT INTO projects(id,name,metadata,position,created_at_ms,updated_at_ms) VALUES(?,?,?, ?,?,?)",
                        (project_id, project["Name"], "{}", position, now_ms, now_ms),
                    )
                    for position, root in enumerate(project["RootPaths"]):
                        connection.execute("INSERT INTO project_roots(project_id,position,path) VALUES(?,?,?)", (project_id, position, root))
                    native[project_id] = {"name": project["Name"], "rootPaths": project["RootPaths"]}
                else:
                    connection.execute("UPDATE projects SET name=?,updated_at_ms=? WHERE id=?", (project["Name"], now_ms, project_id))
                native_map[project["SourceProjectId"]] = project_id
        for thread in plan["Threads"]:
            name_column = "name" if "name" in columns else "title"
            values = ["\\\\?\\" + thread["Cwd"], thread["Title"]]
            updates = ["cwd = ?", name_column + " = ?"]
            source_project = thread.get("SourceProjectId")
            if source_project in native_map:
                updates.append("project_id = ?")
                values.append(native_map[source_project])
            values.append(thread["ThreadId"])
            cursor = connection.execute("UPDATE threads SET " + ", ".join(updates) + " WHERE id = ?", values)
            if cursor.rowcount != 1:
                raise RuntimeError(f"Expected one index row for {thread['ThreadId']}, got {cursor.rowcount}")
        os.replace(temp_state, state_path)
        state_replaced = True
        connection.commit()
    except Exception:
        connection.rollback()
        if state_replaced:
            shutil.copy2(state_backup, state_path)
        # Keep an uncommitted temporary state file available for diagnosis.
        raise
    finally:
        connection.close()

    index_path = codex_home / "session_index.jsonl"
    previous_index = index_path.read_bytes() if index_path.exists() else b""
    try:
        with index_path.open("ab") as handle:
            if previous_index and not previous_index.endswith(b"\n"):
                handle.write(b"\n")
            for thread in plan["Threads"]:
                handle.write((json.dumps({"id": thread["ThreadId"], "thread_name": thread["Title"], "updated_at": utc_now()}, ensure_ascii=False) + "\n").encode("utf-8"))
        verification = verify(codex_home, workspace, path_maps)
        if not verification["Verified"]:
            raise RuntimeError("Workspace post-restore verification failed")
    except Exception:
        # SQLite's backup API restores coherently even if a WAL exists.
        with closing(sqlite3.connect(db_backup)) as backup_connection, closing(sqlite3.connect(db_path)) as destination_connection:
            backup_connection.backup(destination_connection)
        shutil.copy2(state_backup, state_path)
        index_path.write_bytes(previous_index)
        raise
    return {
        "Status": "workspace-restored",
        "BackupDatabase": str(db_backup),
        "BackupGlobalState": str(state_backup),
        "Projects": verification["Projects"],
        "Threads": verification["Threads"],
        "Verified": True,
    }


def verify(codex_home: Path, workspace: dict[str, Any], path_maps: list[str]) -> dict[str, Any]:
    plan = build_plan(workspace, path_maps)
    context = load_context(codex_home, [thread["ThreadId"] for thread in plan["Threads"]])
    projects = {}
    projects.update(context["state"].get("local-projects") or {})
    projects.update(context["projects"] or {})
    project_results = []
    expected_roots = {}
    for project in plan["Projects"]:
        project_id = find_project_by_roots(projects, project["RootPaths"])
        expected_roots[project["SourceProjectId"]] = {comparable_path(root) for root in project["RootPaths"]}
        project_results.append({"Name": project["Name"], "RootPaths": project["RootPaths"], "ProjectId": project_id, "Verified": project_id is not None})
    results = []
    for thread in plan["Threads"]:
        row = context["rows"].get(thread["ThreadId"])
        title_ok = row is not None and display_title(context, thread["ThreadId"]) == thread["Title"]
        cwd_ok = row is not None and comparable_path(row["cwd"]) == comparable_path(thread["Cwd"])
        project_ok = True
        source_project = thread.get("SourceProjectId")
        if source_project:
            _, actual_project, _ = selected_project(context, thread["ThreadId"])
            actual_roots = {comparable_path(root) for root in actual_project.get("rootPaths", [])} if actual_project else set()
            project_ok = actual_project is not None and actual_roots == expected_roots.get(source_project)
        results.append({
            "ThreadId": thread["ThreadId"], "Title": thread["Title"], "Cwd": thread["Cwd"],
            "TitleVerified": title_ok, "CwdVerified": cwd_ok, "ProjectVerified": project_ok,
            "Verified": title_ok and cwd_ok and project_ok,
        })
    return {"Status": "workspace-verification", "Ready": plan["Ready"], "MissingProjectRoots": plan["MissingProjectRoots"],
            "Projects": project_results, "Threads": results,
            "Verified": plan["Ready"] and all(item["Verified"] for item in project_results + results)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("validate-tree", "metadata", "snapshot", "plan", "restore", "verify"), required=True)
    parser.add_argument("--codex-home", required=True)
    parser.add_argument("--workspace")
    parser.add_argument("--output")
    parser.add_argument("--root")
    parser.add_argument("--thread-id", action="append", default=[])
    parser.add_argument("--path-map", action="append", default=[])
    parser.add_argument("--backup-root")
    args = parser.parse_args()

    codex_home = Path(args.codex_home).resolve()
    if args.action == "validate-tree":
        if not args.root:
            raise RuntimeError("validate-tree requires --root")
        result = validate_tree(Path(os.path.abspath(args.root)))
    elif args.action == "metadata":
        result = metadata(codex_home, args.thread_id)
    elif args.action == "snapshot":
        if not args.output:
            raise RuntimeError("Snapshot requires --output")
        result = snapshot(codex_home, args.thread_id, Path(args.output).resolve())
    else:
        if not args.workspace:
            raise RuntimeError(f"{args.action} requires --workspace")
        workspace = read_json(Path(args.workspace).resolve())
        if args.action == "plan":
            result = build_plan(workspace, args.path_map)
        elif args.action == "restore":
            result = restore(
                codex_home,
                workspace,
                args.path_map,
                Path(args.backup_root).resolve() if args.backup_root else None,
            )
        else:
            result = verify(codex_home, workspace, args.path_map)
    # ASCII JSON survives PowerShell hosts that decode redirected stdout with a
    # legacy Windows code page. The saved workspace files remain UTF-8.
    json.dump(result, sys.stdout, ensure_ascii=True, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        json.dump({"Status": "error", "Error": str(exc)}, sys.stderr, ensure_ascii=True)
        sys.stderr.write("\n")
        raise SystemExit(1)
