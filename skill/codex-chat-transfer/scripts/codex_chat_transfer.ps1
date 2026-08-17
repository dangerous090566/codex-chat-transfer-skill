[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('List', 'Export', 'Inspect', 'Import')]
    [string]$Action,

    [string[]]$ThreadId = @(),
    [string]$TransferFolder,
    [string]$CodexHome = (Join-Path $env:USERPROFILE '.codex'),
    [switch]$IncludeArchived,
    [bool]$IncludeMemories = $true,
    [ValidateSet('Block', 'Redact', 'Allow')]
    [string]$SecretsMode = 'Block',
    [ValidateSet('Skip', 'Merge')]
    [string]$MemoryMode = 'Skip',
    [switch]$Apply,
    [switch]$Reconcile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Schema = 'codex-chat-transfer/v1'
$SkillRoot = Split-Path -Parent $PSScriptRoot
$CctPath = Join-Path $SkillRoot 'assets\cct.exe'
$ExpectedCctSha256 = '2C5C7145AD77D457BC0BC3EE7AE81A0F5729C7F4493F43517E76BAE822475A46'
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Get-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Assert-PathUnder {
    param(
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Child
    )
    $parentFull = (Get-FullPath $Parent).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $childFull = Get-FullPath $Child
    if (-not $childFull.StartsWith($parentFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe path outside expected root: $childFull"
    }
    return $childFull
}

function Resolve-SafeRelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )
    if ([string]::IsNullOrWhiteSpace($RelativePath) -or [System.IO.Path]::IsPathRooted($RelativePath)) {
        throw "Unsafe relative path: $RelativePath"
    }
    return Assert-PathUnder -Parent $Root -Child (Join-Path $Root $RelativePath)
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )
    $json = $Value | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, $Utf8NoBom)
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Assert-CctRuntime {
    if (-not (Test-Path -LiteralPath $CctPath -PathType Leaf)) {
        throw "Bundled cct runtime is missing: $CctPath"
    }
    $actual = Get-Sha256 $CctPath
    if ($actual -ne $ExpectedCctSha256) {
        throw "Bundled cct runtime hash mismatch. Expected $ExpectedCctSha256 but found $actual."
    }
}

function Invoke-Cct {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$ParseJson
    )
    Assert-CctRuntime
    $output = @(& $CctPath @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $text = ($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
    if ($exitCode -ne 0) {
        throw "cct failed with exit code ${exitCode}: $text"
    }
    if ($ParseJson) {
        if ([string]::IsNullOrWhiteSpace($text)) { throw 'cct returned empty JSON output.' }
        return $text | ConvertFrom-Json
    }
    return $text
}

function Get-SessionThreadSource {
    param([Parameter(Mandatory = $true)]$Session)
    if ([bool]$Session.compressed -or -not (Test-Path -LiteralPath ([string]$Session.path) -PathType Leaf)) {
        return ''
    }
    foreach ($line in @(Get-Content -LiteralPath ([string]$Session.path) -TotalCount 12 -ErrorAction SilentlyContinue)) {
        try {
            $record = $line | ConvertFrom-Json
            if ([string]$record.type -eq 'session_meta') {
                return [string]$record.payload.thread_source
            }
        } catch {
            continue
        }
    }
    return ''
}

function Get-SelectableSessions {
    $arguments = @('list', '--tool', 'codex', '--codex-home', (Get-FullPath $CodexHome), '--json')
    if ($IncludeArchived) { $arguments += '--include-archived' }
    $result = Invoke-Cct -Arguments $arguments -ParseJson
    $sessions = @()
    foreach ($session in @($result.sessions)) {
        $source = Get-SessionThreadSource $session
        if (-not [string]::IsNullOrWhiteSpace($source) -and $source -ne 'user') { continue }
        $sessions += [pscustomobject]@{
            ThreadId = [string]$session.thread_id
            Title = [string]$session.preview
            UpdatedAt = [string]$session.updated_at
            Cwd = [string]$session.cwd
            Source = [string]$session.source
            ModelProvider = [string]$session.model_provider
            Archived = [bool]$session.archived
            Compressed = [bool]$session.compressed
            ThreadSource = $source
        }
    }
    return @($sessions | Sort-Object UpdatedAt -Descending)
}

function Resolve-SelectedSessions {
    param([Parameter(Mandatory = $true)][object[]]$Sessions)
    if ($ThreadId.Count -eq 0) { throw 'Export requires at least one -ThreadId.' }
    $selected = @()
    foreach ($requested in $ThreadId) {
        $needle = $requested.Trim()
        if ([string]::IsNullOrWhiteSpace($needle)) { continue }
        $matches = @($Sessions | Where-Object {
            $_.ThreadId.Equals($needle, [System.StringComparison]::OrdinalIgnoreCase) -or
            $_.ThreadId.StartsWith($needle, [System.StringComparison]::OrdinalIgnoreCase)
        })
        if ($matches.Count -eq 0) { throw "No selectable chat matches thread id prefix: $needle" }
        if ($matches.Count -gt 1) { throw "Thread id prefix is ambiguous: $needle" }
        $alreadySelected = @($selected | ForEach-Object { $_.ThreadId }) -contains $matches[0].ThreadId
        if (-not $alreadySelected) { $selected += $matches[0] }
    }
    if ($selected.Count -eq 0) { throw 'No chats were selected.' }
    return $selected
}

function Assert-NoReparsePoints {
    param([Parameter(Mandatory = $true)][string]$Root)
    $rootItem = Get-Item -LiteralPath $Root -Force
    if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Linked or reparse-point folder is not allowed: $Root"
    }
    foreach ($item in @(Get-ChildItem -LiteralPath $Root -Recurse -Force)) {
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Linked or reparse-point item is not allowed: $($item.FullName)"
        }
    }
}

function Export-MemorySnapshot {
    param([Parameter(Mandatory = $true)][string]$StagingRoot)
    $memoryRoot = Join-Path (Get-FullPath $CodexHome) 'memories'
    $entries = @()
    if (-not $IncludeMemories -or -not (Test-Path -LiteralPath $memoryRoot -PathType Container)) {
        return [pscustomobject]@{ Included = $false; Scope = 'codex-global-snapshot'; Files = @() }
    }
    Assert-NoReparsePoints $memoryRoot
    $destinationRoot = Join-Path $StagingRoot 'memories'
    [System.IO.Directory]::CreateDirectory($destinationRoot) | Out-Null
    foreach ($file in @(Get-ChildItem -LiteralPath $memoryRoot -File -Recurse -Force | Where-Object { $_.Extension -ieq '.md' })) {
        $relative = [System.IO.Path]::GetRelativePath($memoryRoot, $file.FullName)
        $destination = Resolve-SafeRelativePath -Root $destinationRoot -RelativePath $relative
        [System.IO.Directory]::CreateDirectory((Split-Path -Parent $destination)) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
        $entries += [pscustomobject]@{
            Path = ('memories/' + ($relative -replace '\\', '/'))
            Sha256 = Get-Sha256 $destination
            Bytes = (Get-Item -LiteralPath $destination).Length
        }
    }
    return [pscustomobject]@{ Included = $true; Scope = 'codex-global-snapshot'; Files = $entries }
}

function Export-TransferFolder {
    if ([string]::IsNullOrWhiteSpace($TransferFolder)) { throw 'Export requires -TransferFolder.' }
    $target = Get-FullPath $TransferFolder
    if (Test-Path -LiteralPath $target) { throw "Transfer folder already exists; refusing to overwrite it: $target" }
    $parent = Split-Path -Parent $target
    if ([string]::IsNullOrWhiteSpace($parent)) { throw "Transfer folder must have a parent directory: $target" }
    [System.IO.Directory]::CreateDirectory($parent) | Out-Null
    $staging = $target + '.partial-' + [Guid]::NewGuid().ToString('N')
    Assert-PathUnder -Parent $parent -Child $staging | Out-Null
    [System.IO.Directory]::CreateDirectory($staging) | Out-Null
    try {
        $sessions = Get-SelectableSessions
        $selected = Resolve-SelectedSessions -Sessions $sessions
        $chatRoot = Join-Path $staging 'chats'
        [System.IO.Directory]::CreateDirectory($chatRoot) | Out-Null
        $chatEntries = @()
        foreach ($session in $selected) {
            if ($session.ThreadId -notmatch '^[0-9a-fA-F-]{36}$') { throw "Unexpected thread id format: $($session.ThreadId)" }
            $bundleName = $session.ThreadId + '.codexbundle'
            $bundlePath = Join-Path $chatRoot $bundleName
            $arguments = @('export', '--tool', 'codex', '--codex-home', (Get-FullPath $CodexHome), '--session', $session.ThreadId, '--output', $bundlePath, '--json')
            switch ($SecretsMode) {
                'Redact' { $arguments += '--redact' }
                'Allow' { $arguments += '--allow-secrets' }
            }
            Invoke-Cct -Arguments $arguments | Out-Null
            Invoke-Cct -Arguments @('inspect', $bundlePath, '--json') -ParseJson | Out-Null
            $chatEntries += [pscustomobject]@{
                ThreadId = $session.ThreadId
                Title = $session.Title
                UpdatedAt = $session.UpdatedAt
                Cwd = $session.Cwd
                Bundle = 'chats/' + $bundleName
                Sha256 = Get-Sha256 $bundlePath
                Bytes = (Get-Item -LiteralPath $bundlePath).Length
            }
        }
        $memory = Export-MemorySnapshot -StagingRoot $staging
        $manifest = [pscustomobject]@{
            Schema = $Schema
            ExportId = [Guid]::NewGuid().ToString()
            CreatedUtc = [DateTime]::UtcNow.ToString('o')
            Source = [pscustomobject]@{ Platform = 'windows'; CodexHomeName = (Split-Path -Leaf (Get-FullPath $CodexHome)) }
            Tool = [pscustomobject]@{ Name = 'cct'; Version = '2.0.0'; Sha256 = $ExpectedCctSha256 }
            Exactness = [pscustomobject]@{
                HistoricalSessionRecords = if ($SecretsMode -eq 'Redact') { 'redacted' } else { 'preserved' }
                FutureResponses = 'not-guaranteed'
                ExternalReferencedFiles = 'not-included-unless-embedded-in-session'
            }
            Chats = $chatEntries
            Memories = $memory
        }
        Write-JsonFile -Path (Join-Path $staging 'manifest.json') -Value $manifest
        Move-Item -LiteralPath $staging -Destination $target
        [pscustomobject]@{
            Status = 'exported'
            TransferFolder = $target
            ExportId = $manifest.ExportId
            ChatCount = $chatEntries.Count
            MemoryFileCount = @($memory.Files).Count
            SecretsMode = $SecretsMode
            Warning = 'The transfer folder contains sensitive conversation data. Move it only through a trusted channel.'
        } | ConvertTo-Json -Depth 6
    } catch {
        if (Test-Path -LiteralPath $staging) {
            Assert-PathUnder -Parent $parent -Child $staging | Out-Null
            Remove-Item -LiteralPath $staging -Recurse -Force
        }
        throw
    }
}

function Read-TransferManifest {
    if ([string]::IsNullOrWhiteSpace($TransferFolder)) { throw "$Action requires -TransferFolder." }
    $root = Get-FullPath $TransferFolder
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw "Transfer folder does not exist: $root" }
    Assert-NoReparsePoints $root
    $manifestPath = Join-Path $root 'manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw "manifest.json is missing: $root" }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ([string]$manifest.Schema -ne $Schema) { throw "Unsupported transfer schema: $($manifest.Schema)" }
    if ([string]$manifest.ExportId -notmatch '^[0-9a-fA-F-]{36}$') { throw 'Invalid export id.' }
    if (@($manifest.Chats).Count -eq 0) { throw 'Transfer folder contains no chats.' }
    return [pscustomobject]@{ Root = $root; Manifest = $manifest }
}

function Get-MemoryPlan {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]$Manifest
    )
    $plan = @()
    $destinationRoot = Join-Path (Get-FullPath $CodexHome) 'memories'
    foreach ($entry in @($Manifest.Memories.Files)) {
        $relative = [string]$entry.Path
        if (-not $relative.StartsWith('memories/', [System.StringComparison]::Ordinal)) { throw "Unsafe memory path: $relative" }
        $withinMemories = $relative.Substring('memories/'.Length)
        if ([System.IO.Path]::GetExtension($withinMemories) -ine '.md') { throw "Only Markdown memories are accepted: $relative" }
        $source = Resolve-SafeRelativePath -Root $Root -RelativePath ($relative -replace '/', [System.IO.Path]::DirectorySeparatorChar)
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Memory file is missing: $relative" }
        $actual = Get-Sha256 $source
        if ($actual -ne ([string]$entry.Sha256).ToUpperInvariant()) { throw "Memory hash mismatch: $relative" }
        $destination = Resolve-SafeRelativePath -Root $destinationRoot -RelativePath ($withinMemories -replace '/', [System.IO.Path]::DirectorySeparatorChar)
        $status = if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) {
            'new'
        } elseif ((Get-Sha256 $destination) -eq $actual) {
            'identical'
        } else {
            'conflict'
        }
        $plan += [pscustomobject]@{ Path = $relative; Source = $source; Destination = $destination; Sha256 = $actual; Status = $status }
    }
    return $plan
}

function Get-ImportPlan {
    $transfer = Read-TransferManifest
    $chatPlan = @()
    foreach ($chat in @($transfer.Manifest.Chats)) {
        $threadId = [string]$chat.ThreadId
        if ($threadId -notmatch '^[0-9a-fA-F-]{36}$') { throw "Invalid thread id in manifest: $threadId" }
        $relative = [string]$chat.Bundle
        if (-not $relative.StartsWith('chats/', [System.StringComparison]::Ordinal) -or [System.IO.Path]::GetExtension($relative) -ine '.codexbundle') {
            throw "Unsafe chat bundle path: $relative"
        }
        $bundle = Resolve-SafeRelativePath -Root $transfer.Root -RelativePath ($relative -replace '/', [System.IO.Path]::DirectorySeparatorChar)
        if (-not (Test-Path -LiteralPath $bundle -PathType Leaf)) { throw "Chat bundle is missing: $relative" }
        $actual = Get-Sha256 $bundle
        if ($actual -ne ([string]$chat.Sha256).ToUpperInvariant()) { throw "Chat bundle hash mismatch: $relative" }
        $inspection = Invoke-Cct -Arguments @('inspect', $bundle, '--json') -ParseJson
        $bundleSessions = @($inspection.manifest.sessions)
        if ($bundleSessions.Count -ne 1 -or -not ([string]$bundleSessions[0].thread_id).Equals($threadId, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Bundle identity does not match the outer manifest: $relative"
        }
        $diff = Invoke-Cct -Arguments @('diff', $bundle, '--codex-home', (Get-FullPath $CodexHome), '--json') -ParseJson
        $chatPlan += [pscustomobject]@{
            ThreadId = $threadId
            Title = [string]$chat.Title
            Bundle = $bundle
            Sha256 = $actual
            Inspection = $inspection
            Diff = $diff
        }
    }
    $memoryPlan = Get-MemoryPlan -Root $transfer.Root -Manifest $transfer.Manifest
    return [pscustomobject]@{
        Transfer = $transfer
        Chats = $chatPlan
        Memories = $memoryPlan
    }
}

function Test-CodexRunning {
    return @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessName -in @('ChatGPT', 'Codex', 'codex')
    }).Count -gt 0
}

function Merge-Memories {
    param([Parameter(Mandatory = $true)]$Plan)
    $added = @()
    $identical = @()
    $conflicts = @()
    $conflictRoot = Join-Path (Get-FullPath $CodexHome) (Join-Path 'memory-import-conflicts' ([string]$Plan.Transfer.Manifest.ExportId))
    foreach ($item in @($Plan.Memories)) {
        switch ($item.Status) {
            'new' {
                [System.IO.Directory]::CreateDirectory((Split-Path -Parent $item.Destination)) | Out-Null
                Copy-Item -LiteralPath $item.Source -Destination $item.Destination
                $added += $item.Path
            }
            'identical' { $identical += $item.Path }
            'conflict' {
                $relative = ([string]$item.Path).Substring('memories/'.Length)
                $quarantine = Resolve-SafeRelativePath -Root $conflictRoot -RelativePath ($relative -replace '/', [System.IO.Path]::DirectorySeparatorChar)
                [System.IO.Directory]::CreateDirectory((Split-Path -Parent $quarantine)) | Out-Null
                Copy-Item -LiteralPath $item.Source -Destination $quarantine -Force
                $conflicts += [pscustomobject]@{ Path = $item.Path; ImportedCopy = $quarantine; ActiveFile = $item.Destination }
            }
        }
    }
    return [pscustomobject]@{ Added = $added; Identical = $identical; Conflicts = $conflicts; ConflictRoot = if ($conflicts.Count -gt 0) { $conflictRoot } else { $null } }
}

function Invoke-Import {
    $plan = Get-ImportPlan
    $summary = [pscustomobject]@{
        Schema = $plan.Transfer.Manifest.Schema
        ExportId = $plan.Transfer.Manifest.ExportId
        ChatCount = @($plan.Chats).Count
        Chats = @($plan.Chats | ForEach-Object { [pscustomobject]@{ ThreadId = $_.ThreadId; Title = $_.Title; Diff = $_.Diff } })
        MemoryMode = $MemoryMode
        Memories = [pscustomobject]@{
            New = @($plan.Memories | Where-Object Status -eq 'new').Count
            Identical = @($plan.Memories | Where-Object Status -eq 'identical').Count
            Conflicts = @($plan.Memories | Where-Object Status -eq 'conflict').Count
        }
        Apply = [bool]$Apply
    }
    if (-not $Apply) {
        $summary | ConvertTo-Json -Depth 20
        return
    }
    $defaultCodexHome = Get-FullPath (Join-Path $env:USERPROFILE '.codex')
    $targetsLiveCodexHome = (Get-FullPath $CodexHome).Equals($defaultCodexHome, [System.StringComparison]::OrdinalIgnoreCase)
    if ($targetsLiveCodexHome -and (Test-CodexRunning) -and -not $Reconcile) {
        throw 'Codex is running. Close it before import, or explicitly use -Reconcile to request native app-server reconciliation.'
    }
    $unsafeChatStates = @($plan.Chats | Where-Object {
        [int]$_.Diff.sessions_in_bundle -ne 1 -or
        [int]$_.Diff.conflicts -gt 0 -or
        [int]$_.Diff.grow -gt 0 -or
        [int]$_.Diff.ahead -gt 0 -or
        (([int]$_.Diff.new + [int]$_.Diff.identical) -ne 1)
    })
    if ($unsafeChatStates.Count -gt 0) {
        $ids = ($unsafeChatStates | ForEach-Object { $_.ThreadId }) -join ', '
        throw "Import requires every chat to be new or byte-equivalent. Divergent thread(s): $ids"
    }
    $imported = @()
    foreach ($chat in @($plan.Chats)) {
        if ([int]$chat.Diff.identical -eq 1) {
            $imported += [pscustomobject]@{ ThreadId = $chat.ThreadId; Title = $chat.Title; Result = [pscustomobject]@{ Status = 'identical-skipped' } }
            continue
        }
        $arguments = @('import', $chat.Bundle, '--codex-home', (Get-FullPath $CodexHome), '--json')
        if ($Reconcile) { $arguments += '--reconcile' }
        $result = Invoke-Cct -Arguments $arguments -ParseJson
        $imported += [pscustomobject]@{ ThreadId = $chat.ThreadId; Title = $chat.Title; Result = $result }
    }
    $memoryResult = if ($MemoryMode -eq 'Merge') {
        Merge-Memories -Plan $plan
    } else {
        [pscustomobject]@{ Added = @(); Identical = @(); Conflicts = @(); ConflictRoot = $null }
    }
    $receiptRoot = Join-Path (Get-FullPath $CodexHome) 'chat-transfer-receipts'
    [System.IO.Directory]::CreateDirectory($receiptRoot) | Out-Null
    $receiptPath = Join-Path $receiptRoot (([string]$plan.Transfer.Manifest.ExportId) + '.json')
    $receipt = [pscustomobject]@{
        Schema = 'codex-chat-transfer/receipt-v1'
        ExportId = $plan.Transfer.Manifest.ExportId
        ImportedUtc = [DateTime]::UtcNow.ToString('o')
        TransferFolder = $plan.Transfer.Root
        Chats = $imported
        Memories = $memoryResult
        UndoNote = 'Use cct undo for each changed chat import; remove only memory files listed in Memories.Added after reviewing later edits.'
    }
    Write-JsonFile -Path $receiptPath -Value $receipt
    [pscustomobject]@{
        Status = 'import-complete'
        ExportId = $receipt.ExportId
        ChatCount = $imported.Count
        MemoryAdded = @($memoryResult.Added).Count
        MemoryConflicts = @($memoryResult.Conflicts).Count
        MemoryConflictRoot = $memoryResult.ConflictRoot
        Receipt = $receiptPath
        RestartRecommended = -not [bool]$Reconcile
    } | ConvertTo-Json -Depth 10
}

switch ($Action) {
    'List' {
        [pscustomobject]@{ CodexHome = Get-FullPath $CodexHome; Chats = @(Get-SelectableSessions) } | ConvertTo-Json -Depth 8
    }
    'Export' { Export-TransferFolder }
    'Inspect' {
        $Apply = $false
        Invoke-Import
    }
    'Import' { Invoke-Import }
}
