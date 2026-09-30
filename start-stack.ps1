# 一键拉起整个电商后端栈（开机自启用，已规避 discovery 竞态）
# 用法：右键"使用 PowerShell 运行"，或加到 Windows 任务计划程序（登录时触发）
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 等待某个服务进入 healthy 状态
function Wait-Healthy($name, $timeoutSec = 180) {
  $elapsed = 0
  while ($elapsed -lt $timeoutSec) {
    $st = docker inspect -f '{{.State.Health.Status}}' "springboot-ecommerce-$name-1" 2>$null
    if ($st -eq 'healthy') { return $true }
    Start-Sleep -Seconds 5
    $elapsed += 5
  }
  return $false
}

Write-Host '==> 1. 拉起配置中心 + 基础设施(postgres/redis/rabbitmq/kafka)' -ForegroundColor Cyan
docker compose up -d config-server postgres redis rabbitmq kafka

Write-Host '==> 2. 等 config-server 健康(否则 discovery 会读到错误默认值)' -ForegroundColor Cyan
if (-not (Wait-Healthy config-server)) {
  Write-Host 'config-server 未就绪，请检查 Docker Desktop 内存是否够(建议>=4GB)' -ForegroundColor Yellow
  exit 1
}

Write-Host '==> 3. 拉起 discovery-server(config 已就绪，避免竞态)' -ForegroundColor Cyan
docker compose up -d discovery-server
if (-not (Wait-Healthy discovery-server)) {
  Write-Host 'discovery 仍不健康，强制重建重试一次...' -ForegroundColor Yellow
  docker compose up -d --force-recreate discovery-server
  Wait-Healthy discovery-server
}

Write-Host '==> 4. 拉起其余全部业务服务' -ForegroundColor Cyan
docker compose up -d

# ---------------------------------------------------------------------------
# 5. 看门狗：守护进程开机自启不认 depends_on，个别服务可能在 config-server
#    就绪前抢跑，导致配置回退（Eureka 指向 localhost）而永远起不来。
#    探测每个业务服务健康端点，不通的强制重启一次（此时基础设施已就绪，
#    重启即可拿到正确配置）。
# ---------------------------------------------------------------------------
Write-Host '==> 5. 看门狗：检查业务服务健康，卡死的重启' -ForegroundColor Cyan
# discovery 是 Feign 服务发现的根，挂了会导致 order 等服务间调用 503
$dst = docker inspect -f '{{.State.Health.Status}}' springboot-ecommerce-discovery-server-1 2>$null
if ($dst -ne 'healthy') {
  Write-Host "  discovery-server 不健康($dst)，强制重建..." -ForegroundColor Yellow
  docker compose up -d --force-recreate discovery-server | Out-Null
  Start-Sleep -Seconds 45
}
$biz = @{
  'user-service' = 8081; 'product-service' = 8082; 'cart-service' = 8083
  'inventory-service' = 8084; 'payment-service' = 8085; 'order-service' = 8086
  'notification-service' = 8087; 'recommendation-service' = 8088; 'seller-service' = 8090
}
foreach ($name in $biz.Keys) {
  $port = $biz[$name]
  $code = 0
  try {
    curl.exe -s -o NUL -w '%{http_code}' "http://localhost:$port/actuator/health" --max-time 5 | ForEach-Object { $code = $_ }
  } catch {}
  if ("$code" -ne '200') {
    Write-Host "  $name 不健康($code)，重启..." -ForegroundColor Yellow
    docker compose restart $name | Out-Null
  } else {
    Write-Host "  $name OK" -ForegroundColor DarkGray
  }
}

Write-Host '==> 全部拉起完成。Eureka: http://localhost:8761' -ForegroundColor Green
