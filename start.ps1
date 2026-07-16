$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path '.venv')) {
  python -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path '.env')) {
  $secret = ([Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N'))
  @("API_KEY=$secret", 'HOST=127.0.0.1', 'PORT=8787', 'DOWNLOAD_ROOT=downloads', 'COOKIE_BROWSER=') | Set-Content .env
  Write-Host "Created .env with a random API key."
}

$envLines = Get-Content .env
foreach ($line in $envLines) {
  if ($line -match '^([^#=]+)=(.*)$') { Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2] }
}
& .\.venv\Scripts\python.exe -m uvicorn api:app --host $env:HOST --port $env:PORT
