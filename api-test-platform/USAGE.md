# 使用手册（写给第一次接触这套平台的你）

> 一句话理解：这套平台会在你 **每次 push 代码后自动"考试"你的微服务**——
> 生成考题 → 考试 → AI 批卷 → 自动订正 → 复测 → 演练故障 → 测性能，
> 最后把成绩单贴在 GitHub Actions 的 run 页面上，人人可见。

---

## 0. 你需要准备什么（只做一次）

| 依赖 | 检查命令 | 没有怎么办 |
|---|---|---|
| Docker Desktop | `docker --version` | 官网安装，启动即可 |
| Python 3.12 | `python --version` | 官网安装 |
| DeepSeek API Key | 见下 | platform.deepseek.com 注册充值 |

**配置 Key（两处）：**
- 本地：设置 Windows 环境变量 `DEEPSEEK_API_KEY=<你的key>`
- GitHub：仓库 → Settings → Secrets and variables → Actions → New repository secret
  → Name 填 `DEEPSEEK_API_KEY`，值粘 key

---

## 1. 三种使用姿势

### 姿势 A：全自动（推荐，什么都不用背）

```
你只做一件事：git push
```

之后 CI 自动跑完整流程（约 13 分钟），去仓库 **Actions 页** 点开最新一次 run，
拉到 **Summary 区域**，从上往下读四张成绩单：

| 成绩单 | 回答的问题 |
|---|---|
| 回归摘要（通过率/按服务表） | 功能有没有坏？哪个服务最差？ |
| 失败归因分类 | 坏的原因是"期望写错"还是"真缺陷"？ |
| 韧性场景（Phase 14） | 依赖挂了系统会不会崩？并发下单会不会超卖？ |
| 性能基准（Phase 15） | 每个端点每秒扛多少请求？延迟多少？ |

**只需要记住一条规则：**
- 归因里看到 `REAL_DEFECT` → 这是真缺陷，要提单修复
- 看到 `EXPECTATION_ISSUE` → 平台已自动订正，不用管
- 看到 `AUTH_MODEL` → 鉴权角色语义问题，攒着一起修

### 姿势 B：本地跑（改了代码想立刻看结果，不等 CI）

```powershell
# 1. 起被测系统（第一次或服务挂了时）
docker compose up -d
# 等几分钟，全部服务健康后：

# 2. 装平台（只第一次）
cd api-test-platform
python -m venv .venv
.venv\Scripts\activate
pip install -e .

# 3. 跑完整环（四条命令按顺序）
python -m parser.cli --contracts ../openapi-contracts --out artifacts
python -m generator.cli
python -m executor.cli
python -m analyzer.cli

# 4. 修复回灌 + 复测
python -m analyzer.cli --repair-only
python -m executor.cli --cases artifacts/testcases-repaired.json

# 5. 韧性 + 性能（可选）
python -m chaos.cli
python -m perf.cli
```

看完报告用浏览器打开 `api-test-platform/artifacts/report.html`（每条用例的明细）。

### 姿势 C：发版（打 tag 触发发布流水线）

```powershell
git tag v0.1.2
git push origin v0.1.2
```

流水线自动：跑完整回归 → 门禁检查（通过率 ≥ 阈值）→ 过了才创建 GitHub Release 并附上回归报告。
**门禁拦住了 = 这次代码不许发**，这就是它的价值。

---

## 2. 常用命令速查

| 想做什么 | 命令 |
|---|---|
| 只跑某个服务 | `python -m executor.cli --service cart-service` |
| 只跑某个分类 | `python -m executor.cli --category auth` |
| 加大并发 | `python -m executor.cli --workers 16` |
| 只跑一个韧性场景 | `python -m chaos.cli --scenario payment-race` |
| 调负载测试压力 | `python -m perf.cli --requests 300 --workers 16` |
| 重新渲染汇总 | `python -m analyzer.summary` |

---

## 3. 出问题怎么办

| 症状 | 处理 |
|---|---|
| `git push` 报 Connection reset | 网络波动，重试即可 |
| CI 里 LLM 显示 mock | Secret `DEEPSEEK_API_KEY` 没配对（名字要一字不差） |
| 发布被门禁拦 | 看 gate 日志里的实测通过率，评估后调仓库变量 `RELEASE_GATE_RATE` |
| 服务起了但接口全 503 | discovery-server 僵尸了，`docker restart springboot-ecommerce-discovery-server-1` |
| 通过率比昨天低很多 | 先看归因分类：REAL_DEFECT 涨了=真坏了；只是 DATA_STATE/EXPECTATION 变化=数据漂移，跑一轮 repair 重锚定即可 |

---

## 4. 这套平台的目录地图

```
api-test-platform/
├── parser/     Phase 4  OpenAPI → 统一接口模型
├── generator/  Phase 5  AI/规则 生成测试用例
├── executor/   Phase 6-7 执行器 + 业务数据链供给
├── analyzer/   Phase 9-10 AI 失败归因 + 期望修复回灌
│               summary.py → Job Summary 汇总渲染
├── chaos/      Phase 14 故障注入 + 并发竞态场景
├── perf/       Phase 15 负载测试（RPS/延迟分位数）
└── artifacts/  所有产物（results/report/failure-report/
                chaos-report/perf-report + 各 HTML）
```
