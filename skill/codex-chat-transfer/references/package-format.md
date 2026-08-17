# Transfer folder format

The root contains `manifest.json`, `chats/`, and optionally `memories/`.

```text
transfer-folder/
|-- manifest.json
|-- chats/
|   |-- <thread-id>.codexbundle
|   `-- ...
`-- memories/
    `-- <relative Markdown paths>
```

`manifest.json` uses schema `codex-chat-transfer/v1`. It records an export UUID, creation time, exactness limits, the pinned `cct` runtime hash, per-chat metadata and SHA-256 hashes, and per-memory-file SHA-256 hashes.

Each chat is a native `cct` bundle containing exactly one selected Codex session. The bundle retains the native rollout records and is validated by `cct inspect` before export completes and again before import.

The memory directory is a point-in-time snapshot of global Markdown memories, not a claim that every file belongs to every selected chat. Import never replaces a conflicting active memory. Conflicting source files are copied to `<CODEX_HOME>/memory-import-conflicts/<export-id>/` for review.

The format intentionally excludes credentials, Codex SQLite databases, config, logs, caches, startup configuration, and external workspace files that are merely referenced by path.
