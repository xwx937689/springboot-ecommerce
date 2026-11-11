"""Phase 16 - 需求分析数据模型。

设计原则（修正 WHartTest 的两个短板）：
  1. 数据级追溯：每个实体带全局唯一 ID，用外键式字段关联，
     而不是像 WHartTest 那样把 "F001→TP002" 写进 prompt/notes 字符串。
  2. 可衡量的硬指标：AcceptanceCriterion.testability、Finding.severity
     都是结构化字段，统计指标（AC 可测率、歧义密度）可直接从 JSON 算出。

约束策略：
  - 取值封闭的字段用 Literal（category/kind/priority/source/testability），
    非法值在构造时就报错，而不是等到报表阶段才发现 "P5"；
  - 跨实体关联在 RequirementAnalysis 上做整体校验（model_validator），
    单条数据局部合法不代表链路不断——ac_id 指向不存在的 AC 必须在这里拦下；
  - stats 用固定结构的 AnalysisStats（报表依赖的稳定字段）+ stages（各阶段
    原始统计的自由字典），兼顾稳定与灵活。

ID 前缀约定（全局唯一，人工可读）：
  M001   需求模块（PRD 章节）
  BR-001 业务规则
  AC-001 验收标准
  F-001  歧义/冲突/遗漏发现项
  TP-001 测试点
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# 封闭取值集中定义，避免散在各模型里对不齐
RuleCategory = Literal[
    "constraint", "calculation", "workflow", "permission", "general"
]
TestPointKind = Literal["positive", "boundary", "negative", "exception"]
Priority = Literal["P0", "P1", "P2"]
FindingSource = Literal["rule", "llm"]
# 三态可测性：部分可测是常态（句子里有可验证部分也有模糊部分），二值不够用
Testability = Literal["testable", "partial", "untestable"]
# 人工复核状态：rule 引擎原文直取=approved；LLM 产出=pending（规范化可能扩大原意，
# 程序只能验证"原文存在"，验证不了"语义没被放大"，所以 LLM 条目必须过人）
ReviewStatus = Literal["pending", "approved", "rejected"]


class FindingType(str, Enum):
    """发现项类型：需求质量问题只分 4 种。"""
    AMBIGUITY = "ambiguity"   # 歧义：模糊量词/一词多义（"尽快""大约 1 秒"）
    CONFLICT = "conflict"     # 冲突：文档内两处说法矛盾（上限 50 vs 99）
    OMISSION = "omission"     # 遗漏：该有而没有（异常流、边界值、非功能指标）
    CLARITY = "clarity"       # 清晰度：术语未定义、指代不明


class Severity(str, Enum):
    HIGH = "high"        # 不澄清必然返工（如需求冲突）
    MEDIUM = "medium"    # 影响用例质量（如模糊量词）
    LOW = "low"          # 建议改进（如措辞）


class ReqModule(BaseModel):
    """PRD 的一个章节 = 模块树的节点。parse.py 产出。"""
    id: str                            # M001, M002 ...（按文档出现顺序）
    title: str                         # 标题文本
    level: int                         # 标题级别：# 为 1，## 为 2 ...（前言为 0）
    content: str = ""                  # 该标题下、下一级标题之前的正文（不含代码块）
    children: list["ReqModule"] = Field(default_factory=list)
    # 该模块下出现的代码围栏段数：代码示例不是需求正文，不参与提取，
    # 但要留个计数，避免"文档里有内容却消失了"的困惑
    code_blocks: int = 0
    # 源文档行号范围（1 起，含标题行与正文；line_end=0 表示未知/未关断）。
    # 人工审核时从发现项/测试点跳回 PRD 原文全靠它
    line_start: int = 0
    line_end: int = 0


class BusinessRule(BaseModel):
    """业务规则：PRD 里"系统必须满足"的约束。extract.py 产出。"""
    id: str                            # BR-001
    module_id: str = Field(min_length=1)   # 追溯：出自哪个模块（必填，禁止空串绕过校验）
    text: str                          # 规则原文（保留原句，便于人工核对）
    category: RuleCategory = "general"
    evidence: str = ""                 # text 所依据的正文片段（幻觉防线：必须能在原文找到）
    review_status: ReviewStatus = "pending"   # 默认 pending：没走校验路径的条目待人工复核


class AcceptanceCriterion(BaseModel):
    """验收标准（AC）：可直接判定通过/失败的可验证陈述。extract.py 产出。"""
    id: str                            # AC-001
    module_id: str = Field(min_length=1)   # 追溯：出自哪个模块（必填）
    rule_ids: list[str] = Field(default_factory=list)  # 追溯：依据哪些业务规则
    text: str
    evidence: str = ""                 # 同 BusinessRule.evidence
    review_status: ReviewStatus = "pending"
    # 兼容字段（报表已依赖），由 testability 派生，唯一事实来源是 testability；
    # 不一致会在模型校验阶段直接报错，避免报表统计冲突
    testable: bool = True
    # 三态可测性 + 原因 + 待澄清问题：比二值更接近人工审核的真实场景
    #   testable   可直接判定通过/失败
    #   partial    有可验证部分，但含模糊表述（"尽快处理订单"——处理可测，尽快不可测）
    #   untestable 整句无法判定（"锁定时间待定"——值都没定）
    testability: Testability = "testable"
    testability_reason: str = ""                       # 为什么不可直接验证
    clarification_questions: list[str] = Field(default_factory=list)  # 要问产品的问题

    @model_validator(mode="after")
    def _sync_testable(self) -> "AcceptanceCriterion":
        # 映射规则：testable→True，partial/untestable→False；不一致 = 数据错误
        if self.testable != (self.testability == "testable"):
            raise ValueError(
                f"{self.id}: testable={self.testable} 与 testability='{self.testability}' 矛盾"
                "（testability 是唯一事实来源）"
            )
        return self


class AmbiguityFinding(BaseModel):
    """发现项：一条需求质量问题（歧义/冲突/遗漏/清晰度）。analyze.py 产出。"""
    id: str                            # F-001
    module_id: str                     # 定位：在哪个模块发现
    type: FindingType
    severity: Severity = Severity.MEDIUM
    evidence: str                      # 原文证据（可定位到句子）
    reason: str                        # 为什么是问题
    suggestion: str = ""               # 修改建议
    # 人可读的原文定位（如 "第 12-18 行"），由 analyze 按 module 的行号范围填；
    # 200 页 PRD 人工审核时靠它从问题跳回原文
    source_location: str = ""
    source: FindingSource = "rule"     # rule=规则引擎(离线可复现) | llm


class TestPoint(BaseModel):
    """测试点：从一条 AC 出发的一个验证视角。testpoints.py 产出。"""
    id: str                            # TP-001
    ac_id: str                         # 追溯：验证哪条验收标准（BR 直接补齐的为空串）
    # 追溯：依据哪些业务规则。三种情形——
    #   经 AC 生成：ac_id 有值 + rule_ids 取自该 AC 的 rule_ids；
    #   BR 直接生成：ac_id 为空 + rule_ids 指向来源 BR（补上追溯断点）；
    #   两者都有：正常情形（AC 本身就有 rule_ids）
    rule_ids: list[str] = Field(default_factory=list)
    module_id: str = Field(min_length=1)   # 冗余存一份，统计时不用反查（必填）
    title: str
    kind: TestPointKind = "positive"
    priority: Priority = "P1"
    notes: str = ""
    preconditions: str = "" #前置条件
    steps: list[str] = Field(default_factory=list) # 测试步骤
    expected_results: str = ""


class AnalysisMetrics(BaseModel):
    """4 个硬指标（cli.py 计算），报表与 CI 门禁依赖的稳定结构。

    统计口径（数值范围约束的前提）：
      ac_testable_rate  = testability=="testable" 的 AC 数 / AC 总数，[0,1]
      ambiguity_density = 规则引擎报出的歧义句数 / 句子总数，[0,1]
                          （规则层一句最多报一条，天然 ≤1；LLM 发现不计入，
                           避免同一句多条 LLM 发现把密度顶破 1）
      br_coverage       = 被 TP 覆盖的 BR 数 / BR 总数，[0,1]
      tp_per_ac         = 测试点总数 / 可测 AC 数，≥0（无上限，密度高是好事）
    """
    ac_testable_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    ambiguity_density: float = Field(default=0.0, ge=0.0, le=1.0)
    br_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    tp_per_ac: float = Field(default=0.0, ge=0.0)


class AnalysisStats(BaseModel):
    """整体统计：固定字段给报表，stages 保留各阶段原始统计。"""
    modules: int = 0
    rules: int = 0
    criteria: int = 0
    testable_criteria: int = 0
    partial_criteria: int = 0
    untestable_criteria: int = 0
    test_points: int = 0
    findings: int = 0
    high_findings: int = 0
    metrics: AnalysisMetrics = Field(default_factory=AnalysisMetrics)
    # 各阶段原始统计（extract/analyze/testpoints 的自由 dict）：
    # 字段随实现演进，报表不要依赖这里，依赖上面的固定字段
    stages: dict = Field(default_factory=dict)


class RequirementAnalysis(BaseModel):
    """Phase 16 唯一落盘产物 requirements.json（下游 generator 消费）。

    schema_version：消费方（generator/报表）按版本做兼容迁移，
    字段破坏性变更时递增，避免旧 JSON 交给新代码时静默出错。
    source_* 四件套：确认"这份分析对应哪一版 PRD"——需求改了但
    sha256 没变才是同一版文档，防止拿旧分析结果生成新需求的用例。
    """
    schema_version: str = "1.0"
    source_file: str                   # 输入的 PRD 路径（绝对，兼容保留）
    source_name: str = ""              # 文件名
    source_path: str = ""              # 相对路径（跨机器可读）
    source_sha256: str = ""            # 文档内容指纹，锁定分析对应的 PRD 版本
    analyzed_at: str = ""              # 分析时间（UTC ISO 格式）
    modules: list[ReqModule] = Field(default_factory=list)
    rules: list[BusinessRule] = Field(default_factory=list)
    criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    findings: list[AmbiguityFinding] = Field(default_factory=list)
    test_points: list[TestPoint] = Field(default_factory=list)
    stats: AnalysisStats = Field(default_factory=AnalysisStats)

    @model_validator(mode="after")
    def _check_references(self) -> "RequirementAnalysis":
        """整体校验：ID 唯一 + 格式 + 跨实体引用真实存在（局部合法 ≠ 链路完整）。"""
        module_list: list[str] = []

        def _collect(ms: list[ReqModule]) -> None:
            for m in ms:
                module_list.append(m.id)
                _collect(m.children)

        _collect(self.modules)
        self._check_unique("ReqModule", module_list, r"^M\d{3}$")
        self._check_unique("BusinessRule", [r.id for r in self.rules], r"^BR-\d{3}$")
        self._check_unique("AcceptanceCriterion", [c.id for c in self.criteria], r"^AC-\d{3}$")
        self._check_unique("AmbiguityFinding", [f.id for f in self.findings], r"^F-\d{3}$")
        self._check_unique("TestPoint", [tp.id for tp in self.test_points], r"^TP-\d{3}$")

        module_ids = set(module_list)
        rule_ids = {r.id for r in self.rules}
        ac_ids = {c.id for c in self.criteria}

        for r in self.rules:
            if r.module_id not in module_ids:      # module_id 必填，空串也拦（min_length 双保险）
                raise ValueError(
                    f"BusinessRule {r.id} 的 module_id='{r.module_id}' 不存在于模块树"
                )
        for c in self.criteria:
            if c.module_id not in module_ids:
                raise ValueError(
                    f"AcceptanceCriterion {c.id} 的 module_id='{c.module_id}' 不存在于模块树"
                )
            for rid in c.rule_ids:
                if rid not in rule_ids:
                    raise ValueError(
                        f"AcceptanceCriterion {c.id} 的 rule_ids='{rid}' 找不到对应业务规则"
                    )
        for tp in self.test_points:
            if tp.module_id not in module_ids:
                raise ValueError(
                    f"TestPoint {tp.id} 的 module_id='{tp.module_id}' 不存在于模块树"
                )
            if tp.ac_id and tp.ac_id not in ac_ids:
                raise ValueError(
                    f"TestPoint {tp.id} 的 ac_id='{tp.ac_id}' 找不到对应验收标准"
                )
            for rid in tp.rule_ids:
                if rid not in rule_ids:
                    raise ValueError(
                        f"TestPoint {tp.id} 的 rule_ids='{rid}' 找不到对应业务规则"
                    )
        return self

    @staticmethod
    def _check_unique(label: str, ids: list[str], pattern: str) -> None:
        """每类实体各自查重（集合会吞掉重复，必须用长度比对）+ 前缀格式校验。"""
        if len(ids) != len(set(ids)):
            dup = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"{label} 存在重复 ID: {dup[:5]}")
        bad = [i for i in ids if not re.fullmatch(pattern, i)]
        if bad:
            raise ValueError(f"{label} 的 ID 格式非法（应为 {pattern}）: {bad[:5]}")
