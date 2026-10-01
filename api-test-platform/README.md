# API Test Platform (AI-powered)

企业级「AI 接口自动化测试平台」作品，按 Phase 0~15 路线图实现。
被测系统：本仓库的 13 微服务电商后端（通过 `localhost:8080` 网关访问）。

## 路线图进度

| Phase | 内容 | 状态 |
|---|---|---|
| 0 | 环境准备 | ✅ |
| 1 | 启动真实电商系统 | ✅ (Eureka 13 服务全 UP) |
| 2 | 理解企业架构 | ✅ |
| 3 | 找到 OpenAPI / Swagger | ✅ (override 暴露端口 + 抓取 JSON) |
| 4 | OpenAPI 接入 Parser | ✅ (61 接口, 角色感知鉴权推断) |
| 5 | AI 生成测试用例 | ✅ (DeepSeek/mock 可插拔) |
| 6 | 真实接口执行 | ✅ (httpx 并发执行器) |
| 7 | Token/数据关联/前置依赖 | ✅ (Saga 数据链供给 + ID 替换) |
| 8 | 全量接口自动化 | ✅ (459 条, 修复版 77.1%) |
| 9 | AI Failure Analyzer | ✅ (真实缺陷/数据态/期望口径三类归因) |
| 10 | AI Repair + Regression | ✅ (每轮重锚定, expected_original 审计留痕) |
| 11 | Git + CI | ✅ |
| 12 | CI/CD 自动部署 | ✅ (api-tests.yml 全环入 CI) |
| 13 | 版本发布→全量回归 | 🚧 当前 (tag→回归→门禁→GitHub Release) |
| 14 | 故障注入+复杂场景 | ⬜ |
| 15 | 性能/稳定性/可观测性 | ⬜ |

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env                                 # 填入 LLM_API_KEY
python -m parser.cli --contracts ../openapi-contracts --out artifacts/endpoints.json
```

## 目录约定

- `config/`    环境、base_url、超时、认证配置
- `parser/`    OpenAPI -> 统一接口模型 (Phase 4)
- `generator/` LLM 生成用例 (Phase 5, 可插拔)
- `executor/`  httpx 执行 + token 注入 (Phase 6/7)
- `analyzer/`  失败分析 + 自愈 (Phase 9/10)
- `reporter/`  报告 (HTML / Allure)
- `ci/`        GitHub Actions (Phase 11)
