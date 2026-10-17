# -*- coding: utf-8 -*-
"""HWP·HWPX를 AI 에이전트가 읽고·고치고·새로 쓰고·바꾸는 명령 하나.

  read <파일> [--format md|text|json] [--engine auto|direct|com] [-o 출력]     내용 읽기(표 유지)
  outline <파일>                                    본문 문단 번호·표 번호(edit에서 쓰는 번호)
  edit <파일|new> -o <새 파일> --ops ops.json [--op '{...}'] [--engine auto|com|direct] [--check 폴더]
  exam build|check|fix|diff …                       시험지 초안·점검·자동 고침·편집 전후 비교(hwp_exam.py)
                                                    글자·문단·스타일·표·그림·머리말·쪽·누름틀·각주·목록·바꾸기·합치기(OPS.md)
  render <파일> -o <폴더> · compare <원본> <새 파일> -o <폴더> · check <파일> [--roundtrip 폴더]   쪽 그림·검증
  actions [검색어] [--items]                         한글 액션 표 찾기(액션 표 파일이 있을 때)
  info · replace · fields · fill · export-json · import-json · write · convert   (1판 명령, 그대로)

엔진: 한컴이 있으면 한컴 COM(hwp_com.py), 없으면 HWPX 직접(hwpx_direct.py)·HWP 글 바꾸기 직접(hwp5_direct.py).
원본은 고치지 않는다: -o 가 원본과 같으면 멈춘다.
"""
import argparse, html, io, json, os, re, shutil, struct, sys, time, zipfile, zlib

# ───────────────────────── 공통 문서 모양: 문단과 표 ─────────────────────────
# 블록 = ("p", 글) 또는 ("table", [[칸 글, ...], ...])  — 칸 글 안의 줄바꿈은 <br>로 바꿔 Markdown 표에 담는다.


def to_markdown(blocks):
    out = []
    for kind, value in blocks:
        if kind == "p":
            out.append(value)
        else:
            rows = [r for r in value if any(c.strip() for c in r)]
            if not rows:
                continue
            width = max(len(r) for r in rows)
            esc = lambda c: c.replace("|", "\\|").replace("\n", "<br>").strip()
            lines = ["| " + " | ".join(esc(c) for c in r + [""] * (width - len(r))) + " |" for r in rows]
            lines.insert(1, "|" + "---|" * width)
            out.append("")
            out += lines
            out.append("")
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def to_text(blocks):
    out = []
    for kind, value in blocks:
        if kind == "p":
            out.append(value)
        else:
            out += ["\t".join(c.replace("\n", " ") for c in r) for r in value]
    return "\n".join(out).strip() + "\n"


def grid(cells, rows, cols):
    """(행, 열, 행합침, 열합침, 글) 목록 → rows×cols 표. 합친 칸은 첫 칸에만 글."""
    rows = max([rows] + [r + 1 for r, *_ in cells]) if cells else rows
    cols = max([cols] + [c + 1 for _, c, *_ in cells]) if cells else cols
    g = [[""] * cols for _ in range(rows)]
    for r, c, rs, cs, t in cells:
        if r < rows and c < cols:
            g[r][c] = t
    return g


# ───────────────────────── HWP 5.x 직접 해석 ─────────────────────────
TAG = 0x10
PARA_HEADER, PARA_TEXT, CTRL_HEADER, LIST_HEADER, TABLE = TAG + 50, TAG + 51, TAG + 55, TAG + 56, TAG + 61
WIDE = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}   # 8글자(16바이트) 차지하는 제어


def hwp5_records(buf):
    out, i = [], 0
    while i + 4 <= len(buf):
        h, = struct.unpack_from("<I", buf, i); i += 4
        tag, level, size = h & 0x3FF, (h >> 10) & 0x3FF, h >> 20
        if size == 0xFFF:
            if i + 4 > len(buf): break
            size, = struct.unpack_from("<I", buf, i); i += 4
        if i + size > len(buf): break
        out.append((tag, level, buf[i:i + size]))
        i += size
    return out


def hwp5_text(data):
    out, i = bytearray(), 0
    while i + 1 < len(data):
        c, = struct.unpack_from("<H", data, i)
        if c >= 32:
            out += data[i:i + 2]; i += 2
        elif c in WIDE:
            if c == 9: out += "\t".encode("utf-16le")
            i += 16
        else:
            if c == 10: out += "\n".encode("utf-16le")
            elif c in (24,): out += "-".encode("utf-16le")
            elif c in (30, 31): out += " ".encode("utf-16le")
            i += 2
    return out.decode("utf-16le", "replace").rstrip("\r\n")


def hwp5_blocks(recs):
    """레코드 수준(level)을 따라 문단과 표(칸 주소·합침 포함)를 짠다. 표 밖 다른 개체(글상자·각주 등)의 글은 문단으로."""
    n = len(recs)

    def paras(i, level, stop_at_cell):
        blocks = []
        while i < n:
            tag, lv, data = recs[i]
            if lv < level or (stop_at_cell and lv == level and tag == LIST_HEADER):
                break
            if tag == PARA_HEADER and lv == level:
                i += 1
                text, extra = "", []
                while i < n and recs[i][1] > level:
                    t2, l2, d2 = recs[i]
                    if t2 == PARA_TEXT and l2 == level + 1:
                        text = hwp5_text(d2); i += 1
                    elif t2 == CTRL_HEADER and l2 == level + 1 and d2[:4] == b" lbt":   # 'tbl '를 거꾸로 적음
                        table, i = tbl(i + 1, level + 2); extra.append(table)
                    elif t2 == PARA_TEXT:   # 다른 개체 안 문단(글상자·각주 등)
                        t = hwp5_text(d2)
                        if t.strip(): extra.append(("p", t))
                        i += 1
                    else:
                        i += 1
                blocks.append(("p", text)); blocks += extra
            else:
                i += 1
        return blocks, i

    def tbl(i, level):
        rows = cols = 0
        cells = []
        while i < n and recs[i][1] >= level:
            tag, lv, data = recs[i]
            if tag == TABLE and lv == level and len(data) >= 8:
                rows, cols = struct.unpack_from("<HH", data, 4); i += 1
            elif tag == LIST_HEADER and lv == level and len(data) >= 16:
                col, row, cspan, rspan = struct.unpack_from("<HHHH", data, 8)
                inner, i = paras(i + 1, level, True)
                cells.append((row, col, rspan, cspan, cell_text(inner)))
            else:
                i += 1
        return ("table", grid(cells, rows, cols)), i

    blocks, _ = paras(0, 0, False)
    return blocks


def cell_text(blocks):
    parts = []
    for kind, value in blocks:
        if kind == "p":
            if value.strip(): parts.append(value)
        else:   # 칸 안의 표는 줄로 풀어 넣는다
            parts += [" / ".join(c for c in r if c.strip()) for r in value]
    return "\n".join(parts)


def read_hwp5(path):
    import olefile
    if not olefile.isOleFile(path):
        raise ValueError("HWP 5 형식이 아님")
    ole = olefile.OleFileIO(path)
    try:
        fh = ole.openstream("FileHeader").read()
        if not fh.startswith(b"HWP Document File"):
            raise ValueError("HWP 5 형식이 아님")
        flags, = struct.unpack_from("<I", fh, 36)
        info = {"형식": "HWP " + ".".join(str(b) for b in fh[32:36][::-1]), "압축": bool(flags & 1),
                "암호": bool(flags & 2), "배포용": bool(flags & 4)}
        if info["암호"]:
            raise PermissionError("암호가 걸린 문서")
        if info["배포용"]:
            raise PermissionError("배포용 문서(본문 암호화) — 한컴으로만 열 수 있음")
        secs = sorted((s for s in ole.listdir() if s[0] == "BodyText" and s[1].startswith("Section")),
                      key=lambda s: int(s[1][7:] or 0))
        blocks = []
        for s in secs:
            raw = ole.openstream("/".join(s)).read()
            blocks += hwp5_blocks(hwp5_records(zlib.decompress(raw, -15) if info["압축"] else raw))
        info.update(구역=len(secs))
        return blocks, info
    finally:
        ole.close()


# ───────────────────────── HWPX 직접 해석 ─────────────────────────
HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def read_hwpx(path):
    from lxml import etree
    with zipfile.ZipFile(path) as z:
        names = sorted((n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)),
                       key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        if not names:
            raise ValueError("HWPX 형식이 아님(Contents/section*.xml 없음)")
        blocks = []
        for n in names:
            root = etree.fromstring(z.read(n))
            for p in root.findall(f"{HP}p"):
                blocks += hwpx_para(p)
    return blocks, {"형식": "HWPX", "구역": len(names)}


def hwpx_para(p):
    text, extra = [], []
    for run in p.iter(f"{HP}run"):
        if run.getparent() is not p:
            continue
        for el in run:
            if el.tag == f"{HP}t":
                text.append(hwpx_t(el))
            elif el.tag == f"{HP}tbl":
                extra.append(hwpx_table(el))
            else:   # 글상자 등: 그 안 문단 글
                for sub in el.iter(f"{HP}subList"):
                    for q in sub.findall(f"{HP}p"):
                        extra += [b for b in hwpx_para(q) if b[0] == "p" and b[1].strip()]
                    break
    return [("p", "".join(text))] + extra


def hwpx_t(t):
    s = t.text or ""
    for ch in t:
        name = ch.tag.split("}")[-1]
        s += {"tab": "\t", "lineBreak": "\n", "nbSpace": " ", "fwSpace": " "}.get(name, "")
        s += ch.tail or ""
    return s


def hwpx_table(tbl):
    rows, cols = int(tbl.get("rowCnt", 0)), int(tbl.get("colCnt", 0))
    cells = []
    for tc in tbl.iter(f"{HP}tc"):
        if tc.getparent().getparent() is not tbl:   # 바깥 표의 칸만
            continue
        addr, span = tc.find(f"{HP}cellAddr"), tc.find(f"{HP}cellSpan")
        r = int(addr.get("rowAddr", 0)) if addr is not None else 0
        c = int(addr.get("colAddr", 0)) if addr is not None else 0
        rs = int(span.get("rowSpan", 1)) if span is not None else 1
        cs = int(span.get("colSpan", 1)) if span is not None else 1
        inner = []
        sub = tc.find(f"{HP}subList")
        for q in (sub.findall(f"{HP}p") if sub is not None else []):
            inner += hwpx_para(q)
        cells.append((r, c, rs, cs, cell_text(inner)))
    return ("table", grid(cells, rows, cols))


# ───────────────────────── 한컴 COM ─────────────────────────
FORMATS = {".hwp": "HWP", ".hwpx": "HWPX", ".pdf": "PDF", ".docx": "OOXML", ".html": "HTML", ".htm": "HTML",
           ".txt": "UNICODE", ".hml": "HWPML2X", ".odt": "ODT", ".rtf": "RTF"}


def hancom_installed():
    try:
        import winreg
        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "HWPFrame.HwpObject").Close()
        return True
    except Exception:
        return False


from hwp_com import Session as Hancom   # 창 핸들로 찾은 그 한글만 닫는다(동시 실행에도 안전)


def com_open(hwp, path):
    ok = hwp.Open(os.path.abspath(path), "", "forceopen:true;suspendpassword:true;versionwarning:false")
    if not ok:
        raise RuntimeError("한글이 파일을 열지 못함: " + path)


def com_save(hwp, out):
    fmt = FORMATS.get(os.path.splitext(out)[1].lower())
    if not fmt:
        raise ValueError("이 확장자로는 저장 못 함: " + out)
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    if not hwp.SaveAs(os.path.abspath(out), fmt, ""):
        raise RuntimeError(f"저장 실패({fmt}): {out}")


def hml_blocks(xml):
    """한컴 HWPML 2.x(GetTextFile('HWPML2X'))의 BODY → 문단과 표."""
    from lxml import etree
    root = etree.fromstring(xml.encode("utf-16") if isinstance(xml, str) else xml)
    body = root.find("BODY")

    marks = {"TAB": "\t", "LINEBREAK": "\n", "NBSPACE": " ", "FWSPACE": " ", "HYPEN": "-", "HYPHEN": "-"}

    def para(p):
        text, extra = [], []
        for t in p.findall("TEXT"):
            for node in t:
                if node.tag == "CHAR":   # <CHAR>글<TAB/>글<NBSPACE/>글</CHAR>: 하위 표시 뒤 글(tail)까지
                    text.append(node.text or "")
                    for ch in node:
                        text.append(marks.get(ch.tag, "") + (ch.tail or ""))
                elif node.tag == "TABLE":
                    extra.append(table(node))
                elif node.tag in marks:
                    text.append(marks[node.tag])
        return [("p", "".join(text))] + extra

    def table(t):
        cells = []
        for cell in t.iter("CELL"):
            if _owner(cell, "TABLE") is not t:
                continue
            inner = []
            pl = cell.find("PARALIST")
            for q in (pl.findall("P") if pl is not None else []):
                inner += para(q)
            cells.append((int(cell.get("RowAddr", 0)), int(cell.get("ColAddr", 0)), int(cell.get("RowSpan", 1)),
                          int(cell.get("ColSpan", 1)), cell_text(inner)))
        return ("table", grid(cells, int(t.get("RowCount", 0)), int(t.get("ColCount", 0))))

    blocks = []
    for sec in (body if body is not None else []):
        for pl in sec.findall("P"):
            blocks += para(pl)
    return blocks


def _owner(node, tag):
    a = node.getparent()
    while a is not None and a.tag != tag:
        a = a.getparent()
    return a


def read_com(path):
    with Hancom() as hwp:
        com_open(hwp, path)
        info = {"형식": "한컴 COM으로 읽음", "쪽수": hwp.PageCount}
        xml = hwp.GetTextFile("HWPML2X", "")
        if not xml:
            raise RuntimeError("한글이 내용을 내주지 않음(복사 막힌 배포용 문서일 수 있음)")
        return hml_blocks(xml), info


def read_textlike(path):
    raw = open(path, "rb").read()
    text = None
    for enc in ("utf-8-sig", "utf-16", "cp949"):
        try:
            text = raw.decode(enc)
            if "\x00" not in text:
                break
            text = None
        except UnicodeDecodeError:
            continue
    if text is None:
        return None
    s = text.lstrip()
    if s.startswith("<") and "<HWPML" in s[:2000]:
        return hml_blocks(re.sub(r"^<\?xml[^>]*\?>", "", s)), {"형식": "HWPML(XML)"}
    if s.startswith("<"):
        body = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", s)
        body = re.sub(r"(?i)<(br|/p|/div|/tr|/h\d|/li)\b[^>]*>", "\n", body)
        body = html.unescape(re.sub(r"<[^>]+>", " ", body))
        return [("p", l.strip()) for l in body.splitlines() if l.strip()], {"형식": "HTML"}
    return [("p", l) for l in text.splitlines()], {"형식": "글자 파일(이름만 " + os.path.splitext(path)[1] + ")"}


def read_any(path, engine="auto"):
    ext = os.path.splitext(path)[1].lower()
    errors = []
    if engine in ("auto", "direct"):
        with open(path, "rb") as f:
            head = f.read(8)
        try:
            if head.startswith(b"\xd0\xcf\x11\xe0"):
                return read_hwp5(path)
            if head.startswith(b"PK\x03\x04"):
                return read_hwpx(path)
            if head.startswith(b"HWP Document File"[:8]):
                raise ValueError("HWP 3.x 옛 형식")
            got = read_textlike(path)   # 이름만 .hwp인 HWPML(XML)·HTML·글자 파일
            if got:
                return got
            errors.append(f"직접 해석 못 하는 형식({ext}, 앞 바이트 {head[:4].hex()})")
        except Exception as e:
            errors.append(f"직접 해석: {e}")
        if engine == "direct":
            raise RuntimeError("; ".join(errors))
    if not hancom_installed():
        raise RuntimeError("; ".join(errors + ["한컴오피스가 없어 COM으로도 못 읽음"]))
    return read_com(path)


# ───────────────────────── Markdown → HTML (쓰기용) ─────────────────────────
def md_to_html(md):
    out, i, lines = [], 0, md.replace("\r\n", "\n").split("\n")
    inline = lambda s: re.sub(r"\*(.+?)\*", r"<i>\1</i>", re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(s)))
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1; continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            n = len(m.group(1)); out.append(f"<h{n}>{inline(m.group(2))}</h{n}>"); i += 1; continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                if not re.match(r"^\s*\|?\s*:?-{2,}", lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            t = ["<table border=1 cellspacing=0 cellpadding=4 width='100%'>"]
            for r, row in enumerate(rows):
                tag = "th" if r == 0 else "td"
                t.append("<tr>" + "".join(f"<{tag}>{inline(c).replace('&lt;br&gt;', '<br>')}</{tag}>" for c in row) + "</tr>")
            out.append("\n".join(t + ["</table>"])); continue
        if re.match(r"^\s*([-*]|\d+[.)])\s+", line):
            ordered = bool(re.match(r"^\s*\d+[.)]", line))
            items = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+[.)])\s+", lines[i]):
                items.append("<li>" + inline(re.sub(r"^\s*([-*]|\d+[.)])\s+", "", lines[i])) + "</li>"); i += 1
            out.append(("<ol>" if ordered else "<ul>") + "".join(items) + ("</ol>" if ordered else "</ul>")); continue
        para = []
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6}\s|\s*\||\s*([-*]|\d+[.)])\s)", lines[i]):
            para.append(inline(lines[i])); i += 1
        out.append("<p>" + "<br>".join(para) + "</p>")
    return "<html><head><meta charset='utf-8'></head><body>\n" + "\n".join(out) + "\n</body></html>"


# ───────────────────────── 명령 ─────────────────────────
def same_file(a, b):
    return os.path.abspath(a).lower() == os.path.abspath(b).lower()


def guard_out(src, out):
    if same_file(src, out):
        sys.exit("원본을 덮어쓰지 않습니다. -o 에 다른 파일 이름을 주세요.")


def emit(text, out):
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        open(out, "w", encoding="utf-8").write(text)
        print(f"저장: {out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


def cmd_read(a):
    t = time.time()
    blocks, info = read_any(a.file, a.engine)
    if a.format == "json":
        emit(json.dumps({"정보": info, "블록": [{"종류": k, "값": v} for k, v in blocks]}, ensure_ascii=False, indent=1), a.output)
    else:
        emit(to_markdown(blocks) if a.format == "md" else to_text(blocks), a.output)
    print(f"{info.get('형식')} · 문단 {sum(1 for k, _ in blocks if k == 'p')} · 표 {sum(1 for k, _ in blocks if k == 'table')} · {time.time() - t:.2f}초",
          file=sys.stderr)


def cmd_info(a):
    with open(a.file, "rb") as f:
        head = f.read(8)
    info = {"파일": a.file, "크기": os.path.getsize(a.file)}
    try:
        blocks, more = read_any(a.file, "direct")
        info.update(more)
        info.update(문단=sum(1 for k, _ in blocks if k == "p"), 표=sum(1 for k, _ in blocks if k == "table"),
                    글자=sum(len(v) if k == "p" else sum(len(c) for r in v for c in r) for k, v in blocks))
    except Exception as e:
        info["직접 해석"] = str(e)
    if hancom_installed() and a.pages:
        with Hancom() as hwp:
            com_open(hwp, a.file)
            info["쪽수"] = hwp.PageCount
    print(json.dumps(info, ensure_ascii=False, indent=1))


def cmd_replace(a):
    guard_out(a.file, a.output)
    if len(a.find) != len(a.to):
        sys.exit("--find 와 --to 개수가 같아야 합니다.")
    with Hancom() as hwp:
        com_open(hwp, a.file)
        before = hwp.GetTextFile("UNICODE", "") or ""
        pages = hwp.PageCount
        report = []
        for f, t in zip(a.find, a.to):
            n = before.count(f)
            act, ps = hwp.HAction, hwp.HParameterSet.HFindReplace
            act.GetDefault("AllReplace", ps.HSet)
            ps.FindString, ps.ReplaceString = f, t
            ps.IgnoreMessage, ps.Direction, ps.FindType = 1, hwp.FindDir("AllDoc"), 1
            act.Execute("AllReplace", ps.HSet)
            report.append((f, t, n))
        after = hwp.GetTextFile("UNICODE", "") or ""
        com_save(hwp, a.output)
        for f, t, n in report:
            print(f"'{f}' → '{t}': 본문에 {n}곳, 바꾼 뒤 남은 '{f}' {after.count(f)}곳", file=sys.stderr)
        print(f"쪽수 {pages} → {hwp.PageCount}, 저장: {a.output}", file=sys.stderr)


def cmd_fields(a):
    with Hancom() as hwp:
        com_open(hwp, a.file)
        names = [n for n in (hwp.GetFieldList(0, 0) or "").split("\x02") if n]
        print(json.dumps({n: hwp.GetFieldText(n) for n in names}, ensure_ascii=False, indent=1))


def cmd_fill(a):
    guard_out(a.file, a.output)
    with Hancom() as hwp:
        com_open(hwp, a.file)
        names = set(n for n in (hwp.GetFieldList(0, 0) or "").split("\x02") if n)
        for kv in a.field:
            k, _, v = kv.partition("=")
            if k not in names:
                sys.exit(f"누름틀 '{k}'이(가) 없습니다. 있는 것: {sorted(names)}")
            hwp.PutFieldText(k, v)
        com_save(hwp, a.output)
        print(f"누름틀 {len(a.field)}개 채움, 저장: {a.output}", file=sys.stderr)


def cmd_export_json(a):
    with Hancom() as hwp:
        com_open(hwp, a.file)
        j = hwp.GetTextFile("JSON", "")
        if not j:
            raise RuntimeError("한글이 JSON을 내주지 않음")
        emit(j, a.output)


def cmd_import_json(a):
    with Hancom() as hwp:
        if not hwp.SetTextFile(open(a.file, encoding="utf-8").read(), "JSON", ""):
            raise RuntimeError("한글이 JSON을 받아들이지 않음")
        com_save(hwp, a.output)
        print(f"쪽수 {hwp.PageCount}, 저장: {a.output}", file=sys.stderr)


def cmd_write(a):
    src = open(a.file, encoding="utf-8").read()
    ext = os.path.splitext(a.file)[1].lower()
    doc = src if ext in (".html", ".htm") else md_to_html(src) if ext in (".md", ".markdown") else \
        "<html><body>" + "".join(f"<p>{html.escape(l)}</p>" for l in src.splitlines()) + "</body></html>"
    if getattr(a, "engine", "auto") == "direct" or not hancom_installed():
        if ext not in (".md", ".markdown", ".txt"):
            sys.exit("한컴 없이 쓰기는 Markdown(.md)·글(.txt)만 됩니다. HTML은 한컴 경로(--engine com)")
        import md_write                          # 한컴 없이 Markdown → HWPX(→ HWP)
        t = time.time()
        to_hwp = a.output.lower().endswith(".hwp")
        polish = getattr(a, "polish", False) and hancom_installed()
        target = a.output + ".tmp.hwpx" if (to_hwp or polish) else a.output
        for line in md_write.write(src, target, getattr(a, "header", None), os.path.dirname(os.path.abspath(a.file)),
                                    getattr(a, "style", "report")):
            if "실패" in line:
                print(line, file=sys.stderr)
        if polish:
            polish_layout(target, a.output)
            os.remove(target)
        elif to_hwp:
            hwpx_to_hwp_direct(target, a.output)
            os.remove(target)
        print(f"직접 경로(Markdown → {'HWP' if to_hwp else 'HWPX'}) · {time.time() - t:.1f}초 · 저장: {a.output}", file=sys.stderr)
        return
    with Hancom() as hwp:
        if not hwp.SetTextFile(doc, "HTML", ""):
            raise RuntimeError("한글이 HTML을 받아들이지 않음")
        com_save(hwp, a.output)
        print(f"쪽수 {hwp.PageCount}, 저장: {a.output}", file=sys.stderr)


def loose_lines(d):
    """양쪽 정렬·어절 줄 나눔 문단에서 띄어쓰기가 벌어진 줄 수 → {문단 번호: 줄 수}.
    줄마다 (글자 폭 합 / 줄 폭)을 재서, 같은 문단에서 가장 꽉 찬 줄의 90%에 못 미치면 벌어진 줄로 본다(마지막 줄 제외)."""
    import hwp_typeset as ts
    import hwpx_direct as X
    just = {pr.get("id") for pr in d.header.iter(X.HH + "paraPr")
            if pr.find(X.HH + "align") is not None and pr.find(X.HH + "align").get("horizontal") == "JUSTIFY"}
    out = {}
    for i, p in enumerate(d.all_paras()):
        if p.get("paraPrIDRef") not in just or not ts.word_wrapped(d, p):
            continue
        t, ls = ts.plain_text(p), ts.lines_of(p)
        if not t or len(ls) < 3:
            continue
        fills = [sum(ts._w(c) for c in t[a["textpos"]:b["textpos"]].strip()) / max(1, a["horzsize"]) for a, b in zip(ls, ls[1:])]
        n = sum(1 for f in fills if f < 0.9 * max(fills))
        if n:
            out[i] = n
    return out


def lonely_headers(d):
    """쪽을 넘는(자리 차지) 표 가운데, 표를 담은 문단의 쪽 안 위치 + 머리 줄 + 첫 줄 묶음(첫 칸 세로 합침만큼) 높이가
    본문 높이를 넘는 표는 그 문단에 쪽 나눔을 건다. 고친 표 수를 돌려준다."""
    import hwp_typeset as ts
    import hwpx_direct as X
    pr = d.secprs()[0].find(X.HP + "pagePr")
    m = pr.find(X.HP + "margin")
    body_h = int(pr.get("height")) - sum(int(m.get(k)) for k in ("top", "bottom", "header", "footer"))

    def cell_h(tc):                      # 칸 높이 = 칸 안 마지막 줄 끝 + 칸 위아래 여백(저장된 cellSz 높이는 최솟값일 수 있다)
        end = 0
        for q in tc.iter(X.HP + "p"):
            for ln in ts.lines_of(q):
                end = max(end, ln["vertpos"] + ln["vertsize"])
        mg = tc.find(X.HP + "cellMargin")
        pad = int(mg.get("top", 0)) + int(mg.get("bottom", 0)) if mg is not None else 282
        return max(int(tc.find(X.HP + "cellSz").get("height")), end + pad)
    n = 0
    for p in d.body():
        tbl = p.find(f"{X.HP}run/{X.HP}tbl")
        if tbl is None or tbl.find(X.HP + "pos").get("treatAsChar") != "0" or p.get("pageBreak") == "1":
            continue
        rows = tbl.findall(X.HP + "tr")
        if len(rows) < 2 or rows[0].find(X.HP + "tc").get("header") != "1":
            continue
        ls = ts.lines_of(p)
        if not ls:
            continue
        h = [max(cell_h(tc) for tc in r.findall(X.HP + "tc")) for r in rows]
        span = int(rows[1].find(X.HP + "tc").find(X.HP + "cellSpan").get("rowSpan", "1"))
        if ls[0]["vertpos"] > 0 and ls[0]["vertpos"] + sum(h[:1 + span]) > body_h:
            p.set("pageBreak", "1")
            d.touch(p)
            n += 1
    return n


def polish_layout(src, out):
    """한컴(숨긴 데스크톱)으로 줄 배치를 잰 뒤, 줄 끝에서 낱말이 잘린 문단만 문단 전체의 자간·장평을 조금씩 줄여
    낱말이 한 줄에 들어가게 한다. 잘림이 없는 문단은 건드리지 않는다. out은 .hwp·.hwpx 모두."""
    import tempfile
    import hwp_typeset as ts
    import hwpx_direct as X
    from hwp_com import Session
    # 단계: (자간 더하기 %, 장평 %) — 본문은 4단계(자간 -5·장평 97)까지, 표 칸은 끝까지
    steps = [(0, 100), (-2, 100), (-4, 100), (-5, 98), (-5, 96), (-6, 94), (-6, 92)]
    body_max = 4
    work = tempfile.mkdtemp(prefix="hwppolish_")
    base, cur = os.path.join(work, "laid.hwpx"), os.path.join(work, "cur.hwpx")
    level = {}
    with Session() as hwp:
        ts.relayout(hwp, src, base)
        cur_src = base
        for rnd in range(len(steps)):
            d = X.Hwpx(cur_src)
            paras = d.all_paras()
            _, where = ts.count_breaks(d, list(range(len(paras))))
            split = sorted({w[0] for w in where if w[1] == "split"})
            print(f"자간·장평 {rnd}바퀴: 낱말 잘림 문단 {len(split)}개", file=sys.stderr)
            bumped = False
            for i in split:
                cap = len(steps) - 1 if any(a.tag == X.HP + "tc" for a in paras[i].iterancestors()) else body_max
                if level.get(i, 0) < cap:
                    level[i] = level.get(i, 0) + 1
                    bumped = True
            if not bumped:
                break
            d = X.Hwpx(base)                     # 매 바퀴 원본에 다시 입힌다(겹쳐 줄지 않게)
            paras = d.all_paras()
            for i, lv in level.items():
                sp, ratio = steps[lv]
                for run in paras[i].findall(X.HP + "run"):
                    run.set("charPrIDRef", d.charpr(run.get("charPrIDRef", "0"), {"spacing_add": sp, "ratio": ratio}))
                d.touch(paras[i])
            tmp = os.path.join(work, f"r{rnd}.hwpx")
            d.save(tmp)
            ts.relayout(hwp, tmp, cur)
            cur_src = cur
        # 띄어쓰기가 벌어진 줄(양쪽 정렬에서 다음 어절이 안 들어가 빈칸이 늘어난 줄): 그 문단 자간을 조금씩 줄여
        # 벌어진 줄이 가장 적은 값을 고른다. 낱말 잘림을 맞춘 문단은 건드리지 않는다.
        loose_base = os.path.join(work, "loose_base.hwpx")
        shutil.copy(cur_src, loose_base)
        d = X.Hwpx(loose_base)
        best = {i: (n, 0) for i, n in loose_lines(d).items() if i not in level}
        tried = 0
        for sp, ratio in ((-2, 100), (-4, 100), (-6, 100), (-5, 97), (-6, 95)):
            if not any(n for n, _ in best.values()):
                break
            d = X.Hwpx(loose_base)
            paras = d.all_paras()
            for i, (n, _) in best.items():
                if n:
                    for run in paras[i].findall(X.HP + "run"):
                        run.set("charPrIDRef", d.charpr(run.get("charPrIDRef", "0"), {"spacing_add": sp, "ratio": ratio}))
                    d.touch(paras[i])
            tmp, laid2 = os.path.join(work, f"l{sp}.hwpx"), os.path.join(work, f"l{sp}_laid.hwpx")
            d.save(tmp)
            ts.relayout(hwp, tmp, laid2)
            now = loose_lines(X.Hwpx(laid2))
            for i, (n, v) in list(best.items()):
                if n and now.get(i, 0) < n:
                    best[i] = (now.get(i, 0), (sp, ratio))
            tried += 1
        fixed = {i: v for i, (n, v) in best.items() if v}
        print(f"벌어진 줄: 문단 {len(best)}개 중 {len(fixed)}개 자간·장평 조정, 남은 벌어진 줄 {sum(n for n, _ in best.values())}",
              file=sys.stderr)
        if fixed:
            d = X.Hwpx(loose_base)
            paras = d.all_paras()
            for i, (sp, ratio) in fixed.items():
                for run in paras[i].findall(X.HP + "run"):
                    run.set("charPrIDRef", d.charpr(run.get("charPrIDRef", "0"), {"spacing_add": sp, "ratio": ratio}))
                d.touch(paras[i])
            tmp = os.path.join(work, "loose.hwpx")
            d.save(tmp)
            cur_src = os.path.join(work, "loose_laid.hwpx")
            ts.relayout(hwp, tmp, cur_src)
        # 쪽 끝에 표 머리 줄만 남는 표(머리 줄 + 첫 줄 묶음이 남은 자리에 안 들어감)는 다음 쪽에서 시작한다
        d = X.Hwpx(cur_src)
        moved = lonely_headers(d)
        if moved:
            tmp = os.path.join(work, "head.hwpx")
            d.save(tmp)
            cur_src = os.path.join(work, "head_laid.hwpx")
            ts.relayout(hwp, tmp, cur_src)
            print(f"머리 줄만 남던 표 {moved}개를 다음 쪽으로", file=sys.stderr)
        if not hwp.Open(os.path.abspath(cur_src), "", ts.OPEN_OPT):
            raise RuntimeError("한컴이 다듬은 파일을 열지 못함")
        pages = hwp.PageCount
        if not hwp.SaveAs(os.path.abspath(out), "HWP" if out.lower().endswith(".hwp") else "HWPX", ""):
            raise RuntimeError(f"저장하지 못함(다른 프로그램에서 열려 있는지 확인): {out}")
        hwp.Clear(1)
    print(f"다듬기: 조정한 문단 {len(level)} · 남은 잘림 문단 {len(split)} · {pages}쪽", file=sys.stderr)


def hwpx_to_hwp_direct(src, out):
    """한컴 없이 HWPX → HWP(우리 변환기 hwpx2hwp). 옮기지 못한 요소는 경고로 알린다."""
    import hwpx2hwp
    for w in hwpx2hwp.convert(src, out):
        print("변환 경고:", w, file=sys.stderr)


def cmd_convert(a):
    guard_out(a.file, a.output)
    ext = os.path.splitext(a.output)[1].lower()
    if ext == ".md":
        blocks, _ = read_any(a.file, "auto")
        return emit(to_markdown(blocks), a.output)
    direct = a.engine == "direct" or (a.engine == "auto" and not hancom_installed())
    if direct and ext == ".hwp" and not is_ole(a.file):
        hwpx_to_hwp_direct(a.file, a.output)
        print(f"직접 경로(hwpx2hwp) · 저장: {a.output}", file=sys.stderr)
        return
    if direct and ext == ".hwpx" and is_ole(a.file):
        import hwp5_to_hwpx
        for w in hwp5_to_hwpx.convert(a.file, a.output):
            print("변환 경고:", w, file=sys.stderr)
        print(f"직접 경로(hwp5_to_hwpx) · 저장: {a.output}", file=sys.stderr)
        return
    if direct:
        sys.exit("한컴 없이 되는 변환은 HWPX ↔ HWP·Markdown입니다(PDF·DOCX 등은 한컴 경로)")
    with Hancom() as hwp:
        com_open(hwp, a.file)
        com_save(hwp, a.output)
        print(f"쪽수 {hwp.PageCount}, 저장: {a.output}", file=sys.stderr)


# ───────────────────────── 고치기(ops) · 검증 ─────────────────────────
def load_ops(a):
    ops = []
    if a.ops:
        data = json.load(open(a.ops, encoding="utf-8"))
        ops += data if isinstance(data, list) else data.get("ops", [])
    for s in a.op or []:
        ops.append(json.loads(s))
    if not ops:
        sys.exit("--ops 파일이나 --op '{...}'를 주세요. 명령 목록은 OPS.md")
    return ops


def is_ole(path):
    with open(path, "rb") as f:
        return f.read(4) == bytes.fromhex("d0cf11e0")


def layout_mode(a):
    """직접 경로로 쓴 파일의 줄 배치: hancom = 한컴(숨긴 데스크톱)으로 한 번 열어 저장해 줄 배치를 써 둔다(기본, 한컴이 있을 때),
    none = 줄 배치 없이 둔다(한컴 데스크톱은 열 때 다시 짬. 한컴 뷰어·웹 한글·모바일은 미확인 — 2026-10-16)."""
    m = getattr(a, "layout", "auto")
    if m == "auto":
        return "hancom" if hancom_installed() else "none"
    return m


def relayout_in_place(path):
    """한컴으로 열어 같은 형식으로 다시 저장(줄 배치가 채워짐)."""
    import hwp_check  # noqa: F401  (Session 경로 준비)
    from hwp_com import Session
    fmt = "HWP" if path.lower().endswith(".hwp") else "HWPX"
    tmp = path + ".relayout" + os.path.splitext(path)[1]
    with Session() as hwp:
        if not hwp.Open(os.path.abspath(path), "", "forceopen:true;versionwarning:false"):
            raise RuntimeError("한컴이 결과 파일을 열지 못함: " + path)
        _ = hwp.PageCount                     # 쪽수를 물어야 한컴이 배치를 끝낸다(안 물으면 줄 배치 없이 저장됨)
        hwp.SaveAs(os.path.abspath(tmp), fmt, "")
        hwp.Clear(1)
    os.replace(tmp, path)


def edit_engine(a, ops):
    """auto: 한글 없이 되는 일은 직접 경로로. 한컴은 HWP가 끼거나(바꾸기·누름틀만이면 HWP도 직접) action 명령일 때만."""
    if a.engine != "auto":
        return a.engine
    if not hancom_installed():
        return "direct"
    new = a.file.lower() == "new"
    src_hwp = not new and is_ole(a.file)
    out_hwp = a.output.lower().endswith(".hwp")
    if any(o.get("op") == "action" for o in ops):
        return "com"
    if not src_hwp and not out_hwp:
        return "direct"
    if src_hwp and out_hwp and all(o.get("op") in ("replace", "fill") and not o.get("scope") for o in ops):
        return "direct"
    return "com"


def cmd_edit(a):
    new = a.file.lower() == "new"
    if not new:
        guard_out(a.file, a.output)
    ops = load_ops(a)
    engine = edit_engine(a, ops)
    t = time.time()
    if engine == "com":
        from hwp_com import ComDoc
        with Hancom() as hwp:
            if not new:
                com_open(hwp, a.file)
            d = ComDoc(hwp)
            pages = hwp.PageCount
            for i, o in enumerate(ops, 1):
                try:
                    msg = d.op(dict(o))
                    print(f"[{i}] {o['op']}: {msg}", file=sys.stderr)
                except Exception as e:
                    print(f"[{i}] {o['op']}: 실패 — {e}", file=sys.stderr)
                    if not a.keep_going:
                        sys.exit("멈춤: 저장하지 않았습니다(--keep-going이면 계속).")
            d.body()
            com_save(hwp, a.output)
            print(f"한컴 경로 · 쪽수 {pages} → {hwp.PageCount} · {time.time() - t:.1f}초 · 저장: {a.output}", file=sys.stderr)
    elif not new and a.output.lower().endswith(".hwp") and is_ole(a.file) and             all(o.get("op") in ("replace", "fill") and not o.get("scope") for o in ops):
        import hwp5_direct      # 한컴 없이 HWP → HWP, 글자 바꾸기·누름틀 채우기만: 원본 레코드를 그대로 두고 글만 바꾼다
        for line in hwp5_direct.edit(a.file, a.output, ops):
            print(line, file=sys.stderr)
        print(f"HWP 직접 쓰기 · {time.time() - t:.1f}초 · 저장: {a.output}", file=sys.stderr)
    else:
        import hwpx_direct
        to_hwp = a.output.lower().endswith(".hwp")
        target = a.output + ".tmp.hwpx" if to_hwp else a.output
        for line in hwpx_direct.edit(None if new else a.file, target, ops, keep_going=a.keep_going):
            print(line, file=sys.stderr)
        if to_hwp:                               # HWPX로 고친 뒤 우리 변환기로 HWP(한컴 없이). 원본이 HWP면 hwp5_to_hwpx → 고치기 → hwpx2hwp
            hwpx_to_hwp_direct(target, a.output)
            os.remove(target)
        print(f"직접 경로 · {time.time() - t:.1f}초 · 저장: {a.output}", file=sys.stderr)
    if engine != "com" and a.output.lower().endswith((".hwp", ".hwpx")):
        if layout_mode(a) == "hancom":
            relayout_in_place(a.output)
            print("줄 배치: 한컴으로 다시 짜서 써 둠(--layout none이면 생략)", file=sys.stderr)
        else:
            print("줄 배치: 없음 — 한컴 데스크톱은 열 때 다시 짠다. 한컴 뷰어·웹 한글·모바일은 확인 안 됨", file=sys.stderr)
    if a.check:
        check_report(None if new else a.file, a.output, a.check)


def check_report(src, out, folder):
    import hwp_check
    if not hancom_installed():
        print("검증: 한컴이 없어 쪽 그림·다시 열기를 못 합니다.", file=sys.stderr)
        return
    os.makedirs(folder, exist_ok=True)
    rep = {"다시 열기": hwp_check.reopen(out)}
    if src:
        rep["쪽 비교"] = hwp_check.compare(src, out, folder)
    else:
        rep["쪽 그림"] = hwp_check.render(out, folder)
    if out.lower().endswith((".hwp", ".hwpx")):
        rep["왕복"] = hwp_check.roundtrip(out, os.path.join(folder, "왕복"))
    open(os.path.join(folder, "검증.json"), "w", encoding="utf-8").write(json.dumps(rep, ensure_ascii=False, indent=1))
    print(json.dumps(rep, ensure_ascii=False, indent=1))


def cmd_render(a):
    import hwp_check
    pages = None
    if a.pages:
        lo, _, hi = a.pages.partition("-")
        pages = set(range(int(lo), int(hi or lo) + 1))
    for p in hwp_check.render(a.file, a.output, a.dpi, pages):
        print(p)


def cmd_compare(a):
    import hwp_check
    print(json.dumps(hwp_check.compare(a.file, a.other, a.output, a.dpi), ensure_ascii=False, indent=1))


def cmd_check(a):
    import hwp_check
    rep = {"다시 열기": hwp_check.reopen(a.file)}
    if a.roundtrip:
        rep["왕복"] = hwp_check.roundtrip(a.file, a.roundtrip)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


def cmd_outline(a):
    """본문 문단 번호(0부터)와 표 번호(1부터, 문서 순서·칸 안 표 포함). edit의 para·table 번호와 같다."""
    import hwpx_direct
    print(json.dumps(hwpx_direct.outline(a.file), ensure_ascii=False, indent=1))


def cmd_actions(a):
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "data", "actions.json")
    if not os.path.exists(path):
        sys.exit("액션 표(data/actions.json)는 이 배포에 들어 있지 않습니다. 한컴이 공개한 '한글 오토메이션 액션 테이블'을 보세요.")
    data = json.load(open(path, encoding="utf-8"))
    q = (a.query or "").lower()
    for name, v in sorted(data["actions"].items()):
        if q and q not in name.lower() and q not in (v.get("set") or "").lower():
            continue
        line = name + (f"  [셋 {v['set']}]" if v.get("set") else "")
        if a.items and v.get("set") in data["sets"]:
            items = data["sets"][v["set"]]["items"]
            line += "\n    " + ", ".join(f"{k}:{x['type']}" + (f"={x['default']}" if "default" in x else "") for k, x in items.items())
        print(line)


def may_use_hancom(a):
    """이 명령이 한컴(COM)을 띄울 수 있으면 True. 한컴이 없거나 직접 경로로 끝나는 명령은 False."""
    if a.cmd in ("actions", "outline") or not hancom_installed():
        return False
    if a.cmd == "write" and getattr(a, "polish", False):
        return True
    if a.cmd == "edit":                 # 직접 경로라도 줄 배치(--layout auto|hancom)는 한컴을 쓴다
        return bool(a.check) or edit_engine(a, load_ops(a)) == "com" or layout_mode(a) == "hancom"
    if getattr(a, "engine", "auto") == "direct" and not getattr(a, "check", None):
        return False
    if a.cmd == "info":
        return a.pages
    return True


def main():
    sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "exam":       # 시험지 만들기·점검·고치기·비교(hwp_exam.py) — 한컴 줄 배치를 쓰므로 숨긴 데스크톱에서
        if hancom_installed():
            import hwp_hidden
            hwp_hidden.reexec_hidden()
        import hwp_exam
        sys.exit(hwp_exam.main(sys.argv[2:]))
    ap = argparse.ArgumentParser(description="HWP·HWPX 읽기·쓰기·고치기·변환")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("read"); p.add_argument("file"); p.add_argument("--format", choices=["md", "text", "json"], default="md")
    p.add_argument("--engine", choices=["auto", "direct", "com"], default="auto"); p.add_argument("-o", "--output")
    p = sub.add_parser("info"); p.add_argument("file"); p.add_argument("--pages", action="store_true", help="한컴으로 쪽수도")
    p = sub.add_parser("replace"); p.add_argument("file"); p.add_argument("--find", action="append", required=True)
    p.add_argument("--to", action="append", required=True); p.add_argument("-o", "--output", required=True)
    p = sub.add_parser("fields"); p.add_argument("file")
    p = sub.add_parser("fill"); p.add_argument("file"); p.add_argument("--field", action="append", required=True)
    p.add_argument("-o", "--output", required=True)
    p = sub.add_parser("export-json"); p.add_argument("file"); p.add_argument("-o", "--output")
    p = sub.add_parser("import-json"); p.add_argument("file"); p.add_argument("-o", "--output", required=True)
    p = sub.add_parser("write"); p.add_argument("file"); p.add_argument("-o", "--output", required=True)
    p.add_argument("--engine", choices=["auto", "com", "direct"], default="auto", help="direct = 한컴 없이(Markdown만)")
    p.add_argument("--style", choices=["report", "paper"], default="report", help="report=보고서 · paper=2단 논문(요약·3선 표·각주·참고문헌)")
    p.add_argument("--header", help="머리말 글(기본: 첫 제목)")
    p.add_argument("--polish", action="store_true", help="한컴이 있으면 줄 끝 낱말 끊김을 자간으로 맞춘다(숨긴 데스크톱)")
    p = sub.add_parser("convert"); p.add_argument("file"); p.add_argument("-o", "--output", required=True)
    p.add_argument("--engine", choices=["auto", "com", "direct"], default="auto",
                   help="direct = 한컴 없이(HWPX ↔ HWP는 우리 변환기 hwpx2hwp·hwp5_to_hwpx). auto = 한컴이 있으면 한컴")
    p = sub.add_parser("edit", help="ops(JSON) 차례로 적용: 글자·문단·스타일·표·그림·머리말·쪽·누름틀·각주·목록·바꾸기·합치기")
    p.add_argument("file", help="고칠 파일, 새 문서면 new"); p.add_argument("-o", "--output", required=True)
    p.add_argument("--ops", help="ops JSON 파일([{...}, ...])"); p.add_argument("--op", action="append", help="ops 하나(JSON 글)")
    p.add_argument("--engine", choices=["auto", "com", "direct"], default="auto")
    p.add_argument("--keep-going", action="store_true", help="실패한 명령이 있어도 계속하고 저장")
    p.add_argument("--check", metavar="폴더", help="저장 뒤 쪽 그림 비교·다시 열기·HWP↔HWPX 왕복 결과를 이 폴더에")
    p.add_argument("--layout", choices=["auto", "hancom", "none"], default="auto",
                   help="직접 경로 결과의 줄 배치: auto=한컴이 있으면 한컴으로 다시 짜서 써 둠, none=줄 배치 없이 둠")
    p = sub.add_parser("render"); p.add_argument("file"); p.add_argument("-o", "--output", required=True)
    p.add_argument("--dpi", type=int, default=80); p.add_argument("--pages", help="예: 1-3")
    p = sub.add_parser("compare"); p.add_argument("file"); p.add_argument("other"); p.add_argument("-o", "--output", required=True)
    p.add_argument("--dpi", type=int, default=70)
    p = sub.add_parser("check"); p.add_argument("file"); p.add_argument("--roundtrip", metavar="폴더")
    p = sub.add_parser("outline"); p.add_argument("file")
    p = sub.add_parser("actions"); p.add_argument("query", nargs="?"); p.add_argument("--items", action="store_true")
    a = ap.parse_args()
    if may_use_hancom(a):
        import hwp_hidden
        hwp_hidden.reexec_hidden()      # 한컴을 쓰는 명령은 숨긴 데스크톱에서(한글 창이 사람 화면에 뜨지 않게)
    {"read": cmd_read, "info": cmd_info, "replace": cmd_replace, "fields": cmd_fields, "fill": cmd_fill,
     "export-json": cmd_export_json, "import-json": cmd_import_json, "write": cmd_write, "convert": cmd_convert,
     "edit": cmd_edit, "render": cmd_render, "compare": cmd_compare, "check": cmd_check, "outline": cmd_outline,
     "actions": cmd_actions}[a.cmd](a)


if __name__ == "__main__":
    main()
