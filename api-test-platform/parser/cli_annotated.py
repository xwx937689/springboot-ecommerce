"""【学习用注释版】parser/cli.py —— 原文件 cli.py 未做任何改动。

这是给初学者看的"逐行讲白话"版本，代码逻辑与原文件完全一致，只是加了大量注释。
可以照常运行（命令里的 cli 换成 cli_annotated 即可）：
    python -m parser.cli_annotated --contracts ../openapi-contracts --out artifacts
"""

# ─────────────────────────────────────────────────────────────
# 第 6 行：这是一句"未来导入"。
# 作用：让下面写类型注解（比如 contracts_dir: Path）时更宽松、兼容老版本 Python。
# 小白理解：照抄就行，它不影响代码逻辑，只是让写法更自由。
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

# ─────────────────────────────────────────────────────────────
# 第 8-12 行：导入别人写好的工具（模块）。
# import xxx      = 把整个工具箱拿来，用的时候写 "工具箱.工具"，例如 json.dumps()
# from x import y = 只拿其中一个工具，用的时候直接写工具名，例如 Path()
# ─────────────────────────────────────────────────────────────
import argparse          # 第 8 行：专门用来解析命令行参数的工具（让 --contracts 这种命令能用）
import json              # 第 9 行：把 Python 数据转成 JSON 字符串（json.dumps），或从 JSON 转回来
import sys               # 第 10 行：Python 自带的系统工具，这里主要用它的 sys.stderr（错误输出通道）
from collections import Counter  # 第 11 行：一个"会自动计数"的特殊字典，后面统计鉴权类型用
from pathlib import Path         # 第 12 行：处理文件路径的现代写法（重点！本文件的核心）

# ─────────────────────────────────────────────────────────────
# 第 14-15 行：从"自己这个包"里导入。
# 注意那个点 "." —— 它表示"当前目录（parser 这个包）里面"。
# from .models import ServiceContract        → 拿数据模型（一个服务长什么样）
# from .openapi_parser import parse_dir      → 拿真正干活的解析函数
# 小白理解：这两行是把同目录下的其他文件里的东西拿过来用。
# ─────────────────────────────────────────────────────────────
from .models import ServiceContract
from .openapi_parser import parse_dir

# ─────────────────────────────────────────────────────────────
# 第 17-29 行：定义一个"字典"（键值对），名字叫 SERVICE_MAPPING，全大写表示它是常量（不会变）。
# 作用：告诉程序 "文件名 对应 哪个服务名"。
# 这里左边和右边恰好一样（因为 JSON 文件名就叫 user-service.json），写成字典是为了以后可以不一致时方便改。
# 字典格式：{ "键": 值, "键": 值 }，用键就能查到值，例如 SERVICE_MAPPING["user-service"] 得到 "user-service"
# ─────────────────────────────────────────────────────────────
SERVICE_MAPPING = {
    "user-service": "user-service",
    "product-service": "product-service",
    "cart-service": "cart-service",
    "inventory-service": "inventory-service",
    "payment-service": "payment-service",
    "order-service": "order-service",
    "notification-service": "notification-service",
    "recommendation-service": "recommendation-service",
    "catalog-stream-service": "catalog-stream-service",
    "seller-service": "seller-service",
}


# ═════════════════════════════════════════════════════════════
# 第 32 行：定义函数 run（整个文件的核心）
#
# def             = define（定义），告诉 Python "我要造一个函数"
# run             = 函数名，以后写 run(...) 就是执行这段代码
# contracts_dir   = 第 1 个参数：契约文件所在的目录
# out             = 第 2 个参数：结果要输出到哪个目录
# : Path          = 类型注解，说明这两个参数应该是 Path 类型（只是标注，Python 不强制）
# -> int          = 返回值注解，说明这个函数最后会返回一个整数
#
# 这个整数的含义是"退出码"：0 = 成功，1 和 2 = 不同类型的失败（给 CI 判断用的）
# ═════════════════════════════════════════════════════════════
def run(contracts_dir: Path, out: Path) -> int:

    # ── 第 33 行：第一道检查 ──────────────────────────────
    # contracts_dir.exists()  → 调用 Path 对象的方法，问"这个目录在磁盘上存在吗？" 返回 True/False
    # not                     → 取反：不存在才执行下面缩进的代码
    # 冒号 : 后面的缩进代码，就是"条件成立时要做的事"
    if not contracts_dir.exists():

        # ── 第 34 行：打印错误信息 ────────────────────────
        # f"..."      → f-string：字符串前加 f，里面的 {变量} 会被替换成真实内容
        # [ERROR]     → 只是文字标记，方便人一眼看出是错误
        # file=sys.stderr → 把内容打到"错误通道"而不是普通输出
        #                   （普通输出是 stdout。两个通道分开，方便程序分别处理/重定向）
        print(f"[ERROR] 契约目录不存在: {contracts_dir}", file=sys.stderr)

        # ── 第 35 行：返回数字 2，函数立即结束 ────────────
        # return 的两层作用：①把值交回给调用者 ②函数到此为止，后面的代码不再执行
        # 为什么是 2：约定"输入路径错了"是最严重的一类失败
        return 2

    # ── 第 37 行：真正干活的一行 ──────────────────────────
    # 左边：contracts 是变量名；"list[ServiceContract]" 是类型注解，说明它是个列表，里面每个元素都是 ServiceContract
    # 右边：调用 parse_dir(目录, 映射表)，它会：
    #       ① 遍历目录下所有 .json 文件
    #       ② 认出每个文件属于哪个服务（靠 SERVICE_MAPPING）
    #       ③ 读出接口路径、方法、参数
    #       ④ 推断每个接口要不要登录（auth）
    #       ⑤ 把结果装进 ServiceContract 对象，返回一个列表
    contracts: list[ServiceContract] = parse_dir(contracts_dir, SERVICE_MAPPING)

    # ── 第 38-40 行：第二道检查 ──────────────────────────
    # if not contracts:  → 关键语法：空列表 [] 在 Python 里等于 False！
    #                      所以这行的意思是"如果解析结果是空的"
    #                      等价于写 if len(contracts) == 0:，但 Python 习惯写前者
    # 触发场景：目录存在，但里面没有可用的 .json 文件
    if not contracts:
        # 注意这里是 [WARN]（警告）不是 [ERROR]（错误）——因为目录没错，只是没内容
        print(f"[WARN] 目录 {contracts_dir} 下没有 .json 契约文件", file=sys.stderr)
        # 返回 1，比 2 轻一档：目录在，但没解析出东西
        return 1

    # ── 第 42 行：创建输出目录 ────────────────────────────
    # out.mkdir()           → 建目录
    # parents=True          → 父目录不存在也一起创建（比如 artifacts/a/b 能一次建好）
    # exist_ok=True         → 目录已经存在时不要报错（这样重复运行不会崩溃，很重要）
    # 小白理解：这行保证"输出目录一定存在，且程序可以反复运行"
    out.mkdir(parents=True, exist_ok=True)

    # ── 第 43-45 行：组装要写进文件的数据 ──────────────────
    # payload 是个字典，只有一个键 "services"
    # 值是一个列表推导式（重点语法）：
    #     [ 对每个c做点事  for c in contracts ]
    #     读法：从 contracts 里挨个取出元素叫 c，对 c 做 model_dump(...)，把结果收集成新列表
    #     等价的啰嗦写法：
    #         result = []
    #         for c in contracts:
    #             result.append(c.model_dump(mode="json"))
    #
    # c.model_dump(mode="json") 是 pydantic 库的用法：
    #     把"数据对象"转成"普通字典"，mode="json" 表示顺便把枚举、日期这些特殊类型转成 JSON 能存的值
    #     （比如 AuthType.JWT 会变成字符串 "jwt"，否则下面 json.dumps 会报错）
    payload = {
        "services": [c.model_dump(mode="json") for c in contracts],
    }

    # ── 第 46-48 行：写文件 ────────────────────────────────
    # 这一行拆成 4 块看：
    #
    # ① (out / "endpoints.json")
    #      Path 对象重载了 / 符号，用来拼接路径 → 得到 "artifacts/endpoints.json"
    #      好处：Windows 用 \ 、Linux 用 / ，这样写两种系统都能跑
    #
    # ② .write_text(内容, encoding="utf-8")
    #      一次性完成"打开 → 写入 → 关闭"三步
    #      encoding="utf-8" 明确指定编码（Windows 上不写可能会用 GBK，中文就乱码或报错）
    #      注意是 utf-8 不是 utf-8-sig，所以文件没有 BOM（这和你之前踩过的坑有关）
    #
    # ③ json.dumps(payload, ...)
    #      把 Python 的 dict/list 转成 JSON 格式的字符串（因为文件只能存字符串）
    #
    # ④ ensure_ascii=False, indent=2
    #      ensure_ascii=False → 中文保持原文（默认是 True，会把中文变成 \u9884\u7559 这种看不懂的转义）
    #      indent=2           → 缩进 2 格格式化，方便人阅读、也方便 git diff 对比变化
    (out / "endpoints.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ══════ 下面这部分是"打印统计信息"，不写文件，只是给人在屏幕上看的 ══════

    # ── 第 51 行：算接口总数 ──────────────────────────────
    # sum(...) 是求和；括号里又是个推导式：对每个服务 c，数它有多少个接口 len(c.endpoints)
    # 然后把所有服务的接口数加起来 → 得到 61
    total = sum(len(c.endpoints) for c in contracts)

    # ── 第 52 行：准备一个计数器 ──────────────────────────
    # Counter 是 collections 里的特殊字典：你不存在的键也能直接 +1，它自动从 0 开始
    # 普通字典 dict 做不到这点（会 KeyError）
    auth_counter: Counter = Counter()

    # ── 第 53-55 行：双层循环统计每种鉴权类型有多少个接口 ────
    for c in contracts:              # 外层：挨个服务
        for ep in c.endpoints:       # 内层：挨个接口
            # ep.auth 是个枚举（比如 AuthType.JWT），.value 取它的字符串值（比如 "jwt"）
            # auth_counter["jwt"] += 1 → jwt 类型的计数 +1
            auth_counter[ep.auth.value] += 1

    # ── 第 57-62 行：把统计结果打印出来 ────────────────────
    print(f"[OK] 解析 {len(contracts)} 个服务, 共 {total} 个接口")   # len() 求列表长度
    print("  per-service:")                                        # 普通字符串（没有 f，所以不替换变量）
    for c in contracts:
        # {c.service:28s} 是 f-string 的格式化：占 28 个字符宽度、左对齐（让输出像表格一样整齐）
        # {len(c.endpoints):3d} 是：占 3 位宽度的整数
        print(f"    - {c.service:28s} {len(c.endpoints):3d} endpoints")
    print(f"  auth: {dict(auth_counter)}")                        # dict() 把 Counter 转成普通字典再打印
    print(f"  artifacts -> {out / 'endpoints.json'}")             # 注意这里用的是单引号，因为外层已经是双引号了

    # ── 第 63 行：一切顺利，返回 0 ────────────────────────
    # 0 在约定里代表"成功"，CI 看到 0 就知道这步没问题
    return 0


# ═════════════════════════════════════════════════════════════
# 第 66-71 行：main 函数 —— 负责处理命令行参数
# ═════════════════════════════════════════════════════════════
def main() -> int:
    # 创建一个"参数解析器"，括号里是帮助文字
    ap = argparse.ArgumentParser(description="Phase 4 OpenAPI Parser")

    # 定义第一个参数 --contracts
    #   default="../openapi-contracts" → 命令里不写这个参数时，默认用这个值
    #   type=Path                      → 把输入的字符串自动转成 Path 对象
    ap.add_argument("--contracts", default="../openapi-contracts", type=Path)

    # 定义第二个参数 --out（输出目录，不是文件路径！因为 run 里会再拼上 /endpoints.json）
    ap.add_argument("--out", default="artifacts", type=Path)

    # 真正去解析命令里传进来的参数，结果存在 args 里
    # 之后 args.contracts 就是第一个参数的值，args.out 是第二个
    args = ap.parse_args()

    # 把解析出来的两个参数传给 run()，并把 run 的返回值（退出码）原样返回
    return run(args.contracts, args.out)


# ═════════════════════════════════════════════════════════════
# 第 74-75 行：Python 的固定套路
#
# if __name__ == "__main__":
#     → "只有当这个文件被直接运行时，才执行下面的代码"
#     → 如果它是被别的文件 import 进去的，这段代码不会执行
#     → __name__ 是 Python 自动给每个文件设的变量；直接运行时它的值就是 "__main__"
#
# raise SystemExit(main())
#     → 先执行 main()，拿到返回的退出码（0/1/2）
#     → SystemExit(数字) 把这个数字变成"整个程序的退出状态"
#     → CI 就靠这个数字判断这一步是成功还是失败（非 0 就是失败）
# ═════════════════════════════════════════════════════════════
if __name__ == "__main__":
    raise SystemExit(main())
