"""Agent 层——轻量级 Agent harness（Phase 17 起点）。

对标业界 Agent 的取舍（"先轻量级"拍板）：
  - OpenAI 原生 function calling 循环，不引 LangGraph 等重框架；
  - 检索先用词面重叠（search_examples），embedding RAG 二期再上；
  - 每个技能模块（parser/generator/executor/analyzer/requirements）未来
    以工具 schema 注册进 ToolBox，本期只开"用例生成 Agent"一个闭环。

不编造纪律延续到 Agent 形态：steps 必须以 lookup_interfaces 查到的真实
契约为据，submit 校验不过会被打回重写，超轮次按 failed 收场——
宁可失败，不可编造。
"""
