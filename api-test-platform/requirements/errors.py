# 退出码常量：调用方（CI/脚本）按数字判断，不用猜
EXIT_OK = 0
EXIT_EMPTY = 1
EXIT_MISSING = 2
EXIT_UNREADABLE = 3
EXIT_ENCODING = 4
#（注释写清：需求文档不是 .md/.docx 等受支持格式）；
EXIT_FORMAT = 5

class InputError(Exception):
    """输入文件问题，带退出码；由 run() 统一打印成人能懂的提示。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
