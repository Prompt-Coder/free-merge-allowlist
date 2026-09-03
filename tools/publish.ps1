<#
.SYNOPSIS
  Publishes a map release into the free-merge allowlist.

.DESCRIPTION
  Hashes every merge-relevant file (.ymap .ybn .ydr .ydd .ytyp) inside a
  release zip or resource folder, appends the hashes that are not already
  published for the publisher, and registers the hash file in index.txt.

  Append-only by design: nothing is ever removed, so re-running with the same
  source is a no-op and customers on older map versions keep matching forever.

.EXAMPLE
  .\publish.ps1 -Source "C:\releases\prompt_sandy_bank_v1.2.zip" -Publisher prompt
  .\publish.ps1 -Source "D:\maps\prompt_sandy_bank" -Publisher prompt -Name sandy_bank
#>
param(
    # Release zip or resource folder to hash.
    [Parameter(Mandatory = $true)] [string]$Source,
    # Publisher folder under publishers/ (lowercase letters, digits, - and _).
    [Parameter(Mandatory = $true)] [string]$Publisher,
    # Hash-file name; defaults to the source's file/folder name.
    [string]$Name
)

$ErrorActionPreference = 'Stop'

# The extensions the merger API accepts for upload - keep in sync with
# MAP_EXTENSION_TO_PRICE in vertex-hub-merger-api. Everything else in a
# release (escrow blobs, textures, scripts) is irrelevant to merges.
$mergeExtensions = @('.ymap', '.ybn', '.ydr', '.ydd', '.ytyp')

# Only vanilla-named files are ever merged, so only those are listed - plus
# every lodlights ymap of any name (the LOD-light manager takes them all).
# The name inventory is the same base-game list the Vertex Hub app uses.
$namesUrl = 'https://cdn.vertex-hub.com/gta5-resource-list.json'
$vanillaNames = @{}
foreach ($n in ((Invoke-WebRequest -UseBasicParsing -Uri $namesUrl -UserAgent 'Mozilla/5.0 free-merge-allowlist/publish').Content | ConvertFrom-Json)) {
    $vanillaNames[([string]$n).ToLower()] = $true
}
function Test-ListedName([string]$fileName) {
    $lower = $fileName.ToLower()
    if ($lower -like '*lodlights*.ymap') { return $true }
    return $vanillaNames.ContainsKey($lower)
}

$repoRoot = Split-Path -Parent $PSScriptRoot

if ($Publisher -cnotmatch '^[a-z0-9][a-z0-9_-]*$') {
    throw "Publisher must be lowercase letters/digits/-/_ (got: $Publisher)"
}

if (-not $Name) {
    $Name = [IO.Path]::GetFileNameWithoutExtension((Split-Path -Leaf $Source))
}
$Name = $Name.ToLower() -replace '[^a-z0-9_-]', '_'

# --- collect SHA-256 of every merge-relevant file in the source ---
$collected = New-Object System.Collections.Generic.List[string]

if (Test-Path -LiteralPath $Source -PathType Container) {
    Get-ChildItem -LiteralPath $Source -Recurse -File |
        Where-Object { ($mergeExtensions -contains $_.Extension.ToLower()) -and (Test-ListedName $_.Name) } |
        ForEach-Object {
            $collected.Add((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower())
        }
}
elseif (Test-Path -LiteralPath $Source -PathType Leaf) {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $zip = [System.IO.Compression.ZipFile]::OpenRead((Get-Item -LiteralPath $Source).FullName)
    try {
        foreach ($entry in $zip.Entries) {
            if ($entry.Name -eq '') { continue } # directory entry
            $ext = [IO.Path]::GetExtension($entry.Name).ToLower()
            if ($mergeExtensions -notcontains $ext) { continue }
            if (-not (Test-ListedName $entry.Name)) { continue }
            $stream = $entry.Open()
            try { $bytes = $sha.ComputeHash($stream) } finally { $stream.Dispose() }
            $collected.Add((($bytes | ForEach-Object { $_.ToString('x2') }) -join ''))
        }
    }
    finally { $zip.Dispose() }
}
else {
    throw "Source not found: $Source"
}

$unique = @($collected | Select-Object -Unique)
if ($unique.Count -eq 0) {
    throw "No .ymap/.ybn/.ydr/.ydd/.ytyp files found in: $Source"
}

# --- diff against everything already published for this publisher ---
# Publisher-wide (not per-file): a file shared between two maps should not be
# listed twice.
$publisherDir = Join-Path $repoRoot ('publishers\' + $Publisher)
$existing = @{}
if (Test-Path -LiteralPath $publisherDir) {
    Get-ChildItem -LiteralPath $publisherDir -Filter *.txt -File | ForEach-Object {
        foreach ($line in [IO.File]::ReadAllLines($_.FullName)) {
            $trimmed = $line.Trim()
            if ($trimmed -ne '' -and -not $trimmed.StartsWith('#')) { $existing[$trimmed] = $true }
        }
    }
}

$new = @($unique | Where-Object { -not $existing.ContainsKey($_) })

# UTF-8 without BOM - these files are parsed line-by-line by the API loader.
$encoding = New-Object System.Text.UTF8Encoding($false)
$targetFile = Join-Path $publisherDir ($Name + '.txt')

if ($new.Count -gt 0) {
    New-Item -ItemType Directory -Force -Path $publisherDir | Out-Null
    $stamp = Get-Date -Format 'yyyy-MM-dd'
    $block = @("# $Name $stamp (+$($new.Count))") + $new
    if (Test-Path -LiteralPath $targetFile) { $block = @('') + $block }
    [IO.File]::AppendAllText($targetFile, (($block -join "`n") + "`n"), $encoding)
}

# --- make sure index.txt lists the hash file ---
$indexFile = Join-Path $repoRoot 'index.txt'
$indexEntry = "publishers/$Publisher/$Name.txt"
$indexLines = @()
if (Test-Path -LiteralPath $indexFile) { $indexLines = @([IO.File]::ReadAllLines($indexFile)) }
if ((Test-Path -LiteralPath $targetFile) -and -not ($indexLines -contains $indexEntry)) {
    if ($indexLines.Count -eq 0) {
        $indexLines = @('# free-merge allowlist index - one hash file per line')
    }
    $indexLines += $indexEntry
    [IO.File]::WriteAllText($indexFile, (($indexLines -join "`n") + "`n"), $encoding)
}

Write-Output "source    : $Source"
Write-Output "publisher : $Publisher  list: $indexEntry"
Write-Output "hashed    : $($unique.Count) unique file hash(es), new: $($new.Count)"
if ($new.Count -eq 0) {
    Write-Output "nothing to publish - all hashes already listed"
} else {
    Write-Output "appended  : $targetFile"
    Write-Output "review the diff, then commit + push"
}
