$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$RuntimeDir = Join-Path $RootDir "desktop\runtime\bin\windows-x64\core"
$BuildDir = Join-Path $RootDir "desktop\.build\pyinstaller"
$SpecDir = Join-Path $BuildDir "spec"
$WorkDir = Join-Path $BuildDir "work"
$DistDir = Join-Path $BuildDir "dist"
$EntryPoint = Join-Path $BuildDir "postbridge_runtime_entry.py"

New-Item -ItemType Directory -Force $RuntimeDir, $SpecDir, $WorkDir, $DistDir | Out-Null
@"
from postbridge.desktop_runtime import main

raise SystemExit(main())
"@ | Set-Content -Encoding UTF8 $EntryPoint

$python = Get-Command python -ErrorAction Stop
& $python.Source -m pip install --upgrade pip pyinstaller | Write-Host
& $python.Source -m pip install $RootDir | Write-Host

$env:DATABASE_URL = "postgresql://postbridge:postbridge@127.0.0.1:8822/postbridge"
$env:REDIS_URL = "redis://127.0.0.1:8823/0"
$env:CREDENTIALS_ENCRYPTION_KEY = "desktop-build-encryption-key"
$env:POSTBRIDGE_APP_MODE = "selfhost"

$separator = [IO.Path]::PathSeparator
$addAlembicIni = "$(Join-Path $RootDir "alembic.ini")${separator}."
$addAlembic = "$(Join-Path $RootDir "alembic")${separator}alembic"

& $python.Source -m PyInstaller `
  --clean `
  --noconfirm `
  --onedir `
  --name postbridge-runtime `
  --specpath $SpecDir `
  --workpath $WorkDir `
  --distpath $DistDir `
  --collect-all postbridge `
  --collect-all celery `
  --collect-all kombu `
  --collect-all billiard `
  --collect-all vine `
  --collect-all uvicorn `
  --collect-all sqlalchemy `
  --collect-all alembic `
  --add-data $addAlembicIni `
  --add-data $addAlembic `
  $EntryPoint

$SourceExe = Join-Path $DistDir "postbridge-runtime\postbridge-runtime.exe"
if (!(Test-Path $SourceExe)) {
  throw "PyInstaller did not create $SourceExe"
}

Copy-Item -Recurse -Force (Join-Path $DistDir "postbridge-runtime\*") $RuntimeDir
Copy-Item -Force $SourceExe (Join-Path $RuntimeDir "postbridge-api.exe")
Copy-Item -Force $SourceExe (Join-Path $RuntimeDir "postbridge-worker.exe")
Copy-Item -Force $SourceExe (Join-Path $RuntimeDir "postbridge-migrate.exe")

Write-Host "Created Windows backend runtime in $RuntimeDir"
