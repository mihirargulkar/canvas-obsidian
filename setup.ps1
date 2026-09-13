# One-shot setup on Windows: virtualenv, dependencies, .env, first sync.
#
#   powershell -ExecutionPolicy Bypass -File setup.ps1
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -NoSync
#
# Safe to re-run: it skips whatever is already in place. (macOS/Linux: ./setup.sh)
#
# Unattended (also how CI exercises this script):
#   ... -NoSync -CanvasUrl https://x.instructure.com -CanvasToken t -GeminiKey k
param(
  [switch]$NoSync,
  [string]$CanvasUrl,
  [string]$CanvasToken,
  [string]$GeminiKey
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Say  { param($m) Write-Host "`n$m" -ForegroundColor White }
function Ok   { param($m) Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn { param($m) Write-Host "  [!] $m" -ForegroundColor Yellow }

Say "Checking Python"
$py = Get-Command py -ErrorAction SilentlyContinue
$exe = if ($py) { "py" } else { "python" }
$ver = & $exe -c "import sys;print('.'.join(map(str,sys.version_info[:2])))"
& $exe -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.10+ required (found $ver). https://python.org/downloads" }
Ok "Python $ver"

Say "Installing dependencies"
if (-not (Test-Path ".venv")) { & $exe -m venv .venv }
$vpy = ".\.venv\Scripts\python.exe"
& $vpy -m pip install -q --upgrade pip
& $vpy -m pip install -q -r requirements.txt
Ok "virtualenv ready at .venv (~170MB, no PyTorch)"

Say "Checking LibreOffice"
$found = & $vpy -c "import sys; sys.path.insert(0,'.'); from canvas_vault.ingest import find_soffice; print(find_soffice() or '')"
if ($found) { Ok "found: $found" } else {
  Warn "not found - PDFs still work, but .pptx/.docx slides can't be transcribed."
  Warn "Install from libreoffice.org, or set SOFFICE to the full path of soffice.exe"
}

Say "Credentials"
if (Test-Path ".env") { Ok ".env already exists - leaving it alone" } else {
  Copy-Item ".env.example" ".env"
  Write-Host "  Two values are needed. Both stay in .env on this machine and are gitignored.`n"
  if ($CanvasUrl -and $CanvasToken -and $GeminiKey) {
    $url = $CanvasUrl.TrimEnd("/"); $tok = $CanvasToken; $key = $GeminiKey
    if ($url -notmatch "^https://.+\..+") { throw "-CanvasUrl must be a full https:// URL" }
    Ok "using credentials passed on the command line"
  } else {
    Write-Host "  Canvas URL - your school's Canvas, e.g. https://yourschool.instructure.com"
    do {
      $url = (Read-Host "  Canvas URL").TrimEnd("/")
    } until ($url -match "^https://.+\..+")
    Write-Host "  Canvas token - Canvas > Account > Settings > New Access Token"
    $tok = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
      [Runtime.InteropServices.Marshal]::SecureStringToBSTR((Read-Host "  Canvas token" -AsSecureString)))
    Write-Host "  Gemini key (free tier) - https://aistudio.google.com/app/apikey"
    $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
      [Runtime.InteropServices.Marshal]::SecureStringToBSTR((Read-Host "  Gemini API key" -AsSecureString)))
  }
  # utf8NoBOM: python's dotenv chokes on a BOM, and Set-Content writes one by default
  (Get-Content ".env" -Encoding utf8) | ForEach-Object {
    if ($_ -like "CANVAS_URL=*")       { "CANVAS_URL=$url" }
    elseif ($_ -like "CANVAS_TOKEN=*") { "CANVAS_TOKEN=$tok" }
    elseif ($_ -like "GEMINI_API_KEY=*") { "GEMINI_API_KEY=$key" }
    else { $_ }
  } | Set-Content ".env" -Encoding utf8NoBOM
  Ok "wrote .env"
}

Say "Verifying Canvas access"
& $vpy -m canvas_vault.sync --list
if ($LASTEXITCODE -ne 0) {
  Warn "Could not list your classes. Check CANVAS_URL and CANVAS_TOKEN in .env, then re-run."
  exit 1
}
Ok "Canvas token works"

if (-not $NoSync) {
  Say "First sync"
  Write-Host "  This transcribes your slide decks through a vision model. It can take"
  Write-Host "  tens of minutes and may hit Gemini's free daily quota - it's resumable,"
  Write-Host "  so just run it again tomorrow and it picks up where it stopped."
  if ((Read-Host "  Run it now? [Y/n]") -notmatch "^[Nn]") { & $vpy -m canvas_vault.sync }
  else { Warn "skipped - run: .\.venv\Scripts\python -m canvas_vault.sync" }
}

Say "Done"
@"
  Study with an LLM (MCP):
    Claude Code    - .mcp.json is already here, just open this repo
    Claude Desktop - see the config block in README.md, then quit and reopen it
  Browse the vault:
    open the vault\ folder as an Obsidian vault
  Keep it current:
    powershell -ExecutionPolicy Bypass -File tools\install-daily-sync.ps1
"@ | Write-Host
