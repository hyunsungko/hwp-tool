"""HWPX 직접 경로 — 한컴 없이 XML을 고친다. 명령(ops)은 한컴 경로(hwp_com.py)와 같은 이름·인자.

근거: 같은 기본 문서에 한컴으로 기능 하나씩 적용해 저장한 HWPX들의 XML 차이. 한컴이 쓰는 모양을 그대로 따른다.
- 문단은 '글자·표시·개체' 목록으로 펼쳐 고친 뒤 같은 글자 모양끼리 run으로 다시 묶는다.
- 고친 문단의 배치 정보(linesegarray)는 지운다 → 한컴이 열 때 다시 짠다.
- 용지 방향: OWPML은 세로가 WIDELY, 가로가 NARROWLY(폭·높이 값은 세로 기준 그대로).
"""
import copy
import datetime
import hashlib
import io
import os
import random
import re
import struct
import zipfile
import zlib

from lxml import etree

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HH = "{http://www.hancom.co.kr/hwpml/2011/head}"
HC = "{http://www.hancom.co.kr/hwpml/2011/core}"
HS = "{http://www.hancom.co.kr/hwpml/2011/section}"
OPF = "{http://www.idpf.org/2007/opf/}"
NSMAP = {"hp": HP[1:-1], "hh": HH[1:-1], "hc": HC[1:-1], "hs": HS[1:-1], "opf": OPF[1:-1]}
HERE = os.path.dirname(os.path.abspath(__file__))
BLANK = os.path.join(HERE, "data", "blank.hwpx")
HWPUNIT_PER_MM = 7200 / 25.4
LANGS = ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")
FONT_LANGS = ("HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER")
MARKS = {"tab": "\t", "lineBreak": "\n", "nbSpace": " ", "fwSpace": "　", "hyphen": "-"}
OBJ = "￼"     # 찾기 글에서 개체(표·그림·컨트롤) 자리


def mm(v):
    return int(round(float(v) * HWPUNIT_PER_MM))


def hexcolor(v):
    if isinstance(v, str):
        return "#" + v.lstrip("#").upper()
    r, g, b = v
    return f"#{r:02X}{g:02X}{b:02X}"


def local(tag):
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def rid():
    return str(random.randint(1, 2**31 - 1))


def addr(a):
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", a.strip())
    if not m:
        raise ValueError("칸 주소 형식: A1, B3 …  받은 값: " + a)
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + ord(ch) - 64
    return col - 1, int(m.group(2)) - 1      # (열, 행) 0부터


def cell_name(c, r):
    s, c = "", c + 1
    while c:
        c, k = divmod(c - 1, 26)
        s = chr(65 + k) + s
    return f"{s}{r + 1}"


# ───────────────────────── 문단 펼치기·묶기 ─────────────────────────
class Item:
    """문단 한 칸: kind = ch(글자) | mark(탭 등 hp:t 안 표시) | obj(개체·컨트롤) | empty(빈 hp:t)."""
    __slots__ = ("kind", "val", "run")

    def __init__(self, kind, val, run):
        self.kind, self.val, self.run = kind, val, run   # run = run 속성 dict(charPrIDRef 등)

    @property
    def text(self):
        if self.kind == "ch":
            return self.val
        if self.kind == "mark":
            return MARKS.get(local(self.val.tag), "")
        return OBJ if self.kind == "obj" else ""


def explode(p):
    items = []
    for run in p.findall(HP + "run"):
        ra = dict(run.attrib)
        for ch in run:
            if ch.tag == HP + "t":
                if not (ch.text or len(ch)):
                    items.append(Item("empty", None, ra))
                for c in ch.text or "":
                    items.append(Item("ch", c, ra))
                for sub in ch:
                    tail = sub.tail
                    sub = copy.deepcopy(sub)
                    sub.tail = None
                    items.append(Item("mark", sub, ra))
                    for c in tail or "":
                        items.append(Item("ch", c, ra))
            else:
                items.append(Item("obj", ch, ra))
    return items


def implode(p, items):
    """items로 p의 run들을 다시 만든다(같은 run 속성끼리 묶음). linesegarray는 지운다."""
    for run in p.findall(HP + "run"):
        p.remove(run)
    for ls in p.findall(HP + "linesegarray"):
        p.remove(ls)
    anchor = 0
    runs, cur, key = [], None, None
    for it in items:
        k = tuple(sorted(it.run.items()))
        if cur is None or k != key:
            cur = etree.Element(HP + "run", dict(it.run))
            runs.append(cur)
            key, t = k, None
        if it.kind == "obj":
            cur.append(it.val)
            t = None
        elif it.kind == "empty":
            cur.append(etree.Element(HP + "t"))
            t = None
        else:
            if t is None:
                t = etree.SubElement(cur, HP + "t")
            if it.kind == "ch":
                if len(t):
                    last = t[-1]
                    last.tail = (last.tail or "") + it.val
                else:
                    t.text = (t.text or "") + it.val
            else:
                t.append(copy.deepcopy(it.val))
    if not runs:
        runs = [etree.Element(HP + "run", {"charPrIDRef": "0"})]
        etree.SubElement(runs[0], HP + "t")
    for i, r in enumerate(runs):
        p.insert(anchor + i, r)


def para_text(p):
    return "".join(i.text for i in explode(p))


# ───────────────────────── 문서 ─────────────────────────
class Hwpx:
    def __init__(self, path=None):
        src = path or BLANK
        with zipfile.ZipFile(src) as z:
            self.infos = z.infolist()
            self.files = {i.filename: z.read(i.filename) for i in self.infos}
        if "Contents/header.xml" not in self.files:
            raise ValueError("HWPX가 아닙니다(Contents/header.xml 없음): " + src)
        self.new = path is None
        self.header = etree.fromstring(self.files["Contents/header.xml"])
        self.hpf = etree.fromstring(self.files["Contents/content.hpf"])
        self.sec_names = sorted((n for n in self.files if re.match(r"Contents/section\d+\.xml$", n)),
                                key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        self.sections = [etree.fromstring(self.files[n]) for n in self.sec_names]
        self.added = {}
        self.relayout_all = False
        self._cache = {}

    # ---- 저장 ----
    def save(self, out):
        if self.relayout_all:                    # True = 모두, "page" = 쪽 너비를 따르는 문단만
            for sec in self.sections:
                for ls in list(sec.iter(HP + "linesegarray")):
                    if self.relayout_all == "page" and fixed_width(ls.getparent()):
                        continue
                    ls.getparent().remove(ls)
        # 표에 rowCnt·colCnt가 없으면 한컴이 파일을 열지 못한다→ 채워 둔다
        for sec in self.sections:
            for t in sec.iter(HP + "tbl"):
                trs = t.findall(HP + "tr")
                if not t.get("rowCnt"):
                    t.set("rowCnt", str(len(trs)))
                if not t.get("colCnt"):
                    t.set("colCnt", str(max((sum(int((tc.find(HP + "cellSpan") if tc.find(HP + "cellSpan") is not None
                                                     else {}).get("colSpan", 1)) for tc in tr.findall(HP + "tc"))
                                             for tr in trs), default=0)))
        decl = b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        ser = lambda root: decl + etree.tostring(root, encoding="UTF-8")
        files = dict(self.files)
        files["Contents/header.xml"] = ser(self.header)
        for n, root in zip(self.sec_names, self.sections):
            files[n] = ser(root)
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for m in self.hpf.iter(OPF + "meta"):
            if m.get("name") == "ModifiedDate":
                m.text = now
            if self.new and m.get("name") in ("creator", "lastsaveby"):
                m.text = ""
            if self.new and m.get("name") == "CreatedDate":
                m.text = now
        files["Contents/content.hpf"] = ser(self.hpf)
        files["Preview/PrvText.txt"] = self.plain_text()[:1024].encode("utf-16le") if "Preview/PrvText.txt" in files else None
        files.update(self.added)
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        order = [i.filename for i in self.infos] + [n for n in self.sec_names if n not in self.files] +             [n for n in self.added if n not in self.files]
        with zipfile.ZipFile(out, "w") as z:
            for n in order:
                data = files.get(n)
                if data is None:
                    continue
                stored = n in ("mimetype", "version.xml") or n.endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp"))
                z.writestr(n, data, compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)

    def plain_text(self):
        out = []
        for sec in self.sections:
            for p in sec.findall(HP + "p"):
                out.append(para_text(p).replace(OBJ, ""))
        return "\r\n".join(out)

    # ---- 찾아다니기 ----
    def body(self):
        return [p for sec in self.sections for p in sec.findall(HP + "p")]

    def all_paras(self):
        return [p for sec in self.sections for p in sec.iter(HP + "p")]

    def tables(self):
        return [t for sec in self.sections for t in sec.iter(HP + "tbl")]

    def table(self, n):
        tb = self.tables()
        if not 1 <= int(n) <= len(tb):
            raise ValueError(f"표 {n}번이 없습니다(표 {len(tb)}개)")
        return Table(self, tb[int(n) - 1])

    def owner_para(self, el):
        """el을 품은 가장 가까운 문단."""
        a = el.getparent()
        while a is not None and a.tag != HP + "p":
            a = a.getparent()
        return a

    def touch(self, p):
        """고친 문단과 그 문단을 품은 문단들의 배치 정보를 지운다."""
        while p is not None:
            for ls in p.findall(HP + "linesegarray"):
                p.remove(ls)
            p = self.owner_para(p)

    def matches(self, text, regex=False, paras=None):
        """(문단, 시작, 끝) 목록. 개체 자리를 넘는 찾기는 안 된다."""
        rx = re.compile(text if regex else re.escape(text))
        out = []
        for p in (paras if paras is not None else self.all_paras()):
            s = para_text(p)
            for m in rx.finditer(s):
                if m.end() > m.start() and OBJ not in m.group(0):
                    out.append((p, m.start(), m.end()))
        return out

    # ---- 머리(header.xml) ----
    def reflist(self, name):
        rl = self.header.find(HH + "refList")
        el = rl.find(HH + name)
        if el is None:
            order = ["fontfaces", "borderFills", "charProperties", "tabProperties", "numberings", "bullets",
                     "paraProperties", "styles", "memoProperties", "trackChanges", "trackChangeAuthors"]
            el = etree.Element(HH + name, {"itemCnt": "0"})
            pos = 0
            for i, ch in enumerate(rl):
                if local(ch.tag) in order and order.index(local(ch.tag)) < order.index(name):
                    pos = i + 1
            rl.insert(pos, el)
        return el

    def _add(self, listname, el):
        lst = self.reflist(listname)
        lst.append(el)
        lst.set("itemCnt", str(len(lst)))

    def _find_same(self, listname, el):
        key = etree.tostring(el).replace(b' id="' + el.get("id", "").encode() + b'"', b"")
        for x in self.reflist(listname):
            if etree.tostring(x).replace(b' id="' + x.get("id", "").encode() + b'"', b"") == key:
                return x.get("id")
        return None

    def _derive(self, listname, tag, base_id, change):
        lst = self.reflist(listname)
        base = next((x for x in lst if x.get("id") == str(base_id)), None)
        if base is None:
            base = lst[0]
        el = copy.deepcopy(base)
        change(el)
        new_id = str(max([int(x.get("id")) for x in lst] + [-1]) + 1)
        el.set("id", new_id)
        same = self._find_same(listname, el)
        if same is not None:
            return same
        self._add(listname, el)
        return new_id

    def font_id(self, face, lang):
        ff = next((f for f in self.reflist("fontfaces") if f.get("lang") == lang), None)
        for f in ff:
            if f.get("face") == face:
                return f.get("id")
        nid = str(len(ff.findall(HH + "font")))
        etree.SubElement(ff, HH + "font", {"id": nid, "face": face, "type": "TTF", "isEmbedded": "0"})
        ff.set("fontCnt", str(len(ff.findall(HH + "font"))))
        return nid

    def charpr(self, base_id, o):
        """base_id 글자 모양에 o(font·size·color·bold…)를 적용한 글자 모양 id."""
        key = ("cp", str(base_id), tuple(sorted((k, str(v)) for k, v in o.items())))
        if key in self._cache:
            return self._cache[key]

        def change(el):
            if "size" in o:
                el.set("height", str(int(round(float(o["size"]) * 100))))
            if "color" in o:
                el.set("textColor", hexcolor(o["color"]))
            if "shade" in o:
                el.set("shadeColor", hexcolor(o["shade"]) if o["shade"] else "none")
            if "font" in o:
                fr = el.find(HH + "fontRef")
                for lang, LANG in zip(LANGS, FONT_LANGS):
                    fr.set(lang, self.font_id(o["font"], LANG))
            for k, child in (("ratio", "ratio"), ("spacing", "spacing")):
                if k in o:
                    c = el.find(HH + child)
                    for lang in LANGS:
                        c.set(lang, str(int(o[k])))
            if "spacing_add" in o:          # 자간을 지금 값에서 더하고 뺀다(%, 한컴 범위 -50~50)
                c = el.find(HH + "spacing")
                for lang in LANGS:
                    c.set(lang, str(max(-50, min(50, int(c.get(lang, "0")) + int(round(float(o["spacing_add"])))))))
            order = ["fontRef", "ratio", "spacing", "relSz", "offset", "italic", "bold", "underline", "strikeout",
                     "outline", "shadow", "emboss", "engrave", "supscript", "subscript"]

            def flag(name, on):
                cur = el.find(HH + name)
                if on and cur is None:
                    new = etree.Element(HH + name)
                    idx = order.index(name)
                    pos = len(el)
                    for i, ch in enumerate(el):
                        if local(ch.tag) in order and order.index(local(ch.tag)) > idx:
                            pos = i
                            break
                    el.insert(pos, new)
                elif not on and cur is not None:
                    el.remove(cur)
            for k, name in (("bold", "bold"), ("italic", "italic"), ("superscript", "supscript"), ("subscript", "subscript")):
                if k in o:
                    flag(name, bool(o[k]))
            if "underline" in o:
                u = el.find(HH + "underline")
                if u is not None:
                    u.set("type", "BOTTOM" if o["underline"] else "NONE")
            if "strike" in o:
                s = el.find(HH + "strikeout")
                if s is not None:
                    s.set("shape", "SOLID" if o["strike"] else "NONE")
        new = self._derive("charProperties", "charPr", base_id, change)
        self._cache[key] = new
        return new

    ALIGN = {"justify": "JUSTIFY", "left": "LEFT", "right": "RIGHT", "center": "CENTER",
             "distribute": "DISTRIBUTE", "divide": "DISTRIBUTE_SPACE"}

    def parapr(self, base_id, o):
        key = ("pp", str(base_id), tuple(sorted((k, str(v)) for k, v in o.items())))
        if key in self._cache:
            return self._cache[key]

        def change(el):
            if "align" in o:
                el.find(HH + "align").set("horizontal", self.ALIGN[o["align"]])
            vals = {}
            for k, tag in (("indent_mm", "intent"), ("left_mm", "left"), ("right_mm", "right")):
                if k in o:
                    vals[tag] = mm(o[k])
            for k, tag in (("before_pt", "prev"), ("after_pt", "next")):
                if k in o:
                    vals[tag] = int(round(float(o[k]) * 100))
            # 한컴은 같은 값을 두 번 쓴다: <hp:switch><hp:case HwpUnitChar> HWPUNIT, <hp:default> 그 2배
            for m in el.iter(HH + "margin"):
                factor = 2 if m.getparent().tag == HP + "default" else 1
                for tag, v in vals.items():
                    c = m.find(HC + tag)
                    if c is not None:
                        c.set("value", str(v * factor))
            if "line_spacing" in o:
                for ls in el.iter(HH + "lineSpacing"):
                    ls.set("type", "PERCENT")
                    ls.set("value", str(int(o["line_spacing"])))
            bs = el.find(HH + "breakSetting")
            for k, att in (("page_break_before", "pageBreakBefore"), ("keep_with_next", "keepWithNext"),
                           ("keep_lines", "keepLines"), ("widow_orphan", "widowOrphan")):
                if k in o and bs is not None:
                    bs.set(att, "1" if o[k] else "0")
            if "word_wrap" in o and bs is not None:
                # 한컴 이름이 거꾸로다(실측 2026-10-16): BREAK_WORD = 어절 단위, KEEP_WORD = 글자 단위
                bs.set("breakNonLatinWord", "BREAK_WORD" if o["word_wrap"] == "word" else "KEEP_WORD")
            if "heading" in o:
                h = el.find(HH + "heading")
                h.set("type", o["heading"][0])
                h.set("idRef", str(o["heading"][1]))
                h.set("level", str(o["heading"][2]))
        new = self._derive("paraProperties", "paraPr", base_id, change)
        self._cache[key] = new
        return new

    LINE = {"solid": "SOLID", "dash": "DASH", "dot": "DOT", "double": "DOUBLE_SLIM", "none": "NONE"}

    def borderfill(self, base_id, fill=Ellipsis, edges=None):
        """fill: None=배경 없앰, '#RRGGBB'=단색. edges: {left|right|top|bottom: (type, width, color)}."""
        def change(el):
            if fill is not Ellipsis:
                fb = el.find(HC + "fillBrush")
                if fb is not None:
                    el.remove(fb)
                if fill:
                    fb = etree.SubElement(el, HC + "fillBrush")
                    etree.SubElement(fb, HC + "winBrush", {"faceColor": hexcolor(fill), "hatchColor": "#999999", "alpha": "0"})
            for side, (t, w, c) in (edges or {}).items():
                b = el.find(HH + side + "Border")
                b.set("type", t)
                b.set("width", w)
                b.set("color", c)
        return self._derive("borderFills", "borderFill", base_id, change)

    def styles(self):
        return {s.get("name"): s for s in self.reflist("styles")}

    def style_by(self, name=None, eng=None):
        for s in self.reflist("styles"):
            if (name and s.get("name") == name) or (eng and s.get("engName") == eng):
                return s
        return None

    def base_style(self):
        """0번 스타일(바탕글). 각주·머리말 같은 스타일이 문서에 없으면 한컴은 이 스타일의 문단·글자 모양을 쓴다
        0번 문단·글자 모양은 쓰지 않는다."""
        return next((s for s in self.reflist("styles") if s.get("id") == "0"), None)

    def solid_borderfill(self):
        """네 변 실선 0.12mm, 배경 없는 테두리 id(없으면 만든다)."""
        for b in self.reflist("borderFills"):
            sides = [b.find(HH + s + "Border") for s in ("left", "right", "top", "bottom")]
            if all(x is not None and x.get("type") == "SOLID" for x in sides) and b.find(HC + "fillBrush") is None:
                return b.get("id")
        e = ("SOLID", "0.12 mm", "#000000")
        return self.borderfill(self.reflist("borderFills")[0].get("id"), fill=None,
                               edges={"left": e, "right": e, "top": e, "bottom": e})

    def ensure_list(self, kind):
        """글머리표(bullet)·번호(number) 정의 id. 한컴 기본값(시료 list_bullet·list_number)을 그대로 쓴다."""
        if kind == "bullet":
            lst = self.reflist("bullets")
            for b in lst:
                return b.get("id")
            nid = "1"
            b = etree.SubElement(lst, HH + "bullet", {"id": nid, "char": "", "useImage": "0"})
            etree.SubElement(b, HH + "paraHead", {"level": "0", "align": "LEFT", "useInstWidth": "0", "autoIndent": "1",
                                                  "widthAdjust": "0", "textOffsetType": "PERCENT", "textOffset": "50",
                                                  "numFormat": "DIGIT", "charPrIDRef": "4294967295", "checkable": "0"})
            lst.set("itemCnt", str(len(lst)))
            return nid
        lst = self.reflist("numberings")
        for n in lst:
            heads = n.findall(HH + "paraHead")
            if heads and (heads[0].text or "") == "^1.":
                return n.get("id")
        nid = str(max([int(x.get("id")) for x in lst] + [0]) + 1)
        n = etree.SubElement(lst, HH + "numbering", {"id": nid, "start": "0"})
        fmts = [("DIGIT", "^1."), ("HANGUL_SYLLABLE", "^2."), ("DIGIT", "^3)"), ("HANGUL_SYLLABLE", "^4)"),
                ("DIGIT", "(^5)"), ("HANGUL_SYLLABLE", "(^6)"), ("CIRCLED_DIGIT", "^7"), ("CIRCLED_HANGUL_SYLLABLE", "^8"),
                ("DIGIT", None), ("DIGIT", None)]
        for lv, (fmt, txt) in enumerate(fmts, 1):
            h = etree.SubElement(n, HH + "paraHead", {"start": "1", "level": str(lv), "align": "LEFT", "useInstWidth": "1",
                                                      "autoIndent": "1", "widthAdjust": "0", "textOffsetType": "PERCENT",
                                                      "textOffset": "50", "numFormat": fmt, "charPrIDRef": "4294967295",
                                                      "checkable": "1" if lv in (7, 8) else "0"})
            h.text = txt
        lst.set("itemCnt", str(len(lst)))
        return nid

    # ---- 구역 머리(secPr) ----
    def secprs(self):
        return [sec.find(f"{HP}p/{HP}run/{HP}secPr") for sec in self.sections]

    def put_head_ctrl(self, sec, ctrl):
        """구역 첫 문단에 머리말·꼬리말·쪽 번호 컨트롤을 넣는다. 한컴처럼 글 run 안, 글(hp:t) 바로 앞에 둔다.
        글 run이 따로 없으면(hwp2hwpx는 secPr·단·글을 run 하나에 씀) 그 run의 첫 hp:t 앞에 넣는다.
        컨트롤만 든 run을 새로 만들면 한컴 2024가 파일을 열지 못한다(2026-10-16 실측)."""
        p = sec.find(HP + "p")
        runs = p.findall(HP + "run")
        run = next((r for r in runs if r.find(HP + "secPr") is None), None)
        if run is None and runs:                  # 자식 없는 요소는 거짓이라 `or`로 잇지 않는다
            run = runs[0]
        if run is None:
            run = etree.SubElement(p, HP + "run", {"charPrIDRef": "0"})
        t = run.find(HP + "t")
        if t is None:
            run.append(ctrl)
            etree.SubElement(run, HP + "t")
        else:
            t.addprevious(ctrl)
        self.touch(p)
        return p

    def text_width(self, secpr):
        pp = secpr.find(HP + "pagePr")
        m = pp.find(HP + "margin")
        w, h = int(pp.get("width")), int(pp.get("height"))
        if pp.get("landscape") == "NARROWLY":
            w, h = h, w
        return w - int(m.get("left")) - int(m.get("right")) - int(m.get("gutter", 0))


# ───────────────────────── 표 ─────────────────────────
class Table:
    def __init__(self, doc, el):
        self.doc, self.el = doc, el
        self.reload()

    def reload(self):
        self.rows = int(self.el.get("rowCnt"))
        self.cols = int(self.el.get("colCnt"))
        self.cells = []
        for tr in self.el.findall(HP + "tr"):
            for tc in tr.findall(HP + "tc"):
                a, s, z = tc.find(HP + "cellAddr"), tc.find(HP + "cellSpan"), tc.find(HP + "cellSz")
                self.cells.append({"tc": tc, "c": int(a.get("colAddr")), "r": int(a.get("rowAddr")),
                                   "cs": int(s.get("colSpan")), "rs": int(s.get("rowSpan")),
                                   "w": int(z.get("width")), "h": int(z.get("height"))})

    def at(self, name):
        c, r = addr(name)
        for x in self.cells:
            if x["c"] == c and x["r"] == r:
                return x
        for x in self.cells:
            if x["c"] <= c < x["c"] + x["cs"] and x["r"] <= r < x["r"] + x["rs"]:
                raise ValueError(f"칸 {name}은 합친 칸 {cell_name(x['c'], x['r'])}에 들어 있습니다. 그 주소를 쓰세요.")
        raise ValueError(f"칸 {name}이 없습니다(표 {self.cols}열×{self.rows}행)")

    def block(self, rng):
        a, _, b = rng.upper().partition(":")
        b = b or a
        (c1, r1), (c2, r2) = addr(a), addr(b)
        c1, c2 = min(c1, c2), max(c1, c2)
        r1, r2 = min(r1, r2), max(r1, r2)
        out = [x for x in self.cells if c1 <= x["c"] <= c2 and r1 <= x["r"] <= r2]
        return out, (c1, r1, c2, r2)

    def col_width(self, c):
        for x in self.cells:
            if x["c"] == c and x["cs"] == 1:
                return x["w"]
        return int(self.el.find(HP + "sz").get("width")) // max(self.cols, 1)

    def row_height(self, r):
        for x in self.cells:
            if x["r"] == r and x["rs"] == 1:
                return x["h"]
        return 282

    def write_back(self):
        """self.cells(주소·합침·크기)로 tr/tc를 다시 배열한다."""
        for tr in self.el.findall(HP + "tr"):
            self.el.remove(tr)
        rows = [[] for _ in range(self.rows)]
        for x in sorted(self.cells, key=lambda x: (x["r"], x["c"])):
            tc = x["tc"]
            tc.find(HP + "cellAddr").set("colAddr", str(x["c"]))
            tc.find(HP + "cellAddr").set("rowAddr", str(x["r"]))
            tc.find(HP + "cellSpan").set("colSpan", str(x["cs"]))
            tc.find(HP + "cellSpan").set("rowSpan", str(x["rs"]))
            tc.find(HP + "cellSz").set("width", str(x["w"]))
            tc.find(HP + "cellSz").set("height", str(x["h"]))
            rows[x["r"]].append(tc)
        pos = list(self.el).index(self.el.find(HP + "inMargin")) + 1 if self.el.find(HP + "inMargin") is not None else len(self.el)
        for i, tcs in enumerate(rows):
            tr = etree.Element(HP + "tr")
            for tc in tcs:
                tr.append(tc)
            self.el.insert(pos + i, tr)
        self.el.set("rowCnt", str(self.rows))
        self.el.set("colCnt", str(self.cols))
        sz = self.el.find(HP + "sz")
        sz.set("width", str(sum(self.col_width(c) for c in range(self.cols))))
        sz.set("height", str(sum(self.row_height(r) for r in range(self.rows))))
        for p in self.el.iter(HP + "p"):
            for ls in p.findall(HP + "linesegarray"):
                p.remove(ls)
        self.doc.touch(self.doc.owner_para(self.el))
        self.reload()

    def new_cell(self, like, c, r, w, h):
        tc = copy.deepcopy(like["tc"])
        sub = tc.find(HP + "subList")
        ps = sub.findall(HP + "p")
        for extra in ps[1:]:
            sub.remove(extra)
        p = ps[0]
        items = [i for i in explode(p) if False]
        first_run = p.find(HP + "run")
        ra = dict(first_run.attrib) if first_run is not None else {"charPrIDRef": "0"}
        implode(p, [Item("empty", None, ra)])
        return {"tc": tc, "c": c, "r": r, "cs": 1, "rs": 1, "w": w, "h": h}

    def match_edge(self, cell, like, src, sides):
        """cell의 sides 변을 like의 src 변과 같게(줄·칸 넣기에서 한컴이 맞닿은 변을 맞추는 방식)."""
        bf = next((b for b in self.doc.reflist("borderFills") if b.get("id") == like["tc"].get("borderFillIDRef")), None)
        e = bf.find(HH + src + "Border") if bf is not None else None
        if e is None:
            return
        edge = (e.get("type"), e.get("width"), e.get("color"))
        cell["tc"].set("borderFillIDRef", self.doc.borderfill(cell["tc"].get("borderFillIDRef"), edges={k: edge for k in sides}))

    def insert_row(self, ref, below=True, count=1):
        x = self.at(ref)
        at_row = x["r"] + x["rs"] if below else x["r"]
        for _ in range(count):
            h = self.row_height(min(at_row - (1 if below else 0), self.rows - 1))
            for y in self.cells:
                if y["r"] >= at_row:
                    y["r"] += 1
            newcells = []
            c = 0
            while c < self.cols:
                span = next((y for y in self.cells if y["c"] <= c < y["c"] + y["cs"] and y["r"] < at_row < y["r"] + y["rs"] + (1 if y["r"] < at_row and y["r"] + y["rs"] > at_row else 0) and y["r"] + y["rs"] > at_row), None)
                if span is not None and span["r"] < at_row:
                    span["rs"] += 1
                    c += span["cs"]
                    continue
                # 한컴: 기준 칸(아래에 넣으면 위 칸, 위에 넣으면 아래 칸)을 복사하고, 새 칸의 위·아래 변 = 기준 칸의 맞닿은 변
                above = [y for y in self.cells if y["c"] <= c < y["c"] + y["cs"] and y["r"] + y["rs"] == at_row]
                under = [y for y in self.cells if y["c"] <= c < y["c"] + y["cs"] and y["r"] == at_row + 1]
                like = (((above or under) if below else (under or above)) or [self.cells[0]])[0]
                newcells.append(self.new_cell(like, c, at_row, self.col_width(c), h))
                if like["r"] + like["rs"] == at_row:
                    self.match_edge(newcells[-1], like, "bottom", ("top", "bottom"))
                elif like["r"] == at_row + 1:
                    self.match_edge(newcells[-1], like, "top", ("top", "bottom"))
                c += 1
            self.cells += newcells
            self.rows += 1
        self.write_back()

    def insert_col(self, ref, right=True, count=1):
        x = self.at(ref)
        at_col = x["c"] + x["cs"] if right else x["c"]
        for _ in range(count):
            w = self.col_width(min(at_col - (1 if right else 0), self.cols - 1))
            for y in self.cells:
                if y["c"] >= at_col:
                    y["c"] += 1
            newcells = []
            r = 0
            while r < self.rows:
                span = next((y for y in self.cells if y["r"] <= r < y["r"] + y["rs"] and y["c"] < at_col < y["c"] + y["cs"] + 0 and y["c"] + y["cs"] > at_col), None)
                if span is not None:
                    span["cs"] += 1
                    span["w"] += w
                    r += span["rs"]
                    continue
                # 한컴: 기준 칸(오른쪽에 넣으면 왼쪽 칸, 왼쪽에 넣으면 오른쪽 칸)을 그대로 복사하고(합친 줄 수도 같게),
                # 기준 칸의 맞닿은 변은 기준 칸의 반대쪽 변으로 바꾼다
                left_ = [y for y in self.cells if y["r"] <= r < y["r"] + y["rs"] and y["c"] + y["cs"] == at_col]
                right_ = [y for y in self.cells if y["r"] <= r < y["r"] + y["rs"] and y["c"] == at_col + 1]
                like = (((left_ or right_) if right else (right_ or left_)) or [self.cells[0]])[0]
                rs = like["rs"] if like["r"] == r else 1
                rs = min(rs, self.rows - r)
                cell = self.new_cell(like, at_col, r, w, like["h"] if rs > 1 else self.row_height(r))
                cell["rs"] = rs
                newcells.append(cell)
                if like["c"] + like["cs"] == at_col:
                    self.match_edge(like, like, "left", ("right",))
                elif like["c"] == at_col + 1:
                    self.match_edge(like, like, "right", ("left",))
                r += rs
            self.cells += newcells
            self.cols += 1
        self.write_back()

    def delete_row(self, ref):
        x = self.at(ref)
        r = x["r"]
        h = self.row_height(r)
        # 한컴: 지운 줄 첫 칸의 위 변이 새 경계 변이 된다 — 맨 윗줄이면 아래 칸의 위 변, 아니면 위 칸의 아래 변
        first = min((y for y in self.cells if y["r"] == r), key=lambda y: y["c"], default=None)
        if first is not None:
            nbs = [(y, "bottom") for y in self.cells if y["r"] + y["rs"] == r] or \
                  [(y, "top") for y in self.cells if y["r"] == r + 1]
            for y, side in nbs:
                self.match_edge(y, first, "top", (side,))
        keep = []
        for y in self.cells:
            if y["r"] == r:
                if y["rs"] > 1:      # 이 줄에서 시작해 아래로 합친 칸: 아래 줄로 내려 한 줄 줄인다
                    y["rs"] -= 1
                    y["h"] = max(y["h"] - h, 282)
                    keep.append(y)
                continue
            if y["r"] < r < y["r"] + y["rs"]:
                y["rs"] -= 1
                y["h"] = max(y["h"] - h, 282)
            keep.append(y)
        for y in keep:
            if y["r"] > r:
                y["r"] -= 1
        self.cells = keep
        self.rows -= 1
        if self.rows < 1:
            raise ValueError("마지막 줄은 지울 수 없습니다(표를 지우려면 delete)")
        self.collapse()
        self.write_back()

    def delete_col(self, ref):
        x = self.at(ref)
        c = x["c"]
        w = self.col_width(c)
        # 한컴: 지운 칸 첫 칸의 왼 변이 새 경계 변이 된다 — 맨 왼쪽 칸이면 오른쪽 칸의 왼 변, 아니면 왼쪽 칸의 오른 변
        first = min((y for y in self.cells if y["c"] == c), key=lambda y: y["r"], default=None)
        if first is not None:
            nbs = [(y, "right") for y in self.cells if y["c"] + y["cs"] == c] or \
                  [(y, "left") for y in self.cells if y["c"] == c + 1]
            for y, side in nbs:
                self.match_edge(y, first, "left", (side,))
        keep = []
        for y in self.cells:
            if y["c"] == c:
                if y["cs"] > 1:
                    y["cs"] -= 1
                    y["w"] = max(y["w"] - w, 100)
                    keep.append(y)
                continue
            if y["c"] < c < y["c"] + y["cs"]:
                y["cs"] -= 1
                y["w"] = max(y["w"] - w, 100)
            keep.append(y)
        for y in keep:
            if y["c"] > c:
                y["c"] -= 1
        self.cells = keep
        self.cols -= 1
        if self.cols < 1:
            raise ValueError("마지막 칸은 지울 수 없습니다")
        self.collapse()
        self.write_back()

    def collapse(self):
        """어느 칸도 시작하지 않는 줄(위에서 합친 칸만 지나가는 줄)은 한컴처럼 없앤다.
        칸도 같게 본다(줄 쪽만 실측)."""
        for r in range(self.rows - 1, 0, -1):
            if not any(y["r"] == r for y in self.cells):
                for y in self.cells:
                    if y["r"] < r < y["r"] + y["rs"]:
                        y["rs"] -= 1
                    elif y["r"] > r:
                        y["r"] -= 1
                self.rows -= 1
        for c in range(self.cols - 1, 0, -1):
            if not any(y["c"] == c for y in self.cells):
                for y in self.cells:
                    if y["c"] < c < y["c"] + y["cs"]:
                        y["cs"] -= 1
                    elif y["c"] > c:
                        y["c"] -= 1
                self.cols -= 1

    def merge(self, rng):
        cells, (c1, r1, c2, r2) = self.block(rng)
        for y in self.cells:
            inside = c1 <= y["c"] <= c2 and r1 <= y["r"] <= r2
            ends_out = y["c"] + y["cs"] - 1 > c2 or y["r"] + y["rs"] - 1 > r2
            crosses_in = (not inside) and y["c"] <= c2 and y["c"] + y["cs"] - 1 >= c1 and y["r"] <= r2 and y["r"] + y["rs"] - 1 >= r1
            if (inside and ends_out) or crosses_in:
                raise ValueError(f"{rng} 밖으로 걸친 합친 칸이 있어 합칠 수 없습니다")
        anchor = next((y for y in cells if y["c"] == c1 and y["r"] == r1), None)
        if anchor is None:
            raise ValueError(f"{rng}의 왼쪽 위 칸이 없습니다")
        sub = anchor["tc"].find(HP + "subList")
        for y in sorted(cells, key=lambda y: (y["r"], y["c"])):
            if y is anchor:
                continue
            for p in y["tc"].find(HP + "subList").findall(HP + "p"):
                if para_text(p).strip():
                    sub.append(p)
            self.cells.remove(y)
        anchor["w"] = sum(self.col_width(c) for c in range(c1, c2 + 1))
        anchor["h"] = sum(self.row_height(r) for r in range(r1, r2 + 1))
        anchor["cs"], anchor["rs"] = c2 - c1 + 1, r2 - r1 + 1
        self.write_back()

    def split(self, ref, rows=1, cols=2):
        x = self.at(ref)
        if rows != 1 and cols != 1:
            self.split(ref, rows=1, cols=cols)
            for k in range(cols):
                self.split(cell_name(x["c"] + k, x["r"]), rows=rows, cols=1)
            return
        if cols > 1:
            extra = max(0, cols - x["cs"])
            if extra:
                at = x["c"] + x["cs"]
                for y in self.cells:
                    if y is x:
                        continue
                    if y["c"] < at <= y["c"] + y["cs"] - 1 + 1 and y["c"] + y["cs"] >= at and y["c"] < at:
                        y["cs"] += extra
                    elif y["c"] >= at:
                        y["c"] += extra
                self.cols += extra
            total, n = x["w"], cols
            base_c, base_cs = x["c"], x["cs"] + extra
            spans = [base_cs // n + (1 if k < base_cs % n else 0) for k in range(n)]
            x["cs"], x["w"] = spans[0], total // n
            c = base_c + spans[0]
            for k in range(1, n):
                y = self.new_cell(x, c, x["r"], total // n, x["h"])
                y["cs"], y["rs"] = spans[k], x["rs"]
                self.cells.append(y)
                c += spans[k]
        elif rows > 1:
            extra = max(0, rows - x["rs"])
            if extra:
                at = x["r"] + x["rs"]
                for y in self.cells:
                    if y is x:
                        continue
                    if y["r"] < at and y["r"] + y["rs"] >= at:
                        y["rs"] += extra
                    elif y["r"] >= at:
                        y["r"] += extra
                self.rows += extra
            total, n = x["h"], rows
            base_r, base_rs = x["r"], x["rs"] + extra
            spans = [base_rs // n + (1 if k < base_rs % n else 0) for k in range(n)]
            x["rs"], x["h"] = spans[0], max(total // n, 282)
            r = base_r + spans[0]
            for k in range(1, n):
                y = self.new_cell(x, x["c"], r, x["w"], max(total // n, 282))
                y["rs"], y["cs"] = spans[k], x["cs"]
                self.cells.append(y)
                r += spans[k]
        self.write_back()

    def fill(self, rng, color):
        cells, _ = self.block(rng)
        for y in cells:
            tc = y["tc"]
            tc.set("borderFillIDRef", self.doc.borderfill(tc.get("borderFillIDRef"), fill=color or None))
        self.doc.touch(self.doc.owner_para(self.el))

    def border(self, rng, line="solid", width="0.12mm", color="#000000", sides="all"):
        """한컴처럼 칸 격자 한 칸 단위로 본다: 선택 칸(합친 칸은 통째)끼리 맞닿지 않은 변이 바깥이다.
        칸의 한 변이 모두 바깥이면 그 변을, 맞닿은 범위 밖 칸이 있으면 그 칸의 마주 보는 변도 칠한다.
        한 변이 일부만 바깥이면(합친 칸 옆) 그 칸의 변은 두고 범위 밖 이웃 칸의 변만 칠한다."""
        cells, _ = self.block(rng)
        w = re.sub(r"\s*mm$", "", str(width)).strip() + " mm"
        e = (self.doc.LINE.get(line, "SOLID"), w, hexcolor(color))
        pick = {"all": ["left", "right", "top", "bottom", "horz", "vert"], "outside": ["left", "right", "top", "bottom"],
                "inside": ["horz", "vert"]}.get(sides) or [s.strip() for s in sides.split(",")]
        owner = {}
        for y in self.cells:
            for c in range(y["c"], y["c"] + y["cs"]):
                for r in range(y["r"], y["r"] + y["rs"]):
                    owner[(c, r)] = y
        sel = {id(y) for y in cells}
        face = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}
        edits = {}                                # id(칸) → (칸, {변: 선})

        def put(y, side):
            edits.setdefault(id(y), (y, {}))[1][side] = e

        for y in cells:
            c2, r2 = y["c"] + y["cs"] - 1, y["r"] + y["rs"] - 1
            segs = {"left": [(y["c"] - 1, r) for r in range(y["r"], r2 + 1)],
                    "right": [(c2 + 1, r) for r in range(y["r"], r2 + 1)],
                    "top": [(c, y["r"] - 1) for c in range(y["c"], c2 + 1)],
                    "bottom": [(c, r2 + 1) for c in range(y["c"], c2 + 1)]}
            for side, cs in segs.items():
                inner_kind = "vert" if side in ("left", "right") else "horz"
                nbs = [owner.get(k) for k in cs]
                inner = [n is not None and id(n) in sel for n in nbs]
                mixed = any(inner) and not all(inner)
                if (all(not i for i in inner) and side in pick) or (all(inner) and inner_kind in pick) or \
                        (mixed and side in pick and inner_kind in pick):
                    put(y, side)
                if side in pick:
                    for n, i in zip(nbs, inner):
                        if n is not None and not i:
                            put(n, face[side])
        for y, edges in edits.values():
            y["tc"].set("borderFillIDRef", self.doc.borderfill(y["tc"].get("borderFillIDRef"), edges=edges))
        self.doc.touch(self.doc.owner_para(self.el))

    def align(self, rng, h=None, v=None):
        cells, _ = self.block(rng)
        for y in cells:
            sub = y["tc"].find(HP + "subList")
            if v:
                sub.set("vertAlign", {"top": "TOP", "middle": "CENTER", "bottom": "BOTTOM"}[v])
            if h:
                for p in sub.findall(HP + "p"):
                    p.set("paraPrIDRef", self.doc.parapr(p.get("paraPrIDRef"), {"align": h}))
                    self.doc.touch(p)
        self.doc.touch(self.doc.owner_para(self.el))

    def set_text(self, ref, text):
        y = self.at(ref)
        sub = y["tc"].find(HP + "subList")
        ps = sub.findall(HP + "p")
        first = ps[0]
        run = first.find(HP + "run")
        ra = dict(run.attrib) if run is not None else {"charPrIDRef": "0"}
        for p in ps:
            sub.remove(p)
        for line in str(text).split("\n"):
            p = etree.SubElement(sub, HP + "p", dict(first.attrib))
            implode(p, [Item("ch", c, ra) for c in line] or [Item("empty", None, ra)])
        self.doc.touch(self.doc.owner_para(self.el))


# ───────────────────────── 명령 ─────────────────────────
class Editor:
    def __init__(self, doc):
        self.d = doc

    def op(self, o):
        name = o["op"].replace("-", "_").replace(".", "_")
        f = getattr(self, "op_" + name, None)
        if f is None:
            raise ValueError(f"직접 경로에 없는 명령: {o['op']} (한컴이 있으면 --engine com)")
        return f(o)

    # ---- 대상 ----
    def _target_ranges(self, o):
        """[(문단, 시작, 끝)] — find(+nth) · para · table+cells · all."""
        d = self.d
        if "find" in o:
            ms = d.matches(o["find"], o.get("regex", False))
            if not ms:
                raise ValueError(f"'{o['find']}'을(를) 찾지 못했습니다")
            if "nth" in o:
                if int(o["nth"]) > len(ms):
                    raise ValueError(f"'{o['find']}'은 {len(ms)}곳뿐입니다")
                return [ms[int(o["nth"]) - 1]]
            return ms
        if "para" in o:
            ps = self._paras(o["para"])
            return [(p, 0, len(para_text(p))) for p in ps]
        if "table" in o:
            t = d.table(o["table"])
            cells, _ = t.block(o.get("cells") or o.get("cell") or "A1")
            out = []
            for y in cells:
                for p in y["tc"].find(HP + "subList").findall(HP + "p"):
                    out.append((p, 0, len(para_text(p))))
            return out
        if o.get("all"):
            return [(p, 0, len(para_text(p))) for p in d.all_paras()]
        raise ValueError("대상이 없습니다(find·para·table+cells·all 중 하나)")

    def _paras(self, spec):
        body = self.d.body()
        a, b = (spec, spec) if isinstance(spec, int) else (spec[0], spec[-1])
        if not (0 <= a <= b < len(body)):
            raise ValueError(f"문단 {spec}이 없습니다(본문 문단 {len(body)}개, 0부터)")
        return body[a:b + 1]

    def _group(self, ranges):
        by = {}
        for p, s, e in ranges:
            by.setdefault(id(p), (p, []))[1].append((s, e))
        return by.values()

    def _point(self, o):
        """넣을 자리: (문단, 위치). at·after·before·para_end·table+cell."""
        d = self.d
        if "table" in o and "cell" in o:
            y = d.table(o["table"]).at(o["cell"])
            p = y["tc"].find(HP + "subList").findall(HP + "p")[-1]
            return p, len(para_text(p))
        if "after" in o or "before" in o:
            key = "after" if "after" in o else "before"
            ms = d.matches(o[key])
            n = int(o.get("nth", 1))                 # 음수는 뒤에서부터(-1 = 마지막)
            if len(ms) < abs(n) or n == 0:
                raise ValueError(f"'{o[key]}'을(를) 찾지 못했습니다")
            p, s, e = ms[n - 1] if n > 0 else ms[n]
            return p, (e if key == "after" else s)
        if "para_end" in o:
            p = self._paras(int(o["para_end"]))[0]
            return p, len(para_text(p))
        body = d.body()
        if o.get("at") == "start":
            p = body[0]
            items = explode(p)
            k = 0
            while k < len(items) and items[k].kind == "obj" and local(items[k].val.tag) in ("secPr", "ctrl"):
                k += 1
            return p, k
        p = body[-1]
        return p, len(para_text(p))

    def _run_at(self, items, pos):
        for k in (pos - 1, pos):
            if 0 <= k < len(items) and items[k].kind in ("ch", "mark", "empty"):
                return dict(items[k].run)
        for it in items:
            if it.kind in ("ch", "empty"):
                return dict(it.run)
        return dict(items[0].run) if items else {"charPrIDRef": "0"}

    def _insert_items(self, p, pos, new):
        items = explode(p)
        items[pos:pos] = new
        items = [i for i in items if not (i.kind == "empty" and len(items) > 1 and any(j.kind == "ch" for j in items))] or items
        implode(p, items)
        self.d.touch(p)

    def _new_para_after(self, p, like=None):
        q = etree.Element(HP + "p", dict((like if like is not None else p).attrib))
        q.set("id", "0")
        q.set("pageBreak", "0")
        q.set("columnBreak", "0")
        p.addnext(q)
        return q

    # ---- 글 ----
    def op_text(self, o):
        p, pos = self._point(o)
        lines = str(o["text"]).split("\n")
        items = explode(p)
        ra = self._run_at(items, pos)
        tail = items[pos:]
        head = items[:pos] + [Item("ch", c, ra) for c in lines[0]]
        if len(lines) == 1:
            implode(p, head + tail if (head + tail) else [Item("empty", None, ra)])
            self.d.touch(p)
            return "글 넣음"
        implode(p, head or [Item("empty", None, ra)])
        self.d.touch(p)
        cur = p
        for k, line in enumerate(lines[1:], 1):
            q = self._new_para_after(cur)
            body = [Item("ch", c, ra) for c in line]
            if k == len(lines) - 1:
                body += tail
            implode(q, body or [Item("empty", None, ra)])
            cur = q
        return f"글 넣음({len(lines)}문단)"

    CHAR_KEYS = ("font", "size", "color", "bold", "italic", "underline", "strike", "shade", "spacing", "ratio",
                 "superscript", "subscript")
    PARA_KEYS = ("align", "left_mm", "right_mm", "indent_mm", "before_pt", "after_pt", "line_spacing",
                 "page_break_before", "keep_with_next", "keep_lines", "widow_orphan", "word_wrap")

    def op_p(self, o):
        """문서 끝에 새 문단(문단 모양·글자 모양 함께, runs로 글자 모양 나눔). 문서가 비어 있으면 첫 문단을 쓴다."""
        d = self.d
        body = d.body()
        last = body[-1]
        st = d.style_by(name=o["style"]) if "style" in o else first(d.style_by(name="바탕글"), d.base_style())
        if "style" in o and st is None:
            raise ValueError(f"스타일 '{o['style']}'이 없습니다. 있는 스타일: {', '.join(d.styles())}")
        base_pp = st.get("paraPrIDRef") if st is not None else "0"
        base_cp = st.get("charPrIDRef") if st is not None else "0"
        empty = not getattr(self, "_used", False) and not para_text(last).replace(OBJ, "").strip() and len(body) == 1
        self._used = True
        if empty:
            q = last
            keep = [i for i in explode(q) if i.kind == "obj"]       # 첫 문단의 secPr·단 컨트롤은 둔다
        else:
            q = self._new_para_after(last)
            keep = []
        q.set("styleIDRef", st.get("id") if st is not None else "0")
        pk = {k: o[k] for k in self.PARA_KEYS if k in o}
        pk["heading"] = ("NONE", 0, 0)
        q.set("paraPrIDRef", d.parapr(base_pp, pk))
        items = list(keep)
        base = {k: o[k] for k in self.CHAR_KEYS if k in o}
        ra = {"charPrIDRef": base_cp}
        for r in o.get("runs") or [{"text": o.get("text", "")}]:
            look = {"bold": False, "italic": False, "underline": False, "superscript": False, "subscript": False}
            look.update(base)
            look.update({k: r[k] for k in self.CHAR_KEYS if k in r})
            ra = {"charPrIDRef": d.charpr(base_cp, look)}
            items += [Item("ch", c, ra) for c in str(r.get("text", ""))]
        if empty:                                       # 첫 문단의 구역·단 정의 run도 새 글자 모양을 따른다(한컴 저장본과 같게)
            for i in keep:
                i.run = dict(ra)
        if not any(i.kind == "ch" for i in items):
            items.append(Item("empty", None, ra))      # 빈 문단의 줄 높이 = 문단 끝 표시의 글자 모양(한컴: 간격용 4~6pt 문단)
        implode(q, items)
        if o.get("columns"):                            # 이 문단부터 단 수를 바꾼다(한컴 '다단 설정 나누기': 문단 첫 run의 colPr)
            c = o["columns"] if isinstance(o["columns"], dict) else {"count": int(o["columns"])}
            for old in q.iter(HP + "colPr"):
                old.getparent().getparent().remove(old.getparent())
            ctrl = etree.Element(HP + "ctrl")
            cp = etree.SubElement(ctrl, HP + "colPr", {"id": "", "type": "NEWSPAPER", "layout": "LEFT",
                                                       "colCount": str(int(c.get("count", 2))), "sameSz": "1",
                                                       "sameGap": str(mm(float(c.get("gap_mm", 8))))})
            if c.get("line"):
                etree.SubElement(cp, HP + "colLine", {"type": "SOLID", "width": "0.12 mm", "color": "#000000"})
            run = q.find(HP + "run")
            sp = run.find(HP + "secPr")
            run.insert(0 if sp is None else run.index(sp) + 1, ctrl)
        d.touch(q)
        return "문단"

    def op_page_hide(self, o):
        """그 쪽(기본 첫 쪽)의 머리말·꼬리말·쪽 번호 등을 감춘다. 한컴처럼 문단의 글 run 안 글 앞에 pageHiding 컨트롤."""
        att = {"hideHeader": "1" if o.get("header", True) else "0", "hideFooter": "1" if o.get("footer") else "0",
               "hideMasterPage": "1" if o.get("master") else "0", "hideBorder": "1" if o.get("border") else "0",
               "hideFill": "1" if o.get("fill") else "0", "hidePageNum": "1" if o.get("page_number", True) else "0"}
        ctrl = etree.Element(HP + "ctrl")
        etree.SubElement(ctrl, HP + "pageHiding", att)
        if any(k in o for k in ("after", "before", "para_end")):
            p, pos = self._point(o)
            self._insert_items(p, pos, [Item("obj", ctrl, self._run_at(explode(p), pos))])
        else:
            self.d.put_head_ctrl(self.d.sections[0], ctrl)
        return "감추기"

    def op_page_break(self, o):
        p, pos = self._point(o)
        items = explode(p)
        if 0 < pos < len(items):
            q = self._new_para_after(p)
            implode(q, items[pos:])
            implode(p, items[:pos])
            q.set("pageBreak", "1")
        else:
            q = p.getnext() if pos >= len(items) else p
            if q is None or q.tag != HP + "p":
                q = self._new_para_after(p)
                implode(q, [Item("empty", None, self._run_at(items, pos))])
            q.set("pageBreak", "1")
        self.d.touch(p)
        return "쪽 나눔"

    def op_delete(self, o):
        n = 0
        for p, spans in self._group(self._target_ranges(o)):
            items = explode(p)
            for s, e in sorted(spans, reverse=True):
                items[s:e] = [i for i in items[s:e] if i.kind == "obj"]   # 개체는 남긴다
                n += 1
            implode(p, items or [Item("empty", None, {"charPrIDRef": "0"})])
            self.d.touch(p)
        return f"{n}곳 지움"

    def op_char(self, o):
        keys = {k: o[k] for k in ("font", "size", "color", "bold", "italic", "underline", "strike", "shade",
                                  "spacing", "ratio", "superscript", "subscript") if k in o}
        n = 0
        for p, spans in self._group(self._target_ranges(o)):
            items = explode(p)
            for s, e in spans:
                for it in items[s:e]:
                    if it.kind in ("ch", "mark"):
                        ra = dict(it.run)
                        ra["charPrIDRef"] = self.d.charpr(ra.get("charPrIDRef", "0"), keys)
                        it.run = ra
                n += 1
            implode(p, items)
            self.d.touch(p)
        return f"{n}곳 글자 모양"

    def op_para(self, o):
        keys = {k: o[k] for k in self.PARA_KEYS if k in o}
        ps = {id(p): p for p, _, _ in self._target_ranges(o)}
        for p in ps.values():
            p.set("paraPrIDRef", self.d.parapr(p.get("paraPrIDRef", "0"), keys))
            self.d.touch(p)
        return f"{len(ps)}곳 문단 모양"

    def op_style(self, o):
        st = self.d.style_by(name=o["name"])
        if st is None:
            raise ValueError(f"스타일 '{o['name']}'이 없습니다. 있는 스타일: {', '.join(self.d.styles())}")
        ps = {id(p): p for p, _, _ in self._target_ranges(o)}
        for p in ps.values():
            p.set("styleIDRef", st.get("id"))
            p.set("paraPrIDRef", st.get("paraPrIDRef"))
            items = explode(p)
            for it in items:
                if it.kind in ("ch", "mark", "empty"):
                    ra = dict(it.run)
                    ra["charPrIDRef"] = st.get("charPrIDRef")
                    it.run = ra
            implode(p, items)
            self.d.touch(p)
        return f"{len(ps)}곳 스타일 '{o['name']}'"

    def op_list(self, o):
        kind = o.get("type", "bullet")
        if kind == "bullet":
            head = ("BULLET", self.d.ensure_list("bullet"), 0)
        elif kind == "number":
            head = ("NUMBER", self.d.ensure_list("number"), 0)
        else:
            head = ("NONE", 0, 0)
        ps = {id(p): p for p, _, _ in self._target_ranges(o)}
        for p in ps.values():
            p.set("paraPrIDRef", self.d.parapr(p.get("paraPrIDRef", "0"), {"heading": head}))
            self.d.touch(p)
        return f"{len(ps)}곳 목록({kind})"

    # ---- 표 ----
    def op_table_create(self, o):
        d = self.d
        data = o.get("data") or []
        rows = int(o.get("rows") or len(data) or 2)
        cols = int(o.get("cols") or max((len(r) for r in data), default=2))
        width = d.text_width(d.secprs()[0]) - 566   # 한컴 TableCreate '단 폭에 맞춤' = 단 폭 − 566
        base = d.base_style()                     # 칸 문단 = 0번 스타일(바탕글)이 가리키는 모양(한컴과 같게)
        bpp = base.get("paraPrIDRef") if base is not None else "0"
        bcp = base.get("charPrIDRef") if base is not None else "0"
        if o.get("col_widths_mm"):
            ws = [float(w) for w in o["col_widths_mm"]]
            cws = [mm(ws[i] if i < len(ws) else ws[-1]) for i in range(cols)]
        else:
            cws = [width // cols] * cols
        cw = cws[0]
        bf = d.solid_borderfill()
        tbl = etree.Element(HP + "tbl", {"id": rid(), "zOrder": "0", "numberingType": "TABLE", "textWrap": "TOP_AND_BOTTOM",
                                         "textFlow": "BOTH_SIDES", "lock": "0", "dropcapstyle": "None", "pageBreak": "CELL",
                                         "repeatHeader": "1", "rowCnt": str(rows), "colCnt": str(cols), "cellSpacing": "0",
                                         "borderFillIDRef": bf, "noAdjust": "0"})
        etree.SubElement(tbl, HP + "sz", {"width": str(sum(cws)), "widthRelTo": "ABSOLUTE", "height": str(282 * rows),
                                          "heightRelTo": "ABSOLUTE", "protect": "0"})
        etree.SubElement(tbl, HP + "pos", {"treatAsChar": "1", "affectLSpacing": "0", "flowWithText": "1", "allowOverlap": "0",
                                           "holdAnchorAndSO": "0", "vertRelTo": "PARA", "horzRelTo": "COLUMN", "vertAlign": "TOP",
                                           "horzAlign": "LEFT", "vertOffset": "0", "horzOffset": "0"})
        etree.SubElement(tbl, HP + "outMargin", {"left": "283", "right": "283", "top": "283", "bottom": "283"})
        etree.SubElement(tbl, HP + "inMargin", {"left": "510", "right": "510", "top": "141", "bottom": "141"})
        for r in range(rows):
            tr = etree.SubElement(tbl, HP + "tr")
            for c in range(cols):
                tc = etree.SubElement(tr, HP + "tc", {"name": "", "header": "0", "hasMargin": "0", "protect": "0",
                                                      "editable": "0", "dirty": "0", "borderFillIDRef": bf})
                sub = etree.SubElement(tc, HP + "subList", {"id": "", "textDirection": "HORIZONTAL", "lineWrap": "BREAK",
                                                            "vertAlign": "CENTER", "linkListIDRef": "0", "linkListNextIDRef": "0",
                                                            "textWidth": "0", "textHeight": "0", "hasTextRef": "0", "hasNumRef": "0"})
                p = etree.SubElement(sub, HP + "p", {"id": "0", "paraPrIDRef": bpp, "styleIDRef": "0", "pageBreak": "0",
                                                     "columnBreak": "0", "merged": "0"})
                v = str(data[r][c]) if r < len(data) and c < len(data[r]) else ""
                ra = {"charPrIDRef": bcp}
                lines = v.split("\n")                     # 한컴 text(): 줄바꿈 = 문단 나눔(58개 명령 첫 표: 칸 안 2문단)
                implode(p, [Item("ch", ch, ra) for ch in lines[0]] or [Item("empty", None, ra)])
                cur = p
                for line in lines[1:]:
                    q_ = etree.Element(HP + "p", dict(p.attrib))
                    cur.addnext(q_)
                    implode(q_, [Item("ch", ch, ra) for ch in line] or [Item("empty", None, ra)])
                    cur = q_
                etree.SubElement(tc, HP + "cellAddr", {"colAddr": str(c), "rowAddr": str(r)})
                etree.SubElement(tc, HP + "cellSpan", {"colSpan": "1", "rowSpan": "1"})
                etree.SubElement(tc, HP + "cellSz", {"width": str(cws[c]), "height": "282"})
                etree.SubElement(tc, HP + "cellMargin", {"left": "510", "right": "510", "top": "141", "bottom": "141"})
        # 칸 글 모양·머리줄·정렬·칸 여백(한컴 경로와 같은 옵션)
        look = {k: o[k] for k in self.CHAR_KEYS if k in o}
        if look:
            cp = d.charpr(bcp, look)
            for r_ in tbl.iter(HP + "run"):
                r_.set("charPrIDRef", cp)
        cell_pp = {}
        if o.get("align"):
            cell_pp["align"] = o["align"]
        if o.get("pad_pt"):
            cell_pp.update(before_pt=o["pad_pt"], after_pt=o["pad_pt"], line_spacing=o.get("cell_line_spacing", 130))
        if o.get("word_wrap"):
            cell_pp["word_wrap"] = o["word_wrap"]
        elif o.get("align"):
            # 한컴: 칸 정렬(TableCellAlign)을 하면 칸 문단 줄 나눔이 어절 단위(BREAK_WORD)가 된다. 정렬을 안 준 표는 기본값 그대로
            # (새 문서 58개 명령: 정렬 준 표 5개 = 어절, 정렬 없는 제목줄 표 6개 = 글자)
            cell_pp["word_wrap"] = "word"
        if cell_pp:
            ppid = d.parapr(bpp, cell_pp)
            for q_ in tbl.iter(HP + "p"):
                q_.set("paraPrIDRef", ppid)
        if o.get("header"):
            hcp = d.charpr(bcp, {**look, "bold": True})
            hpp = d.parapr(bpp, {**cell_pp, "align": "center"})
            hbf = d.borderfill(bf, fill=o.get("header_fill", "#D9D9D9"))
            for tc in tbl.find(HP + "tr").findall(HP + "tc"):
                tc.set("borderFillIDRef", hbf)
                tc.set("header", "1")                # 제목 셀: 쪽이 넘어가면 다음 쪽에 머리 줄 반복
                for q_ in tc.iter(HP + "p"):
                    q_.set("paraPrIDRef", hpp)
                for r_ in tc.iter(HP + "run"):
                    r_.set("charPrIDRef", hcp)
        # 표는 자기 문단에(한컴과 같이 run 안에 표 + 빈 글). 한컴 경로처럼 앞 문단을 나눈 새 문단: 스타일·글자 모양은
        # 앞 문단 것을 잇고, 문단 모양은 앞 문단 모양에서 목록·들여쓰기·간격만 뺀다
        p, pos = self._point(o)
        anchor = self._run_at(explode(p), pos)
        # 한컴 TableCreate는 바로 앞 표의 위치·바깥 여백·배치를 물려준다(앞 표가 없으면 단 기준·283)
        prev = None
        for sec in d.sections:
            for el in sec.iter(HP + "tbl", HP + "p"):
                if el is p:
                    break
                if el.tag == HP + "tbl":
                    prev = el
            else:
                continue
            break
        if prev is not None:
            for k in ("textWrap", "textFlow"):
                if prev.get(k):
                    tbl.set(k, prev.get(k))
            for name in ("pos", "outMargin"):
                src, dst = prev.find(HP + name), tbl.find(HP + name)
                if src is not None:
                    for k, v in src.attrib.items():
                        dst.set(k, v)
        if o.get("inline") is False:                 # 글자처럼 취급하지 않음(자리 차지) — 한 쪽보다 긴 표가 쪽을 넘어 나뉜다
            tbl.find(HP + "pos").attrib.update({"treatAsChar": "0", "flowWithText": "1", "vertRelTo": "PARA",
                                                "horzRelTo": "COLUMN", "vertOffset": "0", "horzOffset": "0",
                                                "horzAlign": {"left": "LEFT", "right": "RIGHT"}.get(o.get("table_align"), "CENTER")})
            tbl.set("textWrap", "TOP_AND_BOTTOM")
            tbl.set("pageBreak", "TABLE")             # 쪽 경계에서 행 단위로 나눈다(한 행을 두 쪽에 쪼개지 않게 — 실측 확인 대상)
        elif o.get("inline") is True:                # 글자처럼 취급(앞 표가 자리 차지여도 이 표는 글자처럼)
            tbl.find(HP + "pos").attrib.update({"treatAsChar": "1", "vertRelTo": "PARA", "horzRelTo": "COLUMN",
                                                "vertOffset": "0", "horzOffset": "0", "horzAlign": "LEFT"})
        q = self._new_para_after(p)
        q.set("paraPrIDRef", d.parapr(p.get("paraPrIDRef", "0"), {
            "align": o.get("table_align", "center"), "left_mm": 0, "indent_mm": 0, "before_pt": 0, "after_pt": 0,
            "line_spacing": 100, "keep_with_next": bool(o.get("keep_with_next")), "heading": ("NONE", 0, 0)}))
        implode(q, [Item("obj", tbl, anchor), Item("empty", None, anchor)])
        if o.get("page_break"):                      # 표 문단을 새 쪽에서 시작(장 제목 띠 등)
            q.set("pageBreak", "1")
        if not ("after" in o or "before" in o or "para_end" in o or "cell" in o):
            # 한컴은 문서 끝에 표를 만들면 표 문단 아래에 같은 모양의 빈 문단을 하나 남긴다(빠지면 아래 내용이 한 줄씩 올라온다)
            tail = self._new_para_after(q, like=q)
            if o.get("keep_with_next"):                  # 표 문단만 다음 문단과 붙이고 아래 남는 빈 문단은 붙이지 않는다(한컴 6곳)
                tail.set("paraPrIDRef", d.parapr(q.get("paraPrIDRef"), {"keep_with_next": False}))
            implode(tail, [Item("empty", None, anchor)])
        if "after" in o or "before" in o or "para_end" in o:
            items = explode(p)
            if pos < len(items) and any(i.kind == "ch" for i in items[pos:]):
                rest = self._new_para_after(q, like=p)
                implode(rest, items[pos:])
                implode(p, items[:pos] or [Item("empty", None, self._run_at(items, pos))])
        self.d.touch(p)
        return f"표 {rows}×{cols}"

    def op_table_insert_row(self, o):
        self.d.table(o["table"]).insert_row(o.get("cell", "A1"), below=o.get("where") != "above", count=int(o.get("count", 1)))
        return "줄 넣음"

    def op_table_insert_col(self, o):
        self.d.table(o["table"]).insert_col(o.get("cell", "A1"), right=o.get("where") != "left", count=int(o.get("count", 1)))
        return "칸 넣음"

    def op_table_delete_row(self, o):
        self.d.table(o["table"]).delete_row(o.get("cell", "A1"))
        return "줄 지움"

    def op_table_delete_col(self, o):
        self.d.table(o["table"]).delete_col(o.get("cell", "A1"))
        return "칸 지움"

    def op_table_merge(self, o):
        self.d.table(o["table"]).merge(o["cells"])
        return "칸 합침 " + o["cells"]

    def op_table_split(self, o):
        self.d.table(o["table"]).split(o["cell"], rows=int(o.get("rows", 1)), cols=int(o.get("cols", 2)))
        return "칸 나눔"

    def op_table_fill(self, o):
        self.d.table(o["table"]).fill(o["cells"], o.get("color"))
        return "칸 배경 " + o["cells"]

    def op_table_border(self, o):
        self.d.table(o["table"]).border(o["cells"], o.get("line", "solid"), o.get("width", "0.12mm"),
                                        o.get("color", "#000000"), o.get("sides", "all"))
        return "테두리 " + o["cells"]

    def op_table_align(self, o):
        self.d.table(o["table"]).align(o["cells"], o.get("h", "center"), o.get("v", "middle"))
        return "칸 정렬 " + o["cells"]

    def op_table_cell_text(self, o):
        self.d.table(o["table"]).set_text(o["cell"], o["text"])
        return "칸 글 " + o["cell"]

    # ---- 그림 ----
    def op_image(self, o):
        from PIL import Image
        d = self.d
        path = os.path.abspath(o["path"])
        if not os.path.exists(path):
            raise ValueError("그림 파일이 없습니다: " + path)
        with Image.open(path) as im:
            pw, ph = im.size
            fmt = (im.format or "PNG").lower()
        ext = {"jpeg": "jpg"}.get(fmt, fmt)
        media = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "bmp": "image/bmp"}.get(ext, "image/" + ext)
        w_mm, h_mm = o.get("width_mm"), o.get("height_mm")
        if not w_mm and not h_mm:
            w_mm = pw * 25.4 / 96
        ratio = ph / pw
        w_mm = float(w_mm) if w_mm else float(h_mm) / ratio
        h_mm = float(h_mm) if h_mm else w_mm * ratio
        W, H = mm(w_mm), mm(h_mm)
        man = d.hpf.find(OPF + "manifest")
        used = {i.get("id") for i in man}
        k = 1
        while f"image{k}" in used or f"BinData/image{k}.{ext}" in d.files or f"BinData/image{k}.{ext}" in d.added:
            k += 1
        bid = f"image{k}"
        href = f"BinData/{bid}.{ext}"
        d.added[href] = open(path, "rb").read()
        etree.SubElement(man, OPF + "item", {"id": bid, "href": href, "media-type": media, "isEmbeded": "1"})
        pic = etree.Element(HP + "pic", {"id": rid(), "zOrder": "0", "numberingType": "PICTURE", "textWrap": "TOP_AND_BOTTOM",
                                         "textFlow": "BOTH_SIDES", "lock": "0", "dropcapstyle": "None", "href": "",
                                         "groupLevel": "0", "instid": rid(), "reverse": "0"})
        etree.SubElement(pic, HP + "offset", {"x": "0", "y": "0"})
        etree.SubElement(pic, HP + "orgSz", {"width": str(W), "height": str(H)})
        etree.SubElement(pic, HP + "curSz", {"width": "0", "height": "0"})
        etree.SubElement(pic, HP + "flip", {"horizontal": "0", "vertical": "0"})
        etree.SubElement(pic, HP + "rotationInfo", {"angle": "0", "centerX": str(W // 2), "centerY": str(H // 2), "rotateimage": "1"})
        ri = etree.SubElement(pic, HP + "renderingInfo")
        for m in ("transMatrix", "scaMatrix", "rotMatrix"):
            etree.SubElement(ri, HC + m, {"e1": "1", "e2": "0", "e3": "0", "e4": "0", "e5": "1", "e6": "0"})
        etree.SubElement(pic, HC + "img", {"binaryItemIDRef": bid, "bright": "0", "contrast": "0", "effect": "REAL_PIC", "alpha": "0"})
        ir = etree.SubElement(pic, HP + "imgRect")
        for i, (x, y) in enumerate(((0, 0), (W, 0), (W, H), (0, H))):
            etree.SubElement(ir, HC + f"pt{i}", {"x": str(x), "y": str(y)})
        etree.SubElement(pic, HP + "imgClip", {"left": "0", "right": "0", "top": "0", "bottom": "0"})
        etree.SubElement(pic, HP + "inMargin", {"left": "0", "right": "0", "top": "0", "bottom": "0"})
        etree.SubElement(pic, HP + "imgDim", {"dimwidth": "0", "dimheight": "0"})
        etree.SubElement(pic, HP + "effects")
        etree.SubElement(pic, HP + "sz", {"width": str(W), "widthRelTo": "ABSOLUTE", "height": str(H), "heightRelTo": "ABSOLUTE", "protect": "0"})
        etree.SubElement(pic, HP + "pos", {"treatAsChar": "1", "affectLSpacing": "0", "flowWithText": "1", "allowOverlap": "0",
                                           "holdAnchorAndSO": "0", "vertRelTo": "PARA", "horzRelTo": "COLUMN", "vertAlign": "TOP",
                                           "horzAlign": "LEFT", "vertOffset": "0", "horzOffset": "0"})
        etree.SubElement(pic, HP + "outMargin", {"left": "0", "right": "0", "top": "0", "bottom": "0"})
        sc = etree.SubElement(pic, HP + "shapeComment")
        sc.text = f"그림입니다.\n원본 그림의 이름: {os.path.basename(path)}\n원본 그림의 크기: 가로 {pw}pixel, 세로 {ph}pixel"
        p, pos = self._point(o)
        items = explode(p)
        ra = self._run_at(items, pos)
        self._insert_items(p, pos, [Item("obj", pic, ra), Item("empty", None, ra)])
        return "그림 넣음"

    # ---- 머리말·꼬리말·쪽 ----
    def _headfoot(self, o, tag):
        d = self.d
        page = {"both": "BOTH", "even": "EVEN", "odd": "ODD"}[o.get("page", "both")]
        st = d.style_by(eng="Header")
        if st is None:
            st = first(d.style_by(name="머리말"), d.base_style())
        pp = st.get("paraPrIDRef") if st is not None else "0"
        if "align" in o:
            pp = d.parapr(pp, {"align": o["align"]})
        for sec, secpr in zip(d.sections, d.secprs()):
            old = next((h for h in sec.iter(HP + tag) if h.get("applyPageType") == page), None)
            ra = {"charPrIDRef": st.get("charPrIDRef") if st is not None else "0"}
            if old is not None:
                sub = old.find(HP + "subList")
                for p in sub.findall(HP + "p"):
                    sub.remove(p)
            else:
                ids = [int(x.get("id")) for x in sec.iter() if local(x.tag) in ("header", "footer") and (x.get("id") or "").isdigit()]
                old = etree.Element(HP + tag, {"id": str(max(ids + [0]) + 1), "applyPageType": page})
                pr = secpr.find(HP + "pagePr")
                m = pr.find(HP + "margin")
                sub = etree.SubElement(old, HP + "subList", {"id": "", "textDirection": "HORIZONTAL", "lineWrap": "BREAK",
                                                             "vertAlign": "TOP" if tag == "header" else "BOTTOM",
                                                             "linkListIDRef": "0", "linkListNextIDRef": "0",
                                                             "textWidth": str(d.text_width(secpr)),
                                                             "textHeight": m.get("header" if tag == "header" else "footer"),
                                                             "hasTextRef": "0", "hasNumRef": "0"})
                ctrl = etree.Element(HP + "ctrl")
                ctrl.append(old)
                d.put_head_ctrl(sec, ctrl)
            for line in str(o.get("text", "")).split("\n"):
                p = etree.SubElement(sub, HP + "p", {"id": "0", "paraPrIDRef": pp, "styleIDRef": st.get("id") if st is not None else "0",
                                                     "pageBreak": "0", "columnBreak": "0", "merged": "0"})
                implode(p, [Item("ch", c, ra) for c in line] or [Item("empty", None, ra)])

    def op_header(self, o):
        self._headfoot(o, "header")
        return "머리말"

    def op_footer(self, o):
        self._headfoot(o, "footer")
        return "꼬리말"

    POS = {"none": "NONE", "top-left": "TOP_LEFT", "top-center": "TOP_CENTER", "top-right": "TOP_RIGHT",
           "bottom-left": "BOTTOM_LEFT", "bottom-center": "BOTTOM_CENTER", "bottom-right": "BOTTOM_RIGHT",
           "outside-top": "OUTSIDE_TOP", "outside-bottom": "OUTSIDE_BOTTOM", "inside-top": "INSIDE_TOP", "inside-bottom": "INSIDE_BOTTOM"}
    NUMFMT = {"digit": "DIGIT", "circle": "CIRCLED_DIGIT", "roman": "ROMAN_CAPITAL", "roman-small": "ROMAN_SMALL",
              "latin": "LATIN_CAPITAL", "latin-small": "LATIN_SMALL", "hangul": "HANGUL_SYLLABLE", "hanja": "IDEOGRAPH"}

    def op_page_number(self, o):
        att = {"pos": self.POS[o.get("pos", "bottom-center")], "formatType": self.NUMFMT[o.get("format", "digit")],
               "sideChar": o.get("side") or ""}
        for sec in self.d.sections:
            old = next(sec.iter(HP + "pageNum"), None)
            if old is not None:
                for k, v in att.items():
                    old.set(k, v)
            else:
                ctrl = etree.Element(HP + "ctrl")
                etree.SubElement(ctrl, HP + "pageNum", att)
                self.d.put_head_ctrl(sec, ctrl)
        return "쪽 번호 " + o.get("pos", "bottom-center")

    PAPER = {"A4": (210, 297), "A3": (297, 420), "A5": (148, 210), "B4": (257, 364), "B5": (182, 257),
             "LETTER": (215.9, 279.4), "LEGAL": (215.9, 355.6)}

    def op_page(self, o):
        for secpr in self.d.secprs():
            pr = secpr.find(HP + "pagePr")
            m = pr.find(HP + "margin")
            if "paper" in o:
                w, h = self.PAPER[o["paper"].upper()]
                pr.set("width", str(mm(w)))
                pr.set("height", str(mm(h)))
            if "width_mm" in o:
                pr.set("width", str(mm(o["width_mm"])))
            if "height_mm" in o:
                pr.set("height", str(mm(o["height_mm"])))
            if "landscape" in o:
                pr.set("landscape", "NARROWLY" if o["landscape"] else "WIDELY")   # OWPML: 가로=NARROWLY
            for k in ("left", "right", "top", "bottom", "header", "footer", "gutter"):
                if k in o.get("margins_mm", {}):
                    m.set(k, str(mm(o["margins_mm"][k])))
            if "footnote_place" in o:          # 각주 자리: each(단마다)·merged(전 단 가로지르기)·right(오른쪽 단)
                pl = secpr.find(f"{HP}footNotePr/{HP}placement")
                pl.set("place", {"each": "EACH_COLUMN", "merged": "MERGED_COLUMN", "right": "RIGHT_MOST_COLUMN"}[o["footnote_place"]])
        # 너비가 고정된 표 칸·글상자 안 줄 배치는 그대로 둔다 — 다 지우면 한컴이 원본과 다르게 줄을 나눈다
        self.d.relayout_all = self.d.relayout_all or "page"
        return "쪽 설정"

    # ---- 누름틀·각주 ----
    def op_field(self, o):
        p, pos = self._point(o)
        items = explode(p)
        ra = self._run_at(items, pos)
        help_ = o.get("help", o["name"])
        rest = f"Direction:wstring:{len(help_)}:{help_} HelpState:wstring:0: "
        fid, fieldid = rid(), rid()
        c1 = etree.Element(HP + "ctrl")
        fb = etree.SubElement(c1, HP + "fieldBegin", {"id": fid, "type": "CLICK_HERE", "name": o["name"], "editable": "1",
                                                     "dirty": "1", "zorder": "-1", "fieldid": fieldid})
        ps = etree.SubElement(fb, HP + "parameters", {"cnt": "3", "name": ""})
        etree.SubElement(ps, HP + "integerParam", {"name": "Prop"}).text = "9"
        cmd = etree.SubElement(ps, HP + "stringParam", {"name": "Command"})
        cmd.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        cmd.text = f"Clickhere:set:{len(rest) + 1}:{rest} "
        etree.SubElement(ps, HP + "stringParam", {"name": "Direction"}).text = help_
        c2 = etree.Element(HP + "ctrl")
        etree.SubElement(c2, HP + "fieldEnd", {"beginIDRef": fid, "fieldid": fieldid})
        new = [Item("obj", c1, ra)] + [Item("ch", c, ra) for c in str(o.get("text", ""))] + [Item("obj", c2, ra), Item("empty", None, ra)]
        self._insert_items(p, pos, new)
        return "누름틀 " + o["name"]

    def fields(self):
        """{이름: [(문단, begin 위치, end 위치)]}"""
        out = {}
        for p in self.d.all_paras():
            items = explode(p)
            begins = {}
            for k, it in enumerate(items):
                if it.kind != "obj" or local(it.val.tag) != "ctrl" or not len(it.val):
                    continue
                c = it.val[0]
                if local(c.tag) == "fieldBegin" and c.get("type") == "CLICK_HERE":
                    begins[c.get("id")] = (c.get("name"), k)
                elif local(c.tag) == "fieldEnd" and c.get("beginIDRef") in begins:
                    name, b = begins.pop(c.get("beginIDRef"))
                    out.setdefault(name, []).append((p, b, k))
        return out

    def op_fill(self, o):
        have = self.fields()
        miss = [k for k in o["fields"] if k not in have]
        if miss:
            raise ValueError(f"없는 누름틀: {miss}. 있는 누름틀: {sorted(have)}")
        for name, val in o["fields"].items():
            for p, b, e in reversed(have[name]):
                items = explode(p)
                ra = dict(items[b].run)
                inner = [i for i in items[b + 1:e] if i.kind in ("ch", "mark")]
                if inner:
                    ra = dict(inner[0].run)
                items[b + 1:e] = [Item("ch", c, ra) for c in str(val)]
                implode(p, items)
                self.d.touch(p)
            have = self.fields()
        return f"누름틀 {len(o['fields'])}개 채움"

    def _note(self, o, tag, eng, numtype):
        d = self.d
        p, pos = self._point(o)
        st = first(d.style_by(eng=eng), d.base_style())
        n = sum(1 for _ in d.sections[0].iter(HP + tag)) + 1
        c = etree.Element(HP + "ctrl")
        note = etree.SubElement(c, HP + tag, {"number": str(n), "suffixChar": "41", "instId": rid()})
        sub = etree.SubElement(note, HP + "subList", {"id": "", "textDirection": "HORIZONTAL", "lineWrap": "BREAK", "vertAlign": "TOP",
                                                      "linkListIDRef": "0", "linkListNextIDRef": "0", "textWidth": "0",
                                                      "textHeight": "0", "hasTextRef": "0", "hasNumRef": "0"})
        q = etree.SubElement(sub, HP + "p", {"id": "0", "paraPrIDRef": st.get("paraPrIDRef") if st is not None else "0",
                                             "styleIDRef": st.get("id") if st is not None else "0", "pageBreak": "0",
                                             "columnBreak": "0", "merged": "0"})
        qa = {"charPrIDRef": st.get("charPrIDRef") if st is not None else "0"}
        look = {k: o[k] for k in self.CHAR_KEYS if k in o}
        if look:                                        # 각주 글의 글자 모양(글꼴·크기)
            qa = {"charPrIDRef": d.charpr(qa["charPrIDRef"], look)}
        pk = {k: o[k] for k in self.PARA_KEYS if k in o}
        if pk:
            q.set("paraPrIDRef", d.parapr(q.get("paraPrIDRef"), pk))
        an = etree.Element(HP + "ctrl")
        a = etree.SubElement(an, HP + "autoNum", {"num": str(n), "numType": numtype})
        etree.SubElement(a, HP + "autoNumFormat", {"type": "DIGIT", "userChar": "", "prefixChar": "", "suffixChar": ")", "supscript": "0"})
        implode(q, [Item("obj", an, qa)] + [Item("ch", ch, qa) for ch in " " + str(o["text"])])
        items = explode(p)
        self._insert_items(p, pos, [Item("obj", c, self._run_at(items, pos))])

    def op_footnote(self, o):
        self._note(o, "footNote", "Footnote", "FOOTNOTE")
        return "각주"

    def op_endnote(self, o):
        self._note(o, "endNote", "Endnote", "ENDNOTE")
        return "미주"

    # ---- 바꾸기 ----
    def op_replace(self, o):
        d = self.d
        find, to = o["find"], o.get("to", "")
        regex, scope = bool(o.get("regex")), o.get("scope") or {}
        paras = None
        if "table" in scope:
            t = d.table(scope["table"])
            paras = [p for y in t.cells for p in y["tc"].iter(HP + "p")]
        elif "para" in scope:
            paras = self._paras(scope["para"])
        rx = re.compile(find if regex else re.escape(find), 0 if o.get("match_case", True) or regex else re.I)
        lo = hi = None
        body = d.body()
        if "after" in scope or "before" in scope:
            if "after" in scope:
                ms = d.matches(scope["after"], paras=body)
                if not ms:
                    raise ValueError(f"범위 시작 '{scope['after']}'을 찾지 못했습니다")
                lo = (body.index(ms[0][0]), ms[0][2])
            if "before" in scope:
                ms = d.matches(scope["before"], paras=body)
                if not ms:
                    raise ValueError(f"범위 끝 '{scope['before']}'을 찾지 못했습니다")
                hi = (body.index(ms[0][0]), ms[0][1])
            a = lo[0] if lo else 0
            b = hi[0] if hi else len(body) - 1
            paras = body[a:b + 1]
        n = 0
        for p in (paras if paras is not None else d.all_paras()):
            s = para_text(p)
            spans = []
            for m in rx.finditer(s):
                if m.end() == m.start() or OBJ in m.group(0):
                    continue
                if lo and p is body[lo[0]] and m.start() < lo[1]:
                    continue
                if hi and p is body[hi[0]] and m.end() > hi[1]:
                    continue
                spans.append((m.start(), m.end(), m.expand(to) if regex else to))
            if not spans:
                continue
            items = explode(p)
            for st_, en, new in reversed(spans):
                ra = dict(next((i.run for i in items[st_:en] if i.kind == "ch"), items[st_].run))
                keep = [i for i in items[st_:en] if i.kind == "obj"]
                items[st_:en] = [Item("ch", c, ra) for c in new] + keep
                n += 1
            implode(p, items or [Item("empty", None, {"charPrIDRef": "0"})])
            d.touch(p)
        return f"{n}곳 바꿈"

    # ---- 합치기 ----
    def op_merge(self, o):
        other = Hwpx(os.path.abspath(o["file"])) if str(o["file"]).lower().endswith(".hwpx") else None
        if other is None:
            raise ValueError("직접 경로의 합치기는 HWPX끼리만 됩니다(HWP는 한컴 경로나 convert 뒤)")
        n = merge_docs(self.d, other, o.get("break", "page"))
        return f"합침 {os.path.basename(o['file'])} (문단 {n})"


def merge_docs(d, other, brk="page"):
    """other의 본문을 d 끝에 붙인다. 글자·문단 모양·테두리·스타일·번호·글꼴·그림 id를 d 쪽으로 옮겨 단다."""
    maps = {"charPr": {}, "paraPr": {}, "borderFill": {}, "style": {}, "numbering": {}, "bullet": {}, "tabPr": {}, "bin": {}}
    font_map = {}
    for ff in other.reflist("fontfaces"):
        lang = ff.get("lang")
        for f in ff.findall(HH + "font"):
            font_map[(lang, f.get("id"))] = d.font_id(f.get("face"), lang)

    def clone(listname, key, el, fix):
        lst = d.reflist(listname)
        new = copy.deepcopy(el)
        fix(new)
        nid = str(max([int(x.get("id")) for x in lst] + [-1]) + 1)
        new.set("id", nid)
        same = d._find_same(listname, new)
        if same is None:
            d._add(listname, new)
            same = nid
        maps[key][el.get("id")] = same

    for b in other.reflist("borderFills"):
        clone("borderFills", "borderFill", b, lambda e: None)
    for t in other.reflist("tabProperties"):
        clone("tabProperties", "tabPr", t, lambda e: None)
    for n in other.reflist("numberings"):
        clone("numberings", "numbering", n, lambda e: None)
    for b in other.reflist("bullets"):
        clone("bullets", "bullet", b, lambda e: None)

    def fix_cp(e):
        fr = e.find(HH + "fontRef")
        if fr is not None:
            for lang, LANG in zip(LANGS, FONT_LANGS):
                if fr.get(lang) is not None:
                    fr.set(lang, font_map.get((LANG, fr.get(lang)), fr.get(lang)))
        if e.get("borderFillIDRef") in maps["borderFill"]:
            e.set("borderFillIDRef", maps["borderFill"][e.get("borderFillIDRef")])
    for c in other.reflist("charProperties"):
        clone("charProperties", "charPr", c, fix_cp)

    def fix_pp(e):
        if e.get("tabPrIDRef") in maps["tabPr"]:
            e.set("tabPrIDRef", maps["tabPr"][e.get("tabPrIDRef")])
        h = e.find(HH + "heading")
        if h is not None and h.get("type") in ("NUMBER", "OUTLINE") and h.get("idRef") in maps["numbering"]:
            h.set("idRef", maps["numbering"][h.get("idRef")])
        if h is not None and h.get("type") == "BULLET" and h.get("idRef") in maps["bullet"]:
            h.set("idRef", maps["bullet"][h.get("idRef")])
        for b in e.iter(HH + "border"):
            if b.get("borderFillIDRef") in maps["borderFill"]:
                b.set("borderFillIDRef", maps["borderFill"][b.get("borderFillIDRef")])
    for p in other.reflist("paraProperties"):
        clone("paraProperties", "paraPr", p, fix_pp)
    names = {s.get("name"): s.get("id") for s in d.reflist("styles")}
    for s in other.reflist("styles"):
        if s.get("name") in names:
            maps["style"][s.get("id")] = names[s.get("name")]
        else:
            new = copy.deepcopy(s)
            new.set("paraPrIDRef", maps["paraPr"].get(s.get("paraPrIDRef"), "0"))
            new.set("charPrIDRef", maps["charPr"].get(s.get("charPrIDRef"), "0"))
            nid = str(max(int(x.get("id")) for x in d.reflist("styles")) + 1)
            new.set("id", nid)
            new.set("nextStyleIDRef", nid)
            d._add("styles", new)
            maps["style"][s.get("id")] = nid
    # 그림
    man_o = other.hpf.find(OPF + "manifest")
    man_d = d.hpf.find(OPF + "manifest")
    used = {i.get("id") for i in man_d}
    for it in man_o:
        href = it.get("href", "")
        if not href.startswith("BinData/"):
            continue
        k = 1
        ext = href.rsplit(".", 1)[-1]
        while f"image{k}" in used:
            k += 1
        nid = f"image{k}"
        used.add(nid)
        d.added[f"BinData/{nid}.{ext}"] = other.files.get(href) or other.added.get(href)
        new = copy.deepcopy(it)
        new.set("id", nid)
        new.set("href", f"BinData/{nid}.{ext}")
        if "hashkey" in new.attrib:
            del new.attrib["hashkey"]
        man_d.append(new)
        maps["bin"][it.get("id")] = nid

    attr_map = {"charPrIDRef": "charPr", "paraPrIDRef": "paraPr", "borderFillIDRef": "borderFill", "styleIDRef": "style",
                "binaryItemIDRef": "bin"}
    count = 0
    if brk in ("page", "section"):
        # 한컴 InsertFile(KeepSection=1)처럼 붙는 문서의 구역(용지·여백·머리말)을 지킨다 → 구역마다 새 section 파일
        man = d.hpf.find(OPF + "manifest")
        spine = d.hpf.find(OPF + "spine")
        for sec in other.sections:
            q = copy.deepcopy(sec)
            for el in q.iter():
                for att, key in attr_map.items():
                    v = el.get(att)
                    if v is not None and v in maps[key]:
                        el.set(att, maps[key][v])
                if el.tag == HP + "secPr" and el.get("outlineShapeIDRef") in maps["numbering"]:
                    el.set("outlineShapeIDRef", maps["numbering"][el.get("outlineShapeIDRef")])
            for ls in list(q.iter(HP + "linesegarray")):    # 붙인 문서의 줄 배치는 지운다(남기면 한컴이 옛 줄 배치를 믿어 붙인 쪽이 어긋난다)
                ls.getparent().remove(ls)
            k = len(d.sec_names)
            name = f"Contents/section{k}.xml"
            d.sec_names.append(name)
            d.sections.append(q)
            etree.SubElement(man, OPF + "item", {"id": f"section{k}", "href": name, "media-type": "application/xml"})
            last_ref = [r for r in spine if (r.get("idref") or "").startswith("section")][-1]
            ref = etree.Element(OPF + "itemref", {"idref": f"section{k}", "linear": "yes"})
            last_ref.addnext(ref)
            count += len(q.findall(HP + "p"))
        d.header.set("secCnt", str(len(d.sections)))
        return count
    last = d.sections[-1]
    for si, sec in enumerate(other.sections):
        for k, p in enumerate(sec.findall(HP + "p")):
            q = copy.deepcopy(p)
            for el in q.iter():
                for att, key in attr_map.items():
                    v = el.get(att)
                    if v is not None and v in maps[key]:
                        el.set(att, maps[key][v])
            for ls in list(q.iter(HP + "linesegarray")):
                ls.getparent().remove(ls)
            if k == 0:
                sp = q.find(f"{HP}run/{HP}secPr")
                if brk != "section" and sp is not None:   # 쪽 나눔이면 붙는 문서의 구역 설정은 버린다
                    run = sp.getparent()
                    for c in list(run):
                        if local(c.tag) == "secPr" or (local(c.tag) == "ctrl" and len(c) and local(c[0].tag) == "colPr"):
                            run.remove(c)
                    if not len(run):
                        q.remove(run)
                if si == 0:
                    q.set("pageBreak", "1" if brk == "page" else "0")
            last.append(q)
            count += 1
    return count                                 # 붙인 문단의 줄 배치만 지웠다. 기존 문단은 그대로(한컴 경로와 같게)


# ───────────────────────── 바깥에서 쓰는 함수 ─────────────────────────
def hwp_to_hwpx_direct(src, dst):
    """한컴 없이 HWP→HWPX. 우리 변환기 hwp5_to_hwpx를 먼저 쓴다(2026-10-16: 한컴 정답지 339개와 요소 대조, 한컴 눈 시험
    116개 모두 열림·쪽 수 같음). 그것이 실패하면 vendor/hwp2hwpx로. → (뺀 묶음 도형 수, 쓴 변환기, 경고 목록)"""
    try:
        import hwp5_to_hwpx
        warns = hwp5_to_hwpx.convert(src, dst)
        return 0, "우리 변환기 hwp5_to_hwpx", warns
    except Exception as e:                      # noqa: BLE001 — 변환기 하나가 못 하면 다른 것으로
        first = f"우리 변환기 실패({str(e)[:80]}) → vendor/hwp2hwpx"
    return _vendor_hwp_to_hwpx(src, dst), "vendor/hwp2hwpx", [first]


def _vendor_hwp_to_hwpx(src, dst):
    """vendor/hwp2hwpx(jkf87/hwp2hwpx-python-refactor, Apache-2.0)를 이 프로세스에서 부른다.
    변환 뒤 한컴이 죽는 빈 묶음 도형을 뺀다(sanitize_converted). 뺀 개수를 돌려준다."""
    import sys
    vend = os.path.join(HERE, "vendor")
    if vend not in sys.path:
        sys.path.insert(0, vend)
    try:
        from hwp2hwpx import convert_file
    except ImportError as e:
        raise RuntimeError(f"HWP→HWPX 변환기를 못 불러옴({e}). pyhwp(hwp5)·olefile·lxml이 필요합니다") from e
    convert_file(src, dst)
    if not os.path.exists(dst):
        raise RuntimeError("HWP→HWPX 변환 실패")
    return sanitize_converted(dst)


def fixed_width(p):
    """너비가 고정된(ABSOLUTE) 표 칸·글상자 안 문단이면 True — 쪽 여백·용지가 바뀌어도 그 줄 배치는 그대로다.
    각주·미주·머리말·꼬리말은 쪽(단) 너비를 따른다."""
    for a in p.iterancestors():
        tag = local(a.tag)
        if tag in ("footNote", "endNote", "header", "footer"):
            return False
        if tag in ("tbl", "rect", "ellipse", "arc", "polygon", "curve", "connectLine", "line", "container", "textart"):
            sz = a.find(HP + "sz")
            return sz is not None and sz.get("widthRelTo", "ABSOLUTE") == "ABSOLUTE"
    return False


def first(*xs):
    """None이 아닌 첫 값. lxml 요소는 자식이 없으면 거짓이라 `or`로 이으면 안 된다."""
    return next((x for x in xs if x is not None), None)


SHAPES = {"line", "rect", "ellipse", "arc", "polygon", "curve", "connectLine", "pic", "container", "ole", "equation", "textart"}


def sanitize_converted(path):
    """hwp2hwpx는 묶음 개체(container)를 속 도형 없이 빈 껍데기로 쓴다 → 한컴 2024가 배치하다 죽는다.
    속이 빈 container를 빼고 뺀 개수를 돌려준다."""
    with zipfile.ZipFile(path) as z:
        files = {i.filename: z.read(i.filename) for i in z.infolist()}
        names = z.namelist()
    removed = 0
    for n in names:
        if not re.match(r"Contents/section\d+\.xml$", n):
            continue
        root = etree.fromstring(files[n])
        for c in list(root.iter(HP + "container")):
            if not any(local(ch.tag) in SHAPES for ch in c):
                run = c.getparent()
                run.remove(c)
                removed += 1
        files[n] = b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>' + etree.tostring(root, encoding="UTF-8")
    with zipfile.ZipFile(path, "w") as zo:
        for n in names:
            zo.writestr(n, files[n], compress_type=zipfile.ZIP_STORED if n == "mimetype" else zipfile.ZIP_DEFLATED)
    return removed


def edit(src, out, ops, keep_going=False):
    """src(HWPX·HWP 또는 None=새 문서) → ops → out(.hwpx). 진행 줄을 yield."""
    import tempfile
    if not out.lower().endswith(".hwpx"):
        raise ValueError("직접 경로는 HWPX로 저장합니다(-o 이름.hwpx). HWP로 저장하려면 한컴 경로이거나, 원본이 HWP이고 replace·fill만일 때")
    tmp = None
    if src and open(src, "rb").read(4) == b"\xd0\xcf\x11\xe0":
        tmp = tempfile.mkdtemp()
        conv = os.path.join(tmp, "in.hwpx")
        removed, used, warns = hwp_to_hwpx_direct(src, conv)
        yield f"HWP → HWPX 변환(한컴 없이, {used}) 뒤 고칩니다" + (f" — 변환기가 못 옮긴 묶음 도형 {removed}개는 뺐습니다" if removed else "")
        for w in warns:
            yield "변환 경고: " + w
        src = conv
    doc = Hwpx(src)
    ed = Editor(doc)
    for i, o in enumerate(ops, 1):
        try:
            yield f"[{i}] {o['op']}: {ed.op(dict(o))}"
        except Exception as e:
            yield f"[{i}] {o['op']}: 실패 — {e}"
            if not keep_going:
                raise SystemExit("멈춤: 저장하지 않았습니다(--keep-going이면 계속).")
    doc.save(out)


def outline(path):
    """본문 문단(0부터)과 표(1부터, 문서 순서·칸 안 표 포함) 목록. HWP·HWPX 모두 한컴 없이."""
    head = open(path, "rb").read(8)
    if head.startswith(b"PK"):
        d = Hwpx(path)
        paras = [para_text(p).replace(OBJ, "□") for p in d.body()]
        tables = []
        for i, t in enumerate(d.tables(), 1):
            tb = Table(d, t)
            first = next((y for y in tb.cells if y["c"] == 0 and y["r"] == 0), None)
            txt = " ".join(para_text(p) for p in first["tc"].iter(HP + "p")).strip() if first else ""
            nested = d.owner_para(t) not in d.body()
            tables.append({"표": i, "행": tb.rows, "열": tb.cols, "칸 안 표": nested, "A1": txt[:30]})
    elif head.startswith(b"\xd0\xcf\x11\xe0"):
        paras, tables = _hwp5_outline(path)
    else:
        raise ValueError("HWP·HWPX가 아닙니다")
    return {"본문 문단": [{"번호": i, "글": t[:60]} for i, t in enumerate(paras)], "표": tables}


def _hwp5_outline(path):
    import olefile
    import sys
    sys.path.insert(0, HERE)
    from hwp_tool import hwp5_records, hwp5_text, PARA_HEADER, PARA_TEXT, CTRL_HEADER, TABLE, LIST_HEADER
    ole = olefile.OleFileIO(path)
    try:
        fh = ole.openstream("FileHeader").read()
        flags, = struct.unpack_from("<I", fh, 36)
        if flags & 6:
            raise PermissionError("암호·배포용 문서")
        secs = sorted((s for s in ole.listdir() if s[0] == "BodyText"), key=lambda s: int(s[1][7:] or 0))
        paras, tables = [], []
        for s in secs:
            raw = ole.openstream("/".join(s)).read()
            recs = hwp5_records(zlib.decompress(raw, -15) if flags & 1 else raw)
            cur = None
            for i, (tag, lv, data) in enumerate(recs):
                if tag == PARA_HEADER and lv == 0:
                    paras.append("")
                    cur = len(paras) - 1
                elif tag == PARA_TEXT and lv == 1 and cur is not None:
                    paras[cur] = hwp5_text(data)
                elif tag == CTRL_HEADER and data[:4] == b" lbt":
                    rows = cols = 0
                    first = ""
                    for tag2, lv2, d2 in recs[i + 1:i + 40]:
                        if tag2 == TABLE and len(d2) >= 8:
                            rows, cols = struct.unpack_from("<HH", d2, 4)
                        if tag2 == PARA_TEXT and not first:
                            first = hwp5_text(d2)
                        if rows and first:
                            break
                    tables.append({"표": len(tables) + 1, "행": rows, "열": cols, "칸 안 표": lv > 1, "A1": first[:30]})
        return paras, tables
    finally:
        ole.close()
