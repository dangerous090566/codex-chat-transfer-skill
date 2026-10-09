# Workspace restoration

Use this procedure when the imported tasks must reappear under the same logical local projects with their display titles and destination-device paths.

## Why this is a second phase

Native rollout files preserve conversation history, but the desktop sidebar also depends on a local index and global project state. `cct --reconcile` is best effort and may be unable to launch the packaged Windows app-server. A successful session import therefore does not prove that project membership, titles, or paths are restored.

The transfer package carries only selected workspace metadata in `workspace.json`; it never carries the source database or full global state. The destination bridge applies that metadata only after validating the current schema, confirming every selected thread is indexed, and creating backups.

## Required sequence

1. Inspect the package and determine every source-to-destination mapping. Use absolute `OLD=NEW` values. A user-profile rename alone is not enough when project roots also changed.
2. Confirm every mapped destination project directory exists. Cloud-sync folders may be reparse points; they are valid destinations, but the transfer package itself must not contain reparse points.
3. Import with the same `-PathMap` values so rollout `cwd` records are rewritten as they enter the destination.
4. Restart Codex once. Confirm the imported thread IDs are discoverable, even if they still appear under Recent.
5. Preview workspace restoration while Codex is open:

```powershell
pwsh -NoProfile -File <skill>/scripts/codex_chat_transfer.ps1 `
  -Action RestoreWorkspace -TransferFolder <folder> `
  -PathMap 'C:\Users\OLD\root=C:\Users\NEW\root'
```

6. If the plan is correct, launch the detached helper from a PowerShell 7 process that is not a child of the Codex window. It waits briefly, closes Codex, restores and verifies metadata, then relaunches the app:

```powershell
$pwsh = (Get-Process -Id $PID).Path
$helper = '<skill>\scripts\restore_workspace_detached.ps1'
$args = '-NoProfile -File "' + $helper + '" -TransferFolder "<folder>" -PathMap "C:\Users\OLD\root=C:\Users\NEW\root"'
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=('"' + $pwsh + '" ' + $args)}
```

7. After relaunch and before opening an imported chat, run `-Action Verify` with the identical mappings and check the JSON `Verified` field. Save this baseline result.
8. In the desktop app, find the chat under the mapped local project while it is unpinned, then open it and read several historical turns by its original thread ID. A chat that is only reachable by direct navigation or pinning has not passed the project-list check.

## Safety and recovery

- `RestoreWorkspace -Apply` refuses to run while Codex is open.
- It requires `id`, `title`, and `cwd` columns in the current `threads` table. Schema drift stops the operation instead of guessing.
- It reuses a destination local project only when its normalized root set exactly matches the mapped roots; otherwise it creates a new local project ID.
- On current schemas it updates `threads.name` and `threads.project_id`, and creates/reuses rows in `projects` and `project_roots`. Older schemas use `threads.title` and global project assignments. Display names are also appended to `session_index.jsonl`, whose existing contents are backed up.
- Source snapshots prefer an explicit native or legacy project assignment. If neither exists, a unique exact cwd match against saved project roots may preserve implicit grouping; snapshots record this basis. They do not infer membership for an explicitly projectless chat.
- Native `lstat` reparse tags are used for package files: normal/cloud files are supported, while symlinks, junctions, and unknown reparse types are refused. Project destinations may themselves be cloud-backed.
- Before mutation it creates SQLite and global-state backups under `<CODEX_HOME>/chat-transfer-backups/<timestamp>/`.
- The detached helper writes `<export-id>-workspace-restore.json` under `<CODEX_HOME>/chat-transfer-receipts/`.
- If an imported task is not indexed, restart Codex and retry. Do not insert a guessed row.
- Original source project IDs are metadata references only; destination local project IDs can differ without changing behavior.

## Desktop visibility after a legacy import

The CLI's `Verify` checks the native rollout, local index fields, and mapped project roots. It cannot check the desktop sidebar. If the thread is readable by ID but missing from the unpinned project chat list, first allow the app to finish indexing and restart it once. Check the intended project rather than relying on a short Recent list page, which may omit older chats.

If it is still absent, inspect the destination index **read-only** and compare the imported row with a visible user chat from the same project. Check `thread_source`, `model_provider`, `archived`, `cwd`, native `project_id`, and any legacy `thread-project-assignments`. Also inspect the imported rollout's `session_meta`. These fields are version dependent; a matching root and a verified project ID alone do not prove the app will list the chat. Do not infer that duplicate native and legacy project IDs are the cause.

One Windows desktop migration exposed a legacy chat whose rollout had no `thread_source`; its index initially had `thread_source=NULL` and `model_provider=openai`, while visible user chats used `model_provider=custom`. Setting the index `thread_source` to `user` did not restore visibility. A later backed-up, one-row **index-only** provider change to the peer value made the chat appear in the project list. This is a diagnostic example, not a default value or a blanket migration step. A project-ID adjustment alone did not restore visibility in that case. Do not rewrite `session_meta` to force modern metadata: that changes the transferred history and fails the byte-equivalence check. The app may also rebuild index fields from the rollout after launch.

If a compatibility repair is justified by destination peers, close Codex, take a fresh SQLite backup, and change only the affected thread's index row with an exact old-value guard. Keep the rollout untouched. Relaunch, confirm the chat appears unpinned in the correct project, read historical turns, and record the before/after values and backup path. If the app reverses the change or the evidence is ambiguous, stop and report the remaining limitation rather than applying broader database changes.

Opening a restored chat may append local events. After that, `cct diff` can report `ahead` with zero conflicts. Use the saved, pre-open `identical` verification as the transfer-fidelity evidence, and describe later `ahead` state as local continuation; do not claim the current file is still byte-identical.

## Completion standard

Completion requires evidence for each selected task:

- native bundle was byte-equivalent at the saved pre-open verification;
- display title matches the snapshot;
- indexed `cwd` matches the mapped destination;
- project assignment points to a local project with the mapped root;
- requested memories are identical, newly added, or explicitly quarantined;
- historical turns can be read by the preserved thread ID.
- the unpinned chat appears in the intended desktop project list.

Do not promise identical future responses. Models, tools, permissions, external files, and later global memories can still differ.
