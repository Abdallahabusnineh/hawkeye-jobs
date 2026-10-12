# Hawkeye Jobs — Smart App Control check (Windows 11)
#
#   irm https://abdallahabusnineh.github.io/hawkeye-jobs/sac-check.ps1 | iex
#
# Finds out which parts a future Windows version of Hawkeye Jobs could use on a PC with
# Smart App Control turned on. Everything happens in a temporary folder that is deleted at
# the end; nothing is installed and no setting is changed. Written for Constrained Language
# Mode (cmdlets only, no .NET calls), which Smart App Control may enforce.

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$dir = Join-Path $env:TEMP 'hawkeye-sac-check'
if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
New-Item -ItemType Directory -Path $dir | Out-Null
$results = @()

function Add-Result($name, $ok, $detail) {
    $script:results += "{0,-28} {1}  {2}" -f $name, $(if ($ok) { 'OK     ' } else { 'BLOCKED' }), $detail
}

Write-Host "`n==> Hawkeye Jobs: checking what Smart App Control allows (2-3 minutes)..."
Write-Host "    PowerShell language mode: $($ExecutionContext.SessionState.LanguageMode)"

# 1. Official Python (signed by the Python Software Foundation)
$zip = Join-Path $dir 'python.zip'
Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile $zip
Expand-Archive -Path $zip -DestinationPath (Join-Path $dir 'py')
$py = Join-Path $dir 'py\python.exe'
$out = & $py -c "print('python-ok')" 2>&1 | Out-String
Add-Result 'Python (python.org)' ($out -match 'python-ok') ($out.Trim() -split "`n" | Select-Object -Last 1)

# 2. pip + the packages with compiled parts
$pth = Join-Path $dir 'py\python312._pth'
(Get-Content $pth) -replace '^#import site', 'import site' | Set-Content $pth
Invoke-WebRequest -UseBasicParsing 'https://bootstrap.pypa.io/get-pip.py' -OutFile (Join-Path $dir 'get-pip.py')
& $py (Join-Path $dir 'get-pip.py') --no-warn-script-location -q 2>&1 | Out-Null
Write-Host "==> Downloading test packages..."
& $py -m pip install -q --no-warn-script-location numpy==1.26.3 pandas==2.3.3 pydantic==2.12.5 `
    regex==2024.11.6 tls-client==1.0.1 pillow==12.3.0 websockets 2>&1 | Out-Null

$modules = @(
    @('numpy', 'import numpy'),
    @('pandas', 'import pandas'),
    @('pydantic (pydantic_core)', 'import pydantic_core'),
    @('regex', 'import regex'),
    @('tls_client (LinkedIn/Indeed)', 'import tls_client.cffi'),
    @('pillow', 'from PIL import Image'),
    @('sqlite3 (job store)', 'import sqlite3'),
    @('ssl', 'import ssl')
)
foreach ($m in $modules) {
    $out = & $py -c "$($m[1]); print('import-ok')" 2>&1 | Out-String
    $last = ($out.Trim() -split "`n" | Select-Object -Last 1)
    Add-Result $m[0] ($out -match 'import-ok') $(if ($out -match 'import-ok') { '' } else { $last })
}

# 3. Chrome driven directly over its debugging protocol (no chromedriver file)
$chrome = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
            "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($chrome) {
    $chromeProfile = Join-Path $dir 'chrome-profile'
    $proc = Start-Process -FilePath $chrome -PassThru -ArgumentList '--remote-debugging-port=9333', `
        "--user-data-dir=$chromeProfile", '--headless=new', '--no-first-run', 'about:blank'
    Start-Sleep -Seconds 4
    try {
        $v = Invoke-RestMethod -UseBasicParsing 'http://127.0.0.1:9333/json/version'
        Add-Result 'Chrome via CDP (Bayt/Google)' ($null -ne $v) $v.Browser
    } catch {
        Add-Result 'Chrome via CDP (Bayt/Google)' $false $_.Exception.Message
    }
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
} else {
    $script:results += 'Chrome via CDP (Bayt/Google)  SKIPPED  Google Chrome is not installed'
}

# 4. Edge as the app window
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
          "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
Add-Result 'Microsoft Edge (app window)' ([bool]$edge) $edge

Start-Sleep -Seconds 1
Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue

Write-Host "`n================ RESULT — please send a screenshot of this ================"
$results | ForEach-Object { Write-Host $_ }
Write-Host "=============================================================================`n"
