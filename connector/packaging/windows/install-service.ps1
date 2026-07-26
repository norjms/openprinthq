# OpenPrintHQ local connector — Windows 11 install (PowerShell, run as Administrator)
#
# Installs the connector as an auto-starting Windows service using the built-in
# Service Control Manager via a small wrapper. Requires Node.js >= 20
# (https://nodejs.org). No inbound firewall rules are needed — the connector
# only makes outbound HTTPS connections.
#
# Usage:
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\install-service.ps1 -ControlUrl "https://openprinthq.example.org" -Token "<connector token>" -Name "windows-pc"

param(
  [Parameter(Mandatory=$true)][string]$ControlUrl,
  [Parameter(Mandatory=$true)][string]$Token,
  [string]$Name = "windows-pc",
  [string]$InstallDir = "$env:ProgramData\OpenPrintHQ\connector"
)

$ErrorActionPreference = "Stop"
$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node) { throw "Node.js not found on PATH. Install Node >= 20 from https://nodejs.org first." }

# Copy agent sources next to this script into the install dir.
New-Item -ItemType Directory -Force -Path "$InstallDir\src" | Out-Null
Copy-Item -Force "$PSScriptRoot\..\..\src\agent.js" "$InstallDir\src\agent.js"
Copy-Item -Force "$PSScriptRoot\..\..\package.json" "$InstallDir\package.json"

# Persist config as machine environment variables for the service.
[Environment]::SetEnvironmentVariable("OPHQ_CONTROL_URL", $ControlUrl, "Machine")
[Environment]::SetEnvironmentVariable("OPHQ_CONNECTOR_TOKEN", $Token, "Machine")
[Environment]::SetEnvironmentVariable("OPHQ_CONNECTOR_NAME", $Name, "Machine")

# Create the service. `sc.exe` binPath runs node against the agent.
$bin = "`"$node`" `"$InstallDir\src\agent.js`""
sc.exe create OpenPrintHQConnector binPath= $bin start= auto DisplayName= "OpenPrintHQ Connector" | Out-Null
sc.exe description OpenPrintHQConnector "Outbound tunnel so OpenPrintHQ can reach LAN printers behind NAT/CGNAT." | Out-Null
sc.exe failure OpenPrintHQConnector reset= 60 actions= restart/5000/restart/5000/restart/5000 | Out-Null
Start-Service OpenPrintHQConnector
Write-Host "Installed and started 'OpenPrintHQConnector'. Check status with: Get-Service OpenPrintHQConnector"
Write-Host "Note: the built-in SCM does not capture stdout; for logs, run 'node src\agent.js' manually once to verify connectivity."
