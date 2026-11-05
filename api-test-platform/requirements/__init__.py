"""Phase 16 - 需求分析（PRD → 测试点，全链可追溯）。

模块结构（与 parser/generator/executor 同风格）：
  models.py     数据模型（模块树/业务规则/验收标准/发现项/测试点）
  parse.py      PRD 解析：markdown -> 模块树（纯规则，离线可跑）
  extract.py    业务规则/验收标准提取（LLM + schema 校验）
  analyze.py    歧义/冲突/遗漏检测（规则预检 + LLM 语义）
  testpoints.py 测试点生成（AC -> 测试点，带追溯）
  cli.py        命令行入口

追溯链（数据级，非 prompt 约定）：
  TP.ac_id -> AC.id -> AC.module_id -> ReqModule.id
  AC.rule_ids -> BusinessRule.id
"""
