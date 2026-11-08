import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
from requirements.cli import _InputError

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

def load_document(path:Path) ->str:
    if not path.exists():
        raise _InputError(2,f"文件不存在:{path}")
    # 检查文件格式是否为docx
    if path.suffix.lower() != ".docx":
        raise _InputError(1,f"不支持的格式:{path.suffix}")
    return _docx_to_markdown(path)

def _docx_to_markdown(path):
    with zipfile.ZipFile(path) as zf:
        root = ET.parse(zf.open("word/document.xml")).getroot()
    body = root.find(f"{W}body")
    lines = []
    for elem in body:                          # 只遍历直接子元素
        if elem.tag == f"{W}p":                # 段落
            text = "".join(t.text or "" for t in elem.iter(f"{W}t")).strip()
            style = _para_style(elem)          # 读 pPr/pStyle/@w:val
            prefix = {"Heading1":"# ", "Heading2":"## ", "Heading3":"### "}.get(style, "")
            if text:
                lines.append(prefix + text)
        elif elem.tag == f"{W}tbl":            # 表格
            for i, tr in enumerate(elem.findall(f"{W}tr")):
                cells = [ _cell_text(tc) for tc in tr.findall(f"{W}tc") ]
                lines.append("| " + " | ".join(cells) + " |")
                if i == 0:
                    lines.append("| " + " | ".join(["---"]*len(cells)) + " |")
    return "\n".join(lines)
