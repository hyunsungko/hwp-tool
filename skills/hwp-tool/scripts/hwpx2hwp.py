"""HWPX → HWP 5 변환(한글 프로그램 없이, 우리가 새로 짠 것 — 2026-10-16 5단계).
OWPML(HWPX) 요소를 HWP 5 레코드 필드로 옮겨 hwp5_records 코덱으로 묶고 CFB 파일로 저장한다.
- 줄 배치(PARA_LINE_SEG)는 HWPX의 linesegarray를 그대로 옮긴다. 없으면 비워 둔다(한컴이 열 때 다시 짬).
- HWPX에 없는 값(글꼴 기본 이름·세리프 유형 등)은 쓰지 않는다. 뜻을 모르는 자리는 한컴이 쓰는 기본값으로 채운다.
- 아직 옮기지 못하는 요소는 conv.warnings에 남긴다(조용히 버리지 않음).
- 문단 여백·줄 간격은 hp:switch의 hp:default 값(HWP 단위의 두 배 값)을 쓴다 — HWP 레코드가 그 값을 담는다(blank 짝으로 확인).
사용: python hwpx2hwp.py 입력.hwpx 출력.hwp"""
import copy
import os
import re
import struct
import sys
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lxml import etree  # noqa: E402
import hwp5_records as R  # noqa: E402
from hwp5_direct import write_records  # noqa: E402

NS = {"hh": "http://www.hancom.co.kr/hwpml/2011/head", "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
      "hc": "http://www.hancom.co.kr/hwpml/2011/core", "hs": "http://www.hancom.co.kr/hwpml/2011/section",
      "ha": "http://www.hancom.co.kr/hwpml/2011/app", "opf": "http://www.idpf.org/2007/opf/"}
HH, HP, HC = "{%s}" % NS["hh"], "{%s}" % NS["hp"], "{%s}" % NS["hc"]

# ───────── 값 표 ─────────
LANGS = ["HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER"]
LANG_ATTR = ["hangul", "latin", "hanja", "japanese", "other", "symbol", "user"]
# 테두리선 종류(테두리/배경·각주 구분선·탭 채움): 0 = 없음. 값은 HWPX 값 하나씩 바꿔 한컴이 HWP로 저장하게 해서 찾음
# (2026-10-16 probe): DOT 2·DASH 3(규격 표 25 글과 반대). DOUBLE_WAVE·THICK_3D 계열 이름은 한컴도 못 읽어 0으로 씀 → 우리 추정값.
BORDER_TYPE = {"NONE": 0, "SOLID": 1, "DOT": 2, "DASH": 3, "DASH_DOT": 4, "DASH_DOT_DOT": 5, "LONG_DASH": 6,
               "CIRCLE": 7, "DOUBLE_SLIM": 8, "SLIM_THICK": 9, "THICK_SLIM": 10, "SLIM_THICK_SLIM": 11, "WAVE": 12,
               "DOUBLE_WAVE": 13, "THICK_3D": 14, "THICK_3D_REVERSE_LIGHTING": 15, "3D": 16, "3D_REVERSE_LIGHTING": 17}
# 선 모양(밑줄·취소선): SOLID 0·DOT 1·DASH 2 …(probe). 한컴이 모르는 이름(NONE 포함)은 15
LINE_SHAPE = {"SOLID": 0, "DOT": 1, "DASH": 2, "DASH_DOT": 3, "DASH_DOT_DOT": 4, "LONG_DASH": 5, "CIRCLE": 6,
              "DOUBLE_SLIM": 7, "SLIM_THICK": 8, "THICK_SLIM": 9, "SLIM_THICK_SLIM": 10, "WAVE": 11, "3D": 15}
WIDTHS = ["0.1 mm", "0.12 mm", "0.15 mm", "0.2 mm", "0.25 mm", "0.3 mm", "0.4 mm", "0.5 mm", "0.6 mm", "0.7 mm",
          "1.0 mm", "1.5 mm", "2.0 mm", "3.0 mm", "4.0 mm", "5.0 mm"]
FCAT = {k: i for i, k in enumerate(["FCAT_UNKNOWN", "FCAT_MYUNGJO", "FCAT_GOTHIC", "FCAT_SSERIF", "FCAT_BRUSHSCRIPT",
                                    "FCAT_DECORATIVE", "FCAT_NONRECTMJ", "FCAT_NONRECTGT"])}
FONT_TYPE = {"REP": 0, "TTF": 1, "HFT": 2}
NUMFMT = {k: i for i, k in enumerate(["DIGIT", "CIRCLED_DIGIT", "ROMAN_CAPITAL", "ROMAN_SMALL", "LATIN_CAPITAL",
                                      "LATIN_SMALL", "CIRCLED_LATIN_CAPITAL", "CIRCLED_LATIN_SMALL", "HANGUL_SYLLABLE",
                                      "CIRCLED_HANGUL_SYLLABLE", "HANGUL_JAMO", "CIRCLED_HANGUL_JAMO",
                                      "HANGUL_PHONETIC", "IDEOGRAPH", "CIRCLED_IDEOGRAPH", "DECAGON_CIRCLE",
                                      "DECAGON_CIRCLE_HANJA"])}
NUMFMT.update({"SYMBOL": 0x80, "USER_CHAR": 0x81})
ALIGN_H = {"JUSTIFY": 0, "LEFT": 1, "RIGHT": 2, "CENTER": 3, "DISTRIBUTE": 4, "DISTRIBUTE_SPACE": 5}
ALIGN_V = {"BASELINE": 0, "TOP": 1, "CENTER": 2, "BOTTOM": 3}
BREAK_LATIN = {"KEEP_WORD": 0, "HYPHENATION": 1, "BREAK_WORD": 2}
BREAK_NONLATIN = {"BREAK_WORD": 0, "KEEP_WORD": 1}          # 이름이 뜻과 거꾸로(KEEP_WORD = 글자 단위)
HEADING = {"NONE": 0, "OUTLINE": 1, "NUMBER": 2, "BULLET": 3}
LINE_SPACING = {"PERCENT": 0, "FIXED": 1, "BETWEEN_LINES": 2, "AT_LEAST": 3}
LINE_WRAP = {"BREAK": 0, "SQUEEZE": 1, "KEEP": 2}
TEXT_DIR = {"AUTO": 2, "RTL": 1, "LTR": 2}           # probe: AUTO도 2
TAB_TYPE = {"LEFT": 0, "RIGHT": 1, "CENTER": 2, "DECIMAL": 3}
HEAD_ALIGN = {"LEFT": 0, "CENTER": 1, "RIGHT": 2}
UNDERLINE = {"NONE": 0, "BOTTOM": 1, "CENTER": 2, "TOP": 3}
OUTLINE = {"NONE": 0, "SOLID": 1, "DOT": 2, "BOLD": 3, "DASH": 4, "DASH_DOT": 5, "DASH_DOT_DOT": 6}
SHADOW = {"NONE": 0, "DROP": 1, "CONTINUOUS": 2}
SYMMARK = {k: i for i, k in enumerate(["NONE", "DOT_ABOVE", "RING_ABOVE", "TILDE", "CARON", "SIDE", "COLON",
                                       "GRAVE_ACCENT", "ACUTE_ACCENT", "CIRCUMFLEX", "MACRON", "HOOK_ABOVE",
                                       "DOT_BELOW"])}                     # probe: TILDE 3·CARON 4
SLASH = {"NONE": 0, "CENTER": 2, "CENTER_BELOW": 3, "CENTER_ABOVE": 6, "ALL": 7}
HATCH = {"HORIZONTAL": 0, "VERTICAL": 1, "BACK_SLASH": 2, "SLASH": 3, "CROSS": 4, "CROSS_DIAGONAL": 5}
GRAD = {"LINEAR": 1, "RADIAL": 2, "CONICAL": 3, "SQUARE": 4}
IMG_MODE = {k: i for i, k in enumerate(["TILE", "TILE_HORZ_TOP", "TILE_HORZ_BOTTOM", "TILE_VERT_LEFT", "TILE_VERT_RIGHT",
                                        "TOTAL", "CENTER", "CENTER_TOP", "CENTER_BOTTOM", "LEFT_CENTER", "LEFT_TOP",
                                        "LEFT_BOTTOM", "RIGHT_CENTER", "RIGHT_TOP", "RIGHT_BOTTOM", "ZOOM"])}
IMG_EFFECT = {"REAL_PIC": 0, "GRAY_SCALE": 1, "BLACK_WHITE": 2, "PATTERN8x8": 4}
STYLE_TYPE = {"PARA": 0, "CHAR": 1}
VERT_REL = {"PAPER": 0, "PAGE": 1, "PARA": 2}
HORZ_REL = {"PAPER": 0, "PAGE": 1, "COLUMN": 2, "PARA": 3}
POS_ALIGN = {"TOP": 0, "LEFT": 0, "CENTER": 1, "BOTTOM": 2, "RIGHT": 2, "INSIDE": 3, "OUTSIDE": 4}
WIDTH_REL = {"PAPER": 0, "PAGE": 1, "COLUMN": 2, "PARA": 3, "ABSOLUTE": 4}
HEIGHT_REL = {"PAPER": 0, "PAGE": 1, "ABSOLUTE": 2}
# probe(표): TOP_AND_BOTTOM 1·BEHIND_TEXT 2·IN_FRONT_OF_TEXT 3, TIGHT·THROUGH는 한컴도 0으로 씀(규격 표 70의 순서와 다름)
TEXT_WRAP = {"SQUARE": 0, "TIGHT": 0, "THROUGH": 0, "TOP_AND_BOTTOM": 1, "BEHIND_TEXT": 2, "IN_FRONT_OF_TEXT": 3}
TEXT_FLOW = {"BOTH_SIDES": 0, "LEFT_ONLY": 1, "RIGHT_ONLY": 2, "LARGEST_ONLY": 3}
NUMBERING_TYPE = {"NONE": 0, "PICTURE": 1, "TABLE": 2, "EQUATION": 3}
LIST_DIR = {"HORIZONTAL": 0, "VERTICAL": 1, "VERTICALALL": 2}
LIST_VALIGN = {"TOP": 0, "CENTER": 1, "BOTTOM": 2}
TBL_BREAK = {"NONE": 0, "TABLE": 1, "CELL": 2}      # 한컴 HWPX→HWP: CELL이 2
CAPTION_SIDE = {"LEFT": 0, "RIGHT": 1, "TOP": 2, "BOTTOM": 3}
APPLY_PAGE = {"BOTH": 0, "EVEN": 1, "ODD": 2}
NUM_TYPE = {"PAGE": 0, "FOOTNOTE": 1, "ENDNOTE": 2, "PICTURE": 3, "TABLE": 4, "EQUATION": 5}
PAGENUM_POS = {k: i for i, k in enumerate(["NONE", "TOP_LEFT", "TOP_CENTER", "TOP_RIGHT", "BOTTOM_LEFT", "BOTTOM_CENTER",
                                           "BOTTOM_RIGHT", "OUTSIDE_TOP", "OUTSIDE_BOTTOM", "INSIDE_TOP",
                                           "INSIDE_BOTTOM"])}
NOTE_NUMBERING = {"CONTINUOUS": 0, "ON_SECTION": 1, "ON_PAGE": 2}
NOTE_PLACE = {"EACH_COLUMN": 0, "MERGED_COLUMN": 1, "RIGHT_MOST_COLUMN": 2, "END_OF_DOCUMENT": 0, "END_OF_SECTION": 1}
GUTTER = {"LEFT_ONLY": 0, "LEFT_RIGHT": 1, "TOP_BOTTOM": 2}
COL_TYPE = {"NEWSPAPER": 0, "BALANCED_NEWSPAPER": 1, "PARALLEL": 2}
COL_LAYOUT = {"LEFT": 0, "RIGHT": 1, "MIRROR": 2}
FIELD_ID = {"UNKNOWN": "%unk", "DATE": "%dte", "DOC_DATE": "%ddt", "PATH": "%pat", "BOOKMARK": "%bmk",
            "MAILMERGE": "%mmg", "CROSSREF": "%xrf", "FORMULA": "%fmu", "CLICK_HERE": "%clk", "SUMMARY": "%smr",
            "USER_INFO": "%usr", "HYPERLINK": "%hlk", "MEMO": "%unk", "PRIVATE_INFO_SECURITY": "%cpr",
            "TABLEOFCONTENTS": "%toc"}
# 확장 컨트롤 문자 코드(숨은 설명 tcmt는 23 — 한컴 저장본)
CODE = {"secd": 2, "cold": 2, "tbl ": 11, "gso ": 11, "eqed": 11, "tcmt": 23, "head": 16, "foot": 16, "fn  ": 17,
        "en  ": 17, "atno": 18, "nwno": 21, "pgct": 21, "form": 11, "pghd": 21, "pgnp": 21, "idxm": 22, "bokm": 22, "tcps": 23,
        "tdut": 23}
CHAR_CODE = {"lineBreak": 10, "hyphen": 24, "nbSpace": 30, "fwSpace": 31}
SHAPE_TAGS = {"pic", "rect", "line", "ellipse", "arc", "polygon", "curve", "container", "ole", "connectLine",
              "textart", "video", "chart"}
DRAW_TAGS = {"pic", "rect", "line", "ellipse", "arc", "polygon", "curve", "container"}
SC_ID = {"pic": "$pic", "rect": "$rec", "line": "$lin", "ellipse": "$ell", "arc": "$arc", "polygon": "$pol",
         "curve": "$cur", "container": "$con", "ole": "$ole"}
ARROW = {k: i for i, k in enumerate(["NORMAL", "ARROW", "CONCAVE_ARROW", "EMPTY_DIAMOND", "EMPTY_CIRCLE", "EMPTY_BOX",
                                     "FILLED_DIAMOND", "FILLED_CIRCLE", "FILLED_BOX", "SPEAR"])}
ARROW_SZ = {k: i for i, k in enumerate(["SMALL_SMALL", "SMALL_MEDIUM", "SMALL_LARGE", "MEDIUM_SMALL", "MEDIUM_MEDIUM",
                                        "MEDIUM_LARGE", "LARGE_SMALL", "LARGE_MEDIUM", "LARGE_LARGE"])}
OUTLINE_STYLE = {"NORMAL": 0, "OUTER": 1, "INNER": 2}
SHADOW_SIDES = {1: (0, 2), 2: (1, 2), 3: (0, 3), 4: (1, 3)}    # 평행 그림자(왼위·오위·왼아래·오아래) → 여백 번호(왼0 오1 위2 아래3)


def shadow_margin(margins, kind, ox, oy, sign):
    """한컴은 평행 그림자 쪽 두 변의 HWP 여백에 600 + 그림자 거리를 더해 둔다. HWPX에는 뺀 값을 쓴다
    (예: HWPX 여백 0, 그림자 거리 −453 → HWP 여백 147). sign +1 = HWPX→HWP."""
    m = [v - (1 << 32) if v >= 1 << 31 else v for v in margins]
    if kind in SHADOW_SIDES:
        h, v = SHADOW_SIDES[kind]
        m[h] += sign * (600 + ox)
        m[v] += sign * (600 + oy)
    return m


SHAPE_SHADOW = {k: i for i, k in enumerate(["NONE", "PARELLEL_LEFTTOP", "PARELLEL_RIGHTTOP", "PARELLEL_LEFTBOTTOM",
                                            "PARELLEL_RIGHTBOTTOM", "SHEAR_LEFTTOP", "SHEAR_RIGHTTOP",
                                            "SHEAR_LEFTBOTTOM", "SHEAR_RIGHTBOTTOM", "SHEAR_LEFT", "SHEAR_RIGHT",
                                            "SHEAR_TOP", "SHEAR_BOTTOM", "SCALE_LEFTTOP", "SCALE_RIGHTTOP",
                                            "SCALE_LEFTBOTTOM", "SCALE_RIGHTBOTTOM", "SCALE_NARROW", "SCALE_ENLARGE"])}
ARC_TYPE = {"NORMAL": 0, "PIE": 1, "CHORD": 2}
COMPOSE_CIRCLE = {k: i for i, k in enumerate(["CHAR", "SHAPE_CIRCLE", "SHAPE_REVERSAL_CIRCLE", "SHAPE_RECTANGLE",
                                              "SHAPE_REVERSAL_RECTANGLE", "SHAPE_TRIANGLE", "SHAPE_REVERSAL_TIRANGLE",
                                              "SHAPE_LIGHT", "SHAPE_RHOMBUS", "SHAPE_REVERSAL_RHOMBUS",
                                              "SHAPE_ROUNDED_RECTANGLE", "SHAPE_EMPTY_CIRCULATE_TRIANGLE",
                                              "SHAPE_THIN_CIRCULATE_TRIANGLE"])}

# 한컴 빈 문서에서 그대로 가져오는 작은 스트림(개인 정보 없음)
FILE_HEADER = (b"HWP Document File".ljust(32, b"\0") + struct.pack("<IIII", 0x05010100, 1, 0, 4)).ljust(256, b"\0")
JS_VERSION = bytes.fromhex("6364800000f7df88a908000000")
JS_DEFAULT = bytes.fromhex("63604005ff8100006ebb6ed114000000")


def A(el, name, default=None):
    v = el.get(name) if el is not None else None
    return default if v is None else v


def I(el, name, default=0):
    v = A(el, name)
    if v is None or v == "":
        return default
    return int(float(v))


def B(el, name):
    return 1 if A(el, name) in ("1", "true") else 0


def color(s, none=0xFFFFFFFF):
    """'#RRGGBB' → COLORREF(0x00BBGGRR). '#AARRGGBB'은 앞 두 자리를 위 바이트로. 'none' → 0xFFFFFFFF."""
    if not s:
        return none
    if s == "none":
        return 0xFFFFFFFF
    s = s.lstrip("#")
    a = int(s[:-6] or "0", 16)
    r, g, b = int(s[-6:-4], 16), int(s[-4:-2], 16), int(s[-2:], 16)
    return (a << 24) | (b << 16) | (g << 8) | r


def wchar(s):
    return ord(s[0]) if s else 0


def num(s):
    """HWPX 실수 표기(한컴은 inf·-nan(ind)도 씀) → float."""
    s = (s or "0").strip()
    if "nan" in s:
        return struct.unpack("<d", bytes.fromhex("000000000000f8ff"))[0]
    if s in ("inf", "-inf"):
        return float(s)
    return float(s)


def child(el, *names):
    for n in names:
        el = el.find(n) if el is not None else None
    return el


CASE_NS = {"http://www.hancom.co.kr/hwpml/2016/paragraph"}   # 이 case는 우리가 아는 확장(수준 8~10 등) → case 값을 씀


def switched(el, name):
    """el 바로 아래 또는 hp:switch 안의 name 요소. switch면 CASE_NS의 case를, 아니면 hp:default를 쓴다
    (여백·줄 간격은 default 값 = HWP에 담기는 값, HwpUnitChar case는 그 절반)."""
    if el.find(name) is not None:
        return el.find(name)
    for sw in el.findall(HP + "switch"):
        for case in sw.findall(HP + "case"):
            if case.get("{%s}required-namespace" % NS["hp"]) in CASE_NS and case.find(name) is not None:
                return case.find(name)
        d = sw.find(HP + "default")
        if d is not None and d.find(name) is not None:
            return d.find(name)
    return None


UNIT2 = [True]                                   # 문서가 HwpUnitChar 방식(1.31판 이후)인지 — Conv가 정한다



# 양식 개체: HWP 종류 ID ↔ HWPX 요소(체크 상자·입력 상자는 문서로 확인, 나머지는 추정)
FORM_ID = {"+cbt": "checkBtn", "+edt": "edit", "+rbt": "radioBtn", "+pbt": "btn", "+cmb": "comboBox", "+lbx": "listBox",
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
FORM_ELEMENT = {v: k for k, v in FORM_ID.items()}


def unit_factor(el, owner, holder=None):
    """HWPX 길이 값 → HWP 값 배수. HwpUnitChar 방식 문서에서 switch 밖(owner 바로 아래, 또는 holder가 owner 바로 아래)에
    unit="HWPUNIT"이면 2(probe), hp:default 쪽 값·unit 없는 값·옛 판(1.2·1.3, switch 없음)은 1(이미 HWP 값)."""
    if el is None or A(el, "unit") != "HWPUNIT" or not UNIT2[0]:
        return 1
    parent = holder if holder is not None else el
    return 2 if parent.getparent() is owner else 1


def u16s(s):
    """str → UTF-16 코드 단위 목록."""
    b = s.encode("utf-16-le", errors="surrogatepass")
    return list(struct.unpack(f"<{len(b) // 2}H", b))


def ctrl_chars(code, name):
    v = R.ctrl_value(name)
    return [code, v & 0xFFFF, v >> 16, 0, 0, 0, 0, code]


class Conv:
    def __init__(self, path):
        self.z = zipfile.ZipFile(path)
        self.warnings = []
        self.fields = {}                          # fieldBegin id → (컨트롤 ID, Prop, editable, 메모 번호) — fieldEnd가 씀
        self.memos = []                           # 구역 끝(수준 1)에 붙일 메모 내용 레코드
        self.merged_in = []                       # 변경 추적 BodyText 판에서 다음 문단과 합쳐 들어온 run 사본
        raw = self.z.read("Contents/header.xml")
        self.header = etree.fromstring(raw)
        UNIT2[0] = b"HwpUnitChar" in raw
        hpf = etree.fromstring(self.z.read("Contents/content.hpf"))
        self.items = {it.get("id"): it for it in hpf.iter("{%s}item" % NS["opf"])}
        spine = [ir.get("idref") for ir in hpf.iter("{%s}itemref" % NS["opf"])]
        self.section_files = [self.items[i].get("href") for i in spine if i in self.items
                              and re.search(r"section\d+\.xml$", self.items[i].get("href", ""))]
        if not self.section_files:
            self.section_files = sorted((n for n in self.z.namelist() if re.match(r"Contents/section\d+\.xml$", n)),
                                        key=lambda n: int(re.findall(r"\d+", n)[-1]))
        # 바이너리: content.hpf의 BinData 항목 순서 = BIN_DATA 번호(1부터)
        self.bins = []
        for iid, it in self.items.items():
            href = it.get("href", "")
            if href.startswith("BinData/"):
                self.bins.append((iid, href))
        self.bin_id = {iid: i + 1 for i, (iid, _) in enumerate(self.bins)}

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    # ───────── 문서 정보 ─────────
    def docinfo(self):
        h = self.header
        ref = h.find(HH + "refList")
        recs = []

        def add(tag, level, d):
            recs.append([tag, level, bytearray(R.encode(tag, d))])

        bn = h.find(HH + "beginNum")
        caret = None
        if "settings.xml" in self.z.namelist():
            caret = etree.fromstring(self.z.read("settings.xml")).find(".//{%s}CaretPosition" % NS["ha"])
        add(16, 0, {"sections": I(h, "secCnt", len(self.section_files)), "start_page": I(bn, "page", 1),
                    "start_footnote": I(bn, "footnote", 1), "start_endnote": I(bn, "endnote", 1),
                    "start_picture": I(bn, "pic", 1), "start_table": I(bn, "tbl", 1),
                    "start_equation": I(bn, "equation", 1), "caret_list": max(I(caret, "listIDRef") - 1, 0),
                    "caret_para": I(caret, "paraIDRef"), "caret_pos": I(caret, "pos")})

        def items(group, item):
            g = ref.find(HH + group) if ref is not None else None
            return sorted(g.findall(HH + item), key=lambda e: I(e, "id")) if g is not None else []

        faces = {lang: [] for lang in LANGS}
        g = ref.find(HH + "fontfaces")
        for ff in (g.findall(HH + "fontface") if g is not None else []):
            faces[ff.get("lang")] = sorted(ff.findall(HH + "font"), key=lambda e: I(e, "id"))
        borders, chars, tabs = items("borderFills", "borderFill"), items("charProperties", "charPr"), items("tabProperties", "tabPr")
        numberings, bullets = items("numberings", "numbering"), items("bullets", "bullet")
        paras, styles, memos = items("paraProperties", "paraPr"), items("styles", "style"), items("memoProperties", "memoPr")
        tcs, tcas = items("trackChanges", "trackChange"), items("trackChangeAuthors", "trackChangeAuthor")
        add(17, 0, {"counts": [len(self.bins)] + [len(faces[lang]) for lang in LANGS]
                    + [len(borders), len(chars), len(tabs), len(numberings), len(bullets), len(paras), len(styles),
                       len(memos), len(tcs), len(tcas)]})
        for iid, href in self.bins:
            ext = os.path.splitext(href)[1].lstrip(".").upper()   # 한컴 HWPX→HWP는 대문자(BIN0001.PNG)
            add(18, 1, {"attr": 1, "bin_id": self.bin_id[iid], "ext": ext})
        for lang in LANGS:
            for f in faces[lang]:
                add(19, 1, self.face(f))
        for e in borders:
            add(20, 1, self.border_fill(e))
        for e in chars:
            add(21, 1, self.char_shape(e))
        for e in tabs:
            add(22, 1, self.tab_def(e))
        for e in numberings:
            add(23, 1, self.numbering(e))
        for e in bullets:
            add(24, 1, self.bullet(e))
        for e in paras:
            add(25, 1, self.para_shape(e))
        for e in styles:
            add(26, 1, {"name": A(e, "name", ""), "eng_name": A(e, "engName", ""),
                        "attr": STYLE_TYPE.get(A(e, "type"), 0) | B(e, "lockForm") << 2,
                        "next_id": I(e, "nextStyleIDRef"), "lang": I(e, "langID", 1042),
                        "para_shape_id": I(e, "paraPrIDRef"), "char_shape_id": I(e, "charPrIDRef"),
                        "_rest": b"\0\0"})
        for e in memos:
            add(92, 1, {"width": I(e, "width"), "line_width": I(e, "lineWidth"),
                        "line_type": BORDER_TYPE.get(A(e, "lineType"), 1), "line_color": color(A(e, "lineColor")),
                        "fill_color": color(A(e, "fillColor")), "active_color": color(A(e, "activeColor")),
                        "memo_type": 0})
        # 변경 추적: 작성자(97)·내용(96) — ID_MAPPINGS 개수와 레코드 수가 맞아야 한컴이 연다(안 맞으면 못 엶)
        for e in tcas:
            add(97, 1, {"name": A(e, "name", ""), "_rest": struct.pack("<II", I(e, "mark", 1), 0)})
        for e in tcs:
            add(96, 1, self.track_change(e))
        pi = self.print_info()
        if pi is not None:
            add(27, 0, {"set": pi})
        add(94, 1, {"unk_0": 0, "unk_4": 0, "not_start": "", "not_end": ""})
        cd = h.find(HH + "compatibleDocument")
        add(30, 0, {"target": {"HWP201X": 0, "HWP200X": 1, "MS_WORD": 2}.get(A(cd, "targetProgram"), 0)})
        lc = child(cd, HH + "layoutCompatibility")
        if lc is not None and len(lc):
            self.warn(f"레이아웃 호환 항목 {len(lc)}개는 아직 옮기지 않음")
        add(31, 1, {"char": 0, "para": 0, "section": 0, "object": 0, "field": 0})
        tc = h.find(HH + "trackchageConfig")
        add(32, 1, {"values": [[I(tc, "flags", 56)]] + [[0]] * 257})
        return recs

    def track_change(self, e):
        """변경 추적 내용(정답지 4개 문서): 종류(Insert 16·Delete 17·ParaShape 19), 시각(컴퓨터 지역 시간대, 연·월·일·시·분),
        작성자 번호, 0, ParaShape면 4 + 문단 모양 번호, 숨김."""
        import datetime
        typ = A(e, "type", "Insert")
        t = datetime.datetime.strptime(A(e, "date", "1970-01-01T00:00:00Z"), "%Y-%m-%dT%H:%M:%SZ")
        t = t.replace(tzinfo=datetime.timezone.utc).astimezone()
        rest = struct.pack("<H", 0)
        if A(e, "parashapeID") is not None or typ == "ParaShape":
            rest += struct.pack("<II", 4, I(e, "parashapeID"))
        else:
            rest += struct.pack("<I", 0)
        rest += struct.pack("<I", B(e, "hide"))
        return {"type": {"Insert": 16, "Delete": 17, "CharShape": 18, "ParaShape": 19}.get(typ, 16),
                "time": [t.year, t.month, t.day, t.hour, t.minute, I(e, "authorID", 1)], "_rest": rest}

    def print_info(self):
        """settings.xml의 PrintInfo → 한컴이 HWP에 쓰는 셋(아이템 ID·종류 순서는 정답지 132개 그대로).
        PrintMethod·ZoomX·ZoomY 자리는 값으로 확인, 나머지(0인 것들) 자리는 추정."""
        if "settings.xml" not in self.z.namelist():
            return None
        root = etree.fromstring(self.z.read("settings.xml"))
        cfg = "{urn:oasis:names:tc:opendocument:xmlns:config:1.0}"
        box = next((e for e in root.iter(cfg + "config-item-set") if e.get("name") == "PrintInfo"), None)
        if box is None:
            return None
        v = {}
        for it in box.iter(cfg + "config-item"):
            t = (it.text or "0").strip()
            v[it.get("name")] = 1 if t == "true" else 0 if t == "false" else int(float(t))
        order = [(0x4006, 6, "PrintMethod"), (0x400E, 6, "PrintAutoFootNote"), (0x400A, 6, "PrintAutoHeadNote"),
                 (0x401F, 7, "ZoomX"), (0x401D, 6, "PrintCropMark"), (0x401A, 6, "BinderHoleType"),
                 (0x4010, 7, "OverlapSize"), (0x4020, 7, "ZoomY")]
        inner = {"id": 519, "unk_2": 0, "items": [{"id": i, "type": t, "value": v[n] & 0xFFFFFFFF}
                                                    for i, t, n in order if n in v]}   # 없는 항목은 한컴도 안 씀
        return {"id": 540, "unk_2": 0, "items": [{"id": 519, "type": 0x8000, "value": inner}]}

    def face(self, f):
        d = {"attr": FONT_TYPE.get(A(f, "type"), 1), "name": A(f, "face", "")}
        sub = f.find(HH + "substFont")
        if sub is not None:
            d["attr"] |= 0x80
            d["alt_type"], d["alt_name"] = FONT_TYPE.get(A(sub, "type"), 1), A(sub, "face", "")
        ti = f.find(HH + "typeInfo")
        if ti is not None:
            d["attr"] |= 0x40
            d["type_info"] = [FCAT.get(A(ti, "familyType"), 0), 0] + [
                I(ti, k) & 0xFF for k in ("weight", "proportion", "contrast", "strokeVariation", "armStyle",
                                          "letterform", "midline", "xHeight")]
        return d

    def fill(self, fb):
        f = {"type": 0, "extra_size": 0, "extra": b"", "alphas": []}
        if fb is None:
            return f
        wb, gr, ib = fb.find(HC + "winBrush"), fb.find(HC + "gradation"), fb.find(HC + "imgBrush")
        alphas = []                               # 투명도 바이트 순서: 단색·그러데이션·그림
        if wb is not None:
            f["type"] |= 1
            hs = A(wb, "hatchStyle")
            f.update(bg_color=color(A(wb, "faceColor")), pat_color=color(A(wb, "hatchColor"), 0),
                     pat_type=HATCH.get(hs, -1) if hs else -1)
            alphas.append(int(num(A(wb, "alpha", "0"))))
        if gr is not None:
            f["type"] |= 4
            cols = [color(c.get("value"), 0) for c in gr.findall(HC + "color")]
            f.update(grad_type=GRAD.get(A(gr, "type"), 1), grad_angle=I(gr, "angle"), grad_cx=I(gr, "centerX"),
                     grad_cy=I(gr, "centerY"), grad_step=I(gr, "step"), grad_n=len(cols), grad_pos=[], grad_colors=cols)
            if len(cols) > 2:
                self.warn("색 세 개 이상 그러데이션의 위치는 아직 옮기지 않음(0으로)")
                f["grad_pos"] = [0] * len(cols)
            f["extra_size"], f["extra"] = 1, bytes([I(gr, "stepCenter", 50) & 0xFF])
            alphas.append(int(num(A(gr, "alpha", "0"))))
        if ib is not None:
            f["type"] |= 2
            img = ib.find(HC + "img")
            bid = self.bin_id.get(A(img, "binaryItemIDRef"), 0)
            f.update(img_type=IMG_MODE.get(A(ib, "mode"), 5),
                     img_info=struct.pack("<bbBH", I(img, "contrast"), I(img, "bright"),
                                          IMG_EFFECT.get(A(img, "effect"), 0), bid))
            alphas.append(int(num(A(img, "alpha", "0"))))
        f["alphas"] = [a & 0xFF for a in alphas]
        return f

    def border_fill(self, e):
        sl, bs = e.find(HH + "slash"), e.find(HH + "backSlash")
        attr = B(e, "threeD") | B(e, "shadow") << 1
        attr |= SLASH.get(A(sl, "type", "NONE"), 0) << 2 | SLASH.get(A(bs, "type", "NONE"), 0) << 5
        attr |= (I(sl, "Crooked") & 3) << 8 | (I(bs, "Crooked") & 1) << 10
        attr |= B(sl, "isCounter") << 11 | B(bs, "isCounter") << 12
        attr |= (A(e, "centerLine", "NONE") != "NONE") << 13
        if B(e, "breakCellSeparateLine"):
            self.warn("테두리 breakCellSeparateLine 자리 미확인 — 옮기지 않음")
        sides = [e.find(HH + n) for n in ("leftBorder", "rightBorder", "topBorder", "bottomBorder")]
        dg = e.find(HH + "diagonal")
        diag = (BORDER_TYPE.get(A(dg, "type", "SOLID"), 1), self.width(A(dg, "width")), color(A(dg, "color"), 0)) \
            if dg is not None else (0, 1, 0)          # 대각선 요소가 없으면 한컴은 종류 0·굵기 1(0.12 mm)
        return {"attr": attr, "line_types": [BORDER_TYPE.get(A(s, "type", "NONE"), 0) for s in sides],
                "line_widths": [self.width(A(s, "width")) for s in sides],
                "line_colors": [color(A(s, "color"), 0) for s in sides],
                "diag_type": diag[0], "diag_width": diag[1], "diag_color": diag[2],
                "fill": self.fill(e.find(HC + "fillBrush"))}

    def width(self, s):
        s = (s or "0.1 mm").strip()
        if s in WIDTHS:
            return WIDTHS.index(s)
        try:
            v = float(s.split()[0])
            return min(range(len(WIDTHS)), key=lambda i: abs(float(WIDTHS[i].split()[0]) - v))
        except ValueError:
            return 0

    def char_shape(self, e):
        def seven(name, default):
            el = e.find(HH + name)
            return [I(el, k, default) for k in LANG_ATTR]
        ul, so, ol, sh = (e.find(HH + n) for n in ("underline", "strikeout", "outline", "shadow"))
        attr = (e.find(HH + "italic") is not None) | (e.find(HH + "bold") is not None) << 1
        attr |= UNDERLINE.get(A(ul, "type", "NONE"), 0) << 2 | LINE_SHAPE.get(A(ul, "shape", "SOLID"), 15) << 4
        attr |= OUTLINE.get(A(ol, "type", "NONE"), 0) << 8 | SHADOW.get(A(sh, "type", "NONE"), 0) << 11
        attr |= (e.find(HH + "emboss") is not None) << 13 | (e.find(HH + "engrave") is not None) << 14
        attr |= (e.find(HH + "supscript") is not None) << 15 | (e.find(HH + "subscript") is not None) << 16
        sshape = A(so, "shape", "NONE")
        if sshape in LINE_SHAPE:                  # 취소선 = 가운데 밑줄(종류 2) + 취소선 비트(probe)
            attr |= 1 << 18 | LINE_SHAPE[sshape] << 26
            if UNDERLINE.get(A(ul, "type", "NONE"), 0) == 0:
                attr = attr & ~0xFC | 2 << 2 | LINE_SHAPE[sshape] << 4
        attr |= SYMMARK.get(A(e, "symMark", "NONE"), 0) << 21
        attr |= B(e, "useFontSpace") << 25 | B(e, "useKerning") << 30
        return {"face_ids": seven("fontRef", 0), "ratios": seven("ratio", 100), "spacings": seven("spacing", 0),
                "rel_sizes": seven("relSz", 100), "offsets": seven("offset", 0), "base_size": I(e, "height", 1000),
                "attr": attr, "shadow_x": I(sh, "offsetX", 10), "shadow_y": I(sh, "offsetY", 10),
                "text_color": color(A(e, "textColor"), 0), "underline_color": color(A(ul, "color"), 0),
                "shade_color": color(A(e, "shadeColor")), "shadow_color": color(A(sh, "color", "#C0C0C0"), 0),
                "border_fill_id": I(e, "borderFillIDRef"), "strike_color": color(A(so, "color"), 0)}

    def tab_def(self, e):
        its = []
        for t in e.iter(HH + "tabItem"):
            if t.getparent().tag == HP + "case":
                continue
            pos = max(I(t, "pos"), 0) * unit_factor(t, e)   # 음수 위치는 한컴이 0으로
            its.append([pos, TAB_TYPE.get(A(t, "type"), 0), BORDER_TYPE.get(A(t, "leader", "NONE"), 0), 0])
        return {"attr": B(e, "autoTabLeft") | B(e, "autoTabRight") << 1, "tabs": its}

    def head(self, ph):
        attr = HEAD_ALIGN.get(A(ph, "align", "LEFT"), 0) | B(ph, "useInstWidth") << 2
        attr |= (B(ph, "autoIndent") if ph is not None else 1) << 3
        attr |= (A(ph, "textOffsetType", "PERCENT") == "HWPUNIT") << 4 | (NUMFMT.get(A(ph, "numFormat", "DIGIT"), 0) & 0xFF) << 5
        return [attr, I(ph, "widthAdjust"), I(ph, "textOffset", 50), I(ph, "charPrIDRef", 0xFFFFFFFF) & 0xFFFFFFFF]

    def numbering(self, e):
        heads = {I(ph, "level"): ph for ph in e.findall(HH + "paraHead")}
        lv = [heads.get(i) for i in range(1, 11)]
        d = {"levels": [{"head": self.head(ph), "format": (ph.text or "") if ph is not None else ""} for ph in lv[:7]],
             "start": I(e, "start"), "level_starts": [I(ph, "start", 1) for ph in lv[:7]]}
        d["ext_levels"] = [{"head": self.head(ph), "format": (ph.text or "") if ph is not None else ""} for ph in lv[7:]]
        d["ext_starts"] = [I(ph, "start", 1) for ph in lv[7:]]
        return d

    def bullet(self, e):
        ph = e.find(HH + "paraHead")
        img = e.find(HC + "img")
        return {"head": self.head(ph), "char": wchar(A(e, "char", "")),
                "image_id": self.bin_id.get(A(img, "binaryItemIDRef"), 0) if B(e, "useImage") else 0,
                "image_info": [I(img, "contrast"), I(img, "bright"), IMG_EFFECT.get(A(img, "effect"), 0), 0]
                if img is not None else [0, 0, 0, 0],
                "check_char": wchar(A(e, "checkedChar", "")), "_rest": b"\0"}

    def para_shape(self, e):
        al, hd, bk, sp, bd = (switched(e, HH + n) for n in ("align", "heading", "breakSetting", "autoSpacing", "border"))
        mg, ls = switched(e, HH + "margin"), switched(e, HH + "lineSpacing")
        lst = LINE_SPACING.get(A(ls, "type", "PERCENT"), 0)
        attr1 = min(lst, 2) | ALIGN_H.get(A(al, "horizontal", "JUSTIFY"), 0) << 2
        attr1 |= BREAK_LATIN.get(A(bk, "breakLatinWord", "KEEP_WORD"), 0) << 5
        attr1 |= BREAK_NONLATIN.get(A(bk, "breakNonLatinWord", "KEEP_WORD"), 1) << 7
        attr1 |= B(e, "snapToGrid") << 8 | (I(e, "condense") & 0x7F) << 9
        attr1 |= B(bk, "widowOrphan") << 16 | B(bk, "keepWithNext") << 17 | B(bk, "keepLines") << 18
        attr1 |= B(bk, "pageBreakBefore") << 19 | ALIGN_V.get(A(al, "vertical", "BASELINE"), 0) << 20
        attr1 |= B(e, "fontLineHeight") << 22 | HEADING.get(A(hd, "type", "NONE"), 0) << 23
        attr1 |= min(I(hd, "level"), 6) << 25 | B(bd, "connect") << 28 | B(bd, "ignoreMargin") << 29
        attr2 = LINE_WRAP.get(A(bk, "lineWrap", "BREAK"), 0) | TEXT_DIR.get(A(e, "textDir", "LTR"), 2) << 2
        attr2 |= B(sp, "eAsianEng") << 4 | B(sp, "eAsianNum") << 5
        if B(e, "suppressLineNumbers") or B(e, "checked"):
            self.warn("문단 suppressLineNumbers·checked 자리 미확인 — 옮기지 않음")

        def m(name):
            el = child(mg, HC + name)
            if el is not None and A(el, "unit") == "CHAR" and UNIT2[0] and mg.getparent() is e:
                return I(el, "value") * 2 + 1           # 글자 단위 = 2×값 + 1(맨 아래 비트가 단위 표시)
            return I(el, "value") * (unit_factor(el, e, mg) if el is not None else 1)
        return {"attr1": attr1, "left": m("left"), "right": m("right"), "indent": m("intent"), "prev": m("prev"),
                "next": m("next"), "line_spacing_old": self.spacing(ls, e), "tab_def_id": I(e, "tabPrIDRef"),
                "numbering_id": I(hd, "idRef"), "border_fill_id": I(bd, "borderFillIDRef"),
                "border_left": I(bd, "offsetLeft"), "border_right": I(bd, "offsetRight"),
                "border_top": I(bd, "offsetTop"), "border_bottom": I(bd, "offsetBottom"), "attr2": attr2,
                "attr3": lst, "line_spacing": self.spacing(ls, e), "_rest": struct.pack("<I", I(hd, "level"))}

    def spacing(self, ls, e):
        v = I(ls, "value", 160)
        return v if A(ls, "type", "PERCENT") == "PERCENT" or ls is None else v * unit_factor(ls, e)

    # ───────── 본문 ─────────
    def merged(self, ps):
        """변경 추적 BodyText 판: 문단 끝까지 지운 문단은 다음 문단과 한 문단이 된다(한컴 저장본과 같게). 머리 값은 앞 문단 것.
        원본 나무는 ViewText 판에도 쓰므로 합칠 때는 사본을 고친다."""
        if not getattr(self, "final", False):
            return ps

        def end_deleted(p):
            ends = p.findall(f"{HP}run/{HP}t/{HP}deleteEnd") + p.findall(f"{HP}run/{HP}deleteEnd")
            return any(B(e, "paraend") for e in ends)

        out, cur = [], None
        for p in ps:
            if cur is None:
                cur = p
            else:
                if cur in ps:
                    cur = copy.deepcopy(cur)
                for ch in p:
                    if etree.QName(ch).localname != "linesegarray":
                        c = copy.deepcopy(ch)
                        self.merged_in.append(c)          # 뒤에서 합쳐 들어온 run(남는 글 없으면 글자 모양을 남기지 않음)
                        cur.append(c)
            if end_deleted(p):
                if p is not ps[-1]:
                    continue
                self.warn("목록 마지막 문단의 끝을 지운 변경 추적은 합칠 다음 문단이 없어 그대로 둠")
            out.append(cur)
            cur = None
        return out

    def section(self, root):
        paras = self.merged(root.findall(HP + "p"))
        recs = []
        for i, p in enumerate(paras):
            recs += self.para(p, 0, i == len(paras) - 1)
        recs += self.memos                        # 메모 내용은 구역 끝에 모아 둔다
        self.memos = []
        return recs

    def para_list(self, parent, level):
        ps = self.merged(parent.findall(HP + "p"))
        out = []
        for i, p in enumerate(ps):
            out += self.para(p, level, i == len(ps) - 1)
        return out, len(ps)

    def para(self, p, lv, last):
        text, runs, ctrls, tags = [], [], [], []
        mask = 0
        pen = []                                  # 열린 형광펜 (시작 위치, 색)
        track = {}                                # 열린 변경 추적 Id → (시작 위치, 종류)
        final = getattr(self, "final", False)     # 변경 추적 문서의 BodyText 판: 지운 내용 빼고 표시 없음
        st_ = {"del": False, "touched": False}
        merged_in = {id(x) for x in getattr(self, "merged_in", [])}

        def run_at(cp):
            pos = len(text)
            if runs and runs[-1][0] == pos:
                runs[-1][1] = cp
                if len(runs) > 1 and runs[-2][1] == cp:
                    runs.pop()
            elif not runs or runs[-1][1] != cp:
                runs.append([pos, cp])

        def ext(name, records):
            nonlocal mask
            if final and st_["del"]:
                return
            code = CODE[name]
            text.extend(ctrl_chars(code, name))
            mask |= 1 << code
            ctrls.extend(records)

        def put_text(s):
            if s and not (final and st_["del"]):
                text.extend(u16s(s))

        for run in p.findall(HP + "run"):
            if id(run) in merged_in:              # 합쳐 들어온 run이 남기는 글이 없으면 글자 모양도 남기지 않는다
                snap, start = [r[:] for r in runs], len(text)  # (시험 문서 merge_A·B: 합친 문단 끝 = 앞 문단 마지막 run 모양)
            run_at(I(run, "charPrIDRef"))
            for el in run:
                tag = etree.QName(el).localname
                if tag == "secPr":
                    ext("secd", self.secd(el, lv + 1))
                elif tag == "ctrl":
                    if final and st_["del"]:
                        continue
                    for c in el:
                        mask |= self.ctrl(c, lv + 1, text, ctrls)
                elif tag == "t":
                    put_text(el.text)
                    for c in el:
                        ct = etree.QName(c).localname
                        if final and st_["del"] and ct not in ("deleteEnd",):
                            put_text(c.tail)
                            continue
                        if ct == "tab":
                            text.extend([9, I(c, "width") & 0xFFFF, I(c, "width") >> 16 & 0xFFFF,
                                         (I(c, "leader") & 0xFF) | (I(c, "type") & 0xFF) << 8, 0x20, 0x20, 0x20, 9])
                            mask |= 1 << 9
                        elif ct in CHAR_CODE:
                            text.append(CHAR_CODE[ct])
                            mask |= 1 << CHAR_CODE[ct]
                        elif ct == "markpenBegin":
                            pen.append((len(text), color(A(c, "color", "#FFFF00"), 0)))
                        elif ct == "markpenEnd":
                            if pen:
                                st, col = pen.pop()
                                tags.append([st, len(text), 0x02000000 | (col & 0xFFFFFF)])
                        elif ct == "titleMark":
                            text.extend([8, 0x6974, 0x4D74, 0x20, 0x20, 0x20, 0x20, 8])
                            mask |= 1 << 8
                        elif ct in ("insertBegin", "deleteBegin"):
                            st_["touched"] = True
                            if final:
                                st_["del"] = st_["del"] or ct == "deleteBegin"
                            else:
                                track[A(c, "Id")] = (len(text), 0x10 if ct == "insertBegin" else 0x11, I(c, "TcId"))
                        elif ct in ("insertEnd", "deleteEnd"):
                            if final:
                                if ct == "deleteEnd":
                                    st_["del"] = False
                            else:
                                st0 = track.pop(A(c, "Id"), None)
                                if st0:
                                    tags.append([st0[0], len(text), st0[1] << 24 | (st0[2] & 0xFFFFFF)])
                        else:
                            self.warn(f"글 안의 {ct}는 아직 옮기지 않음")
                        put_text(c.tail)
                elif tag == "tbl":
                    ext("tbl ", self.table(el, lv + 1))
                elif tag == "equation":
                    ext("eqed", self.equation(el, lv + 1))
                elif tag in FORM_ELEMENT:
                    ext("form", self.form(el, lv + 1))
                elif tag in DRAW_TAGS:
                    ext("gso ", self.gso(el, lv + 1))
                elif tag in SHAPE_TAGS:
                    self.warn(f"개체 {tag}는 아직 옮기지 않음(빠짐)")
                elif tag == "compose":
                    ids = [I(x, "prIDRef") & 0xFFFFFFFF for x in el.findall(HP + "charPr")]
                    ids += [0xFFFFFFFF] * (I(el, "charPrCnt", len(ids)) - len(ids))
                    ext("tcps", [[71, lv + 1, bytearray(R.encode(71, {
                        "ctrl_id": "tcps", "text": A(el, "composeText", ""),
                        "border_type": COMPOSE_CIRCLE.get(A(el, "circleType", "CHAR"), 0), "inner_size": I(el, "charSz"),
                        "expand": A(el, "composeType", "SPREAD") == "OVERLAP", "char_shape_ids": ids}))]])
                elif tag == "dutmal":
                    mt, sb = el.find(HP + "mainText"), el.find(HP + "subText")
                    ext("tdut", [[71, lv + 1, bytearray(R.encode(71, {
                        "ctrl_id": "tdut", "main_text": (mt.text or "") if mt is not None else "",
                        "sub_text": (sb.text or "") if sb is not None else "",
                        "position": {"TOP": 0, "BOTTOM": 1, "CENTER": 2}.get(A(el, "posType", "TOP"), 0),
                        "size_ratio": I(el, "szRatio"), "option": I(el, "option"), "style_id": I(el, "styleIDRef"),
                        "align": ALIGN_H.get(A(el, "align", "CENTER"), 3)}))]])
                else:
                    self.warn(f"run 안의 {tag}는 아직 옮기지 않음")
            if id(run) in merged_in and len(text) == start:
                runs[:] = snap
        text.append(13)
        segs = []
        la = p.find(HP + "linesegarray")
        if la is not None:
            for s in la.findall(HP + "lineseg"):
                segs.append([I(s, k) for k in ("textpos", "vertpos", "vertsize", "textheight", "baseline", "spacing",
                                               "horzpos", "horzsize")] + [I(s, "flags") & 0xFFFFFFFF])
        if not runs:
            runs = [[0, 0]]
        if final and st_["touched"]:              # 한컴 BodyText: 바뀐 문단은 줄 배치 없이(열 때 다시 짬)
            segs = []
        runs = [r for r in runs if r[0] < len(text)] or [[0, runs[0][1]]]
        pens = [t_ for t_ in tags if t_[2] >> 24 == 2]
        if pens:
            tags = [[0, len(text) - 1, 0] for _ in pens] + tags
        brk = (B(p, "pageBreak") << 2) | (B(p, "columnBreak") << 3)
        direct = {bytes(c[2][:4]) for c in ctrls if c[0] == 71 and c[1] == lv + 1}
        if struct.pack("<I", R.ctrl_value("secd")) in direct:
            brk |= 1
        if struct.pack("<I", R.ctrl_value("cold")) in direct:
            brk |= 2
        hdr = {"nchars": len(text) | (0x80000000 if last else 0), "ctrl_mask": mask,
               "para_shape_id": I(p, "paraPrIDRef"), "style_id": I(p, "styleIDRef"), "break_type": brk,
               "char_shape_count": len(runs), "range_tag_count": len(tags), "line_seg_count": len(segs),
               "instance_id": I(p, "id") & 0xFFFFFFFF, "track_merge": B(p, "merged")}
        out = [[66, lv, bytearray(R.encode(66, hdr))]]
        if len(text) > 1:
            out.append([67, lv + 1, bytearray(struct.pack(f"<{len(text)}H", *text))])
        out.append([68, lv + 1, bytearray(R.encode(68, {"runs": runs}))])
        if segs:
            out.append([69, lv + 1, bytearray(R.encode(69, {"segs": segs}))])
        if tags:
            out.append([70, lv + 1, bytearray(R.encode(70, {"tags": tags}))])
        return out + ctrls

    def ctrl(self, c, lv, text, ctrls):
        """hp:ctrl 아래 요소 하나 → 글자 + 레코드. 쓴 컨트롤 코드의 비트를 돌려준다."""
        tag = etree.QName(c).localname

        def ext(name, records):
            code = CODE[name]
            text.extend(ctrl_chars(code, name))
            ctrls.extend(records)
            return 1 << code

        def head_rec(d):
            return [71, lv, bytearray(R.encode(71, d))]

        if tag == "colPr":
            return ext("cold", [head_rec(self.cold(c))])
        if tag in ("header", "footer"):
            name = "head" if tag == "header" else "foot"
            sub = c.find(HP + "subList")
            body, n = self.para_list(sub, lv + 1)
            lh = self.list_header(sub, n, "header")
            lh.update(text_width=I(sub, "textWidth"), text_height=I(sub, "textHeight"), text_ref=B(sub, "hasTextRef"),
                      num_ref=B(sub, "hasNumRef"), _rest=b"\0" * 16)
            return ext(name, [head_rec({"ctrl_id": name, "attr": APPLY_PAGE.get(A(c, "applyPageType"), 0),
                                        "_rest": struct.pack("<I", I(c, "id"))}),
                              [72, lv + 1, bytearray(R.encode(72, lh))]] + body)
        if tag in ("footNote", "endNote"):
            name = "fn  " if tag == "footNote" else "en  "
            sub = c.find(HP + "subList")
            body, n = self.para_list(sub, lv + 1)
            lh = self.list_header(sub, n, "plain")
            lh["_rest"] = b"\0" * 8
            return ext(name, [head_rec({"ctrl_id": name, "number": I(c, "number"), "user_char": 0, "prefix_char": 0,
                                        "suffix_char": I(c, "suffixChar"), "unk_8": 0,
                                        "instance_id": I(c, "instId") & 0xFFFFFFFF}),
                              [72, lv + 1, bytearray(R.encode(72, lh))]] + body)
        if tag == "autoNum":
            f = c.find(HP + "autoNumFormat")
            return ext("atno", [head_rec({"ctrl_id": "atno", "attr": self.autonum_attr(c, f), "number": I(c, "num"),
                                          "user_char": wchar(A(f, "userChar", "")),
                                          "prefix_char": wchar(A(f, "prefixChar", "")),
                                          "suffix_char": wchar(A(f, "suffixChar", ""))})])
        if tag == "newNum":
            return ext("nwno", [head_rec({"ctrl_id": "nwno", "attr": NUM_TYPE.get(A(c, "numType"), 0),
                                          "number": I(c, "num")})])
        if tag == "pageNum":
            return ext("pgnp", [head_rec({"ctrl_id": "pgnp",
                                          "attr": NUMFMT.get(A(c, "formatType", "DIGIT"), 0) & 0xFF
                                          | PAGENUM_POS.get(A(c, "pos"), 0) << 8,
                                          "user_char": 0, "prefix_char": 0, "suffix_char": 0,
                                          "dash_char": wchar(A(c, "sideChar", ""))})])
        if tag == "pageHiding":
            attr = 0
            for i, k in enumerate(("hideHeader", "hideFooter", "hideMasterPage", "hideBorder", "hideFill", "hidePageNum")):
                attr |= B(c, k) << i
            return ext("pghd", [head_rec({"ctrl_id": "pghd", "attr": attr})])
        if tag == "pageNumCtrl":
            return ext("pgct", [head_rec({"ctrl_id": "pgct", "attr": APPLY_PAGE.get(A(c, "pageStartsOn"), 0)})])
        if tag == "bookmark":
            return ext("bokm", [head_rec({"ctrl_id": "bokm"}),
                                [87, lv + 1, bytearray(R.encode(87, {"set": self.name_set(A(c, "name", ""))}))]])
        if tag == "fieldBegin":
            return self.field_begin(c, lv, text, ctrls)
        if tag == "hiddenComment":
            sub = c.find(HP + "subList")
            body, n = self.para_list(sub, lv + 1)
            lh = self.list_header(sub, n, "plain")
            lh["_rest"] = b"\0" * 8
            return ext("tcmt", [head_rec({"ctrl_id": "tcmt"}), [72, lv + 1, bytearray(R.encode(72, lh))]] + body)
        if tag == "indexmark":
            k1, k2 = c.find(HP + "firstKey"), c.find(HP + "secondKey")
            return ext("idxm", [head_rec({"ctrl_id": "idxm", "keyword1": (k1.text or "") if k1 is not None else "",
                                          "keyword2": (k2.text or "") if k2 is not None else "", "dummy": 0,
                                          "_rest": b"\0\0"})])
        if tag == "fieldEnd":
            info = self.fields.get(A(c, "beginIDRef"), ("%unk", 0, 0, 0))
            v = (R.ctrl_value(info[0]) & 0xFFFFFF) | (info[1] & 0xFF) << 24
            text.extend([4, v & 0xFFFF, v >> 16, info[2] & 0xFFFF, 0, info[3] & 0xFFFF, 0, 4])
            return 1 << 4
        self.warn(f"컨트롤 {tag}는 아직 옮기지 않음(빠짐)")
        return 0

    def field_begin(self, c, lv, text, ctrls):
        typ = A(c, "type", "UNKNOWN")
        name = FIELD_ID.get(typ, "%unk")
        text_id = "%%me" if typ == "MEMO" else name
        params = {pp.get("name"): (pp.text or "") for pp in c.iter() if etree.QName(pp).localname in
                  ("stringParam", "integerParam", "booleanParam")}
        prop = int(params.get("Prop", "0") or 0)
        attr = B(c, "editable") | B(c, "dirty") << 15 | (typ == "CROSSREF") << 1   # 상호 참조는 비트 1(정답지 5/5)
        memo = int(params.get("Number", "0") or 0) if typ == "MEMO" else 0
        self.fields[A(c, "id")] = (text_id, prop, B(c, "editable"), memo)
        d = {"ctrl_id": name, "attr": attr, "extra_attr": prop & 0xFF, "command": params.get("Command", ""),
             "field_id": I(c, "id") & 0xFFFFFFFF, "memo_index": memo}
        text.extend(ctrl_chars(3, text_id))
        ctrls.append([71, lv, bytearray(R.encode(71, d))])
        if A(c, "name"):
            ctrls.append([87, lv + 1, bytearray(R.encode(87, {"set": self.name_set(A(c, "name"))}))])
        sub = c.find(HP + "subList")
        if typ == "MEMO" and sub is not None:
            body, n = self.para_list(sub, 1)
            lh = self.list_header(sub, n, "plain")
            lh["_rest"] = b"\0" * 8
            self.memos += [[93, 1, bytearray(R.encode(93, {"memo_id": memo}))], [72, 1, bytearray(R.encode(72, lh))]] + body
        elif sub is not None:
            self.warn(f"필드 {typ}의 subList는 아직 옮기지 않음")
        return 1 << 3

    def name_set(self, name):
        return {"id": 0x021B, "unk_2": 0, "items": [{"id": 0x4000, "type": 1, "value": name}]}

    def autonum_attr(self, c, f):
        return (NUM_TYPE.get(A(c, "numType"), 0) | (NUMFMT.get(A(f, "type", "DIGIT"), 0) & 0xFF) << 4
                | B(f, "supscript") << 12)

    def list_header(self, sub, n, kind):
        attr = LIST_DIR.get(A(sub, "textDirection", "HORIZONTAL"), 0) | LINE_WRAP.get(A(sub, "lineWrap", "BREAK"), 0) << 3
        attr |= LIST_VALIGN.get(A(sub, "vertAlign", "TOP"), 0) << 5
        return {"kind": kind, "para_count": n, "unk_2": 0, "attr": attr}

    def secd(self, s, lv):
        vis, st, grid = s.find(HP + "visibility"), s.find(HP + "startNum"), s.find(HP + "grid")
        attr = B(vis, "hideFirstHeader") | B(vis, "hideFirstFooter") << 1 | B(vis, "hideFirstMasterPage") << 2
        border, fill = A(vis, "border", "SHOW_ALL"), A(vis, "fill", "SHOW_ALL")
        attr |= (border == "HIDE_FIRST") << 3 | (fill == "HIDE_FIRST") << 4 | B(vis, "hideFirstPageNum") << 5
        attr |= (border == "SHOW_FIRST") << 8 | (fill == "SHOW_FIRST") << 9
        attr |= LIST_DIR.get(A(s, "textDirection", "HORIZONTAL"), 0) << 16 | B(vis, "hideFirstEmptyLine") << 19
        attr |= APPLY_PAGE.get(A(st, "pageStartsOn", "BOTH"), 0) << 20 | B(grid, "wonggojiFormat") << 22
        attr |= (s.find(HP + "masterPage") is not None) << 29   # 바탕쪽이 있으면 비트 29(공개 HWP 367구역: 있음 3 = 비트 3)
        d = {"ctrl_id": "secd", "attr": attr, "column_gap": I(s, "spaceColumns"), "vert_grid": I(grid, "lineGrid"),
             "horz_grid": I(grid, "charGrid"), "default_tab": I(s, "tabStop", 8000),
             "numbering_para_shape_id": I(s, "outlineShapeIDRef"), "start_page": I(st, "page"),
             "start_picture": I(st, "pic"), "start_table": I(st, "tbl"), "start_equation": I(st, "equation"),
             "lang": 0, "_rest": b"\0" * 4 + struct.pack("<I", I(s, "memoShapeIDRef")) + b"\0" * 9}
        recs = [[71, lv, bytearray(R.encode(71, d))]]
        pp = s.find(HP + "pagePr")
        mg = child(pp, HP + "margin")
        recs.append([73, lv + 1, bytearray(R.encode(73, {
            "width": I(pp, "width"), "height": I(pp, "height"), "left": I(mg, "left"), "right": I(mg, "right"),
            "top": I(mg, "top"), "bottom": I(mg, "bottom"), "header": I(mg, "header"), "footer": I(mg, "footer"),
            "gutter": I(mg, "gutter"),
            "attr": (A(pp, "landscape") == "NARROWLY") | GUTTER.get(A(pp, "gutterType", "LEFT_ONLY"), 0) << 1}))])
        for name in ("footNotePr", "endNotePr"):
            recs.append([74, lv + 1, bytearray(R.encode(74, self.note_shape(s.find(HP + name))))])
        for pb in s.findall(HP + "pageBorderFill"):
            off = pb.find(HP + "offset")
            attr = (A(pb, "textBorder", "PAPER") == "PAPER") | B(pb, "headerInside") << 1 | B(pb, "footerInside") << 2
            attr |= {"PAPER": 0, "PAGE": 1, "BORDER": 2}.get(A(pb, "fillArea", "PAPER"), 0) << 3
            recs.append([75, lv + 1, bytearray(R.encode(75, {
                "attr": attr, "left": I(off, "left"), "right": I(off, "right"), "top": I(off, "top"),
                "bottom": I(off, "bottom"), "border_fill_id": I(pb, "borderFillIDRef")}))])
        for mp in s.findall(HP + "masterPage"):      # 바탕쪽: 구역 정의 아래 LIST_HEADER(머리말 모양) + 문단
            it = self.items.get(mp.get("idRef"))
            if it is None:
                self.warn("바탕쪽 파일을 찾지 못함: " + str(mp.get("idRef")))
                continue
            root = etree.fromstring(self.z.read(it.get("href")))
            if A(root, "type", "BOTH") != "BOTH" or any(I(root, k) for k in ("pageNumber", "pageDuplicate", "pageFront")):
                self.warn("바탕쪽 종류·쪽 번호 설정은 아직 옮기지 않음(양쪽 바탕쪽으로)")
            sub = root.find(HP + "subList")
            body, n = self.para_list(sub, lv + 1)
            lh = self.list_header(sub, n, "header")
            lh.update(text_width=I(sub, "textWidth"), text_height=I(sub, "textHeight"), text_ref=B(sub, "hasTextRef"),
                      num_ref=B(sub, "hasNumRef"), _rest=b"\0" * 16)
            recs += [[72, lv + 1, bytearray(R.encode(72, lh))]] + body
        return recs

    def note_shape(self, n):
        f, nl, ns, nb, pl = (child(n, HP + k) for k in ("autoNumFormat", "noteLine", "noteSpacing", "numbering",
                                                        "placement"))
        attr = NUMFMT.get(A(f, "type", "DIGIT"), 0) & 0xFF | NOTE_PLACE.get(A(pl, "place"), 0) << 8
        attr |= NOTE_NUMBERING.get(A(nb, "type", "CONTINUOUS"), 0) << 10 | B(f, "supscript") << 12
        attr |= B(pl, "beneathText") << 13
        return {"attr": attr, "user_char": wchar(A(f, "userChar", "")), "prefix_char": wchar(A(f, "prefixChar", "")),
                "suffix_char": wchar(A(f, "suffixChar", "")), "start": I(nb, "newNum", 1),
                "line_length": I(nl, "length"), "above": I(ns, "aboveLine"), "below": I(ns, "belowLine"),
                "between": I(ns, "betweenNotes"), "line_type": BORDER_TYPE.get(A(nl, "type", "SOLID"), 1),
                "line_width": self.width(A(nl, "width")), "line_color": color(A(nl, "color"), 0)}

    def cold(self, c):
        cnt = I(c, "colCount", 1)
        same = B(c, "sameSz") if A(c, "sameSz") is not None else 1
        attr = COL_TYPE.get(A(c, "type", "NEWSPAPER"), 0) | (cnt & 0xFF) << 2
        attr |= COL_LAYOUT.get(A(c, "layout", "LEFT"), 0) << 10 | same << 12
        d = {"ctrl_id": "cold", "attr": attr, "gap": I(c, "sameGap")}
        if not same:
            szs = c.findall(HP + "colSz")
            d["widths"] = [I(x, "width") for x in szs][:cnt]
            if szs:
                d["gap"] = I(szs[0], "gap")
        ln = c.find(HP + "colLine")
        d.update(attr2=0, line_type=BORDER_TYPE.get(A(ln, "type", "NONE"), 0), line_width=self.width(A(ln, "width")),
                 line_color=color(A(ln, "color"), 0))
        return d

    def obj_common(self, el, name):
        pos, sz, om = el.find(HP + "pos"), el.find(HP + "sz"), el.find(HP + "outMargin")
        attr = B(pos, "treatAsChar") | B(pos, "affectLSpacing") << 2 | VERT_REL.get(A(pos, "vertRelTo", "PARA"), 2) << 3
        attr |= POS_ALIGN.get(A(pos, "vertAlign", "TOP"), 0) << 5 | HORZ_REL.get(A(pos, "horzRelTo", "PARA"), 3) << 8
        attr |= POS_ALIGN.get(A(pos, "horzAlign", "LEFT"), 0) << 10 | B(pos, "flowWithText") << 13
        attr |= B(pos, "allowOverlap") << 14 | WIDTH_REL.get(A(sz, "widthRelTo", "ABSOLUTE"), 4) << 15
        attr |= HEIGHT_REL.get(A(sz, "heightRelTo", "ABSOLUTE"), 2) << 18 | B(sz, "protect") << 20
        attr |= TEXT_WRAP.get(A(el, "textWrap", "TOP_AND_BOTTOM"), 3) << 21
        attr |= TEXT_FLOW.get(A(el, "textFlow", "BOTH_SIDES"), 0) << 24
        attr |= NUMBERING_TYPE.get(A(el, "numberingType", "NONE"), 0) << 26 | B(el, "lock") << 30
        attr |= (el.find(HP + "caption") is not None) << 29
        sc = el.find(HP + "shapeComment")
        return {"ctrl_id": name, "attr": attr, "v_offset": I(pos, "vertOffset"), "h_offset": I(pos, "horzOffset"),
                "width": I(sz, "width"), "height": I(sz, "height"), "z_order": I(el, "zOrder"),
                "margins": [I(om, k) for k in ("left", "right", "top", "bottom")],
                "instance_id": (I(el, "id") or zlib.crc32(etree.tostring(el)) | 0x40000000) & 0xFFFFFFFF,  # id 없으면 만든다(한컴도 새로 매김)
                "prevent_page_break": 0,
                "description": ((sc.text or "") if sc is not None else "").replace("\n", "\r\n"), "_rest": b"\0\0"}

    def table(self, t, lv):
        recs = [[71, lv, bytearray(R.encode(71, self.obj_common(t, "tbl ")))]]
        recs += self.caption(t, lv)
        rows = t.findall(HP + "tr")
        im = t.find(HP + "inMargin")
        attr = TBL_BREAK.get(A(t, "pageBreak", "CELL"), 2) | B(t, "repeatHeader") << 2 | B(t, "noAdjust") << 3
        zones = [[I(z, "startRowAddr"), I(z, "startColAddr"), I(z, "endRowAddr"), I(z, "endColAddr"),
                  I(z, "borderFillIDRef")] for z in t.iter(HP + "cellzone") if z.getparent().getparent() is t]
        recs.append([77, lv + 1, bytearray(R.encode(77, {
            "attr": attr, "cols": I(t, "colCnt"), "spacing": I(t, "cellSpacing"),
            "margins": [I(im, k) for k in ("left", "right", "top", "bottom")],
            "row_sizes": [len(r.findall(HP + "tc")) for r in rows], "border_fill_id": I(t, "borderFillIDRef"),
            "zones": zones}))])
        for r in rows:
            for tc in r.findall(HP + "tc"):
                sub = tc.find(HP + "subList")
                body, n = self.para_list(sub, lv + 1)
                ad, sp, sz, mg = (tc.find(HP + k) for k in ("cellAddr", "cellSpan", "cellSz", "cellMargin"))
                lh = self.list_header(sub, n, "cell")
                lh["attr"] |= B(tc, "hasMargin") << 16 | B(tc, "protect") << 17 | B(tc, "header") << 18
                lh["attr"] |= B(tc, "editable") << 19 | B(tc, "dirty") << 20
                lh.update(col=I(ad, "colAddr"), row=I(ad, "rowAddr"), col_span=I(sp, "colSpan", 1),
                          row_span=I(sp, "rowSpan", 1), width=I(sz, "width"), height=I(sz, "height"),
                          margins=[I(mg, k) for k in ("left", "right", "top", "bottom")],
                          border_fill_id=I(tc, "borderFillIDRef"), text_width=I(sz, "width"),
                          name_flag=0xFF if A(tc, "name") else 0, _rest=b"\0" * 8)
                if A(tc, "name"):
                    lh["name_set"] = self.name_set(A(tc, "name"))
                recs += [[72, lv + 1, bytearray(R.encode(72, lh))]] + body
        return recs

    # ───────── 그리기 개체·수식 ─────────
    def equation(self, e, lv):
        recs = [[71, lv, bytearray(R.encode(71, self.obj_common(e, "eqed")))]]
        recs += self.caption(e, lv)
        sc = e.find(HP + "script")
        recs.append([88, lv + 1, bytearray(R.encode(88, {
            "attr": A(e, "lineMode", "CHAR") == "LINE",
            "script": ((sc.text or "") if sc is not None else "").replace("\r\n", "\n").replace("\n", "\r\n"),
            "size": I(e, "baseUnit", 1000), "color": color(A(e, "textColor"), 0), "baseline": I(e, "baseLine"),
            "unk_2": 0, "version": A(e, "version", ""), "font": A(e, "font", "")}))])
        return recs

    def form(self, e, lv):
        """양식 개체(입력 상자·체크 상자 등): 개체 공통 'form' + FORM_OBJECT(종류 ID 두 번, 속성 문자열)."""
        tag = etree.QName(e).localname

        def items(table):
            out = []
            for n, t, a in table:
                if a is None:
                    if n == "Text":
                        tx = e.find(HP + "text")
                        out.append((n, t, (tx.text or "") if tx is not None else ""))
                    continue
                el = e.find(HP + "formCharPr") if table is FORM_CHAR else e
                v = A(el, a)
                if v is None:
                    continue
                if a in ("foreColor", "backColor"):
                    v = str(color(v, 0))
                elif a in FORM_ENUM:
                    v = str(FORM_ENUM[a].index(v)) if v in FORM_ENUM[a] else "0"
                out.append((n, t, v))
            return out
        sets = [("CommonSet", items(FORM_COMMON)), ("CharShapeSet", items(FORM_CHAR))]
        if tag in ("checkBtn", "radioBtn", "btn"):
            sets.append(("ButtonSet", [it for it in items(FORM_BUTTON) if not (it[0] == "RadioGroupName" and tag == "checkBtn")]))
        elif tag == "edit":
            sets.append(("EditSet", items(FORM_EDIT)))
        else:
            self.warn(f"양식 개체 {tag}의 고유 속성은 아직 옮기지 않음")
        props = R.form_props_build(sets)
        tid = R.ctrl_value(FORM_ELEMENT[tag])
        return [[71, lv, bytearray(R.encode(71, self.obj_common(e, "form")))],
                [91, lv + 1, bytearray(R.encode(91, {"type_id": tid, "type_id2": tid, "text_len": len(props), "props": props}))]]

    def caption(self, e, lv):
        cap = e.find(HP + "caption")
        if cap is None:
            return []
        sub = cap.find(HP + "subList")
        body, n = self.para_list(sub, lv + 1)
        lh = self.list_header(sub, n, "caption")
        lh.update(caption_attr=CAPTION_SIDE.get(A(cap, "side", "BOTTOM"), 3) | B(cap, "fullSz") << 2,
                  caption_width=I(cap, "width"), caption_gap=I(cap, "gap"), caption_max_width=I(cap, "lastWidth"),
                  _rest=b"\0" * 8)
        return [[72, lv + 1, bytearray(R.encode(72, lh))]] + body

    def gso(self, e, lv):
        oc = self.obj_common(e, "gso ")
        sh = e.find(HP + "shadow")
        kind = SHAPE_SHADOW.get(A(sh, "type", "NONE"), 0) if sh is not None else 0
        if kind in SHADOW_SIDES:
            oc["margins"] = shadow_margin(oc["margins"], kind, I(sh, "offsetX"), I(sh, "offsetY"), +1)
        elif kind:
            self.warn("기울어진 그림자 개체의 바깥 여백 규칙은 아직 모름 — 값 그대로")
        recs = [[71, lv, bytearray(R.encode(71, oc))]]
        recs += self.caption(e, lv)
        return recs + self.shape_component(e, lv + 1, top=True)

    def line_attr(self, ls):
        a = BORDER_TYPE.get(A(ls, "style", "SOLID"), 1) & 0x3F | (A(ls, "endCap", "FLAT") == "FLAT") << 6
        a |= ARROW.get(A(ls, "headStyle", "NORMAL"), 0) << 10 | ARROW.get(A(ls, "tailStyle", "NORMAL"), 0) << 16
        a |= ARROW_SZ.get(A(ls, "headSz", "MEDIUM_MEDIUM"), 4) << 22 | ARROW_SZ.get(A(ls, "tailSz", "MEDIUM_MEDIUM"), 4) << 26
        a |= B(ls, "headfill") << 30 | B(ls, "tailfill") << 31
        return a

    def shape_component(self, e, lv, top):
        tag = etree.QName(e).localname
        name = SC_ID[tag]
        off, org, cur, flip, rot = (e.find(HP + k) for k in ("offset", "orgSz", "curSz", "flip", "rotationInfo"))
        ri = e.find(HP + "renderingInfo")
        mats = [[num(m.get(f"e{i}")) for i in range(1, 7)] for m in (ri if ri is not None else [])]
        trans = mats[0] if mats else [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        pairs = [[mats[i], mats[i + 1]] for i in range(1, len(mats) - 1, 2)] or [[[1.0, 0.0, 0.0, 0.0, 1.0, 0.0]] * 2]
        d = {"top": top, "ctrl_id": name, "ctrl_id2": name, "x": I(off, "x"), "y": I(off, "y"),
             "group_level": I(e, "groupLevel"), "local_version": 1, "init_width": I(org, "width"),
             "init_height": I(org, "height"), "width": I(cur, "width") or I(org, "width"),
             "height": I(cur, "height") or I(org, "height"), "attr": self.sc_attr(e, tag, flip, rot),
             "rotation": I(rot, "angle"), "rot_cx": I(rot, "centerX"), "rot_cy": I(rot, "centerY"),
             "translation": trans, "matrices": pairs}
        kids = []
        if tag in ("rect", "line", "ellipse", "arc", "polygon", "curve"):
            ls, sh = e.find(HP + "lineShape"), e.find(HP + "shadow")
            d.update(line_color=color(A(ls, "color"), 0), line_width=I(ls, "width"), line_attr=self.line_attr(ls),
                     outline_style=OUTLINE_STYLE.get(A(ls, "outlineStyle", "NORMAL"), 0),
                     fill=self.fill(e.find(HC + "fillBrush")), shadow_type=SHAPE_SHADOW.get(A(sh, "type", "NONE"), 0),
                     shadow_color=color(A(sh, "color", "#B2B2B2"), 0), shadow_x=I(sh, "offsetX"),
                     shadow_y=I(sh, "offsetY"), instance_id=I(e, "instid") & 0xFFFFFFFF, _rest=b"\0\0")
            dt = e.find(HP + "drawText")
            if dt is not None:
                sub = dt.find(HP + "subList")
                body, n = self.para_list(sub, lv + 1)
                lh = self.list_header(sub, n, "textbox")
                tm = dt.find(HP + "textMargin")
                lh.update(margins=[I(tm, k) for k in ("left", "right", "top", "bottom")], max_width=I(dt, "lastWidth"),
                          unk_12=[0, 0, 0], name_flag=0, _rest=b"")
                kids += [[72, lv + 1, bytearray(R.encode(72, lh))]] + body
            kids.append(self.shape_body(e, tag, lv + 1))
        elif tag == "container":
            members = [c for c in e if etree.QName(c).localname in DRAW_TAGS]
            d.update(children=[SC_ID[etree.QName(c).localname] for c in members],
                     instance_id=I(e, "instid") & 0xFFFFFFFF)
            for c in members:
                kids += self.shape_component(c, lv + 1, top=False)
        elif tag == "pic":
            kids.append(self.picture(e, lv + 1))
        return [[76, lv, bytearray(R.encode(76, d))]] + kids

    def sc_attr(self, e, tag, flip, rot):
        """비트 0·1 뒤집기, 16 묶음(묶음 개체·묶인 개체), 17 묶인 개체, 19 rotateimage, 24 글상자, 26·29 그림(정답지에서)."""
        a = B(flip, "horizontal") | B(flip, "vertical") << 1 | B(rot, "rotateimage") << 19
        if tag == "container" or I(e, "groupLevel"):
            a |= 1 << 16
        if I(e, "groupLevel") and tag != "container":
            a |= 1 << 17
        if e.find(HP + "drawText") is not None:
            a |= 1 << 24
        if tag == "pic":
            a |= 1 << 26 | 1 << 29
        return a

    def shape_body(self, e, tag, lv):
        def pt(name, el=e):
            p = el.find(HC + name)
            return [I(p, "x"), I(p, "y")]
        if tag == "rect":
            return [79, lv, bytearray(R.encode(79, {"round": I(e, "ratio"),
                                                    "points": sum((pt(f"pt{i}") for i in range(4)), [])}))]
        if tag == "line":
            (x1, y1), (x2, y2) = pt("startPt"), pt("endPt")
            return [78, lv, bytearray(R.encode(78, {"x1": x1, "y1": y1, "x2": x2, "y2": y2,
                                                    "attr": B(e, "isReverseHV")}))]
        if tag == "ellipse":
            d = {"attr": B(e, "intervalDirty") | B(e, "hasArcPr") << 1 | ARC_TYPE.get(A(e, "arcType", "NORMAL"), 0) << 2}
            for key, nm in (("c", "center"), ("a1", "ax1"), ("a2", "ax2"), ("s1", "start1"), ("e1", "end1"),
                            ("s2", "start2"), ("e2", "end2")):
                d[key] = pt(nm)
            return [80, lv, bytearray(R.encode(80, {
                "attr": d["attr"], "cx": d["c"][0], "cy": d["c"][1], "ax1": d["a1"][0], "ay1": d["a1"][1],
                "ax2": d["a2"][0], "ay2": d["a2"][1], "sx": d["s1"][0], "sy": d["s1"][1], "ex": d["e1"][0],
                "ey": d["e1"][1], "sx2": d["s2"][0], "sy2": d["s2"][1], "ex2": d["e2"][0], "ey2": d["e2"][1]}))]
        if tag == "arc":
            c, a1, a2 = pt("center"), pt("ax1"), pt("ax2")
            return [81, lv, bytearray(R.encode(81, {"arc_type": ARC_TYPE.get(A(e, "type", "NORMAL"), 0), "cx": c[0],
                                                    "cy": c[1], "ax1": a1[0], "ay1": a1[1], "ax2": a2[0],
                                                    "ay2": a2[1]}))]
        if tag == "polygon":
            pts = [[I(p, "x"), I(p, "y")] for p in e.findall(HC + "pt")]
            return [82, lv, bytearray(R.encode(82, {"points": pts, "_rest": b"\0" * 4}))]
        if tag == "curve":
            segs = e.findall(HP + "seg")
            pts = [[I(s_, "x1"), I(s_, "y1")] for s_ in segs] + ([[I(segs[-1], "x2"), I(segs[-1], "y2")]] if segs else [])
            return [83, lv, bytearray(R.encode(83, {"points": pts,
                                                    "segments": [1 if A(s_, "type") == "CURVE" else 0 for s_ in segs],
                                                    "_rest": b"\0" * 4}))]
        raise ValueError(tag)

    def picture(self, e, lv):
        ls, img, rect, clip, im, dim = (e.find(n) for n in (HP + "lineShape", HC + "img", HP + "imgRect", HP + "imgClip",
                                                           HP + "inMargin", HP + "imgDim"))
        pts = []
        for i in range(4):
            p = child(rect, HC + f"pt{i}")
            pts += [I(p, "x"), I(p, "y")]
        if e.find(HP + "effects") is not None and len(e.find(HP + "effects")):
            self.warn("그림 효과(그림자·네온 등)는 아직 옮기지 않음")
        return [85, lv, bytearray(R.encode(85, {
            "border_color": color(A(ls, "color"), 0), "border_width": I(ls, "width"),
            "border_attr": self.line_attr(ls) if ls is not None else 0, "points": pts,
            "crop": [I(clip, k) for k in ("left", "top", "right", "bottom")],
            "margins": [I(im, k) for k in ("left", "right", "top", "bottom")], "contrast": I(img, "contrast"),
            "brightness": I(img, "bright"), "effect": IMG_EFFECT.get(A(img, "effect"), 0),
            "bin_id": self.bin_id.get(A(img, "binaryItemIDRef"), 0), "border_alpha": int(num(A(ls, "alpha", "0"))),
            "instance_id": I(e, "instid") & 0xFFFFFFFF, "effects": 0, "orig_width": I(dim, "dimwidth"),
            "orig_height": I(dim, "dimheight"), "alpha": int(num(A(img, "alpha", "0")))}))]

    # ───────── 저장 ─────────
    def build(self):
        streams = {"FileHeader": FILE_HEADER}
        docinfo = write_records(self.docinfo())
        sections, views = [], []
        tracked = self.header.find(".//" + HH + "trackChange") is not None
        for n in self.section_files:
            root = etree.fromstring(self.z.read(n if n.startswith("Contents/") else "Contents/" + n.split("/")[-1]))
            if tracked:                           # 변경 추적: BodyText = 지운 내용 뺀 판, ViewText = 표시가 든 판(한컴과 같게)
                views.append(write_records(self.section(root)))
                self.final = True
                sections.append(write_records(self.section(root)))
                self.final = False
            else:
                sections.append(write_records(self.section(root)))

        def deflate(b):
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            return c.compress(b) + c.flush()
        streams["DocInfo"] = deflate(docinfo)
        for i, s in enumerate(sections):
            streams[f"BodyText/Section{i}"] = deflate(s)
        for i, s in enumerate(views):
            streams[f"ViewText/Section{i}"] = deflate(s)
        for iid, href in self.bins:
            data = self.z.read(href)
            ext = os.path.splitext(href)[1].lstrip(".").upper()   # 한컴 HWPX→HWP는 대문자(BIN0001.PNG)
            streams[f"BinData/BIN{self.bin_id[iid]:04X}.{ext}"] = deflate(data)
        names = self.z.namelist()
        if "Preview/PrvText.txt" in names:
            streams["PrvText"] = self.z.read("Preview/PrvText.txt").decode("utf-8", "replace").encode("utf-16-le")
        if "Preview/PrvImage.png" in names:
            streams["PrvImage"] = self.z.read("Preview/PrvImage.png")
        streams["DocOptions/_LinkDoc"] = b"\0" * 524
        streams["Scripts/DefaultJScript"] = JS_DEFAULT
        streams["Scripts/JScriptVersion"] = JS_VERSION
        streams["\x05HwpSummaryInformation"] = summary_info()
        return streams

    def save(self, out):
        write_cfb(out, self.build())


def summary_info():
    """최소 OLE 속성 집합(코드 페이지 UTF-16만). FMTID = HwpSummaryInformation."""
    fmtid = bytes.fromhex("60B6A29F6110D411B4C6006097C09D8C")
    sec = struct.pack("<II", 24, 1) + struct.pack("<II", 1, 16) + struct.pack("<HHhH", 2, 0, 1200, 0)
    head = struct.pack("<HHI", 0xFFFE, 0, 0x00020A00) + b"\0" * 16 + struct.pack("<I", 1) + fmtid + struct.pack("<I", 48)
    return head + sec


def write_cfb(out, streams, clsid=None):
    try:
        import pythoncom
    except ImportError as e:
        raise RuntimeError("HWP 파일 쓰기는 지금 윈도(pywin32의 구조화 저장소)에서만 됩니다. 다른 OS에서는 HWPX로 저장하세요") from e
    if os.path.exists(out):
        os.remove(out)
    mode = 0x1000 | 0x2 | 0x10
    root = pythoncom.StgCreateDocfile(os.path.abspath(out), mode, 0)
    cache = {"": root}

    def storage(path):
        if path in cache:
            return cache[path]
        parent, _, name = path.rpartition("/")
        cache[path] = storage(parent).CreateStorage(name, mode, 0, 0)
        return cache[path]
    for path, data in streams.items():
        parent, _, name = path.rpartition("/")
        stm = storage(parent).CreateStream(name, mode, 0, 0)
        stm.Write(data)
        stm = None
    for k in sorted(cache, key=len, reverse=True):
        cache[k].Commit(0)
    cache.clear()
    root = None


def convert(src, out):
    c = Conv(src)
    c.save(out)
    return c.warnings


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for w in convert(sys.argv[1], sys.argv[2]):
        print("경고:", w)
    print("저장:", sys.argv[2])
