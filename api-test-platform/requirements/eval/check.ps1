# 手动质量门禁（先手动；将来接 CI 直接复用本脚本，命令不变）
# 用法: powershell -ExecutionPolicy Bypass -File requirements\eval\check.ps1
# 退出码: 0=PASS, 非0=FAIL（与 run_eval 一致，CI 可直接判）
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

$out = Join-Path $env:TEMP "req-gate.json"

Write-Host "[1/2] 生成 sample 产物（mock，确定性）..." -ForegroundColor Cyan
python -m requirements.cli --prd requirements/samples/sample-prd.md --provider mock --out $out
if ($LASTEXITCODE -ne 0) { Write-Host "产物生成失败" -ForegroundColor Red; exit 1 }

Write-Host "[2/2] golden 门禁打分..." -ForegroundColor Cyan
python -m requirements.eval.run_eval --golden requirements/eval/golden.json --product $out
exit $LASTEXITCODE
