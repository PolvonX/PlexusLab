<#
    Plexus Lab :: Cortex — установка и запуск.

    .\deploy.ps1            установить зависимости и запустить Cortex
    .\deploy.ps1 -Setup     только установка (venv + зависимости)
    .\deploy.ps1 -Test      прогнать тесты
    .\deploy.ps1 -Doctor    предполётная проверка (токены, группа, agy)
    .\deploy.ps1 -Mock      запустить с mock-драйвером вместо agy
#>

param(
    [switch]$Setup,
    [switch]$Test,
    [switch]$Doctor,
    [switch]$Mock
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $root ".venv"
$python = Join-Path $venv "Scripts\python.exe"

function Write-Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

# --- venv -------------------------------------------------------------
if (-not (Test-Path $python)) {
    Write-Step "Создаю виртуальное окружение"
    python -m venv $venv
    if (-not $?) { throw "Не удалось создать venv. Установлен ли Python 3.11+?" }
}

Write-Step "Устанавливаю зависимости"
& $python -m pip install --disable-pip-version-check -q -r (Join-Path $root "requirements-dev.txt")
if (-not $?) { throw "Установка зависимостей не удалась" }

# --- .env -------------------------------------------------------------
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $root ".env.example") $envFile
    Write-Host ""
    Write-Host "Создан .env — заполни его перед запуском:" -ForegroundColor Yellow
    Write-Host "  CORTEX_BOT_TOKEN  — токен бота-шлюза из @BotFather"
    Write-Host "  CEO_TELEGRAM_ID   — твой Telegram ID (@userinfobot)"
    Write-Host "  CORP_GROUP_ID     — ID рабочей группы (узнать: команда /id в группе)"
    Write-Host ""
    Write-Host "Не забудь выключить боту privacy mode в BotFather," -ForegroundColor Yellow
    Write-Host "иначе Cortex не увидит сообщения в группе." -ForegroundColor Yellow
    exit 0
}

# --- режимы -----------------------------------------------------------
if ($Test) {
    Write-Step "Тесты"
    & $python -m pytest -q
    exit $LASTEXITCODE
}

if ($Doctor) {
    Write-Step "Предполётная проверка"
    & $python (Join-Path $root "scripts\doctor.py")
    exit $LASTEXITCODE
}

if ($Setup) {
    Write-Step "Готово"
    Write-Host "Запуск: .\deploy.ps1"
    exit 0
}

if ($Mock) {
    Write-Step "Запуск в mock-режиме (без agy)"
    $env:PLEXUS_FORCE_DRIVER = "mock"
}

Write-Step "Запускаю Cortex"
& $python (Join-Path $root "run.py")
exit $LASTEXITCODE
