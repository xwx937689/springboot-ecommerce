"""Phase 16 - 需求分析数据模型。

设计原则（修正 WHartTest 的两个短板）：
  1. 数据级追溯：每个实体带全局唯一 ID，用外键式字段关联，
     而不是像 WHartTest 那样把 "F001→TP002" 写进 prompt/notes 字符串。
  2. 可衡量的硬指标：AcceptanceCriterion.testable、Finding.severity
     都是结构化字段，统计指标（AC 可测率、歧义密度）可直接从 JSON 算出。

ID 前缀约定（全局唯一，人工可读）：
  M001   需求模块（PRD 章节）
  BR-001 业务规则
  AC-001 验收标准
  F-001  歧义/冲突/遗漏发现项
  TP-001 测试点
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


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
    content: str = ""                  # 该标题下、下一级标题之前的正文
    children: list["ReqModule"] = Field(default_factory=list)


class BusinessRule(BaseModel):
    """业务规则：PRD 里"系统必须满足"的约束。extract.py 产出。"""
    id: str                            # BR-001
    module_id: str                     # 追溯：出自哪个模块
    text: str                          # 规则原文（保留原句，便于人工核对）
    category: str = "general"          # constraint|calculation|workflow|permission


class AcceptanceCriterion(BaseModel):
    """验收标准（AC）：可直接判定通过/失败的可验证陈述。extract.py 产出。"""
    id: str                            # AC-001
    module_id: str                     # 追溯：出自哪个模块
    rule_ids: list[str] = Field(default_factory=list)  # 追溯：依据哪些业务规则
    text: str
    testable: bool = True              # 衡量指标：不可测 AC 会被 analyze.py 标记


class AmbiguityFinding(BaseModel):
    """发现项：一条需求质量问题（歧义/冲突/遗漏/清晰度）。analyze.py 产出。"""
    id: str                            # F-001
    module_id: str                     # 定位：在哪个模块发现
    type: FindingType
    severity: Severity = Severity.MEDIUM
    evidence: str                      # 原文证据（可定位到句子）
    reason: str                        # 为什么是问题
    suggestion: str = ""               # 修改建议
    source: str = "rule"               # rule=规则引擎(离线可复现) | llm


class TestPoint(BaseModel):
    """测试点：从一条 AC 出发的一个验证视角。testpoints.py 产出。"""
    id: str                            # TP-001
    ac_id: str                         # 追溯：验证哪条验收标准
    module_id: str                     # 冗余存一份，统计时不用反查
    title: str
    kind: str = "positive"             # positive|boundary|negative|exception
    priority: str = "P1"               # P0|P1|P2
    notes: str = ""


class RequirementAnalysis(BaseModel):
    """Phase 16 唯一落盘产物 requirements.json（下游 generator 消费）。"""
    source_file: str                   # 输入的 PRD 路径
    modules: list[ReqModule] = Field(default_factory=list)
    rules: list[BusinessRule] = Field(default_factory=list)
    criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    findings: list[AmbiguityFinding] = Field(default_factory=list)
    test_points: list[TestPoint] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)   # 衡量指标在 cli.py 里汇总填入
