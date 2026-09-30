# Phase 8 全量回归基线（2026-09-30）

## 数据
- 范围：全部 8 个 Web 服务，451 条用例（归一化后，auth 类公开端点用例已剔除）
- 结果：通过 259 / 451，通过率 **57.4%**
- 数据链 registry：`{userId: 8, sellerId: 1, listingId: 1, productId: 1, orderId: 1, paymentId: 2}`
- 对比 Phase 6 基线（payment 单服务 40.7%）→ 全平台 48.0% → 本基线 57.4%

## 本阶段打通的关键机制
1. **服务级安全豁免**（Parser）：payment/inventory/recommendation 无 spring-security，不再生成 401 用例
2. **auth 归一化**（执行器）：修复 AI 乱写 `auth=none`；挖出 `Endpoint.key` 为 @property 不进 model_dump 的隐蔽 bug
3. **角色感知注入**：`/api/coupons`、`/api/admin/**` → ADMIN；seller → SELLER
4. **数据链供给**：注册→卖家申请→ADMIN 激活→上架(409 幂等回查)→加购→下单→支付，全链 OK
5. **ID 替换**：positive/conflict/boundary 用例自动注入真实资源 ID
6. **看门狗**（start-stack.ps1）：discovery-server + 9 个业务服务开机健康巡检

## 过程中发现并验证的系统问题（缺陷清单素材）
| # | 问题 | 证据 | 严重度 |
|---|------|------|--------|
| 1 | springdoc 2.6.0 与 Spring Boot 3.4.1 不兼容，api-docs 全挂 | NoSuchMethodError ControllerAdviceBean | 高（已修 2.7.0） |
| 2 | CQRS 读模型不同步：`/api/listings/me` 有 id=1，`GET /api/listings/1` 404 | catalog-stream 事件同步断链 | 高 |
| 3 | cart 加购校验 listingId 时撞上问题 2 → 404 | 加购去掉可选 listingId 即成功 | 中（绕过） |
| 4 | discovery-server 不健康时 Feign 全断（order 503、recommendation 全灭） | force-recreate 后恢复 | 高（运维） |
| 5 | 基础设施无 restart 策略 + 开机竞态 | cart/recommendation 卡死 1 小时 | 中（已加看门狗） |
| 6 | inventory 6 个 500 | 待 Phase 9 归因 | 待定 |

## 剩余失败画像（~192 条，全部为业务语义层）
- 业务状态依赖：重复支付/重复注册 409、已支付订单再操作等（幂等与状态机类，属正常业务拒绝）
- 读模型不同步导致的 seller listing 查询 404（缺陷 #2 的下游）
- AI 期望口径偏差（201/204 vs 200、包装响应字段猜测）→ Phase 9 归因
- inventory/product 少量 500/503 底噪 → Phase 14 故障注入观察

## 下一阶段
- Phase 9：AI Failure Analyzer——把剩余失败分类为"预期口径 / 数据状态 / 真缺陷"
- Phase 11：Git 提交本阶段全部改动 + CI 流水线
