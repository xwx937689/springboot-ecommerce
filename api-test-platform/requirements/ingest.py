import zipfile
import re
from pathlib import Path
import xml.etree.ElementTree as ET
from .errors import EXIT_FORMAT, EXIT_MISSING, InputError

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

def load_document(path:Path) ->str:
    if not path.exists():
        raise InputError(EXIT_MISSING, f"文件不存在: {path}")
    # 检查文件格式是否为docx
    if path.suffix.lower() != ".docx":
        raise InputError(EXIT_FORMAT, f"文件格式不支持: {path.suffix}")
    return _docx_to_markdown(path)
def _docx_to_markdown(path):
    with zipfile.ZipFile(path) as zf:
      root = ET.parse(zf.open("word/document.xml")).getroot()
      body = root.find(f"{W}body")
      style_level = _load_style_level(zf)
      lines = []
      _walk(body,lines,style_level)
    return "\n".join(lines)
def _walk(BODY,LINES,STYLE_LEVEL):
    for elem in BODY:                # 只遍历直接子元素
        if elem.tag == f"{W}p":                # 段落
            text = "".join(t.text or "" for t in elem.iter(f"{W}t")).strip()
            style = _para_style(elem,STYLE_LEVEL)          # 读 pPr/pStyle/@w:val
            prefix = ("#" * style + " ") if style else ""
            if text:
                LINES.append(prefix + text)
        elif elem.tag == f"{W}tbl":            # 表格
            for i, tr in enumerate(elem.findall(f"{W}tr")):
                cells = [ _cell_text(tc) for tc in tr.findall(f"{W}tc") ]
                LINES.append("| " + " | ".join(cells) + " |")
                if i == 0:
                    LINES.append("| " + " | ".join(["---"]*len(cells)) + " |")
        elif elem.tag == f"{W}sdt":           #加 w:sdt 分支：封面/目录丢、其他递归 sdtContent。
            val =None
            sdt_pr = elem.find(f"{W}sdtPr")
            if sdt_pr is not None:
               obj = sdt_pr.find(f"{W}docPartObj")
               if obj is not None:
                    gal = obj.find(f"{W}docPartGallery")
                    if gal is not None:
                        val = gal.get(f"{W}val")
            if val in {"Cover Pages","Table of Contents"}:
                continue          #封面/目录: 整块跳过
            content = elem.find(f"{W}sdtContent")
            if content is not None:    #sdtContent 可能不存在，判空防空崩
                _walk(content,LINES,STYLE_LEVEL)

def _cell_text(tc):
    # 单元格 <w:tc> 内含若干 <w:p>，逐段取 <w:t> 文字拼接
    return "".join(
        "".join(t.text or "" for t in p.iter(f"{W}t"))
        for p in tc.iter(f"{W}p")).strip()
def _load_style_level(zf) -> dict:
    stroot = zf.open("word/styles.xml")
    #遍历 w:style，只要 w:type="paragraph"
    styles = ET.parse(stroot).getroot()
    out = {}
    for style in styles.findall(f"{W}style"):
       if not style.get(f"{W}type") == "paragraph":continue
       name_el=style.find(W+"name")
       if name_el is None:
        continue
       name = name_el.get(W +"val").replace("\u3000"," ").lower()
       m = re.match(r"(?:heading|标题)\s*([1-9])", name)
       if m:out[style.get(W + "styleId")] = int(m.group(1))
    return out
def _para_style(elem,STYLE_LEVEL):
    pPr = elem.find(W+"pPr")
    if pPr is None:
        return None
    # ② 有 pPr 但可能没 pStyle
    pStyle = pPr.find(W + "pStyle")
    if pStyle is None:
        return None
    #③ 属性带命名空间，去掉空格
    style_id = pStyle.get(W + "val")
    # ④ 查不到 → None
    return STYLE_LEVEL.get(style_id)


