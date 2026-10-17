"""HWP 5 → HWPX 변환(한글 프로그램 없이, 우리가 새로 짠 것 — 2026-10-16 5단계).
hwp5_records 코덱으로 레코드를 풀어 OWPML(HWPX) XML로 쓴다. 값 표는 hwpx2hwp의 표를 거꾸로 쓴다(정답지·probe로 맞춘 것).
- 길이 값: HWP에 담긴 값(HWPX hp:default 쪽 값)을 default에, 그 절반을 HwpUnitChar case에 쓴다(한컴 HWPX와 같은 모양).
- 줄 배치(PARA_LINE_SEG)는 linesegarray로 그대로 옮긴다.
- 아직 옮기지 못하는 레코드는 conv.warnings에 남긴다(조용히 버리지 않음).
사용: python hwp5_to_hwpx.py 입력.hwp 출력.hwpx (vendor/hwp2hwpx와 다른 우리 것)"""
import datetime
import os
import struct
import sys
import zipfile
import zlib
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hwp5_records as R  # noqa: E402
import hwpx2hwp as X  # noqa: E402
from hwp5_direct import Hwp5, read_records  # noqa: E402

NSDECL = ('xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
          'xmlns:hp10="http://www.hancom.co.kr/hwpml/2016/paragraph" xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
          'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
          'xmlns:hhs="http://www.hancom.co.kr/hwpml/2011/history" xmlns:hm="http://www.hancom.co.kr/hwpml/2011/master-page" '
          'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" xmlns:dc="http://purl.org/dc/elements/1.1/" '
          'xmlns:opf="http://www.idpf.org/2007/opf/" xmlns:ooxmlchart="http://www.hancom.co.kr/hwpml/2016/ooxmlchart" '
          'xmlns:hwpunitchar="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar" xmlns:epub="http://www.idpf.org/2007/ops" '
          'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0"')
XMLHEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
CASE_UNIT = '<hp:switch><hp:case hp:required-namespace="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar">'
CASE_PARA = '<hp:switch><hp:case hp:required-namespace="http://www.hancom.co.kr/hwpml/2016/paragraph">'


def inv(d, prefer=None):
    """값 → 이름(같은 값이 여럿이면 표에서 먼저 나온 이름, prefer가 있으면 그것)."""
    out = {}
    for k, v in d.items():
        out.setdefault(v, k)
    out.update(prefer or {})
    return out


BORDER = inv(X.BORDER_TYPE)
SHAPE = inv(X.LINE_SHAPE)
FCAT = inv(X.FCAT)
FONT_TYPE = inv(X.FONT_TYPE, {1: "TTF", 2: "HFT", 0: "REP"})
NUMFMT = inv(X.NUMFMT)
ALIGN_H, ALIGN_V = inv(X.ALIGN_H), inv(X.ALIGN_V)
BREAK_LATIN, BREAK_NONLATIN = inv(X.BREAK_LATIN), inv(X.BREAK_NONLATIN)
HEADING, LINE_SPACING, LINE_WRAP = inv(X.HEADING), inv(X.LINE_SPACING), inv(X.LINE_WRAP)
TEXT_DIR = {0: "AUTO", 1: "RTL", 2: "LTR"}
TAB_TYPE, HEAD_ALIGN = inv(X.TAB_TYPE), inv(X.HEAD_ALIGN)
UNDERLINE, OUTLINE, SHADOW, SYMMARK = inv(X.UNDERLINE), inv(X.OUTLINE), inv(X.SHADOW), inv(X.SYMMARK)
SLASH = inv(X.SLASH)
HATCH, GRAD, IMG_MODE, IMG_EFFECT = inv(X.HATCH), inv(X.GRAD), inv(X.IMG_MODE), inv(X.IMG_EFFECT)
STYLE_TYPE = inv(X.STYLE_TYPE)
VERT_REL, HORZ_REL, WIDTH_REL, HEIGHT_REL = inv(X.VERT_REL), inv(X.HORZ_REL), inv(X.WIDTH_REL), inv(X.HEIGHT_REL)
VALIGN = {0: "TOP", 1: "CENTER", 2: "BOTTOM", 3: "INSIDE", 4: "OUTSIDE"}
HALIGN = {0: "LEFT", 1: "CENTER", 2: "RIGHT", 3: "INSIDE", 4: "OUTSIDE"}
TEXT_WRAP = {0: "SQUARE", 1: "TOP_AND_BOTTOM", 2: "BEHIND_TEXT", 3: "IN_FRONT_OF_TEXT"}
TEXT_FLOW, NUMBERING_TYPE = inv(X.TEXT_FLOW), inv(X.NUMBERING_TYPE)
LIST_DIR, LIST_VALIGN = inv(X.LIST_DIR), inv(X.LIST_VALIGN)
TBL_BREAK, CAPTION_SIDE, APPLY_PAGE = inv(X.TBL_BREAK), inv(X.CAPTION_SIDE), inv(X.APPLY_PAGE)
NUM_TYPE, PAGENUM_POS = inv(X.NUM_TYPE), inv(X.PAGENUM_POS)
NOTE_NUMBERING, GUTTER, COL_TYPE, COL_LAYOUT = inv(X.NOTE_NUMBERING), inv(X.GUTTER), inv(X.COL_TYPE), inv(X.COL_LAYOUT)
FIELD_TYPE = inv(X.FIELD_ID, {"%unk": "UNKNOWN"})
ARROW, ARROW_SZ, OUTLINE_STYLE = inv(X.ARROW), inv(X.ARROW_SZ), inv(X.OUTLINE_STYLE)
SHAPE_SHADOW, ARC_TYPE, COMPOSE_CIRCLE = inv(X.SHAPE_SHADOW), inv(X.ARC_TYPE), inv(X.COMPOSE_CIRCLE)
SC_TAG = {v: k for k, v in X.SC_ID.items()}
LANGS, LANG_ATTR = X.LANGS, X.LANG_ATTR


def q(v):
    """속성 값 이스케이프."""
    return escape(str(v), {'"': "&quot;"})


def attrs(**kv):
    return "".join(f' {k}="{q(v)}"' for k, v in kv.items() if v is not None)


def color(c, allow_none=True):
    if allow_none and c == 0xFFFFFFFF:
        return "none"
    a = c >> 24 & 0xFF
    r, g, b = c & 0xFF, c >> 8 & 0xFF, c >> 16 & 0xFF
    return (f"#{a:02X}" if a else "#") + f"{r:02X}{g:02X}{b:02X}"


def width(i):
    return X.WIDTHS[i] if 0 <= i < len(X.WIDTHS) else "0.1 mm"


def num(v):
    if v != v:
        return "-nan(ind)"
    if v in (float("inf"), float("-inf")):
        return "inf" if v > 0 else "-inf"
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return repr(v)


def text_xml(s):
    return escape(s)


class Conv:
    def __init__(self, path):
        self.d = Hwp5(path)
        self.warnings = []
        raw = self.d.streams["DocInfo"]
        self.di = list(R.walk(read_records(zlib.decompress(raw, -15) if self.d.compressed else raw)))
        self.by = {}
        for t, lv, data, ctx in self.di:
            self.by.setdefault(t, []).append(R.decode(t, data, ctx))
        self.counts = self.by[17][0]["counts"]
        self.bins = self.by.get(18, [])
        self.bin_name = {}
        # 그림이 가리키는 번호 = BIN_DATA 레코드 순서(1부터). 레코드 안 bin_id는 스트림 이름(BIN%04X) — 둘이 다를 수 있다
        for i, b in enumerate(self.bins, 1):
            self.bin_name[i] = f"image{i}"
        self.memos = []
        self.masters = []                         # (이름, XML) — 바탕쪽 파일

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    # ───────── header.xml ─────────
    def header(self):
        dp = self.by[16][0]
        o = [XMLHEAD, f'<hh:head {NSDECL} version="1.5" secCnt="{dp["sections"]}">',
             f'<hh:beginNum{attrs(page=dp["start_page"], footnote=dp["start_footnote"], endnote=dp["start_endnote"], pic=dp["start_picture"], tbl=dp["start_table"], equation=dp["start_equation"])}/>',
             "<hh:refList>"]
        faces = self.by.get(19, [])
        o.append(f'<hh:fontfaces itemCnt="7">')
        i = 0
        for li, lang in enumerate(LANGS):
            n = self.counts[1 + li]
            o.append(f'<hh:fontface lang="{lang}" fontCnt="{n}">')
            for k in range(n):
                o.append(self.font(k, faces[i]))
                i += 1
            o.append("</hh:fontface>")
        o.append("</hh:fontfaces>")
        o.append(self.group("borderFills", [self.border_fill(k + 1, x) for k, x in enumerate(self.by.get(20, []))]))
        o.append(self.group("charProperties", [self.char_pr(k, x) for k, x in enumerate(self.by.get(21, []))]))
        o.append(self.group("tabProperties", [self.tab_pr(k, x) for k, x in enumerate(self.by.get(22, []))]))
        o.append(self.group("numberings", [self.numbering(k + 1, x) for k, x in enumerate(self.by.get(23, []))]))
        o.append(self.group("bullets", [self.bullet(k + 1, x) for k, x in enumerate(self.by.get(24, []))]))
        o.append(self.group("paraProperties", [self.para_pr(k, x) for k, x in enumerate(self.by.get(25, []))]))
        o.append(self.group("styles", [self.style(k, x) for k, x in enumerate(self.by.get(26, []))]))
        o.append(self.group("memoProperties", [self.memo_pr(k + 1, x) for k, x in enumerate(self.by.get(92, []))]))
        if self.by.get(96) or self.by.get(97):
            o.append(self.group("trackChanges", [self.track(k + 1, x) for k, x in enumerate(self.by.get(96, []))]))
            o.append(self.group("trackChangeAuthors", [
                f'<hh:trackChangeAuthor{attrs(name=x["name"], mark=struct.unpack_from("<I", x["_rest"] + bytes(4))[0], id=k + 1)}/>'
                for k, x in enumerate(self.by.get(97, []))]))
        o.append("</hh:refList>")
        tgt = {0: "HWP201X", 1: "HWP200X", 2: "MS_WORD"}.get((self.by.get(30) or [{"target": 0}])[0]["target"], "HWP201X")
        o.append(f'<hh:compatibleDocument targetProgram="{tgt}"><hh:layoutCompatibility/></hh:compatibleDocument>')
        o.append('<hh:docOption><hh:linkinfo path="" pageInherit="0" footnoteInherit="0"/></hh:docOption>')
        flags = (self.by.get(32) or [{"values": [[56]]}])[0]["values"][0][0]
        o.append(f'<hh:trackchageConfig flags="{flags}"/></hh:head>')
        return "".join(o)

    @staticmethod
    def group(name, items):
        if not items:
            return ""
        return f'<hh:{name} itemCnt="{len(items)}">' + "".join(items) + f"</hh:{name}>"

    def font(self, k, f):
        s = f'<hh:font{attrs(id=k, face=f["name"], type=FONT_TYPE.get(f["attr"] & 3, "TTF"), isEmbedded=0)}>'
        if f["attr"] & 0x80:
            s += f'<hh:substFont{attrs(face=f.get("alt_name", ""), type=FONT_TYPE.get(f.get("alt_type", 1), "TTF"), isEmbedded=0, binaryItemIDRef="")}/>'
        if f["attr"] & 0x40:
            ti = f["type_info"]
            s += ('<hh:typeInfo' + attrs(familyType=FCAT.get(ti[0], "FCAT_UNKNOWN"), weight=ti[2], proportion=ti[3],
                                         contrast=ti[4], strokeVariation=ti[5], armStyle=ti[6], letterform=ti[7],
                                         midline=ti[8], xHeight=ti[9]) + '/>')
        return s + "</hh:font>"

    def fill(self, f):
        t = f["type"]
        if not t:
            return ""
        s = "<hc:fillBrush>"
        al = list(f.get("alphas", []))
        if t & 1:
            a = al.pop(0) if al else 0
            hs = f.get("pat_type", -1)
            s += f'<hc:winBrush{attrs(faceColor=color(f["bg_color"]), hatchColor=color(f["pat_color"]), hatchStyle=HATCH.get(hs) if hs >= 0 else None, alpha=a)}/>'
        if t & 4:
            a = al.pop(0) if al else 0
            s += (f'<hc:gradation{attrs(type=GRAD.get(f["grad_type"], "LINEAR"), angle=f["grad_angle"], centerX=f["grad_cx"], centerY=f["grad_cy"], step=f["grad_step"], colorNum=f["grad_n"], stepCenter=f["extra"][0] if f["extra"] else 50, alpha=a)}>'
                  + "".join(f'<hc:color value="{color(c, False)}"/>' for c in f["grad_colors"]) + "</hc:gradation>")
        if t & 2:
            a = al.pop(0) if al else 0
            contrast, bright, effect, bid = struct.unpack("<bbBH", f["img_info"])
            s += (f'<hc:imgBrush mode="{IMG_MODE.get(f["img_type"], "TOTAL")}"><hc:img'
                  + attrs(binaryItemIDRef=self.bin_name.get(bid, f"image{bid}"), bright=bright, contrast=contrast,
                          effect=IMG_EFFECT.get(effect, "REAL_PIC"), alpha=a) + "/></hc:imgBrush>")
        return s + "</hc:fillBrush>"

    def border_fill(self, k, b):
        a = b["attr"]
        s = (f'<hh:borderFill{attrs(id=k, threeD=a & 1, shadow=a >> 1 & 1, centerLine="VERTICAL" if a >> 13 & 1 else "NONE", breakCellSeparateLine=0)}>'
             f'<hh:slash{attrs(type=SLASH.get(a >> 2 & 7, "NONE"), Crooked=a >> 8 & 3, isCounter=a >> 11 & 1)}/>'
             f'<hh:backSlash{attrs(type=SLASH.get(a >> 5 & 7, "NONE"), Crooked=a >> 10 & 1, isCounter=a >> 12 & 1)}/>')
        for side, t, w, c in zip(("left", "right", "top", "bottom"), b["line_types"], b["line_widths"], b["line_colors"]):
            s += f'<hh:{side}Border{attrs(type=BORDER.get(t, "NONE"), width=width(w), color=color(c))}/>'
        if b["diag_type"]:
            s += f'<hh:diagonal{attrs(type=BORDER.get(b["diag_type"], "NONE"), width=width(b["diag_width"]), color=color(b["diag_color"]))}/>'
        return s + self.fill(b["fill"]) + "</hh:borderFill>"

    def char_pr(self, k, c):
        a = c["attr"]
        s = f'<hh:charPr{attrs(id=k, height=c["base_size"], textColor=color(c["text_color"]), shadeColor=color(c["shade_color"]), useFontSpace=a >> 25 & 1, useKerning=a >> 30 & 1, symMark=SYMMARK.get(a >> 21 & 15, "NONE"), borderFillIDRef=c.get("border_fill_id", 0))}>'
        for name, key in (("fontRef", "face_ids"), ("ratio", "ratios"), ("spacing", "spacings"), ("relSz", "rel_sizes"),
                          ("offset", "offsets")):
            s += f"<hh:{name}" + "".join(f' {la}="{v}"' for la, v in zip(LANG_ATTR, c[key])) + "/>"
        if a & 1:
            s += "<hh:italic/>"
        if a & 2:
            s += "<hh:bold/>"
        ut, us = a >> 2 & 3, a >> 4 & 15
        strike = a >> 18 & 7
        if strike and ut == 2:                    # 가운데 밑줄 = 취소선 표현(hwpx2hwp 참고)
            ut = 0
        s += f'<hh:underline{attrs(type=UNDERLINE.get(ut, "NONE"), shape=SHAPE.get(us, "SOLID") if ut else "SOLID", color=color(c["underline_color"]))}/>'
        s += f'<hh:strikeout{attrs(shape=SHAPE.get(a >> 26 & 15, "SOLID") if strike else "NONE", color=color(c.get("strike_color", 0)))}/>'
        s += f'<hh:outline type="{OUTLINE.get(a >> 8 & 7, "NONE")}"/>'
        s += f'<hh:shadow{attrs(type=SHADOW.get(a >> 11 & 3, "NONE"), color=color(c["shadow_color"]), offsetX=c["shadow_x"], offsetY=c["shadow_y"])}/>'
        for bit, name in ((13, "emboss"), (14, "engrave"), (15, "supscript"), (16, "subscript")):
            if a >> bit & 1:
                s += f"<hh:{name}/>"
        return s + "</hh:charPr>"

    def tab_pr(self, k, t):
        s = f'<hh:tabPr{attrs(id=k, autoTabLeft=t["attr"] & 1, autoTabRight=t["attr"] >> 1 & 1)}'
        if not t["tabs"]:
            return s + "/>"
        items = ""
        for p, ty, ld, _ in t["tabs"]:
            kind = dict(type=TAB_TYPE.get(ty, "LEFT"), leader=BORDER.get(ld, "NONE"))
            items += (CASE_UNIT + f'<hh:tabItem{attrs(pos=p // 2, **kind, unit="HWPUNIT")}/></hp:case><hp:default>'
                      f'<hh:tabItem{attrs(pos=p, **kind)}/></hp:default></hp:switch>')
        return s + ">" + items + "</hh:tabPr>"

    def para_head(self, level, h, fmt, start, tag="paraHead"):
        a, wadj, toff, cp = h
        return (f'<hh:{tag}' + attrs(start=start, level=level, align=HEAD_ALIGN.get(a & 3, "LEFT"), useInstWidth=a >> 2 & 1,
                                     autoIndent=a >> 3 & 1, widthAdjust=wadj,
                                     textOffsetType="HWPUNIT" if a >> 4 & 1 else "PERCENT", textOffset=toff,
                                     numFormat=NUMFMT.get(a >> 5 & 0xFF, "DIGIT"), charPrIDRef=cp, checkable=a >> 5 & 1)
                + f">{text_xml(fmt)}</hh:{tag}>")

    def numbering(self, k, n):
        levels = n["levels"] + n.get("ext_levels", [])
        starts = n.get("level_starts", [1] * 7) + n.get("ext_starts", [1] * 3)
        s = f'<hh:numbering{attrs(id=k, start=n["start"])}>'
        for i, lv in enumerate(levels):
            s += self.para_head(i + 1, lv["head"], lv["format"], starts[i] if i < len(starts) else 1)
        return s + "</hh:numbering>"

    def bullet(self, k, b):
        s = f'<hh:bullet{attrs(id=k, char=chr(b["char"]) if b["char"] else "", useImage=1 if b["image_id"] else 0)}>'
        s += self.para_head(0, b["head"], "", None)
        return s + "</hh:bullet>"

    def para_pr(self, k, p):
        a1, a2 = p["attr1"], p.get("attr2", 0)
        level = struct.unpack_from("<I", p.get("_rest", b"") + bytes(4))[0] or (a1 >> 25 & 7)
        head_type = HEADING.get(a1 >> 23 & 3, "NONE")
        s = (f'<hh:paraPr{attrs(id=k, tabPrIDRef=p["tab_def_id"], condense=a1 >> 9 & 0x7F, fontLineHeight=a1 >> 22 & 1, snapToGrid=a1 >> 8 & 1, suppressLineNumbers=0, checked=0, textDir=TEXT_DIR.get(a2 >> 2 & 3, "LTR"))}>'
             f'<hh:align{attrs(horizontal=ALIGN_H.get(a1 >> 2 & 7, "JUSTIFY"), vertical=ALIGN_V.get(a1 >> 20 & 3, "BASELINE"))}/>')
        heading = f'<hh:heading{attrs(type=head_type, idRef=p["numbering_id"], level=level)}/>'
        if level > 6:                             # 수준 7~10은 2016 확장(case), 옛 판에는 없음(default)
            s += CASE_PARA + heading + '</hp:case><hp:default><hh:heading type="NONE" idRef="0" level="0"/></hp:default></hp:switch>'
        else:
            s += heading
        s += (f'<hh:breakSetting{attrs(breakLatinWord=BREAK_LATIN.get(a1 >> 5 & 3, "KEEP_WORD"), breakNonLatinWord=BREAK_NONLATIN.get(a1 >> 7 & 1, "KEEP_WORD"), widowOrphan=a1 >> 16 & 1, keepWithNext=a1 >> 17 & 1, keepLines=a1 >> 18 & 1, pageBreakBefore=a1 >> 19 & 1, lineWrap=LINE_WRAP.get(a2 & 3, "BREAK"))}/>'
              f'<hh:autoSpacing{attrs(eAsianEng=a2 >> 4 & 1, eAsianNum=a2 >> 5 & 1)}/>')
        lst = p.get("attr3", a1 & 3) & 0x1F
        ls = p.get("line_spacing", p["line_spacing_old"])

        def margin(div):
            # HWP 여백 값 = 2×값(+1이면 글자 단위 CHAR) — 맨 아래 비트가 단위(예: -519 → 글자 단위 -260)
            def one(v):
                if div == 1:
                    return attrs(value=v, unit="HWPUNIT")
                return attrs(value=(v - 1) // 2, unit="CHAR") if v & 1 else attrs(value=v // 2, unit="HWPUNIT")
            return ("<hh:margin>" + "".join(f'<hc:{n}{one(p[key])}/>' for n, key in (
                ("intent", "indent"), ("left", "left"), ("right", "right"), ("prev", "prev"), ("next", "next")))
                + "</hh:margin>" + f'<hh:lineSpacing{attrs(type=LINE_SPACING.get(lst, "PERCENT"), value=ls if lst == 0 else ls // div, unit="HWPUNIT")}/>')
        s += CASE_UNIT + margin(2) + "</hp:case><hp:default>" + margin(1) + "</hp:default></hp:switch>"
        s += f'<hh:border{attrs(borderFillIDRef=p["border_fill_id"], offsetLeft=p["border_left"], offsetRight=p["border_right"], offsetTop=p["border_top"], offsetBottom=p["border_bottom"], connect=a1 >> 28 & 1, ignoreMargin=a1 >> 29 & 1)}/>'
        return s + "</hh:paraPr>"

    def style(self, k, x):
        return (f'<hh:style{attrs(id=k, type=STYLE_TYPE.get(x["attr"] & 7, "PARA"), name=x["name"], engName=x["eng_name"], paraPrIDRef=x["para_shape_id"], charPrIDRef=x["char_shape_id"], nextStyleIDRef=x["next_id"], langID=x["lang"], lockForm=x["attr"] >> 2 & 1)}/>')

    def memo_pr(self, k, m):
        return (f'<hh:memoPr{attrs(id=k, width=m["width"], lineWidth=m["line_width"], lineType=BORDER.get(m["line_type"], "SOLID"), lineColor=color(m["line_color"]), fillColor=color(m["fill_color"]), activeColor=color(m["active_color"]), memoType="NOMAL")}/>')

    def track(self, k, t):
        y, mo, d, h, mi, author = t["time"]
        utc = datetime.datetime(y, mo, d, h, mi).astimezone(datetime.timezone.utc)
        typ = {16: "Insert", 17: "Delete", 18: "CharShape", 19: "ParaShape"}.get(t["type"], "Insert")
        rest = t.get("_rest", b"")
        hide = struct.unpack_from("<I", rest, len(rest) - 4)[0] if len(rest) >= 4 else 0
        ps = struct.unpack_from("<I", rest, 6)[0] if typ == "ParaShape" and len(rest) >= 10 else None
        return f'<hh:trackChange{attrs(type=typ, date=utc.strftime("%Y-%m-%dT%H:%M:%SZ"), authorID=author, hide=hide, id=k, parashapeID=ps)}/>'

    # ───────── 본문 ─────────
    @staticmethod
    def tree(recs):
        """레코드 → 나무(수준으로 부모·자식). node = {tag, lv, x(풀린 값), raw, kids}"""
        root = {"lv": -1, "kids": []}
        stack = [root]
        for t, lv, data, ctx in recs:
            n = {"tag": t, "lv": lv, "x": R.decode(t, data, ctx), "raw": bytes(data), "kids": []}
            while stack[-1]["lv"] >= lv:
                stack.pop()
            stack[-1]["kids"].append(n)
            stack.append(n)
        return root["kids"]

    def section(self, recs):
        nodes = self.tree(R.walk(recs))
        # 구역 끝 메모 내용: MEMO_LIST(93) 뒤 LIST_HEADER·문단(마지막 문단의 자식으로 붙어 있다 — hwpx2hwp와 같은 자리)
        self.memo_lists = {}
        if nodes:
            kids = nodes[-1]["kids"]
            for i, k in enumerate(kids):
                if k["tag"] == 93 and i + 1 < len(kids) and kids[i + 1]["tag"] == 72:
                    lh, paras = self.list_paras(kids, i + 1)
                    self.memo_lists[k["x"]["memo_id"]] = (lh, paras)
        # 바탕쪽이 구역 정의 아래가 아니라 구역 마지막 문단 아래(메모 자리)에 놓인 문서도 있다 — 메모(93 뒤)와 구분
        self.para_masters = []
        if nodes:
            kids = nodes[-1]["kids"]
            for j, k in enumerate(kids):
                if k["tag"] == 72 and k["x"]["kind"] == "plain" and (j == 0 or kids[j - 1]["tag"] != 93)                         and len(k["raw"]) >= 26:
                    self.para_masters.append(self.list_paras(kids, j))
        out = [XMLHEAD, f"<hs:sec {NSDECL}>"]
        out += [self.para(p) for p in nodes if p["tag"] == 66]
        return "".join(out) + "</hs:sec>"

    def para_list(self, paras):
        return "".join(self.para(p) for p in paras if p["tag"] == 66)

    @staticmethod
    def list_paras(kids, start):
        """kids[start]가 LIST_HEADER → (그 헤더, 뒤따르는 문단 노드 para_count개)."""
        lh = kids[start]
        n = lh["x"]["para_count"]
        return lh, [k for k in kids[start + 1:start + 1 + n] if k["tag"] == 66]

    def sublist(self, lh, paras):
        x = lh["x"]
        a = x["attr"]
        head = x["kind"] == "header"
        s = (f'<hp:subList{attrs(id="", textDirection=LIST_DIR.get(a & 7, "HORIZONTAL"), lineWrap=LINE_WRAP.get(a >> 3 & 3, "BREAK"), vertAlign=LIST_VALIGN.get(a >> 5 & 3, "TOP"), linkListIDRef=0, linkListNextIDRef=0, textWidth=x.get("text_width", 0) if head else 0, textHeight=x.get("text_height", 0) if head else 0, hasTextRef=x.get("text_ref", 0) if head else 0, hasNumRef=x.get("num_ref", 0) if head else 0)}>')
        return s + self.para_list(paras) + "</hp:subList>"

    def para(self, p):
        h = p["x"]
        kids = p["kids"]
        text = next((k["raw"] for k in kids if k["tag"] == 67), b"\r\x00")
        runs = next((k["x"]["runs"] for k in kids if k["tag"] == 68), [[0, 0]])
        segs = next((k["x"]["segs"] for k in kids if k["tag"] == 69), [])
        tags = next((k["x"]["tags"] for k in kids if k["tag"] == 70), [])
        ctrls = [k for k in kids if k["tag"] == 71]
        w = list(struct.unpack(f"<{len(text) // 2}H", text))
        s = [f'<hp:p{attrs(id=h["instance_id"], paraPrIDRef=h["para_shape_id"], styleIDRef=h["style_id"], pageBreak=h["break_type"] >> 2 & 1, columnBreak=h["break_type"] >> 3 & 1, merged=h.get("track_merge", 0))}>']
        marks = {}                                # 위치 → 범위 태그 표시(형광펜·변경 추적)
        for st, en, tag in tags:
            kind, val = tag >> 24, tag & 0xFFFFFF
            if kind == 2:
                marks.setdefault(st, []).append(f'<hp:markpenBegin color="{color(val, False)}"/>')
                marks.setdefault(en, []).insert(0, "<hp:markpenEnd/>")
            elif kind in (0x10, 0x11):
                nm = "insert" if kind == 0x10 else "delete"
                marks.setdefault(st, []).append(f'<hp:{nm}Begin Id="{val}" TcId="{val}"/>')
                marks.setdefault(en, []).insert(0, f'<hp:{nm}End Id="{val}" TcId="{val}" paraend="0"/>')
        bounds = [r[0] for r in runs[1:]]
        st_ = {"ci": 0, "ri": 0, "t": False, "last": None, "split": False}
        cur = [f'<hp:run charPrIDRef="{runs[0][1]}">']
        buf = []
        # 컨트롤 문자 자리에서 시작하는 형광펜·변경 추적 표시는 다음 글자 자리로 옮긴다(한컴 HWPX와 같게)
        j = 0
        while j < len(w):
            step = 8 if w[j] < 32 and w[j] not in (0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31) else 1
            if j in marks and step == 8:
                marks.setdefault(j + 8, [])[:0] = marks.pop(j)
            j += step

        def flush_text():
            if buf:
                if not st_["t"]:
                    cur.append("<hp:t>")
                    st_["t"] = True
                cur.append(text_xml("".join(buf)))
                buf.clear()
                st_["last"] = "text"

        def in_t(xml):
            flush_text()
            if not st_["t"]:
                cur.append("<hp:t>")
                st_["t"] = True
            cur.append(xml)
            st_["last"] = "text"

        def new_run(cp):
            close_t()
            cur.append("</hp:run>")
            s.append("".join(cur))
            cur[:] = [f'<hp:run charPrIDRef="{cp}">']
            st_["last"] = None

        def close_t():
            flush_text()
            if st_["t"]:
                cur.append("</hp:t>")
                st_["t"] = False

        i = 0
        while i < len(w):
            if st_["ri"] < len(bounds) and i >= bounds[st_["ri"]]:
                st_["ri"] += 1
                flush_text()
                st_["prev_last"] = st_["last"]
                new_run(runs[st_["ri"]][1])
                st_["after_split"] = st_["split"]
                st_["split"] = False
                continue
            if st_["split"] and not (w[i] == 2):    # 구역·단 정의 다음 내용은 새 run(한컴 HWPX와 같게)
                new_run(runs[st_["ri"]][1])
                st_["split"] = False
            if i in marks:
                for m in marks.pop(i):
                    in_t(m)
            c = w[i]
            if c == 13:
                i += 1
                continue
            if c >= 32:
                if 0xD800 <= c < 0xDC00 and i + 1 < len(w):
                    buf.append(struct.pack("<HH", c, w[i + 1]).decode("utf-16-le", errors="replace"))
                    i += 2
                else:
                    buf.append(chr(c))
                    i += 1
                continue
            if c in (0, 10, 24, 25, 26, 27, 28, 29, 30, 31):
                tag_ = {10: "<hp:lineBreak/>", 24: "<hp:hyphen/>", 30: "<hp:nbSpace/>", 31: "<hp:fwSpace/>"}.get(c)
                if tag_:
                    in_t(tag_)
                i += 1
                continue
            unit = w[i:i + 8]
            i += 8
            if c == 9:
                in_t(f'<hp:tab{attrs(width=unit[1] | unit[2] << 16, leader=unit[3] & 0xFF, type=unit[3] >> 8)}/>')
                continue
            if c == 8:
                in_t('<hp:titleMark ignore="1"/>')
                continue
            if c == 4:
                close_t()
                fid = self.open_fields.pop() if self.open_fields else (0, 0)
                cur.append(f'<hp:ctrl><hp:fieldEnd{attrs(beginIDRef=fid[0], fieldid=fid[1])}/></hp:ctrl>')
                st_["last"] = "ctrl"
                continue
            if c in (5, 6, 7, 19, 20):
                self.warn(f"인라인 컨트롤 {c}는 아직 옮기지 않음")
                continue
            if st_["ci"] >= len(ctrls):            # 확장 컨트롤: 다음 CTRL_HEADER와 짝
                self.warn("컨트롤 문자보다 CTRL_HEADER가 적음")
                continue
            node = ctrls[st_["ci"]]
            st_["ci"] += 1
            close_t()
            cur.append(self.control(node))
            st_["last"] = "ctrl"
            if node["x"]["ctrl_id"] in ("secd", "cold"):
                st_["split"] = True
        for pos in sorted(marks):                  # 문단 끝(마지막 글자 뒤)에 닫히는 표시
            for m in marks[pos]:
                in_t(m)
        flush_text()
        if st_["split"]:                           # 구역·단 정의뿐인 문단: 빈 글 run을 하나 더
            new_run(runs[st_["ri"]][1])
            cur.append("<hp:t/>")
        elif st_["last"] == "ctrl":                # 컨트롤로 끝나는 문단: 끝에 빈 hp:t
            close_t()
            cur.append("<hp:t/>")
        elif st_["last"] is None and (st_.get("after_split") or len(s) > 1 and st_.get("prev_last") == "ctrl"):
            cur.append("<hp:t/>")                  # 컨트롤 뒤에 나뉜 빈 마지막 run(한컴 HWPX와 같게)
        close_t()
        cur.append("</hp:run>")
        s.append("".join(cur))
        if segs:
            s.append("<hp:linesegarray>" + "".join(
                f'<hp:lineseg{attrs(textpos=g[0], vertpos=g[1], vertsize=g[2], textheight=g[3], baseline=g[4], spacing=g[5], horzpos=g[6], horzsize=g[7], flags=g[8])}/>'
                for g in segs) + "</hp:linesegarray>")
        return "".join(s) + "</hp:p>"

    def first_list(self, kids):
        i = next(j for j, k in enumerate(kids) if k["tag"] == 72)
        return self.list_paras(kids, i)

    def control(self, n):
        x = n["x"]
        c = x["ctrl_id"]
        kids = n["kids"]
        if c == "secd":
            return self.sec_pr(x, kids)
        if c == "cold":
            return "<hp:ctrl>" + self.col_pr(x) + "</hp:ctrl>"
        if c in ("head", "foot"):
            lh, paras = self.first_list(kids)
            tag = "header" if c == "head" else "footer"
            hid = struct.unpack_from("<I", x.get("_rest", b"") + bytes(4))[0]
            return (f'<hp:ctrl><hp:{tag}{attrs(id=hid, applyPageType=APPLY_PAGE.get(x["attr"] & 3, "BOTH"))}>'
                    + self.sublist(lh, paras) + f"</hp:{tag}></hp:ctrl>")
        if c in ("fn  ", "en  "):
            lh, paras = self.first_list(kids)
            tag = "footNote" if c == "fn  " else "endNote"
            return (f'<hp:ctrl><hp:{tag}{attrs(number=x["number"], suffixChar=x["suffix_char"], instId=x.get("instance_id", 0))}>'
                    + self.sublist(lh, paras) + f"</hp:{tag}></hp:ctrl>")
        if c == "atno":
            a = x["attr"]
            return (f'<hp:ctrl><hp:autoNum{attrs(num=x["number"], numType=NUM_TYPE.get(a & 15, "PAGE"))}>'
                    f'<hp:autoNumFormat{attrs(type=NUMFMT.get(a >> 4 & 0xFF, "DIGIT"), userChar=ch(x["user_char"]), prefixChar=ch(x["prefix_char"]), suffixChar=ch(x["suffix_char"]), supscript=a >> 12 & 1)}/>'
                    "</hp:autoNum></hp:ctrl>")
        if c == "nwno":
            return f'<hp:ctrl><hp:newNum{attrs(num=x["number"], numType=NUM_TYPE.get(x["attr"] & 15, "PAGE"))}/></hp:ctrl>'
        if c == "pgnp":
            a = x["attr"]
            return f'<hp:ctrl><hp:pageNum{attrs(pos=PAGENUM_POS.get(a >> 8 & 15, "NONE"), formatType=NUMFMT.get(a & 0xFF, "DIGIT"), sideChar=ch(x["dash_char"]))}/></hp:ctrl>'
        if c == "pghd":
            a = x["attr"]
            names = ("hideHeader", "hideFooter", "hideMasterPage", "hideBorder", "hideFill", "hidePageNum")
            return "<hp:ctrl><hp:pageHiding" + "".join(f' {k}="{a >> i & 1}"' for i, k in enumerate(names)) + "/></hp:ctrl>"
        if c == "pgct":
            return f'<hp:ctrl><hp:pageNumCtrl pageStartsOn="{APPLY_PAGE.get(x["attr"] & 3, "BOTH")}"/></hp:ctrl>'
        if c == "bokm":
            return f'<hp:ctrl><hp:bookmark name="{q(self.data_name(kids))}"/></hp:ctrl>'
        if c == "idxm":
            s = "<hp:ctrl><hp:indexmark>" + f"<hp:firstKey>{text_xml(x['keyword1'])}</hp:firstKey>"
            if x["keyword2"]:
                s += f"<hp:secondKey>{text_xml(x['keyword2'])}</hp:secondKey>"
            return s + "</hp:indexmark></hp:ctrl>"
        if c == "tcmt":
            lh, paras = self.first_list(kids)
            return "<hp:ctrl><hp:hiddenComment>" + self.sublist(lh, paras) + "</hp:hiddenComment></hp:ctrl>"
        if c.startswith("%"):
            return self.field(x, kids)
        if c == "tbl ":
            return self.table(x, kids)
        if c == "gso ":
            return self.gso(x, kids)
        if c == "eqed":
            return self.equation(x, kids)
        if c == "form":
            return self.form(x, kids)
        if c == "tcps":
            ids = x["char_shape_ids"]
            return (f'<hp:compose{attrs(circleType=COMPOSE_CIRCLE.get(x["border_type"], "CHAR"), charSz=x["inner_size"], composeType="OVERLAP" if x["expand"] else "SPREAD", charPrCnt=len(ids), composeText=x["text"])}>'
                    + "".join(f'<hp:charPr prIDRef="{i}"/>' for i in ids) + "</hp:compose>")
        if c == "tdut":
            return (f'<hp:dutmal{attrs(posType={0: "TOP", 1: "BOTTOM", 2: "CENTER"}.get(x["position"], "TOP"), szRatio=x["size_ratio"], option=x["option"], styleIDRef=x["style_id"], align=ALIGN_H.get(x["align"], "CENTER"))}>'
                    f"<hp:mainText>{text_xml(x['main_text'])}</hp:mainText><hp:subText>{text_xml(x['sub_text'])}</hp:subText></hp:dutmal>")
        self.warn(f"컨트롤 {c!r}는 아직 옮기지 않음(빠짐)")
        return ""

    @staticmethod
    def data_name(kids):
        for k in kids:
            if k["tag"] == 87 and "set" in k["x"]:
                for it in k["x"]["set"]["items"]:
                    if it["id"] == 0x4000 and it["type"] == 1:
                        return it["value"]
        return ""

    def field(self, x, kids):
        c = x["ctrl_id"]
        memo = x.get("memo_index", 0) if c == "%unk" and x["command"].startswith("MEMO") else 0
        typ = "MEMO" if memo else FIELD_TYPE.get(c, "UNKNOWN")
        fid = R.ctrl_value("%%me" if memo else c)
        self.open_fields.append((x["field_id"], fid))
        plist = field_params(typ, x["command"], x["extra_attr"], memo)
        params = "".join(param_xml(kind, name, val) for kind, name, val in plist)
        s = (f'<hp:ctrl><hp:fieldBegin{attrs(id=x["field_id"], type=typ, name=self.data_name(kids), editable=x["attr"] & 1, dirty=x["attr"] >> 15 & 1, zorder=memo if memo else -1, fieldid=fid)}>'
             f'<hp:parameters cnt="{len(plist)}" name="">{params}</hp:parameters>')
        if memo and memo in self.memo_lists:
            s += self.sublist(*self.memo_lists[memo])
        return s + "</hp:fieldBegin></hp:ctrl>"

    def sec_pr(self, x, kids):
        a = x["attr"]
        rest = x.get("_rest", b"") + bytes(8)
        pd = next((k["x"] for k in kids if k["tag"] == 73), None)
        notes = [k["x"] for k in kids if k["tag"] == 74]
        borders = [k["x"] for k in kids if k["tag"] == 75]
        masters = []                              # 바탕쪽: LIST_HEADER + 문단 → masterpageN.xml
        found = [self.list_paras(kids, j) for j, k in enumerate(kids) if k["tag"] == 72] + self.para_masters
        self.para_masters = []
        for lh, paras in found:
            if lh["x"]["kind"] != "header":        # 문단 아래 놓인 것은 'plain'으로 풀려 있다 → 폭·높이를 다시 읽는다
                lh = dict(lh, x=R.decode(72, lh["raw"], {"parent": 71, "ctrl": "secd"}))
            name = f"masterpage{len(self.masters)}"
            self.masters.append((name, XMLHEAD + f'<masterPage {NSDECL} id="{name}" type="BOTH" pageNumber="0" '
                                 'pageDuplicate="0" pageFront="0">' + self.sublist(lh, paras) + "</masterPage>"))
            masters.append(name)
        border = "HIDE_FIRST" if a >> 3 & 1 else "SHOW_FIRST" if a >> 8 & 1 else "SHOW_ALL"
        fill = "HIDE_FIRST" if a >> 4 & 1 else "SHOW_FIRST" if a >> 9 & 1 else "SHOW_ALL"
        s = (f'<hp:secPr{attrs(id="", textDirection=LIST_DIR.get(a >> 16 & 7, "HORIZONTAL"), spaceColumns=x["column_gap"], tabStop=x["default_tab"], tabStopVal=x["default_tab"] // 2, tabStopUnit="HWPUNIT", outlineShapeIDRef=x["numbering_para_shape_id"], memoShapeIDRef=struct.unpack_from("<I", rest, 4)[0], textVerticalWidthHead=0, masterPageCnt=len(masters))}>'
             f'<hp:grid{attrs(lineGrid=x["vert_grid"], charGrid=x["horz_grid"], wonggojiFormat=a >> 22 & 1)}/>'
             f'<hp:startNum{attrs(pageStartsOn=APPLY_PAGE.get(a >> 20 & 3, "BOTH"), page=x["start_page"], pic=x["start_picture"], tbl=x["start_table"], equation=x["start_equation"])}/>'
             f'<hp:visibility{attrs(hideFirstHeader=a & 1, hideFirstFooter=a >> 1 & 1, hideFirstMasterPage=a >> 2 & 1, border=border, fill=fill, hideFirstPageNum=a >> 5 & 1, hideFirstEmptyLine=a >> 19 & 1, showLineNumber=0)}/>'
             '<hp:lineNumberShape restartType="0" countBy="0" distance="0" startNumber="0"/>')
        if pd:
            s += (f'<hp:pagePr{attrs(landscape="NARROWLY" if pd["attr"] & 1 else "WIDELY", width=pd["width"], height=pd["height"], gutterType=GUTTER.get(pd["attr"] >> 1 & 3, "LEFT_ONLY"))}>'
                  f'<hp:margin{attrs(header=pd["header"], footer=pd["footer"], gutter=pd["gutter"], left=pd["left"], right=pd["right"], top=pd["top"], bottom=pd["bottom"])}/></hp:pagePr>')
        places = ({0: "EACH_COLUMN", 1: "MERGED_COLUMN", 2: "RIGHT_MOST_COLUMN"}, {0: "END_OF_DOCUMENT", 1: "END_OF_SECTION"})
        for tag, nx, pl in zip(("footNotePr", "endNotePr"), notes[:2], places):
            na = nx["attr"]
            s += (f'<hp:{tag}><hp:autoNumFormat{attrs(type=NUMFMT.get(na & 0xFF, "DIGIT"), userChar=ch(nx["user_char"]), prefixChar=ch(nx["prefix_char"]), suffixChar=ch(nx["suffix_char"]), supscript=na >> 12 & 1)}/>'
                  f'<hp:noteLine{attrs(length=nx["line_length"], type=BORDER.get(nx["line_type"], "SOLID"), width=width(nx["line_width"]), color=color(nx["line_color"]))}/>'
                  f'<hp:noteSpacing{attrs(betweenNotes=nx["between"], belowLine=nx["below"], aboveLine=nx["above"])}/>'
                  f'<hp:numbering{attrs(type=NOTE_NUMBERING.get(na >> 10 & 3, "CONTINUOUS"), newNum=nx["start"])}/>'
                  f'<hp:placement{attrs(place=pl.get(na >> 8 & 3, pl[0]), beneathText=na >> 13 & 1)}/></hp:{tag}>')
        for typ, b in zip(("BOTH", "EVEN", "ODD"), borders):
            ba = b["attr"]
            s += (f'<hp:pageBorderFill{attrs(type=typ, borderFillIDRef=b["border_fill_id"], textBorder="PAPER" if ba & 1 else "CONTENT", headerInside=ba >> 1 & 1, footerInside=ba >> 2 & 1, fillArea={0: "PAPER", 1: "PAGE", 2: "BORDER"}.get(ba >> 3 & 3, "PAPER"))}>'
                  f'<hp:offset{attrs(left=b["left"], right=b["right"], top=b["top"], bottom=b["bottom"])}/></hp:pageBorderFill>')
        s += "".join(f'<hp:masterPage idRef="{m}"/>' for m in masters)
        return s + "</hp:secPr>"

    @staticmethod
    def col_pr(x):
        a = x["attr"]
        same = a >> 12 & 1
        s = f'<hp:colPr{attrs(id="", type=COL_TYPE.get(a & 3, "NEWSPAPER"), layout=COL_LAYOUT.get(a >> 10 & 3, "LEFT"), colCount=a >> 2 & 0xFF, sameSz=same, sameGap=x["gap"] if same else 0)}'
        inner = ""
        if x.get("line_type"):
            inner += f'<hp:colLine{attrs(type=BORDER.get(x["line_type"], "SOLID"), width=width(x["line_width"]), color=color(x["line_color"]))}/>'
        if not same:
            inner += "".join(f'<hp:colSz{attrs(width=wd, gap=x["gap"])}/>' for wd in x.get("widths", []))
        return s + (">" + inner + "</hp:colPr>" if inner else "/>")

    @staticmethod
    def obj_attrs(x):
        a = x["attr"]
        return dict(id=x["instance_id"], zOrder=x["z_order"], numberingType=NUMBERING_TYPE.get(a >> 26 & 3, "NONE"),
                    textWrap=TEXT_WRAP.get(a >> 21 & 7, "TOP_AND_BOTTOM"), textFlow=TEXT_FLOW.get(a >> 24 & 3, "BOTH_SIDES"),
                    lock=a >> 30 & 1, dropcapstyle="None")

    def obj_tail(self, x, kids):
        a = x["attr"]
        m = x["margins"]
        s = (f'<hp:sz{attrs(width=x["width"], widthRelTo=WIDTH_REL.get(a >> 15 & 7, "ABSOLUTE"), height=x["height"], heightRelTo=HEIGHT_REL.get(a >> 18 & 3, "ABSOLUTE"), protect=a >> 20 & 1)}/>'
             f'<hp:pos{attrs(treatAsChar=a & 1, affectLSpacing=a >> 2 & 1, flowWithText=a >> 13 & 1, allowOverlap=a >> 14 & 1, holdAnchorAndSO=0, vertRelTo=VERT_REL.get(a >> 3 & 3, "PARA"), horzRelTo=HORZ_REL.get(a >> 8 & 3, "PARA"), vertAlign=VALIGN.get(a >> 5 & 7, "TOP"), horzAlign=HALIGN.get(a >> 10 & 7, "LEFT"), vertOffset=x["v_offset"] & 0xFFFFFFFF, horzOffset=x["h_offset"] & 0xFFFFFFFF)}/>'
             f'<hp:outMargin{attrs(left=m[0] & 0xFFFFFFFF, right=m[1] & 0xFFFFFFFF, top=m[2] & 0xFFFFFFFF, bottom=m[3] & 0xFFFFFFFF)}/>')
        cap = next((j for j, k in enumerate(kids) if k["tag"] == 72 and k["x"]["kind"] == "caption"), None)
        if cap is not None:
            lh, paras = self.list_paras(kids, cap)
            cx = lh["x"]
            s += (f'<hp:caption{attrs(side=CAPTION_SIDE.get(cx["caption_attr"] & 3, "BOTTOM"), fullSz=cx["caption_attr"] >> 2 & 1, width=cx["caption_width"], gap=cx["caption_gap"], lastWidth=cx["caption_max_width"])}>'
                  + self.sublist(lh, paras) + "</hp:caption>")
        return s

    @staticmethod
    def comment(x):
        d = (x.get("description") or "").replace("\r\n", "\n")
        return f"<hp:shapeComment>{text_xml(d)}</hp:shapeComment>" if d else ""

    def table(self, x, kids):
        ti = next(j for j, k in enumerate(kids) if k["tag"] == 77)
        tb = kids[ti]["x"]
        a = tb["attr"]
        rows = tb["row_sizes"]
        im = tb["margins"]
        s = (f'<hp:tbl{attrs(**self.obj_attrs(x), pageBreak=TBL_BREAK.get(a & 3, "CELL"), repeatHeader=a >> 2 & 1, rowCnt=len(rows), colCnt=tb["cols"], cellSpacing=tb["spacing"], borderFillIDRef=tb["border_fill_id"], noAdjust=a >> 3 & 1)}>'
             + self.obj_tail(x, kids) + f'<hp:inMargin{attrs(left=im[0], right=im[1], top=im[2], bottom=im[3])}/>')
        if tb.get("zones"):
            s += "<hp:cellzoneList>" + "".join(
                f'<hp:cellzone{attrs(startRowAddr=z[0], startColAddr=z[1], endRowAddr=z[2], endColAddr=z[3], borderFillIDRef=z[4])}/>'
                for z in tb["zones"]) + "</hp:cellzoneList>"
        cells, j = [], ti + 1
        while j < len(kids):
            if kids[j]["tag"] == 72:
                lh, paras = self.list_paras(kids, j)
                cells.append((lh, paras))
                j += 1 + len(paras)
            else:
                j += 1
        k = 0
        for n in rows:
            s += "<hp:tr>"
            for lh, paras in cells[k:k + n]:
                cx = lh["x"]
                ca = cx["attr"]
                name = next((it["value"] for it in cx["name_set"]["items"] if it["type"] == 1), "") \
                    if cx.get("name_flag") == 0xFF else ""
                cm = cx["margins"]
                s += (f'<hp:tc{attrs(name=name, header=ca >> 18 & 1, hasMargin=ca >> 16 & 1, protect=ca >> 17 & 1, editable=ca >> 19 & 1, dirty=ca >> 20 & 1, borderFillIDRef=cx["border_fill_id"])}>'
                      + self.sublist(lh, paras)
                      + f'<hp:cellAddr{attrs(colAddr=cx["col"], rowAddr=cx["row"])}/><hp:cellSpan{attrs(colSpan=cx["col_span"], rowSpan=cx["row_span"])}/>'
                      f'<hp:cellSz{attrs(width=cx["width"], height=cx["height"])}/>'
                      f'<hp:cellMargin{attrs(left=cm[0] & 0xFFFFFFFF, right=cm[1] & 0xFFFFFFFF, top=cm[2] & 0xFFFFFFFF, bottom=cm[3] & 0xFFFFFFFF)}/></hp:tc>')
            k += n
            s += "</hp:tr>"
        return s + self.comment(x) + "</hp:tbl>"

    def form(self, x, kids):
        fo = next((k["x"] for k in kids if k["tag"] == 91), None)
        tag = FORM_TAG.get(R.ctrl_name(fo["type_id"])) if fo else None
        if tag is None:
            self.warn("양식 개체 종류 %r는 아직 옮기지 않음(빠짐)" % (R.ctrl_name(fo["type_id"]) if fo else None))
            return ""
        sets = dict(R.form_props_parse(fo["props"]))

        def vals(set_name, table):
            got = {n: v for n, t, v in sets.get(set_name, [])}
            out = {}
            for n, t, a in table:
                if a is None or n not in got:
                    continue
                v = got[n]
                if a in ("foreColor", "backColor"):
                    v = color(int(v) & 0xFFFFFFFF, False)
                elif a in FORM_ENUM:
                    lst = FORM_ENUM[a]
                    v = lst[int(v)] if 0 <= int(v) < len(lst) else lst[0]
                out[a] = v
            return out
        own = vals("ButtonSet", FORM_BUTTON) if tag in ("checkBtn", "radioBtn", "btn") else vals("EditSet", FORM_EDIT) if tag == "edit" else {}
        if tag == "checkBtn":
            own.setdefault("radioGroupName", "")
        s = f"<hp:{tag}{attrs(**own, **vals('CommonSet', FORM_COMMON))}>"
        s += f"<hp:formCharPr{attrs(**vals('CharShapeSet', FORM_CHAR))}/>"
        if tag == "edit":
            text = next((v for n, t, v in sets.get("EditSet", []) if n == "Text"), "")
            s += f"<hp:text>{text_xml(text)}</hp:text>" if text else "<hp:text/>"
        return s + self.obj_tail(x, kids) + self.comment(x) + f"</hp:{tag}>"

    def equation(self, x, kids):
        e = next(k["x"] for k in kids if k["tag"] == 88)
        return (f'<hp:equation{attrs(**self.obj_attrs(x), version=e.get("version", ""), baseLine=e["baseline"], textColor=color(e["color"]), baseUnit=e["size"], lineMode="LINE" if e["attr"] & 1 else "CHAR", font=e.get("font", ""))}>'
                + self.obj_tail(x, kids) + self.comment(x)
                + f"<hp:script>{text_xml(e['script'].replace(chr(13) + chr(10), chr(10)))}</hp:script></hp:equation>")

    @staticmethod
    def line_shape(color_, width_, a, outline, alpha=0):
        return (f'<hp:lineShape{attrs(color=color(color_), width=width_, style=BORDER.get(a & 0x3F, "SOLID"), endCap="FLAT" if a >> 6 & 1 else "ROUND", headStyle=ARROW.get(a >> 10 & 0x3F, "NORMAL"), tailStyle=ARROW.get(a >> 16 & 0x3F, "NORMAL"), headfill=a >> 30 & 1, tailfill=a >> 31 & 1, headSz=ARROW_SZ.get(a >> 22 & 15, "MEDIUM_MEDIUM"), tailSz=ARROW_SZ.get(a >> 26 & 15, "MEDIUM_MEDIUM"), outlineStyle=OUTLINE_STYLE.get(outline, "NORMAL"), alpha=alpha)}/>')

    def gso(self, x, kids):
        sc = next((k for k in kids if k["tag"] == 76), None)
        if sc is None:
            self.warn("그리기 개체에 SHAPE_COMPONENT가 없음")
            return ""
        kind = sc["x"].get("shadow_type", 0)
        if kind in X.SHADOW_SIDES:                # 한컴은 평행 그림자 쪽 여백에서 600 + 그림자 거리를 빼서 HWPX에 쓴다
            x = dict(x, margins=X.shadow_margin(list(x["margins"]), kind, sc["x"].get("shadow_x", 0), sc["x"].get("shadow_y", 0), -1))
        elif kind:
            self.warn("기울어진 그림자 개체의 바깥 여백 규칙은 아직 모름 — 값 그대로")
        return self.shape(sc, x, kids)

    def shape(self, scn, x=None, ctrl_kids=()):
        sc = scn["x"]
        tag = SC_TAG.get(sc["ctrl_id"])
        if tag is None or tag == "ole":
            self.warn(f"개체 {sc['ctrl_id']}는 아직 옮기지 않음(빠짐)")
            return ""
        a = sc["attr"]
        base = self.obj_attrs(x) if x else dict(id=0, zOrder=0, numberingType="NONE", textWrap="TOP_AND_BOTTOM",
                                                 textFlow="BOTH_SIDES", lock=0, dropcapstyle="None")
        kids = scn["kids"]
        body = next((k["x"] for k in kids if k["tag"] in (78, 79, 80, 81, 82, 83, 85)), {})
        inst = body.get("instance_id") if tag == "pic" else sc.get("instance_id")
        base.update(href="", groupLevel=sc["group_level"], instid=inst if inst is not None else (x["instance_id"] if x else 0))
        extra = {}
        if tag == "rect":
            extra["ratio"] = body.get("round", 0)
        elif tag == "line":
            extra["isReverseHV"] = body.get("attr", 0)
        elif tag == "ellipse":
            ea = body.get("attr", 0)
            extra.update(intervalDirty=ea & 1, hasArcPr=ea >> 1 & 1, arcType=ARC_TYPE.get(ea >> 2 & 0xFF, "NORMAL"))
        elif tag == "arc":
            extra["type"] = ARC_TYPE.get(body.get("arc_type", 0), "NORMAL")
        elif tag == "pic":
            extra["reverse"] = 0
        w_, h_ = sc["width"], sc["height"]
        cur = (0, 0) if (w_, h_) == (sc["init_width"], sc["init_height"]) else (w_, h_)
        s = (f"<hp:{tag}{attrs(**base, **extra)}>"
             f'<hp:offset{attrs(x=sc["x"] & 0xFFFFFFFF, y=sc["y"] & 0xFFFFFFFF)}/><hp:orgSz{attrs(width=sc["init_width"], height=sc["init_height"])}/>'
             f'<hp:curSz{attrs(width=cur[0], height=cur[1])}/><hp:flip{attrs(horizontal=a & 1, vertical=a >> 1 & 1)}/>'
             f'<hp:rotationInfo{attrs(angle=sc["rotation"], centerX=sc["rot_cx"], centerY=sc["rot_cy"], rotateimage=a >> 19 & 1)}/>'
             "<hp:renderingInfo>" + self.matrix("transMatrix", sc["translation"])
             + "".join(self.matrix("scaMatrix", m[0]) + self.matrix("rotMatrix", m[1]) for m in sc["matrices"])
             + "</hp:renderingInfo>")
        if tag in ("rect", "line", "ellipse", "arc", "polygon", "curve"):
            s += self.line_shape(sc["line_color"], sc["line_width"], sc["line_attr"], sc["outline_style"])
            s += self.fill(sc["fill"])
            s += f'<hp:shadow{attrs(type=SHAPE_SHADOW.get(sc.get("shadow_type", 0), "NONE"), color=color(sc.get("shadow_color", 0xB2B2B2)), offsetX=sc.get("shadow_x", 0), offsetY=sc.get("shadow_y", 0), alpha=0)}/>'
            ti = next((j for j, k in enumerate(kids) if k["tag"] == 72), None)
            if ti is not None:
                lh, paras = self.list_paras(kids, ti)
                lx = lh["x"]
                tm = lx.get("margins", [0, 0, 0, 0])
                s += (f'<hp:drawText{attrs(lastWidth=lx.get("max_width", 0), name="", editable=0)}>' + self.sublist(lh, paras)
                      + f'<hp:textMargin{attrs(left=tm[0], right=tm[1], top=tm[2], bottom=tm[3])}/></hp:drawText>')
            s += self.shape_points(tag, body)
        elif tag == "container":
            s += "".join(self.shape(k) for k in kids if k["tag"] == 76)
        elif tag == "pic":
            p = body
            if p.get("border_width") or p.get("border_attr"):
                s += self.line_shape(p["border_color"], p["border_width"], p["border_attr"], 0, p.get("border_alpha", 0))
            pts = p.get("points", [0] * 8)
            cr, im = p.get("crop", [0] * 4), p.get("margins", [0] * 4)
            s += (f'<hc:img{attrs(binaryItemIDRef=self.bin_name.get(p.get("bin_id"), "image%d" % p.get("bin_id", 0)), bright=p.get("brightness", 0), contrast=p.get("contrast", 0), effect=IMG_EFFECT.get(p.get("effect", 0), "REAL_PIC"), alpha=p.get("alpha", 0))}/>'
                  "<hp:imgRect>" + "".join(f'<hc:pt{i}{attrs(x=pts[2 * i], y=pts[2 * i + 1])}/>' for i in range(4))
                  + "</hp:imgRect>"
                  f'<hp:imgClip{attrs(left=cr[0], right=cr[2], top=cr[1], bottom=cr[3])}/>'
                  f'<hp:inMargin{attrs(left=im[0], right=im[1], top=im[2], bottom=im[3])}/>'
                  f'<hp:imgDim{attrs(dimwidth=p.get("orig_width", 0), dimheight=p.get("orig_height", 0))}/><hp:effects/>')
        if x is not None:
            s += self.obj_tail(x, ctrl_kids) + self.comment(x)
        return s + f"</hp:{tag}>"

    @staticmethod
    def matrix(name, m):
        return f'<hc:{name}' + "".join(f' e{i + 1}="{num(v)}"' for i, v in enumerate(m)) + "/>"

    @staticmethod
    def shape_points(tag, b):
        if not b:
            return ""
        if tag == "rect":
            return "".join(f'<hc:pt{i}{attrs(x=b["points"][2 * i], y=b["points"][2 * i + 1])}/>' for i in range(4))
        if tag == "line":
            return f'<hc:startPt{attrs(x=b["x1"], y=b["y1"])}/><hc:endPt{attrs(x=b["x2"], y=b["y2"])}/>'
        if tag == "ellipse":
            return "".join(f'<hc:{n}{attrs(x=b[kx], y=b[ky])}/>' for n, kx, ky in (
                ("center", "cx", "cy"), ("ax1", "ax1", "ay1"), ("ax2", "ax2", "ay2"), ("start1", "sx", "sy"),
                ("end1", "ex", "ey"), ("start2", "sx2", "sy2"), ("end2", "ex2", "ey2")))
        if tag == "arc":
            return "".join(f'<hc:{n}{attrs(x=b[kx], y=b[ky])}/>' for n, kx, ky in (
                ("center", "cx", "cy"), ("ax1", "ax1", "ay1"), ("ax2", "ax2", "ay2")))
        if tag == "polygon":
            return "".join(f'<hc:pt{attrs(x=p[0], y=p[1])}/>' for p in b.get("points", []))
        if tag == "curve":
            pts, segs = b.get("points", []), b.get("segments", [])
            return "".join(f'<hp:seg{attrs(type="CURVE" if i < len(segs) and segs[i] else "LINE", x1=pts[i][0], y1=pts[i][1], x2=pts[i + 1][0], y2=pts[i + 1][1])}/>'
                           for i in range(len(pts) - 1))
        return ""

    def print_info(self):
        """문서 임의의 데이터(셋 540 > 519) → settings.xml PrintInfo(hwpx2hwp.print_info의 거꾸로)."""
        for dd in self.by.get(27, []):
            st = dd.get("set")
            if not st or st["id"] != 540 or not st["items"] or st["items"][0]["type"] != 0x8000:
                continue
            inner = st["items"][0]["value"]
            got = {it["id"]: it["value"] for it in inner["items"]}
            out = []
            for iid in (0x400E, 0x400A, 0x4006, 0x4010, 0x401D, 0x401A, 0x401F, 0x4020):
                if iid in got:
                    name, typ = PRINT_ITEMS[iid]
                    val = ("true" if got[iid] else "false") if typ == "boolean" else got[iid]
                    out.append(f'<config:config-item name="{name}" type="{typ}">{val}</config:config-item>')
            return '<config:config-item-set name="PrintInfo">' + "".join(out) + "</config:config-item-set>"
        return ""

    # ───────── 꾸러미 ─────────
    def build(self, out):
        self.open_fields = []
        sections = []
        for k in sorted(self.d.sections, key=lambda n: int(n[16:] or 0)):
            view = "ViewText/" + k.split("/")[1]
            recs = self.d.sections[k]
            if view in self.d.streams:            # 변경 추적 문서: 한컴 HWPX는 표시와 줄 배치가 든 ViewText로 만든다
                raw = self.d.streams[view]
                try:
                    recs = read_records(zlib.decompress(raw, -15) if self.d.compressed else raw)
                except zlib.error:
                    self.warn("ViewText를 풀지 못해 본문으로 씀")
            sections.append(self.section(recs))
        header = self.header()
        z = zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED)
        z.writestr(zipfile.ZipInfo("mimetype"), "application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("version.xml", XMLHEAD + '<hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" '
                   'tagetApplication="WORDPROCESSOR" major="5" minor="1" micro="1" buildNumber="0" os="1" xmlVersion="1.5" '
                   'application="Hancom Office Hangul" appVersion="hwp2hwpx"/>')
        z.writestr("Contents/header.xml", header)
        items = ['<opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>']
        spine = ['<opf:itemref idref="header" linear="yes"/>']
        for i, s in enumerate(sections):
            z.writestr(f"Contents/section{i}.xml", s)
            items.append(f'<opf:item id="section{i}" href="Contents/section{i}.xml" media-type="application/xml"/>')
            spine.append(f'<opf:itemref idref="section{i}" linear="yes"/>')
        mt = {"png": "image/png", "jpg": "image/jpg", "jpeg": "image/jpg", "bmp": "image/bmp", "gif": "image/gif",
              "wmf": "image/x-wmf", "emf": "image/x-emf", "tif": "image/tif", "tiff": "image/tif"}
        streams = {k.split("/")[1].lower(): k for k in self.d.streams if k.startswith("BinData/")}
        for i, b in enumerate(self.bins, 1):
            if (b["attr"] & 0xF) == 0:
                self.warn("바깥 파일에 연결된 그림은 아직 옮기지 않음")
                continue
            ext = (b.get("ext") or "").lower()
            key = streams.get(f"bin{b.get('bin_id', i):04x}.{ext}")
            if ext == "ole" or key is None:
                if ext == "ole":
                    self.warn("OLE 바이너리는 아직 옮기지 않음")
                else:
                    self.warn(f"BIN_DATA {i}의 스트림이 없음")
                continue
            data = self.d.streams[key]
            try:
                data = zlib.decompress(data, -15)
            except zlib.error:
                pass
            href = f"BinData/image{i}.{ext}"
            z.writestr(href, data)
            items.append(f'<opf:item id="image{i}" href="{href}" media-type="{mt.get(ext, "application/octet-stream")}" isEmbeded="1"/>')
        for name, xml in self.masters:
            z.writestr(f"Contents/{name}.xml", xml)
            items.append(f'<opf:item id="{name}" href="Contents/{name}.xml" media-type="application/xml"/>')
        items.append('<opf:item id="settings" href="settings.xml" media-type="application/xml"/>')
        z.writestr("Contents/content.hpf", XMLHEAD + f'<opf:package {NSDECL} version="" unique-identifier="" id="">'
                   "<opf:metadata><opf:title/><opf:language>ko</opf:language></opf:metadata>"
                   f"<opf:manifest>{''.join(items)}</opf:manifest><opf:spine>{''.join(spine)}</opf:spine></opf:package>")
        dp = self.by[16][0]
        z.writestr("settings.xml", XMLHEAD + '<ha:HWPApplicationSetting xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
                   'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0">'
                   f'<ha:CaretPosition listIDRef="{dp["caret_list"] + 1 if dp["caret_list"] else 0}" paraIDRef="{dp["caret_para"]}" pos="{dp["caret_pos"]}"/>'
                   + self.print_info() + "</ha:HWPApplicationSetting>")
        z.writestr("META-INF/container.xml", XMLHEAD + '<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container" '
                   'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf"><ocf:rootfiles>'
                   '<ocf:rootfile full-path="Contents/content.hpf" media-type="application/hwpml-package+xml"/>'
                   '<ocf:rootfile full-path="Preview/PrvText.txt" media-type="text/plain"/></ocf:rootfiles></ocf:container>')
        z.writestr("META-INF/manifest.xml", XMLHEAD + '<odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>')
        if "PrvText" in self.d.streams:
            z.writestr("Preview/PrvText.txt", self.d.streams["PrvText"].decode("utf-16-le", "replace").encode("utf-8"))
        if self.d.streams.get("PrvImage", b"")[:4] == b"\x89PNG":
            z.writestr("Preview/PrvImage.png", self.d.streams["PrvImage"])
        z.close()


PRINT_ITEMS = {0x4006: ("PrintMethod", "short"), 0x400E: ("PrintAutoFootNote", "boolean"),
               0x400A: ("PrintAutoHeadNote", "boolean"), 0x401F: ("ZoomX", "short"), 0x401D: ("PrintCropMark", "short"),
               0x401A: ("BinderHoleType", "short"), 0x4010: ("OverlapSize", "short"), 0x4020: ("ZoomY", "short")}


def param_xml(kind, name, val):
    sp = ' xml:space="preserve"' if kind == "stringParam" and (val != val.strip() or "  " in val) else ""
    return f'<hp:{kind} name="{name}"{sp}>{text_xml(str(val))}</hp:{kind}>'


def field_params(typ, cmd, prop, memo):
    """필드 명령 문자열 → HWPX 파라미터(한컴 HWPX에서 본 모양: 하이퍼링크·계산식·누름틀·메모)."""
    out = [("integerParam", "Prop", prop)]
    if typ == "BOOKMARK":
        return out
    out.append(("stringParam", "Command", cmd))
    if typ == "HYPERLINK":
        target = cmd.split(";")[0]
        if target.startswith("?"):
            out.append(("stringParam", "Category", "HWPHYPERLINK_TYPE_HWP"))
        else:
            out.append(("stringParam", "Path", target))
            out.append(("stringParam", "Category", "HWPHYPERLINK_TYPE_EMAIL" if target.lower().startswith("mailto:")
                        else "HWPHYPERLINK_TYPE_URL"))
        out += [("stringParam", "TargetType", "HWPHYPERLINK_TARGET_BOOKMARK"),
                ("stringParam", "DocOpenType", "HWPHYPERLINK_JUMP_CURRENTTAB")]
    elif typ == "FORMULA" and "??" in cmd:
        formula, _, rest = cmd.partition("??")
        fmt, _, last = rest.partition(";;")
        out += [("stringParam", "Formula", formula), ("stringParam", "ResultFormat", fmt), ("stringParam", "LastResult", last)]
    elif typ == "CLICK_HERE":
        body = cmd.split(":", 3)[3] if cmd.count(":") >= 3 else ""
        while body:                                # 이름:형식:글자수:값 …
            parts = body.split(":", 3)
            if len(parts) < 4 or not parts[2].isdigit():
                break
            name, n, rest = parts[0].strip(), int(parts[2]), parts[3]
            val = rest[:n]
            if val:
                out.append(("stringParam", name, val))
            body = rest[n:].lstrip(" ")
    elif typ == "MEMO":
        f = cmd.split("/")
        if len(f) >= 6:
            try:
                ft = int(f[4]) << 32 | int(f[3])
                utc = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(microseconds=ft // 10)
                created = utc.astimezone().strftime("%Y-%m-%dT%H:%M:%SZ")   # 한컴은 컴퓨터 지역 시각에 Z를 붙여 쓴다
            except (ValueError, OverflowError):
                created = ""
            out += [("stringParam", "ID", f"memo{memo}"), ("integerParam", "Number", memo), ("stringParam", "Author", f[5]),
                    ("stringParam", "MemoShapeIDRef", f[1]), ("stringParam", "CreateDateTime", created)]
    return out



# 양식 개체: HWP 종류 ID ↔ HWPX 요소(체크 상자·입력 상자는 문서로 확인, 나머지는 추정)
FORM_TAG = {"+cbt": "checkBtn", "+edt": "edit", "+rbt": "radioBtn", "+pbt": "btn", "+cmb": "comboBox", "+lbx": "listBox",
            "+sbr": "scrollBar"}
FORM_COMMON = [("Name", "wstring", "name"), ("GroupName", "wstring", "groupName"), ("TabStop", "bool", "tabStop"),
               ("TabOrder", "int", "tabOrder"), ("Command", "wstring", "command"), ("Editable", "bool", "editable"),
               ("ForeColor", "int", "foreColor"), ("BackColor", "int", "backColor"), ("Enabled", "bool", "enabled"),
               ("BorderType", "int", "borderTypeIDRef"), ("DrawFrame", "bool", "drawFrame"), ("Printable", "bool", "printable")]
FORM_CHAR = [("CharShapeID", "int", "charPrIDRef"), ("FollowContext", "bool", "followContext"), ("AutoSize", "bool", "autoSz"),
             ("WordWrap", "bool", "wordWrap")]
FORM_BUTTON = [("Caption", "wstring", "caption"), ("Value", "int", "value"), ("RadioGroupName", "wstring", "radioGroupName"),
               ("TriState", "bool", "triState"), ("BackStyle", "int", "backStyle")]
FORM_EDIT = [("Text", "wstring", None), ("MultiLine", "bool", "multiLine"), ("PasswordChar", "wstring", "passwordChar"),
             ("MaxLength", "int", "maxLength"), ("ScrollBars", "int", "scrollBars"), ("TabKeyBehavior", "int", "tabKeyBehavior"),
             ("Number", "bool", "numOnly"), ("ReadOnly", "bool", "readOnly"), ("AlignText", "int", "alignText")]
FORM_ENUM = {"value": ["UNCHECKED", "CHECKED", "INDETERMINATE"], "backStyle": ["TRANSPARENT", "OPAQUE"],
             "scrollBars": ["NONE", "VERTICAL", "HORIZONTAL", "BOTH"], "tabKeyBehavior": ["NEXT_OBJECT", "INSERT_TAB"],
             "alignText": ["LEFT", "CENTER", "RIGHT"]}


def ch(v):
    return chr(v) if v else ""


def convert(src, out):
    c = Conv(src)
    c.build(out)
    return c.warnings


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for w in convert(sys.argv[1], sys.argv[2]):
        print("경고:", w)
    print("저장:", sys.argv[2])
