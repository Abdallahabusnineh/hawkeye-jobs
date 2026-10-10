# Hawkeye Jobs installer for Windows.
#
#   irm https://abdallahabusnineh.github.io/hawkeye-jobs/install.ps1 | iex
#
# Downloads the latest installer, checks it against the release's SHA256SUMS.txt, installs for
# the current user (no administrator prompt) and opens the app. A file downloaded by PowerShell
# is not marked "downloaded from the internet", so SmartScreen does not block it the way it
# blocks a browser download of an unsigned app.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # the progress bar makes Invoke-WebRequest very slow

$Base = 'https://github.com/Abdallahabusnineh/hawkeye-jobs/releases/latest/download'
$File = 'HawkeyeJobs-Windows-Setup.exe'
$Work = Join-Path ([IO.Path]::GetTempPath()) ("hawkeye-install-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Work | Out-Null

try {
    Write-Host "`n==> Downloading $File"
    Invoke-WebRequest -UseBasicParsing "$Base/$File" -OutFile (Join-Path $Work $File)
    Invoke-WebRequest -UseBasicParsing "$Base/SHA256SUMS.txt" -OutFile (Join-Path $Work 'SHA256SUMS.txt')

    Write-Host "==> Checking the download"
    $expected = (Get-Content (Join-Path $Work 'SHA256SUMS.txt') |
        Where-Object { ($_ -split '\s+')[1] -eq $File } | Select-Object -First 1) -split '\s+' | Select-Object -First 1
    $actual = (Get-FileHash -Algorithm SHA256 (Join-Path $Work $File)).Hash
    if (-not $expected -or $expected.ToLower() -ne $actual.ToLower()) {
        throw "The download is damaged or was changed (checksum mismatch). Nothing was installed."
    }

    Write-Host "==> Installing"
    $setup = Start-Process -FilePath (Join-Path $Work $File) -Wait -PassThru `
        -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS', '/TASKS=desktopicon'
    if ($setup.ExitCode -ne 0) { throw "The installer failed (code $($setup.ExitCode))." }

    $app = Join-Path $env:LOCALAPPDATA 'Programs\Hawkeye Jobs\Hawkeye Jobs.exe'
    Write-Host "==> Done. Opening Hawkeye Jobs"
    Start-Process -FilePath $app
}
catch {
    Write-Host "`nError: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
}
