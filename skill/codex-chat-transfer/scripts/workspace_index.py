"""Read legacy and current Codex title/project metadata without changing the index."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import stat


def normalized(path: str) -> str:
    if path.startswith("\\\\?\\"):
        path = path[4:]
    return os.path.normcase(os.path.normpath(path))


def table_columns(connection, table):
    if table not in {"threads", "projects", "project_roots"}:
        raise ValueError("Unexpected table")
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def native_projects(connection):
    project_columns = table_columns(connection, "projects")
    root_columns = table_columns(connection, "project_roots")
    if not project_columns and not root_columns:
        return None
    if not {"id", "name", "metadata", "position", "created_at_ms", "updated_at_ms"} <= project_columns or not {
        "project_id", "position", "path"
    } <= root_columns:
        raise RuntimeError("Unsupported native project schema")
    projects = {}
    for row in connection.execute("SELECT id, name FROM projects"):
        projects[row[0]] = {"id": row[0], "name": row[1], "rootPaths": [], "native": True}
    for row in connection.execute("SELECT project_id, path FROM project_roots ORDER BY position"):
        if row[0] in projects:
            projects[row[0]]["rootPaths"].append(row[1])
    return projects


def load_context(codex_home: Path, thread_ids: list[str]):
    state_path = codex_home / ".codex-global-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8-sig")) if state_path.exists() else {}
    titles = {}
    title_path = codex_home / "session_index.jsonl"
    if title_path.exists():
        with title_path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("id") and record.get("thread_name"):
                    titles[record["id"]] = record["thread_name"]
    databases = sorted(codex_home.glob("state_*.sqlite"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    rows, projects = {}, None
    if databases:
        connection = sqlite3.connect(databases[0].resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            columns = table_columns(connection, "threads")
            wanted = [key for key in ("id", "title", "name", "cwd", "updated_at", "updated_at_ms", "project_id", "thread_source", "archived") if key in columns]
            if "id" not in wanted:
                raise RuntimeError("Missing threads.id")
            # Chunk placeholders so listing many chats never hits SQLite's parameter limit.
            for start in range(0, len(thread_ids), 500):
                subset = thread_ids[start:start + 500]
                marks = ",".join("?" for _ in subset)
                for row in connection.execute(f"SELECT {','.join(wanted)} FROM threads WHERE id IN ({marks})", subset):
                    rows[row["id"]] = dict(row)
            projects = native_projects(connection)
        finally:
            connection.close()
    return {"rows": rows, "projects": projects, "state": state, "titles": titles}


def display_title(context, thread_id):
    row = context["rows"].get(thread_id, {})
    return str(row.get("name") or context["titles"].get(thread_id) or row.get("title") or "")


def selected_project(context, thread_id):
    row = context["rows"].get(thread_id, {})
    native = context["projects"] or {}
    legacy = context["state"].get("local-projects") or {}
    project_id = row.get("project_id")
    if project_id:
        if project_id not in native:
            raise RuntimeError(f"Thread has an unknown native project: {thread_id}")
        return project_id, native[project_id], "native-assignment"
    assignment = (context["state"].get("thread-project-assignments") or {}).get(thread_id) or {}
    project_id = assignment.get("projectId") if assignment.get("projectKind") == "local" else None
    if project_id and project_id in legacy:
        return project_id, legacy[project_id], "legacy-assignment"
    if thread_id in (context["state"].get("projectless-thread-ids") or []):
        return None, None, "explicit-projectless"
    cwd = normalized(str(row.get("cwd") or ""))
    matches = {}
    for project_id, project in list(native.items()) + list(legacy.items()):
        roots = frozenset(normalized(str(root)) for root in project.get("rootPaths", []))
        if cwd and cwd in roots:
            matches.setdefault(roots, (project_id, project))
    if len(matches) == 1:
        project_id, project = next(iter(matches.values()))
        return project_id, project, "unique-root-match"
    return None, None, "ambiguous-root-match" if matches else "unassigned"


def metadata(codex_home: Path, thread_ids: list[str]):
    context = load_context(codex_home, thread_ids)
    threads = []
    for thread_id in dict.fromkeys(thread_ids):
        row = context["rows"].get(thread_id, {})
        updated = row.get("updated_at_ms")
        if updated:
            updated = updated / 1000
        else:
            updated = row.get("updated_at")
        updated_text = dt.datetime.fromtimestamp(updated, dt.timezone.utc).isoformat() if updated else ""
        cwd = str(row.get("cwd") or "")
        threads.append({
            "ThreadId": thread_id, "Title": display_title(context, thread_id),
            "Cwd": cwd[4:] if cwd.startswith("\\\\?\\") else cwd,
            "UpdatedAt": updated_text, "ThreadSource": row.get("thread_source") or "",
            "Archived": bool(row.get("archived")),
        })
    return {"Threads": threads}


def validate_tree(root: Path):
    """Use native lstat tags; .NET can mislabel hydrated Nutstore folders."""
    checked = 0
    pending = [root]
    while pending:
        path = pending.pop()
        info = path.lstat()
        tag = getattr(info, "st_reparse_tag", 0)
        cloud_tag = (tag & 0xFFFF0FFF) == 0x9000001A
        if stat.S_ISLNK(info.st_mode) or (tag and not cloud_tag):
            raise RuntimeError(f"Links and non-cloud reparse points are not allowed: {path}")
        checked += 1
        if stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())
        elif not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"Unsupported transfer file type: {path}")
    return {"Status": "tree-validated", "Entries": checked}
