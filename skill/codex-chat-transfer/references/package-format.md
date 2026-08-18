# Transfer folder format

The root contains `manifest.json`, `chats/`, optional `memories/`, and optional selected workspace metadata.

```text
transfer-folder/
|-- manifest.json
|-- chats/
|   |-- <thread-id>.codexbundle
|   `-- ...
|-- workspace.json
`-- memories/
    `-- <relative Markdown paths>
```

`manifest.json` uses schema `codex-chat-transfer/v1`. It records an export UUID, creation time, exactness limits, the pinned `cct` runtime hash, per-chat metadata and SHA-256 hashes, per-memory-file SHA-256 hashes, and an optional hashed `Workspace` entry. Older v1 folders without `Workspace` remain valid.

Each chat is a native `cct` bundle containing exactly one selected Codex session. The bundle retains the native rollout records and is validated by `cct inspect` before export completes and again before import.

The memory directory is a point-in-time snapshot of global Markdown memories, not a claim that every file belongs to every selected chat. Import never replaces a conflicting active memory. Conflicting source files are copied to `<CODEX_HOME>/memory-import-conflicts/<export-id>/` for review.

`workspace.json` uses schema `codex-chat-transfer/workspace-v1`. It contains only the selected tasks' display titles, recorded cwd values, local project references, project names, and root paths. It excludes the source database and unrelated projects. Source project IDs are used only to preserve grouping; restoration can create different destination IDs.

The format intentionally excludes credentials, Codex SQLite databases, complete global state, config, logs, caches, startup configuration, and external workspace files that are merely referenced by path.
