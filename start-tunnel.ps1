$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Runtime = Join-Path $Root 'runtime'
$Cloudflared = Join-Path $Runtime 'cloudflared.exe'
New-Item -ItemType Directory -Force $Runtime | Out-Null

if (-not (Test-Path $Cloudflared)) {
  Invoke-WebRequest 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile $Cloudflared
}
& $Cloudflared tunnel --url http://127.0.0.1:8787 --no-autoupdate

