# Installs the Store package on this machine and runs the smoke test against it (release workflow).
#
#   pwsh packaging/windows/test_msix.ps1 out/HawkeyeJobs-Windows-Store.msix
#
# The package from build_msix.py is unsigned (the Store signs it), and Windows installs only signed
# packages, so this signs a COPY with a throwaway certificate made for this run and trusts it on
# this machine only. The certificate is removed afterwards and never leaves the runner.
param([Parameter(Mandatory = $true)][string]$Msix)
$ErrorActionPreference = "Stop"

[xml]$manifest = Get-Content packaging/windows/msix/AppxManifest.xml
$publisher = $manifest.Package.Identity.Publisher
$name = $manifest.Package.Identity.Name

$cert = New-SelfSignedCertificate -Type Custom -Subject $publisher -KeyUsage DigitalSignature `
  -FriendlyName "Hawkeye Jobs package test" -CertStoreLocation "Cert:\CurrentUser\My" `
  -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}") -NotAfter (Get-Date).AddDays(1)
$cer = Join-Path $env:RUNNER_TEMP "package-test.cer"
$test = Join-Path $env:RUNNER_TEMP "package-test.msix"
try {
  Export-Certificate -Cert $cert -FilePath $cer | Out-Null
  Import-Certificate -FilePath $cer -CertStoreLocation "Cert:\LocalMachine\TrustedPeople" | Out-Null
  Copy-Item $Msix $test -Force

  $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.*\x64\signtool.exe" |
    Sort-Object { [version]$_.Directory.Parent.Name } | Select-Object -Last 1
  & $signtool.FullName sign /fd SHA256 /sha1 $cert.Thumbprint /s My $test
  if ($LASTEXITCODE -ne 0) { throw "signtool failed ($LASTEXITCODE)" }

  Add-AppxPackage -Path $test
  $package = Get-AppxPackage -Name $name
  if (-not $package) { throw "the package did not install" }
  Write-Host "Installed $($package.PackageFullName)"

  # The execution alias starts the app inside its package, as the Start menu would.
  $alias = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\hawkeye-jobs.exe"
  $env:HAWKEYE_EXPECT_PACKAGED = "1"
  python packaging/smoke_test.py $alias
  if ($LASTEXITCODE -ne 0) { throw "smoke test of the installed package failed" }
} finally {
  Remove-Item Env:\HAWKEYE_EXPECT_PACKAGED -ErrorAction SilentlyContinue
  Get-AppxPackage -Name $name | Remove-AppxPackage -ErrorAction SilentlyContinue
  Get-ChildItem "Cert:\LocalMachine\TrustedPeople" | Where-Object Thumbprint -eq $cert.Thumbprint | Remove-Item
  Remove-Item "Cert:\CurrentUser\My\$($cert.Thumbprint)" -ErrorAction SilentlyContinue
  Remove-Item $cer, $test -ErrorAction SilentlyContinue
}
