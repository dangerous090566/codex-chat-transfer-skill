function Get-ConfiguredTransferRoot {
    $candidate = $TransferRoot
    if ([string]::IsNullOrWhiteSpace($candidate)) { $candidate = $env:CODEX_CHAT_TRANSFER_ROOT }
    $settingsPath = Join-Path (Get-FullPath $CodexHome) 'chat-transfer-settings.json'
    if ([string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $settingsPath -PathType Leaf)) {
        $settings = Get-Content -LiteralPath $settingsPath -Encoding UTF8 -Raw | ConvertFrom-Json
        if ($settings.PSObject.Properties['TransferRoot']) { $candidate = [string]$settings.TransferRoot }
    }
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $candidate = Join-Path $env:USERPROFILE 'Nutstore\1\我的坚果云\codex文件处理\Codex聊天迁移'
    }
    if (-not [IO.Path]::IsPathFullyQualified($candidate)) { throw 'TransferRoot must be an absolute path.' }
    return Get-FullPath $candidate
}

function Get-TransferPackages {
    param([Parameter(Mandatory = $true)][string]$Root)
    $packages = @()
    if (Test-Path -LiteralPath $Root -PathType Container) {
        foreach ($folder in @(Get-ChildItem -LiteralPath $Root -Directory | Where-Object Name -NotLike '*.partial-*')) {
            $manifestPath = Join-Path $folder.FullName 'manifest.json'
            if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { continue }
            try {
                $manifest = Get-Content -LiteralPath $manifestPath -Encoding UTF8 -Raw | ConvertFrom-Json
                if ($manifest.Schema -ne 'codex-chat-transfer/v1') { continue }
                $packages += [pscustomobject]@{
                    Name = $folder.Name; TransferFolder = $folder.FullName
                    ExportId = [string]$manifest.ExportId; CreatedUtc = Get-IsoTimestamp $manifest.CreatedUtc
                    ChatCount = @($manifest.Chats).Count
                    Titles = @($manifest.Chats | ForEach-Object { [string]$_.Title })
                }
            } catch {
                Write-Warning "Unreadable transfer manifest: $manifestPath"
            }
        }
    }
    return @($packages | Sort-Object CreatedUtc -Descending)
}

function Get-TransferStorageSummary {
    $archives = @()
    if (Test-Path -LiteralPath $ResolvedTransferRoot -PathType Container) {
        $archives = @(Get-ChildItem -LiteralPath $ResolvedTransferRoot -File -Filter '*.zip' |
            ForEach-Object { [pscustomobject]@{ Path = $_.FullName; Bytes = $_.Length; RequiresExtraction = $true } })
    }
    return [pscustomobject]@{
        TransferRoot = $ResolvedTransferRoot
        Packages = @(Get-TransferPackages -Root $ResolvedTransferRoot)
        Archives = $archives
    }
}

function Resolve-TransferFolderArgument {
    if (-not [string]::IsNullOrWhiteSpace($TransferFolder)) {
        if ([IO.Path]::IsPathFullyQualified($TransferFolder)) { return Get-FullPath $TransferFolder }
        return Assert-PathUnder -Parent $ResolvedTransferRoot -Child (Join-Path $ResolvedTransferRoot $TransferFolder)
    }
    if ($Action -eq 'Export') {
        $name = 'chat-transfer-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
        return Assert-PathUnder -Parent $ResolvedTransferRoot -Child (Join-Path $ResolvedTransferRoot $name)
    }
    $packages = @(Get-TransferPackages -Root $ResolvedTransferRoot)
    if ($packages.Count -eq 1) { return $packages[0].TransferFolder }
    return $null
}
