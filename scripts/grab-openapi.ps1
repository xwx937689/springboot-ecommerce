# Phase 3 - 一键抓取全部业务服务的 OpenAPI JSON 契约
# 用法（在 d:\springboot-ecommerce 下）:
#   .\scripts\grab-openapi.ps1
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$out = Join-Path $root "openapi-contracts"
New-Item -ItemType Directory -Force -Path $out | Out-Null

$services = @(
  "user-service:8081",
  "product-service:8082",
  "cart-service:8083",
  "inventory-service:8084",
  "payment-service:8085",
  "order-service:8086",
  "notification-service:8087",
  "recommendation-service:8088",
  "catalog-stream-service:8089",
  "seller-service:8090"
)

foreach ($s in $services) {
  $name, $port = $s.Split(":")
  $url = "http://localhost:$port/v3/api-docs"
  $dest = Join-Path $out "$name.json"
  Write-Host "[fetch] $url -> $dest"
  try {
    # curl.exe -o 直接写文件（原始字节，无 BOM）；不用 Out-File，它会在 Windows PowerShell 5.1 下写入 UTF-8 BOM
    curl.exe -s -m 30 -o $dest $url
    if ($LASTEXITCODE -ne 0) { Write-Warning ("curl exit " + $LASTEXITCODE + " : " + $name) }
  } catch {
    Write-Warning "  失败: $name ($_)"
  }
}

Write-Host "`n--- 结果 ---"
Get-ChildItem $out | Select-Object Name, @{N="Size(KB)";E={[math]::Round($_.Length/1KB,1)}}
