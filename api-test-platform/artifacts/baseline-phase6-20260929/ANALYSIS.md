# Phase 6 基线执行记录（2026-09-29）

## 数据
- 范围：payment-service（27 条用例 = 3 接口 × 规则基线 + AI 增强）
- 结果：通过 11 / 27，通过率 **40.7%**
- 文件：`results.json`（原始结果）、`report.html`（报告）、`testcases.json`（当期用例）

## 失败归因（人工分析，作为 Phase 9 AI Failure Analyzer 的标注样本）

| 模式 | 数量 | 例子 | 归因 |
|------|------|------|------|
| positive→400 | 4 | POST /api/payments 期望 2xx 实际 400 | 用例造的数据不合法（兜底 `{"name":"sample-name"}` 过不了 ChargeRequest 校验）——**平台缺陷，非系统缺陷** |
| positive→404 | 2 | GET /api/payments/1 期望 2xx | ID=1 不存在，正向查询缺"先创建后查询"的数据依赖——**用例缺前置（Phase 7 数据关联）** |
| auth→400 | 3 | 无 token 期望 401 实际 400 | 系统校验顺序：请求体校验先于鉴权；auth 用例必须带合法 body 才能测出 401——**预期口径错误** |
| 缺字段 message | 4 | json_path_contains=["message"] | AI 不了解统一响应体 ApiResponse{success,data,error}，猜了不存在字段——**AI 上下文不足（Phase 5 提示词）** |
| not_found/negative PASS | — | — | 兜底口径（期望 4xx）恰好命中，说明执行链路本身正常 |

## 结论
首次执行通过率低 ≠ 系统有 bug，而是**测试平台三层不足**：
1. 造数据未基于真实 schema（规则层）
2. AI 提示词缺响应模型/业务前提上下文（AI 层）
3. 正向用例缺数据依赖链（Phase 7 范畴）

## 优化项（对应本次改进）
- [x] 解析器导出 components.schemas，造数据基于真实字段
- [x] auth 用例改用合法 body（401 口径修正）
- [x] AI 提示词注入 ApiResponse 结构与"ID 需先创建"前提
- [ ] 数据依赖链（Phase 7）
