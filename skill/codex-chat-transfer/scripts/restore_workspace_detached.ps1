[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$TransferFolder,
    [string]$CodexHome = (Join-Path $env:USERPROFILE '.codex'),
    [string[]]$PathMap = @(),
    [int]$WaitSeconds = 12
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$mainScript = Join-Path $PSScriptRoot 'codex_chat_transfer.ps1'
$manifestPath = Join-Path ([IO.Path]::GetFullPath($TransferFolder)) 'manifest.json'
$appId = 'OpenAI.Codex_2p2nqsd0c76g0!App'
$result = [ordered]@{
    StartedUtc = [DateTime]::UtcNow.ToString('o')
    Phase = 'scheduled'
    Success = $false
    Error = $null
    Restore = $null
    Verification = $null
    RelaunchAttempted = $false
}

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw "manifest.json is missing: $manifestPath" }
$manifest = Get-Content -LiteralPath $manifestPath -Encoding UTF8 -Raw | ConvertFrom-Json
$receiptRoot = Join-Path ([IO.Path]::GetFullPath($CodexHome)) 'chat-transfer-receipts'
[IO.Directory]::CreateDirectory($receiptRoot) | Out-Null
$statusPath = Join-Path $receiptRoot (([string]$manifest.ExportId) + '-workspace-restore.json')

function Save-Status {
    param([string]$Phase)
    $result.Phase = $Phase
    $result.UpdatedUtc = [DateTime]::UtcNow.ToString('o')
    [IO.File]::WriteAllText($statusPath, ($result | ConvertTo-Json -Depth 30) + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
}

function Get-CodexMainProcess {
    return @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -match 'OpenAI\.Codex_' -and
        $_.Name -match '^(ChatGPT|Codex)\.exe$' -and
        $_.CommandLine -notmatch '--type='
    } | Sort-Object CreationDate | Select-Object -First 1)
}

function Get-DescendantProcessIds {
    param([int]$RootId)
    $all = @(Get-CimInstance Win32_Process)
    $found = [Collections.Generic.List[int]]::new()
    $pending = [Collections.Generic.Queue[int]]::new()
    $pending.Enqueue($RootId)
    while ($pending.Count -gt 0) {
        $parent = $pending.Dequeue()
        foreach ($child in $all | Where-Object ParentProcessId -eq $parent) {
            if (-not $found.Contains([int]$child.ProcessId)) {
                $found.Add([int]$child.ProcessId)
                $pending.Enqueue([int]$child.ProcessId)
            }
        }
    }
    return @($found)
}

Save-Status -Phase 'waiting-before-shutdown'
Start-Sleep -Seconds $WaitSeconds

try {
    Save-Status -Phase 'closing-codex'
    $main = Get-CodexMainProcess
    if ($main.Count -gt 0) {
        $mainId = [int]$main[0].ProcessId
        $descendants = @(Get-DescendantProcessIds -RootId $mainId)
        $process = Get-Process -Id $mainId -ErrorAction SilentlyContinue
        if ($null -ne $process) { [void]$process.CloseMainWindow() }
        $deadline = (Get-Date).AddSeconds(20)
        while ((Get-Process -Id $mainId -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 500
        }
        foreach ($processId in (@($descendants) + @($mainId))) {
            if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
                Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Start-Sleep -Seconds 2

    Save-Status -Phase 'restoring-workspace'
    $restoreOutput = @(& $mainScript -Action RestoreWorkspace -TransferFolder ([IO.Path]::GetFullPath($TransferFolder)) -CodexHome ([IO.Path]::GetFullPath($CodexHome)) -Apply -PathMap $PathMap 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Workspace restore failed: $($restoreOutput -join [Environment]::NewLine)" }
    $result.Restore = (($restoreOutput -join [Environment]::NewLine) | ConvertFrom-Json)

    Save-Status -Phase 'verifying-workspace'
    $verifyOutput = @(& $mainScript -Action Verify -TransferFolder ([IO.Path]::GetFullPath($TransferFolder)) -CodexHome ([IO.Path]::GetFullPath($CodexHome)) -PathMap $PathMap 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Workspace verification failed: $($verifyOutput -join [Environment]::NewLine)" }
    $result.Verification = (($verifyOutput -join [Environment]::NewLine) | ConvertFrom-Json)
    if (-not [bool]$result.Verification.Verified) { throw 'Workspace verification did not pass.' }

    $result.Success = $true
    $result.CompletedUtc = [DateTime]::UtcNow.ToString('o')
    Save-Status -Phase 'complete'
}
catch {
    $result.Error = $_.Exception.ToString()
    $result.CompletedUtc = [DateTime]::UtcNow.ToString('o')
    Save-Status -Phase 'failed'
}
finally {
    $result.RelaunchAttempted = $true
    Save-Status -Phase $result.Phase
    Start-Process -FilePath 'explorer.exe' -ArgumentList "shell:AppsFolder\$appId"
}
