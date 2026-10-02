# paper-to-youtube 설치 스크립트 (Windows PowerShell)
# 실행:  powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = "Stop"
$Kit    = Split-Path -Parent $MyInvocation.MyCommand.Path
$Skills = Join-Path $env:USERPROFILE ".claude\skills"
. (Join-Path $Kit "config.ps1")

Write-Host "== 1. Claude Code 스킬 복사 -> $Skills" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $Skills | Out-Null
Get-ChildItem (Join-Path $Kit "skills") -Directory | ForEach-Object {
    $dst = Join-Path $Skills $_.Name
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    Copy-Item -Recurse $_.FullName $dst
    Write-Host "   + $($_.Name)"
}

Write-Host "== 2. 파이썬 패키지 설치" -ForegroundColor Cyan
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue) }
if (-not $py) { throw "Python 3.10+ 이 필요합니다: winget install Python.Python.3.12" }
& $py.Source -m pip install --upgrade edge-tts pdf2image pymupdf pillow requests scikit-learn `
    google-api-python-client google-auth-oauthlib google-auth-httplib2

Write-Host "== 3. 필요한 프로그램 확인" -ForegroundColor Cyan
$need = @{
  "xelatex"  = "TeX Live (https://tug.org/texlive/windows.html) 또는 MiKTeX";
  "latexmk"  = "TeX Live 에 포함 (MiKTeX 는 Perl 필요: winget install StrawberryPerl.StrawberryPerl)";
  "ffmpeg"   = "winget install Gyan.FFmpeg";
  "ffprobe"  = "winget install Gyan.FFmpeg";
  "pdftoppm" = "Poppler: scoop install poppler  (또는 conda install -c conda-forge poppler)";
  "edge-tts" = "pip install edge-tts (위에서 설치됨, 안 보이면 Python Scripts 폴더를 PATH 에 추가)";
  "uv"       = "winget install astral-sh.uv  (arxiv-downloader 설치에 사용)";
  "git"      = "winget install Git.Git  (Claude Code 가 Git Bash 를 사용)";
  "claude"   = "Claude Code: https://docs.claude.com/en/docs/claude-code";
}
$missing = 0
foreach ($k in $need.Keys) {
  if (Get-Command $k -ErrorAction SilentlyContinue) { Write-Host "   OK  $k" -ForegroundColor Green }
  else { Write-Host "   없음 $k  ->  $($need[$k])" -ForegroundColor Yellow; $missing++ }
}

Write-Host "== 3-1. arxiv-downloader (uv 전용 가상환경)" -ForegroundColor Cyan
$dlVenv = Join-Path $env:USERPROFILE ".paper-to-youtube\arxiv-dl-venv"
if (Get-Command uv -ErrorAction SilentlyContinue) {
  if (-not (Test-Path (Join-Path $dlVenv "Scripts\python.exe"))) { uv venv --python 3.12 $dlVenv }
  uv pip install --python (Join-Path $dlVenv "Scripts\python.exe") arxiv-downloader
  Write-Host "   설치 위치: $dlVenv"
} else { Write-Host "   uv 가 없어 건너뜀 (첫 실행 때 자동 설치 시도, 없으면 기존 다운로더 사용)" -ForegroundColor Yellow }

Write-Host "== 4. 작업 폴더" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $env:PP_ROOT | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $env:YT_CLIENT_SECRET) | Out-Null
Write-Host "   작업 폴더: $env:PP_ROOT"
Write-Host "   YouTube OAuth 파일 둘 곳: $env:YT_CLIENT_SECRET"

if ($missing -gt 0) { Write-Host "`n위의 '없음' 항목을 설치한 뒤 다시 실행하세요." -ForegroundColor Yellow }
else { Write-Host "`n설치 완료! 테스트:  powershell -ExecutionPolicy Bypass -File .\run_daily.ps1" -ForegroundColor Green }
