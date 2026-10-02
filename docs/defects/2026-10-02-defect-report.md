# 缺陷报告 — AI 测试平台自动发现（2026-10-02）

> 来源：api-test-platform 完整迭代环（生成→执行→AI 归因→修复→回归→混沌→性能），
> 本地（Windows/Docker）与 CI（GitHub Actions ubuntu-latest，run #7/#8）双环境复现。
>
> 每条缺陷按证据链五要素组织：
> **① 现象 → ② 原因（代码级）→ ③ Agent 判定依据（为何不是误判）→ ④ 验证与复现 → ⑤ 修复建议与验收标准**
>
> 误判排除的总原则：`EXPECTATION_ISSUE` 是"用例期望写错、系统行为合理"；
> `DATA_STATE` 是"数据不存在/漂移，换个数据就好了"；
> `REAL_DEFECT` 必须满足：**响应违反接口契约（状态码/结构）+ 跨环境稳定复现 + 归因后修复回灌明确豁免**。

---

## DEF-01 cart-add 跨服务状态不一致：有库存的商品无法加购 🔴 高

**阻塞面**：provisioning 数据链断裂 → 下单/支付链全部级联失败；chaos S2/S3 无法执行。

### ① 现象
- `POST /api/cart/items {"productId":1,"quantity":25}` → **400** `{"code":"VALIDATION_FAILED","message":"Product 1 is not available"}`
- 同一时刻 `GET /api/inventory/1` → 200，`availableQty = 75`（库存充足）
- 复现环境：**本地 + CI（run #7/#8，全新空库）均 100% 复现**，CI 三轮连续
- 涉及用例：`cart-service__addItem__ai-01`、`cart-service__addItem__ai-06`（404 变体见 DEF-07），以及 provisioning 链 `cart-add` 步骤
- 对比证据：昨日同库同接口加购成功（`chain: cart-add: OK`），今日全环境失败——状态随时间变化

### ② 原因（代码级，已定位到行）
`cart-service/.../CartService.java:33-36`：
```java
ProductSnapshot snap = fetchProduct(productId);   // 调 product-service
if (!snap.enabled()) {                            // ← 拒绝点在这里
  throw new ValidationException("Product " + productId + " is not available");
}
```
- **cart 校验的是 product-service 的 `enabled` 标志，与 inventory 库存无关**——inventory 有 75 库存也照样被拒
- 种子数据 `V1__init_products.sql:18` 定义 `enabled BOOLEAN DEFAULT TRUE`，`V2__seed_data.sql` 未显式设置
- **未闭环点**：CI 全新库首次 provisioning 即拿到 `enabled=false`，与迁移默认值矛盾。怀疑方向（按优先级）：
  1. `ProductSnapshot` 反序列化字段缺失 → `enabled` 缺省为 `false`（Jackson 宽松绑定）
  2. catalog-stream 事件时序竞态，cart 读到旧快照
  3. AI 用例在早前轮次 PUT 改掉 `enabled`（仅能解释本地，不能解释 CI 首轮）
- **验收前第一步**：在 CI 里加探针 `GET /api/products/1` 看返回的 `enabled` 值，即可区分怀疑方向 1/2

### ③ Agent 判定依据（为何是 REAL_DEFECT）
- 归因判词："正向添加商品返回 404，接口路径或路由可能不存在，违反接口契约"
- 排除 EXPECTATION：响应是系统主动抛出的 `VALIDATION_FAILED`，不是"期望 200 实际 404"的口径偏差
- 排除 DATA_STATE：inventory 明确有库存（75），不是"数据不存在"；且换环境（全新库）复现
- 加权证据：chaos 场景把 inventory 可用库存与 cart 拒绝行为同屏对照，人工复核代码后确认校验点错位

### ④ 验证与复现
```powershell
# 本地（库存充足时）
curl http://localhost:8084/api/inventory/1          # 200, availableQty=75
curl -X POST http://localhost:8083/api/cart/items `
  -H "Content-Type: application/json" `
  -H "X-User-Id: 8100" -H "X-User-Role: USER" `
  -d "{\"productId\":1,\"quantity\":25}"             # 400 "not available"
```
CI：run #7/#8 的 Job Summary → 数据链供给笔记 → `cart-add: HTTP 400 ... not available`

### ⑤ 修复建议与验收标准
1. 在 CI 的 digest 里加 `GET /api/products/1` 探针（区分怀疑方向）
2. 若是反序列化问题：`ProductSnapshot` 补字段/改名对齐
3. 若是业务规则：cart 应在 `enabled=false` 时给出与库存相关的明确错误码，且 inventory 与 product 的可购状态应有单一事实源
- **验收**：修复后重跑迭代环 → `cart-service__addItem__ai-01` 变 PASS、provisioning 链 `cart-add: OK`、chaos S2/S3 可执行

---

## DEF-02 inventory upsert：裸 Map 强转 + 零校验，全场景 500 🔴 高（7 条归因）

`POST /api/inventory/items` — 涉及 `inventory-service__upsert__positive-01/ai-01/02/03/04/06`

### ① 现象
| 请求 | 期望 | 实际 |
|---|---|---|
| 合法创建（ai-01） | 2xx + ApiResponse | **500** 裸异常 |
| 缺 productId（ai-02） | 400 校验失败 | **500** |
| availableQty 负数（ai-03） | 400 | **500** |
| availableQty 零（ai-04） | 200/400 | **500** |
| 重复提交（ai-06） | 200 幂等/409 | **500** |
| 正向（positive-01） | 2xx + ApiResponse | **400** + Spring 默认错误体 |

### ② 原因（代码级）
`InventoryController.java:31-38`：
```java
@PostMapping("/items")
public ResponseEntity<...> upsert(@RequestBody java.util.Map<String, Object> req) {
    Long productId = ((Number) req.get("productId")).longValue();  // ← 裸强转
```
- `productId` 缺失 → `null.longValue()` → **NullPointerException → 500**
- `productId` 为字符串 → **ClassCastException → 500**（positive-01 的 400 即绑定阶段异常，未包 ApiResponse）
- 请求体不用 DTO、无 `@Valid`，负数/零直接透传 `service.upsert()` 落库
- 对照：同文件 `reserve()` 用了 `@Valid @RequestBody ReserveRequest`（第 41-45 行）——**同服务内两套标准**

### ③ Agent 判定依据
- 归因判词（conf 0.7-0.95）："返回 500 且响应体为 Spring 默认错误结构，未按 ApiResponse 契约包装"
- 排除 EXPECTATION：500 是服务端未处理异常，任何客户端期望都"不该"接受 500
- 排除 DATA_STATE：与库存数据无关——六种请求形态全部异常，纯输入映射问题
- 佐证：AI 归因置信度最高的一组（0.95×3），与代码审阅结论完全一致

### ④ 验证与复现
```powershell
curl -X POST http://localhost:8084/api/inventory/items `
  -H "Content-Type: application/json" -d "{}"                        # 500 NPE
curl -X POST http://localhost:8084/api/inventory/items `
  -H "Content-Type: application/json" `
  -d "{\"productId\":\"abc\",\"availableQty\":5}"                    # 500 CCE
```
本地 + CI run #7/#8 稳定复现。

### ⑤ 修复建议与验收标准
- 定义 `UpsertRequest(productId @NotNull @Positive, availableQty @Min(0))`，`@Valid` 校验
- 补全局异常处理器把绑定异常映射为 `400 + ApiResponse`
- **验收**：上述 6 个用例全部 PASS；合法创建 201+ApiResponse；负数/缺失 → 400

---

## DEF-03 recommendation similar：不存在商品 500（未映射 404）🟡 中（2 条归因）

`GET /api/recommendations/products/{id}/similar` — `recommendation-service__similar__notfound-01/ai-02`

### ① 现象
- 查询不存在商品的相似推荐 → **500**（裸异常），契约要求 **404 + ApiResponse**

### ② 原因（代码级）
`RecommendationController.java:27-32` 直接 `service.similarProducts(productId, k)`；
service 内部对不存在商品抛出的异常未被映射为 404，异常穿透到容器 → 500。
（对照：inventory 同类场景用 `ResourceNotFoundException` 已正确映射 404 —— **shared/error 机制存在，此处漏用**）

### ③ Agent 判定依据
- conf 0.95："查询不存在商品时服务端抛出未处理异常返回 500，违反 404 契约"
- 排除 EXPECTATION：404 是 REST 语义对"资源不存在"的标准答案，不是 AI 猜的口径
- 排除 DATA_STATE：用例目的就是测"不存在"分支，数据状态本身就是被测输入

### ④ 验证与复现
```powershell
curl http://localhost:8088/api/recommendations/products/99999/similar   # 500，应 404
```
本地 + CI 双复现。

### ⑤ 修复建议与验收标准
- `similarProducts` 对不存在商品抛 `ResourceNotFoundException`（shared/error 已有映射）
- **验收**：`similar__notfound-01/ai-02` 变 PASS
- 顺带排查姊妹端点：`GET /api/recommendations/users/{id}` 曾观察到基线 500（同一缺陷家族）

---

## DEF-04 payment charge：合法请求 400 + 双错误体并存 🟡 中（2 条归因）

`POST /api/payments` — `payment-service__charge__positive-01/ai-01`

### ① 现象
- 合法扣款请求（与 provisioning 成功链路同构的 body）→ **400**，响应体为 `code/message/status/path/traceId` 结构
- 同平台其他服务错误体为 `ApiResponse{success,data,error,timestamp}` —— **全系统存在两种错误包装**

### ② 原因（代码级，部分待定位）
- `PaymentController.charge`（24-28 行）有 `@Valid @RequestBody ChargeRequest`——400 来自校验层
- **待定位**：ChargeRequest 具体哪个字段对 AI/provisioning 的 body 校验失败（需打印 400 响应 message 对照 DTO 注解）；以及该服务为何走了非 ApiResponse 错误处理器
- 已确认：响应结构非统一契约（与 DEF-02 的"默认错误体"同族）

### ③ Agent 判定依据
- conf 0.7："合法参数发起支付返回 400，未按契约返回 200 与 success 包装"
- 排除 DATA_STATE：orderId 来自 provisioning 真实数据链（本 run 刚创建），非悬空 ID
- 保守性说明：此条置信度 0.7，若修复时发现是 AI 造数缺字段，可降级改判 EXPECTATION——**但双错误体问题独立成立**

### ④ 验证与复现
provisioning 同构 body（见 `executor/provision.py:149-156`）直接 POST；本地+CI 复现。

### ⑤ 修复建议与验收标准
- 打印 400 响应 message → 对齐 ChargeRequest 校验注解与 OpenAPI schema
- 错误处理器统一为 ApiResponse
- **验收**：`charge__positive-01/ai-01` PASS；全服务错误体结构一致

---

## DEF-05 order place：合法下单体 400 🟡 中（1 条归因，CI 新发现）

`POST /api/orders` — `order-service__place__ai-01`（run #7/#8 新晋）

### ① 现象
- AI 按 OpenAPI schema 构造的合法下单体 → **400**

### ② 原因（待定位）
- `OrderController.place` 有 `@Valid`，DTO 必填集与 OpenAPI schema 不一致的可能性最大（schema 生成自 springdoc，理论应同源——若不一致则说明 DTO 注解与 schema 脱节）
- **与 DEF-01 有耦合**：place 依赖 cart 有货，CI 里 cart-add 即失败，place 的 400 可能是空购物车业务错误被泛化

### ③ Agent 判定依据
- conf 0.75："合法下单正例实际返回 400 参数校验失败，疑似请求体校验或数据问题"——归因器自己标注了不确定性
- **诚实说明**：此条处于 REAL_DEFECT/EXPECTATION 边界，修复验证时若确认是 AI 造数字段缺失，应改判

### ④ 验证与复现
CI run #7/#8；本地因 DEF-01 阻塞下单链暂无法独立复现——**修复 DEF-01 后自动解锁本条验证**

### ⑤ 修复建议与验收标准
- 修 DEF-01 后重跑，取 place 的 400 响应 message 定位
- **验收**：合法下单体（含 card）→ 2xx；`place__ai-01` PASS

---

## DEF-06 inventory reserve/status：非数字 productId 映射 404 而非 400 🟢 低（2 条归因）

`GET /api/inventory/{productId}`（status ai-02）、`POST /api/inventory/reservations`（reserve ai-06）

### ① 现象
- 非数字 productId → 期望 400 类型校验，实际 **404 + 默认错误体**

### ② 原因（代码级）
`InventoryController.java:66`：`@GetMapping("/{productId:\\d+}")` —— **路径正则只匹配数字**，非数字根本不进入 handler，Spring 按"路由不存在"返回 404。这是"路径正则收窄"与"类型校验语义"的设计冲突。

### ③ Agent 判定依据
- conf 0.7："错误码映射不符合契约"
- **可辩论点**：REST 惯例上"路径不匹配→404"也说得通，404 vs 400 属于可接受的语义分歧 → 建议降级为"契约澄清"而非缺陷，修改用例期望或统一路由风格

### ④ 验证与复现
```powershell
curl http://localhost:8084/api/inventory/abc    # 404
```

### ⑤ 处置建议
二选一并全局统一：A) 去掉正则收窄，让类型错误走 400；B) 保持现状，修用例期望为 404。**这是归因器"低置信度改判"的好案例**。

---

## DEF-07 cart addItem 带 listingId 时 404（CQRS 读模型断链下游）🟡 中（2 条归因，历史缺陷确认）

`cart-service__addItem__ai-01/ai-06`（404 变体）

### ① 现象
- AI 造的加购体含 `listingId` → **404**

### ② 原因（代码级）
`CartService.java:40-43`：`fetchListing(listingId)` → `SellerListingClient.getById` → **seller 读模型 `GET /api/listings/{id}` 404**（Phase 8 缺陷 #2：`/api/listings/me` 有数据但 `GET /api/listings/1` 404，catalog-stream 事件同步断链）。cart 的 404 只是下游断链的传导。

### ③ Agent 判定依据
- conf 0.55-0.6（低置信）："接口路径或路由可能不存在"——归因器猜错了方向（不是路由，是下游读模型）
- **价值**：低置信度 + 人工深挖修正方向，恰好证明"AI 归因 + 人工复核"流程必要性

### ④ 验证与复现
```powershell
curl http://localhost:8090/api/listings/1        # 404（读模型断链）
curl http://localhost:8090/api/listings/me       # 200（写模型有数据）
```

### ⑤ 修复建议与验收标准
- 修 catalog-stream 事件同步（Phase 8 缺陷 #2 的正主）
- **验收**：`GET /api/listings/{id}` 与 `/me` 数据一致；addItem 带 listingId 用例 PASS

---

## OPS-01 discovery-server 僵尸运行（watchdog 盲区）🟡 运维

### 现象
容器 `Up 2 hours (unhealthy)` 但应用无响应（actuator 000），cart 心跳全部 Connection refused，Feign 全断（order 503、recommendation 全灭）。
### 原因
`start-stack.ps1` 看门狗只看**容器状态/进程存活**，不查**应用健康**；CI 的 bash 版看门狗查了 actuator（CI 未复现此问题）。
### 修复建议
看门狗改用 `GET /actuator/health` 判定；连续 N 次失败 → `docker restart`。
### 验收
手动 `docker pause` 模拟僵尸 → 看门狗 5 分钟内自动恢复。

---

## OPS-02 cart 重启后直连信任头 401（状态漂移）🟢 观察项

### 现象
cart 容器重启后，带 `X-User-Id + X-User-Role` 直连 `POST /api/cart/items` 返回 401（重启前同请求可用）；执行器同款头亦被拒。
### 已排除
不是缺 `X-User-Role`（补齐后仍 401）；不是 userId 不存在（userId=8 真实存在）。
### 怀疑方向
重启后从 config-server 拉到的安全配置与首次启动不同（配置回退），或信任头过滤器对来源有额外校验。
### 处置
先复现固定（restart → 立即 curl → 记录），再对照 config-server 的 cart 配置历史。**这是"容器重启改变安全行为"的状态漂移案例，值得单独讲。**

---

## 统计与优先级

| 优先级 | 缺陷 | 阻塞面 |
|---|---|---|
| 🔴 P0 | DEF-01（cart-add 状态不一致） | 数据链 + chaos S2/S3 + 全部下单链用例 |
| 🔴 P0 | DEF-02（inventory upsert 500×6） | inventory 全写场景 |
| 🟡 P1 | DEF-03 / DEF-04 / DEF-05 / DEF-07 | 单端点 |
| 🟢 P2 | DEF-06（契约澄清）、OPS-02 | 低频分支 |
| 🟡 P1 | OPS-01（watchdog 盲区） | 环境稳定性 |

**证据链通用复现入口**：`cd api-test-platform && python -m executor.cli && python -m analyzer.cli`
（归因报告自动产出上述全部 case_id 的判词；CI 每次 run 的 Job Summary 公开可查）
