# 手动质量门禁（先手动；CI 直接复用本脚本，命令不变）。
# P-CI：路径与临时目录写法改为跨平台（ubuntu runner 上 $env:TEMP 不存在、
# "..\.." 反斜杠在 Linux pwsh 下不可靠），Windows 行为不变。
# 用法: powershell -ExecutionPolicy Bypass -File requirements\eval\check.ps1
# 退出码: 0=PASS, 非0=FAIL（与 run_eval 一致，CI 可直接判）
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "../..")

$out = Join-Path ([System.IO.Path]::GetTempPath()) "req-gate.json"

Write-Host "[1/2] 生成 sample 产物（mock，确定性）..." -ForegroundColor Cyan
python -m requirements.cli --prd requirements/samples/sample-prd.md --provider mock --out $out
if ($LASTEXITCODE -ne 0) { Write-Host "产物生成失败" -ForegroundColor Red; exit 1 }

Write-Host "[2/2] golden 门禁打分..." -ForegroundColor Cyan
python -m requirements.eval.run_eval --golden requirements/eval/golden.json --product $out
exit $LASTEXITCODE
