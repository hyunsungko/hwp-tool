"""HWP 5 레코드 코덱 — 레코드 데이터를 규격(한컴 공개 'HWP 5.0 문서 파일 구조' rev1.3) 필드로 풀고(decode) 다시 묶는다(encode).
규칙: 규격 필드를 다 읽고도 남은 바이트는 '_rest'에 그대로 담아, 풀고 묶으면 원래 바이트와 똑같이 나와야 한다.
'_rest'가 비지 않는 레코드는 규격에 없는 필드가 있다는 증거다(규격 외 찾기 4-2).
판(version)에 따라 붙는 필드는 남은 길이로 정한다(판보다 실제 바이트가 정확하다).
사용: decode(tag, data) → dict, encode(tag, dict) → bytes, roundtrip_corpus(파일들) → 태그별 일치·_rest 통계."""
import re
import struct


class Rd:
    def __init__(self, b):
        self.b, self.i = bytes(b), 0

    def left(self):
        return len(self.b) - self.i

    def take(self, fmt):
        v = struct.unpack_from("<" + fmt, self.b, self.i)
        self.i += struct.calcsize("<" + fmt)
        return v if len(v) > 1 else v[0]

    def raw(self, n):
        v = self.b[self.i:self.i + n]
        self.i += n
        return v

    def wstr(self):
        n = self.take("H")
        return self.raw(2 * n).decode("utf-16-le", errors="surrogatepass")

    def rest(self):
        return self.raw(self.left())


def _fit(fmt, vals):
    codes = []
    for n, c in re.findall(r"(\d*)([a-zA-Z?])", fmt):
        codes += [c] * int(n or 1)
    out = []
    for c, v in zip(codes, vals):
        if c in "bBhHiIlLqQ" and isinstance(v, int):
            bits = struct.calcsize(c) * 8
            v &= (1 << bits) - 1
            if c.islower() and v >= 1 << (bits - 1):
                v -= 1 << bits
        out.append(v)
    return out


class Wr:
    def __init__(self):
        self.b = bytearray()

    def put(self, fmt, *v):
        try:
            self.b += struct.pack("<" + fmt, *v)
        except struct.error:                      # 범위 밖 정수(HWPX의 4294966617 = -679 등)는 그 폭으로 감아 넣는다
            self.b += struct.pack("<" + fmt, *_fit(fmt, v))

    def raw(self, v):
        self.b += v

    def wstr(self, s):
        e = s.encode("utf-16-le", errors="surrogatepass")
        self.put("H", len(e) // 2)
        self.b += e


# ───────── 선언형 필드: (이름, 형식) 형식 = struct 글자(숫자 배열 가능) 또는 'wstr' ─────────
def _dec_fields(r, fields, out, optional=False):
    for name, fmt in fields:
        if fmt == "wstr":
            if optional and r.left() < 2:
                return False
            out[name] = r.wstr()
        else:
            size = struct.calcsize("<" + fmt)
            if optional and r.left() < size:
                return False
            v = r.take(fmt)
            out[name] = list(v) if isinstance(v, tuple) else v
    return True


def _enc_fields(w, fields, d):
    for name, fmt in fields:
        if name not in d:
            return
        if fmt == "wstr":
            w.wstr(d[name])
        else:
            v = d[name]
            w.put(fmt, *(v if isinstance(v, list) else [v]))


def simple(fields, *optional_groups):
    """기본 필드 + 남은 길이가 되면 붙는 선택 필드 묶음들(판에 따라 생긴 필드)."""
    def dec(data):
        r, out = Rd(data), {}
        _dec_fields(r, fields, out)
        for g in optional_groups:
            if not _dec_fields(r, g, out, optional=True):
                break
        out["_rest"] = r.rest()
        return out

    def enc(d):
        w = Wr()
        _enc_fields(w, fields, d)
        for g in optional_groups:
            _enc_fields(w, g, d)
        w.raw(d.get("_rest", b""))
        return bytes(w.b)
    return dec, enc


CODECS = {}

# 16 문서 속성 (표 14)
CODECS[16] = simple([("sections", "H"), ("start_page", "H"), ("start_footnote", "H"), ("start_endnote", "H"),
                     ("start_picture", "H"), ("start_table", "H"), ("start_equation", "H"),
                     ("caret_list", "I"), ("caret_para", "I"), ("caret_pos", "I")])
# 17 아이디 매핑 (표 15·16): INT32 배열, 판에 따라 개수가 다름
def _idmap_dec(data):
    n = len(data) // 4
    r = Rd(data)
    return {"counts": list(r.take(f"{n}i")) if n > 1 else [r.take("i")] if n else [], "_rest": r.rest()}


def _idmap_enc(d):
    w = Wr()
    w.put(f"{len(d['counts'])}i", *d["counts"])
    w.raw(d.get("_rest", b""))
    return bytes(w.b)


CODECS[17] = (_idmap_dec, _idmap_enc)


# 18 바이너리 데이터 (표 17·18)
def _bin_dec(data):
    r, d = Rd(data), {}
    d["attr"] = r.take("H")
    typ = d["attr"] & 0xF
    if typ == 0:
        d["abs_path"], d["rel_path"] = r.wstr(), r.wstr()
    else:
        d["bin_id"] = r.take("H")
        if typ == 1 or r.left() >= 2:            # 규격은 EMBEDDING(1)에만 적었지만 STORAGE(2)에도 'OLE'가 붙는다
            d["ext"] = r.wstr()
    d["_rest"] = r.rest()
    return d


def _bin_enc(d):
    w = Wr()
    w.put("H", d["attr"])
    typ = d["attr"] & 0xF
    if typ == 0:
        w.wstr(d["abs_path"])
        w.wstr(d["rel_path"])
    else:
        w.put("H", d["bin_id"])
        if "ext" in d:
            w.wstr(d["ext"])
    w.raw(d.get("_rest", b""))
    return bytes(w.b)


CODECS[18] = (_bin_dec, _bin_enc)


# 19 글꼴 (표 19~22)
def _face_dec(data):
    r, d = Rd(data), {}
    d["attr"] = r.take("B")
    d["name"] = r.wstr()
    if d["attr"] & 0x80:
        d["alt_type"] = r.take("B")
        d["alt_name"] = r.wstr()
    if d["attr"] & 0x40:
        d["type_info"] = list(r.take("10B"))
    if d["attr"] & 0x20:
        d["base_name"] = r.wstr()
    d["_rest"] = r.rest()
    return d


def _face_enc(d):
    w = Wr()
    w.put("B", d["attr"])
    w.wstr(d["name"])
    if d["attr"] & 0x80:
        w.put("B", d["alt_type"])
        w.wstr(d["alt_name"])
    if d["attr"] & 0x40:
        w.put("10B", *d["type_info"])
    if d["attr"] & 0x20:
        w.wstr(d["base_name"])
    w.raw(d.get("_rest", b""))
    return bytes(w.b)


CODECS[19] = (_face_dec, _face_enc)


# 20 테두리/배경 (표 23~32)
def _fill_dec(r):
    """채우기 정보(표 28). 실제 바이트(공개 문서 343개)로 맞춘 것: 그러데이션 유형은 1바이트, 각도·중심·번짐·색 수는 INT32
    (규격 표는 INT16). 끝에 채우기 종류마다 1바이트(단색·그러데이션·그림 순)가 더 있다 — 규격에 없음(투명도로 보임)."""
    f = {"type": r.take("I")}
    t = f["type"]
    if t & 1:
        f["bg_color"], f["pat_color"], f["pat_type"] = r.take("I"), r.take("I"), r.take("i")
    if t & 4:
        f["grad_type"] = r.take("B")
        f["grad_angle"], f["grad_cx"], f["grad_cy"], f["grad_step"], n = r.take("5i")
        f["grad_n"] = n
        f["grad_pos"] = list(r.take(f"{n}i")) if n > 2 else []
        f["grad_colors"] = [r.take("I") for _ in range(n)]
    if t & 2:
        f["img_type"] = r.take("B")
        f["img_info"] = r.raw(5)
    f["extra_size"] = r.take("I")
    f["extra"] = r.raw(f["extra_size"])
    f["alphas"] = []
    for bit in (1, 4, 2):                     # 규격 외: 종류마다 1바이트
        if t & bit and r.left() >= 1:
            f["alphas"].append(r.take("B"))
    return f


def _fill_enc(w, f):
    t = f["type"]
    w.put("I", t)
    if t & 1:
        w.put("IIi", f["bg_color"], f["pat_color"], f["pat_type"])
    if t & 4:
        w.put("B", f["grad_type"])
        w.put("5i", f["grad_angle"], f["grad_cx"], f["grad_cy"], f["grad_step"], f["grad_n"])
        if f["grad_n"] > 2:
            w.put(f"{f['grad_n']}i", *f["grad_pos"])
        for c in f["grad_colors"]:
            w.put("I", c)
    if t & 2:
        w.put("B", f["img_type"])
        w.raw(f["img_info"])
    w.put("I", f["extra_size"])
    w.raw(f["extra"])
    for a in f.get("alphas", []):
        w.put("B", a)


def _bf_dec(data):
    """테두리선은 방향(왼·오른·위·아래)마다 종류·굵기·색(6바이트)이 번갈아 온다(규격 표 23은 종류 4개·굵기 4개·색 4개로
    묶어 적었지만 실제는 아님 — 한컴이 HWPX에서 저장한 HWP로 확인 2026-10-16)."""
    r, d = Rd(data), {}
    d["attr"] = r.take("H")
    sides = [r.take("BBI") for _ in range(4)]
    d["line_types"], d["line_widths"], d["line_colors"] = [list(x) for x in zip(*sides)]
    d["diag_type"], d["diag_width"], d["diag_color"] = r.take("B"), r.take("B"), r.take("I")
    d["fill"] = _fill_dec(r)
    d["_rest"] = r.rest()
    return d


def _bf_enc(d):
    w = Wr()
    w.put("H", d["attr"])
    for t, wd, c in zip(d["line_types"], d["line_widths"], d["line_colors"]):
        w.put("BBI", t, wd, c)
    w.put("BBI", d["diag_type"], d["diag_width"], d["diag_color"])
    _fill_enc(w, d["fill"])
    w.raw(d.get("_rest", b""))
    return bytes(w.b)


CODECS[20] = (_bf_dec, _bf_enc)

# 21 글자 모양 (표 33·35) — 글자 테두리/배경 ID(5.0.2.1+)·취소선 색(5.0.3.0+)은 남은 길이로
CODECS[21] = simple([("face_ids", "7H"), ("ratios", "7B"), ("spacings", "7b"), ("rel_sizes", "7B"), ("offsets", "7b"),
                     ("base_size", "i"), ("attr", "I"), ("shadow_x", "b"), ("shadow_y", "b"), ("text_color", "I"),
                     ("underline_color", "I"), ("shade_color", "I"), ("shadow_color", "I")],
                    [("border_fill_id", "H")], [("strike_color", "I")])


# 22 탭 정의 (표 36·37) — 규격 표에는 count가 INT16(4바이트)로 적혀 있다
def _tab_dec(data):
    r, d = Rd(data), {}
    d["attr"], n = r.take("I"), r.take("i")
    d["tabs"] = [list(r.take("iBBH")) for _ in range(n)]
    d["_rest"] = r.rest()
    return d


def _tab_enc(d):
    w = Wr()
    w.put("Ii", d["attr"], len(d["tabs"]))
    for t in d["tabs"]:
        w.put("iBBH", *t)
    w.raw(d.get("_rest", b""))
    return bytes(w.b)


CODECS[22] = (_tab_dec, _tab_enc)


# 23 문단 번호 (표 38~41): 수준 1~7(머리 정보 8 + 형식), 시작 번호, 수준별 시작 번호(5.0.2.5+), 수준 8~10(5.1.0.0+)
def _head(r):
    return list(r.take("IhhI"))


def _num_dec(data):
    r, d = Rd(data), {"levels": []}
    for _ in range(7):
        d["levels"].append({"head": _head(r), "format": r.wstr()})
    d["start"] = r.take("H")
    if r.left() >= 28:
        d["level_starts"] = list(r.take("7I"))
        ext = []
        for _ in range(3):
            if r.left() < 14:
                break
            ext.append({"head": _head(r), "format": r.wstr()})
        if ext:
            d["ext_levels"] = ext
        if r.left() >= 12 and len(ext) == 3:
            d["ext_starts"] = list(r.take("3I"))
    d["_rest"] = r.rest()
    return d


def _num_enc(d):
    w = Wr()
    for lv in d["levels"]:
        w.put("IhhI", *lv["head"])
        w.wstr(lv["format"])
    w.put("H", d["start"])
    if "level_starts" in d:
        w.put("7I", *d["level_starts"])
    for lv in d.get("ext_levels", []):
        w.put("IhhI", *lv["head"])
        w.wstr(lv["format"])
    if "ext_starts" in d:
        w.put("3I", *d["ext_starts"])
    w.raw(d.get("_rest", b""))
    return bytes(w.b)


CODECS[23] = (_num_dec, _num_enc)

# 24 글머리표 (표 42)
CODECS[24] = simple([("head", "IhhI"), ("char", "H"), ("image_id", "i"), ("image_info", "4B")], [("check_char", "H")])

# 25 문단 모양 (표 43~46) — 속성2(5.0.1.7+)·속성3·줄 간격(5.0.2.5+)은 남은 길이로.
# 끝 4바이트(_rest, 규격 외)는 문단 수준(UINT32): 속성1의 수준 3비트는 6까지만 쓰고 7~10 수준은 여기에 둔다
CODECS[25] = simple([("attr1", "I"), ("left", "i"), ("right", "i"), ("indent", "i"), ("prev", "i"), ("next", "i"),
                     ("line_spacing_old", "i"), ("tab_def_id", "H"), ("numbering_id", "H"), ("border_fill_id", "H"),
                     ("border_left", "h"), ("border_right", "h"), ("border_top", "h"), ("border_bottom", "h")],
                    [("attr2", "I")], [("attr3", "I"), ("line_spacing", "I")])

# 26 스타일 (표 47·48)
CODECS[26] = simple([("name", "wstr"), ("eng_name", "wstr"), ("attr", "B"), ("next_id", "B"), ("lang", "h"),
                     ("para_shape_id", "H"), ("char_shape_id", "H")])
# 30 호환 문서 (표 54), 31 레이아웃 호환성 (표 56)
CODECS[30] = simple([("target", "I")])
CODECS[31] = simple([("char", "I"), ("para", "I"), ("section", "I"), ("object", "I"), ("field", "I")])


# ───────── 본문: 문단 ─────────
# 66 문단 헤더 (표 58·59) — 변경추적 병합 여부(5.0.3.2+)는 남은 길이로
CODECS[66] = simple([("nchars", "I"), ("ctrl_mask", "I"), ("para_shape_id", "H"), ("style_id", "B"), ("break_type", "B"),
                     ("char_shape_count", "H"), ("range_tag_count", "H"), ("line_seg_count", "H"), ("instance_id", "I")],
                    [("track_merge", "H")])


# 67 문단 텍스트 (표 60): WCHAR 그대로(확장 제어 문자 8 WCHAR 포함) — 풀이는 hwp5_direct.units
def _text_dec(data):
    return {"wchars": bytes(data), "_rest": b""}


CODECS[67] = (_text_dec, lambda d: d["wchars"] + d.get("_rest", b""))


def _array(fmt, key):
    size = struct.calcsize("<" + fmt)

    def dec(data):
        n = len(data) // size
        r = Rd(data)
        return {key: [list(r.take(fmt)) if len(fmt) > 1 else [r.take(fmt)] for _ in range(n)], "_rest": r.rest()}

    def enc(d):
        w = Wr()
        for x in d[key]:
            w.put(fmt, *x)
        w.raw(d.get("_rest", b""))
        return bytes(w.b)
    return dec, enc


CODECS[68] = _array("II", "runs")                 # 표 61: (시작 위치, 글자 모양 ID)
CODECS[69] = _array("Iiiiiiiii", "segs")          # 표 62: 줄 하나 36바이트
CODECS[70] = _array("III", "tags")                # 표 63: (시작, 끝, 태그)


# ───────── 본문: 컨트롤 ─────────
# 아래 코덱은 부모 맥락(ctx)이 필요하다: walk()가 붙여 준다. 맥락으로 정한 종류는 풀린 dict의 'kind'에 남겨, 묶을 때는 맥락이 없어도 된다.
def ctrl_name(v):
    """u32 컨트롤 ID → 4글자(MAKE_4CHID 순서)."""
    return struct.pack("<I", v)[::-1].decode("latin-1")


def ctrl_value(name):
    return int.from_bytes(name.encode("latin-1"), "big")


def walk(recs):
    """레코드마다 부모 맥락을 붙인다 → (tag, level, data, ctx).
    ctx: parent = 부모 레코드 태그, ctrl = 가장 가까운 CTRL_HEADER의 컨트롤 ID, after_table = 그 컨트롤 아래에 TABLE이 이미 나왔는지."""
    stack = []                                   # [level, tag, ctrl, state]
    for tag, lv, data in recs:
        while stack and stack[-1][0] >= lv:
            stack.pop()
        parent = stack[-1] if stack else None
        owner = next((s for s in reversed(stack) if s[1] == 71), None)
        yield tag, lv, data, {"parent": parent[1] if parent else None, "ctrl": owner[2] if owner else None,
                              "after_table": bool(owner and owner[3].get("table"))}
        if parent is not None and parent[1] == 71 and tag == 77:
            parent[3]["table"] = True
        name = ctrl_name(struct.unpack_from("<I", data, 0)[0]) if tag == 71 and len(data) >= 4 else None
        stack.append([lv, tag, name, {}])


def _fields(*groups):
    """선언형 묶음들을 Rd/Wr에 바로 쓰는 짝(필수 첫 묶음 + 선택 묶음)."""
    def dec(r, out):
        _dec_fields(r, groups[0], out)
        for g in groups[1:]:
            if not _dec_fields(r, g, out, optional=True):
                break

    def enc(w, d):
        for g in groups:
            _enc_fields(w, g, d)
    return dec, enc


# 개체 공통 속성 (표 69) — 컨트롤 ID 뒤. 설명문 뒤에 2바이트가 더 붙는 판이 있고, 설명문이 아예 없는 판도 있다
# (정부24 별지 제27호서식: 표 컨트롤 44바이트 — 2026-10-21)
OBJ_COMMON = _fields([("attr", "I"), ("v_offset", "i"), ("h_offset", "i"), ("width", "i"), ("height", "i"),
                      ("z_order", "i"), ("margins", "4h"), ("instance_id", "I"), ("prevent_page_break", "i")],
                     [("description", "wstr")])
OBJ_CTRLS = {"tbl ", "gso ", "eqed", "form", "$lin", "$rec", "$ell", "$arc", "$pol", "$cur", "$pic", "$ole", "$con"}
# 필드 (표 152) — 끝 4바이트는 규격에 없다: 메모(%unk)면 메모 번호(MEMO_LIST 값·HWPX zorder와 같음), 다른 필드는 0
FIELD = _fields([("attr", "I"), ("extra_attr", "B"), ("command", "wstr"), ("field_id", "I")], [("memo_index", "I")])
CTRL_BODY = {
    # 구역 정의 (표 129) — 규격 26바이트 뒤에 8·17바이트가 더 있다(_rest)
    "secd": _fields([("attr", "I"), ("column_gap", "h"), ("vert_grid", "h"), ("horz_grid", "h"), ("default_tab", "i"),
                     ("numbering_para_shape_id", "H"), ("start_page", "H"), ("start_picture", "H"), ("start_table", "H"),
                     ("start_equation", "H")], [("lang", "H")]),
    # 머리말/꼬리말 (표 140) — CTRL_HEADER에는 속성 4바이트 + 4바이트. 폭·높이·참조는 LIST_HEADER에 있다(규격 표는 여기 적음)
    "head": _fields([("attr", "I")]),
    "foot": _fields([("attr", "I")]),
    # 각주/미주 (4.3.10.4 '8바이트') — 실제: 번호·장식 문자 8바이트 + 4바이트 + 고유 아이디(HWPX instId)
    "fn  ": _fields([("number", "H"), ("user_char", "H"), ("prefix_char", "H"), ("suffix_char", "H")],
                    [("unk_8", "I"), ("instance_id", "I")]),
    "en  ": _fields([("number", "H"), ("user_char", "H"), ("prefix_char", "H"), ("suffix_char", "H")],
                    [("unk_8", "I"), ("instance_id", "I")]),
    "atno": _fields([("attr", "I"), ("number", "H"), ("user_char", "H"), ("prefix_char", "H"), ("suffix_char", "H")]),
    "nwno": _fields([("attr", "I"), ("number", "H")]),
    "pghd": _fields([("attr", "I")]),             # 규격 표는 UINT 2바이트, 실제 4바이트
    "pgct": _fields([("attr", "I")]),
    "pgnp": _fields([("attr", "I"), ("user_char", "H"), ("prefix_char", "H"), ("suffix_char", "H"), ("dash_char", "H")]),
    "idxm": _fields([("keyword1", "wstr"), ("keyword2", "wstr"), ("dummy", "H")]),
    "bokm": _fields([]),
    "tcmt": _fields([]),
    # 덧말 (표 151) — 본말·덧말은 길이 붙은 문자열(규격 표는 WCHAR 하나)
    "tdut": _fields([("main_text", "wstr"), ("sub_text", "wstr"), ("position", "I"), ("size_ratio", "I"),
                     ("option", "I"), ("style_id", "I"), ("align", "I")]),
}


def _ctrl_dec(data, ctx=None):
    r = Rd(data)
    d = {"ctrl_id": ctrl_name(r.take("I"))}
    c = d["ctrl_id"]
    if c in OBJ_CTRLS:
        OBJ_COMMON[0](r, d)
    elif c.startswith("%"):
        FIELD[0](r, d)
    elif c == "cold":                             # 단 정의 (표 138·139)
        d["attr"], d["gap"] = r.take("H"), r.take("h")
        n, same = (d["attr"] >> 2) & 0xFF, d["attr"] >> 12 & 1
        if not same and n:
            d["widths"] = list(r.take(f"{n}H")) if n > 1 else [r.take("H")]
        d["attr2"], d["line_type"], d["line_width"], d["line_color"] = r.take("H"), r.take("B"), r.take("B"), r.take("I")
    elif c == "tcps":                             # 글자 겹침 (표 150)
        d["text"] = r.wstr()
        d["border_type"], d["inner_size"], d["expand"], n = r.take("B"), r.take("b"), r.take("B"), r.take("B")
        d["char_shape_ids"] = [r.take("I") for _ in range(n)]
    elif c in CTRL_BODY:
        CTRL_BODY[c][0](r, d)
    d["_rest"] = r.rest()
    return d


def _ctrl_enc(d):
    w, c = Wr(), d["ctrl_id"]
    w.put("I", ctrl_value(c))
    if c in OBJ_CTRLS:
        OBJ_COMMON[1](w, d)
    elif c.startswith("%"):
        FIELD[1](w, d)
    elif c == "cold":
        w.put("Hh", d["attr"], d["gap"])
        for x in d.get("widths", []):
            w.put("H", x)
        w.put("HBBI", d["attr2"], d["line_type"], d["line_width"], d["line_color"])
    elif c == "tcps":
        w.wstr(d["text"])
        w.put("BbBB", d["border_type"], d["inner_size"], d["expand"], len(d["char_shape_ids"]))
        for x in d["char_shape_ids"]:
            w.put("I", x)
    elif c in CTRL_BODY:
        CTRL_BODY[c][1](w, d)
    w.raw(d.get("_rest", b""))
    return bytes(w.b)


CODECS[71] = (_ctrl_dec, _ctrl_enc)

# 문단 리스트 헤더 (표 65) — 규격은 문단 수 INT16 + 속성 UINT32(6바이트), 실제는 문단 수 뒤 2바이트가 더 있어 8바이트.
# 뒤에 오는 것은 부모에 따라 다르다: 셀(표 80), 캡션(표 72), 글상자(표 90), 머리말/꼬리말·바탕쪽(표 137의 폭·높이·참조).
LIST_BASE = _fields([("para_count", "h"), ("unk_2", "H"), ("attr", "I")])
LIST_KIND = {
    "cell": _fields([("col", "H"), ("row", "H"), ("col_span", "H"), ("row_span", "H"), ("width", "i"), ("height", "i"),
                     ("margins", "4h"), ("border_fill_id", "H")], [("text_width", "i")]),
    "caption": _fields([("caption_attr", "I"), ("caption_width", "i"), ("caption_gap", "h"), ("caption_max_width", "i")]),
    "textbox": _fields([("margins", "4h"), ("max_width", "i")]),
    "header": _fields([("text_width", "i"), ("text_height", "i"), ("text_ref", "B"), ("num_ref", "B")]),
}


def _list_kind(ctx):
    ctx = ctx or {}
    c, p = ctx.get("ctrl"), ctx.get("parent")
    if p == 76:
        return "textbox"
    if p != 71:
        return "plain"
    if c == "tbl ":
        return "cell" if ctx.get("after_table") else "caption"
    if c in OBJ_CTRLS:
        return "caption"
    if c in ("head", "foot", "secd"):            # secd 아래는 바탕쪽(표 137, 같은 모양)
        return "header"
    return "plain"


def _list_dec(data, ctx=None):
    r, d = Rd(data), {"kind": _list_kind(ctx)}
    LIST_BASE[0](r, d)
    k = d["kind"]
    if k in LIST_KIND:
        LIST_KIND[k][0](r, d)
    if k == "textbox" and r.left() >= 13:
        d["unk_12"] = list(r.take("3I"))
    if (k == "textbox" and "unk_12" in d or k == "cell" and "text_width" in d) and r.left() >= 1 and r.b[r.i] in (0, 0xFF):
        d["name_flag"] = r.take("B")                # 규격 외: 0xFF면 뒤에 이름 파라미터 셋(셀 이름 = HWPX hp:tc@name)
        if d["name_flag"] == 0xFF:
            d["name_set"] = _pset_dec(r)
    d["_rest"] = r.rest()
    return d


def _list_enc(d):
    w = Wr()
    LIST_BASE[1](w, d)
    if d["kind"] in LIST_KIND:
        LIST_KIND[d["kind"]][1](w, d)
    if "unk_12" in d:
        w.put("3I", *d["unk_12"])
    if "name_flag" in d:
        w.put("B", d["name_flag"])
        if d["name_flag"] == 0xFF:
            _pset_enc(w, d["name_set"])
    w.raw(d.get("_rest", b""))
    return bytes(w.b)


CODECS[72] = (_list_dec, _list_enc)

# 용지 설정 (표 131)
CODECS[73] = simple([("width", "i"), ("height", "i"), ("left", "i"), ("right", "i"), ("top", "i"), ("bottom", "i"),
                     ("header", "i"), ("footer", "i"), ("gutter", "i"), ("attr", "I")])
# 각주/미주 모양 (표 133) — 구분선 길이는 INT32(규격 표는 HWPUNIT16. HWPX noteLine@length="-1"이 ffffffff)
CODECS[74] = simple([("attr", "I"), ("user_char", "H"), ("prefix_char", "H"), ("suffix_char", "H"), ("start", "H"),
                     ("line_length", "i"), ("above", "h"), ("below", "h"), ("between", "h"), ("line_type", "B"),
                     ("line_width", "B"), ("line_color", "I")])
# 쪽 테두리/배경 (표 135) — 필드 합이 14바이트(규격의 '전체 길이 12'는 오기)
CODECS[75] = simple([("attr", "I"), ("left", "h"), ("right", "h"), ("top", "h"), ("bottom", "h"), ("border_fill_id", "H")])


# 표 (표 75·78)
def _table_dec(data, ctx=None):
    r, d = Rd(data), {}
    d["attr"], rows, d["cols"], d["spacing"] = r.take("I"), r.take("H"), r.take("H"), r.take("h")
    d["margins"] = list(r.take("4h"))
    d["row_sizes"] = [r.take("H") for _ in range(rows)]
    d["border_fill_id"] = r.take("H")
    if r.left() >= 2:
        n = r.take("H")
        d["zones"] = [list(r.take("5H")) for _ in range(n)]
    d["_rest"] = r.rest()
    return d


def _table_enc(d):
    w = Wr()
    w.put("IHHh", d["attr"], len(d["row_sizes"]), d["cols"], d["spacing"])
    w.put("4h", *d["margins"])
    for x in d["row_sizes"]:
        w.put("H", x)
    w.put("H", d["border_fill_id"])
    if "zones" in d:
        w.put("H", len(d["zones"]))
        for z in d["zones"]:
            w.put("5H", *z)
    w.raw(d.get("_rest", b""))
    return bytes(w.b)


CODECS[77] = (_table_dec, _table_enc)

# 개체 요소 (표 83·84·85). 맨 위 개체(부모가 CTRL_HEADER)는 컨트롤 ID가 두 번, 묶음 안의 개체는 한 번.
SC_HEAD = _fields([("x", "i"), ("y", "i"), ("group_level", "H"), ("local_version", "H"), ("init_width", "I"),
                   ("init_height", "I"), ("width", "I"), ("height", "I"), ("attr", "I"), ("rotation", "h"),
                   ("rot_cx", "i"), ("rot_cy", "i")])
DRAW_KINDS = {"$lin", "$rec", "$ell", "$arc", "$pol", "$cur"}
# 그리기 개체 공통(표 81·86): 테두리 선 굵기는 INT32라 선 정보는 13바이트(규격 표는 INT16, 11바이트).
# 채우기 뒤 16바이트는 규격에 없다: 그림자(종류·색·x·y — 기본 색 B2B2B2, HWPX hp:shadow와 같은 값), 그다음 고유 아이디.
DRAW_LINE = _fields([("line_color", "I"), ("line_width", "i"), ("line_attr", "I"), ("outline_style", "B")])
DRAW_AFTER = _fields([], [("shadow_type", "I"), ("shadow_color", "I"), ("shadow_x", "i"), ("shadow_y", "i")],
                     [("instance_id", "I")])


def _sc_dec(data, ctx=None):
    r, d = Rd(data), {}
    d["top"] = (ctx or {}).get("parent") == 71
    d["ctrl_id"] = ctrl_name(r.take("I"))
    if d["top"]:
        d["ctrl_id2"] = ctrl_name(r.take("I"))
    SC_HEAD[0](r, d)
    n = r.take("H")
    d["translation"] = list(r.take("6d"))
    d["matrices"] = [[list(r.take("6d")), list(r.take("6d"))] for _ in range(n)]
    k = d["ctrl_id"]
    if k in DRAW_KINDS:
        DRAW_LINE[0](r, d)
        d["fill"] = _fill_dec(r)
        DRAW_AFTER[0](r, d)
    elif k == "$con":                             # 묶음 개체 속성(표 121)이 여기 들어 있다(CONTAINER 레코드는 따로 없음)
        n = r.take("H")
        d["children"] = [ctrl_name(r.take("I")) for _ in range(n)]
        if r.left() >= 4:
            d["instance_id"] = r.take("I")
    d["_rest"] = r.rest()
    return d


def _sc_enc(d):
    w = Wr()
    w.put("I", ctrl_value(d["ctrl_id"]))
    if d["top"]:
        w.put("I", ctrl_value(d["ctrl_id2"]))
    SC_HEAD[1](w, d)
    w.put("H", len(d["matrices"]))
    w.put("6d", *d["translation"])
    for a, b in d["matrices"]:
        w.put("6d", *a)
        w.put("6d", *b)
    k = d["ctrl_id"]
    if k in DRAW_KINDS:
        DRAW_LINE[1](w, d)
        _fill_enc(w, d["fill"])
        DRAW_AFTER[1](w, d)
    elif k == "$con":
        w.put("H", len(d["children"]))
        for c in d["children"]:
            w.put("I", ctrl_value(c))
        if "instance_id" in d:
            w.put("I", d["instance_id"])
    w.raw(d.get("_rest", b""))
    return bytes(w.b)


CODECS[76] = (_sc_dec, _sc_enc)

# 선 (표 92) — 속성 4바이트(규격 표는 UINT16)
CODECS[78] = simple([("x1", "i"), ("y1", "i"), ("x2", "i"), ("y2", "i")], [("attr", "I")])
# 사각형 (표 94) — 꼭짓점은 (x, y) 쌍 4개(규격 표는 x 4개 뒤 y 4개)
CODECS[79] = simple([("round", "B"), ("points", "8i")])
# 타원 (표 96)
CODECS[80] = simple([("attr", "I"), ("cx", "i"), ("cy", "i"), ("ax1", "i"), ("ay1", "i"), ("ax2", "i"), ("ay2", "i"),
                     ("sx", "i"), ("sy", "i"), ("ex", "i"), ("ey", "i"), ("sx2", "i"), ("sy2", "i"), ("ex2", "i"),
                     ("ey2", "i")])
# 호 (표 101) — 속성 1바이트(호 종류. 규격 표는 UINT32)
CODECS[81] = simple([("arc_type", "B"), ("cx", "i"), ("cy", "i"), ("ax1", "i"), ("ay1", "i"), ("ax2", "i"), ("ay2", "i")])


# 다각형·곡선 (표 99·103) — 점 수는 INT32, 좌표는 (x, y) 쌍(규격: INT16, x 배열 뒤 y 배열). 끝에 4바이트(_rest)
def _poly(curve):
    def dec(data, ctx=None):
        r, d = Rd(data), {}
        n = r.take("i")
        d["points"] = [list(r.take("2i")) for _ in range(n)]
        if curve:
            d["segments"] = list(r.raw(max(n - 1, 0)))
        d["_rest"] = r.rest()
        return d

    def enc(d):
        w = Wr()
        w.put("i", len(d["points"]))
        for p in d["points"]:
            w.put("2i", *p)
        if curve:
            w.raw(bytes(d["segments"]))
        w.raw(d.get("_rest", b""))
        return bytes(w.b)
    return dec, enc


CODECS[82] = _poly(False)
CODECS[83] = _poly(True)
# OLE (표 118) — 속성 4바이트(규격 표는 UINT16이지만 비트 16~21 개체 종류가 있다). 끝 4바이트(_rest)
CODECS[84] = simple([("attr", "I"), ("extent_x", "i"), ("extent_y", "i"), ("bin_id", "H"), ("border_color", "I"),
                     ("border_width", "i"), ("border_attr", "I")])


# 그림 (표 107·108·116) — 테두리 사각형은 (x, y) 쌍 4개. 그림 정보(표 27)는 명암·밝기 순(규격 표는 밝기·명암 —
# HWPX bright를 바꿔 한컴이 HWP로 저장하면 둘째 바이트가 바뀜). 효과 비트가 0이면 바로 '그림 추가 속성'(원래 크기·투명도)이 온다
PIC_HEAD = _fields([("border_color", "I"), ("border_width", "i"), ("border_attr", "I"), ("points", "8i"),
                    ("crop", "4i"), ("margins", "4h"), ("contrast", "b"), ("brightness", "b"), ("effect", "B"),
                    ("bin_id", "H"), ("border_alpha", "B"), ("instance_id", "I"), ("effects", "I")])
PIC_TAIL = _fields([], [("orig_width", "i"), ("orig_height", "i")], [("alpha", "B")])


def _pic_dec(data, ctx=None):
    r, d = Rd(data), {}
    PIC_HEAD[0](r, d)
    if d["effects"] == 0:
        PIC_TAIL[0](r, d)
    d["_rest"] = r.rest()
    return d


def _pic_enc(d):
    w = Wr()
    PIC_HEAD[1](w, d)
    if d["effects"] == 0:
        PIC_TAIL[1](w, d)
    w.raw(d.get("_rest", b""))
    return bytes(w.b)


CODECS[85] = (_pic_dec, _pic_enc)


# 파라미터 셋 (표 50~52). 규격과 다른 점: 아이템 개수 INT16 뒤에 2바이트가 더 있고(배열 개수도 같음),
# 배열(PIT_ARRAY)의 원소는 '종류(UINT16) + 값'이다(규격 표는 파라미터 셋 배열).
def _pval_dec(r, t):
    if t == 1:
        return r.wstr()
    if t == 0x8000:
        return _pset_dec(r)
    if t == 0x8001:
        n, pad = r.take("h"), r.take("H")
        out = {"unk_2": pad, "items": []}
        for _ in range(n):
            et = r.take("H")
            out["items"].append({"type": et, "value": _pval_dec(r, et)})
        return out
    if t == 0x8002:
        return r.take("H")
    return r.take("I")


def _pval_enc(w, t, v):
    if t == 1:
        w.wstr(v)
    elif t == 0x8000:
        _pset_enc(w, v)
    elif t == 0x8001:
        w.put("hH", len(v["items"]), v["unk_2"])
        for e in v["items"]:
            w.put("H", e["type"])
            _pval_enc(w, e["type"], e["value"])
    elif t == 0x8002:
        w.put("H", v)
    else:
        w.put("I", v)


def _pset_dec(r):
    s = {"id": r.take("H")}
    n = r.take("h")
    s["unk_2"], s["items"] = r.take("H"), []
    for _ in range(n):
        it = {"id": r.take("H"), "type": r.take("H")}
        it["value"] = _pval_dec(r, it["type"])
        s["items"].append(it)
    return s


def _pset_enc(w, s):
    w.put("HhH", s["id"], len(s["items"]), s["unk_2"])
    for it in s["items"]:
        w.put("HH", it["id"], it["type"])
        _pval_enc(w, it["type"], it["value"])


def _pset_record():
    def dec(data, ctx=None):
        r = Rd(data)
        d = {"set": _pset_dec(r)} if r.left() >= 6 else {}
        d["_rest"] = r.rest()
        return d

    def enc(d):
        w = Wr()
        if "set" in d:
            _pset_enc(w, d["set"])
        w.raw(d.get("_rest", b""))
        return bytes(w.b)
    return dec, enc


CODECS[27] = _pset_record()                       # 문서 임의의 데이터 (표 49)
CODECS[87] = _pset_record()                       # 컨트롤 임의의 데이터 (표 66)
# 수식 (표 105) — 기준선 뒤 2바이트는 규격에 없다. 판에 따라 뒤 문자열이 없을 수 있어 선택 묶음
CODECS[88] = simple([("attr", "I"), ("script", "wstr"), ("size", "I"), ("color", "I"), ("baseline", "h")],
                    [("unk_2", "H")], [("version", "wstr")], [("font", "wstr")])
# 양식 개체 — 규격에 구조 없음. 실제: 종류 ID 두 번 + 글자 수(UINT32) + 속성 문자열(길이 붙은 WCHAR)
CODECS[91] = simple([("type_id", "I"), ("type_id2", "I"), ("text_len", "I"), ("props", "wstr")])
def form_props_parse(s):
    """양식 개체 속성 문자열 → [(셋 이름, [(이름, 형식, 값)])]. 모양: '셋:set:글자수:' + 본문(항목마다 뒤 공백) + ' '.
    항목: '이름:wstring:글자수:값' 또는 '이름:형식:값'."""
    out, i = [], 0
    while i < len(s):
        m = re.match(r"(\w+):set:(\d+):", s[i:])
        if not m:
            break
        n = int(m.group(2))
        body = s[i + m.end():i + m.end() + n]
        i += m.end() + n + 1
        items, j = [], 0
        while j < len(body):
            m2 = re.match(r"(\w+):(\w+):", body[j:])
            if not m2:
                break
            name, typ = m2.group(1), m2.group(2)
            j += m2.end()
            if typ == "wstring":
                k = body.index(":", j)
                ln = int(body[j:k])
                val = body[k + 1:k + 1 + ln]
                j = k + 1 + ln + 1
            else:
                k = body.find(" ", j)
                k = len(body) if k < 0 else k
                val = body[j:k]
                j = k + 1
            items.append((name, typ, val))
        out.append((m.group(1), items))
    return out


def form_props_build(sets):
    out = ""
    for name, items in sets:
        body = "".join((f"{n}:wstring:{len(v)}:{v}" if t == "wstring" else f"{n}:{t}:{v}") + " " for n, t, v in items)
        out += f"{name}:set:{len(body)}:{body} "
    return out


# 메모 리스트 헤더 — 규격에 구조 없음. 실제 4바이트
CODECS[93] = simple([("memo_id", "I")])
# 메모 모양 — 값은 HWPX hh:memoPr(width·lineWidth·lineType·lineColor·fillColor·activeColor·memoType)와 맞춰 봄
CODECS[92] = simple([("width", "I"), ("line_width", "B"), ("line_type", "B"), ("line_color", "I"), ("fill_color", "I"),
                     ("active_color", "I"), ("memo_type", "I")])


# 금칙 문자 — 4바이트 둘 + 줄 앞 금칙 글자 수 + 줄 뒤 금칙 글자 수 + 글자들
def _forbid_dec(data):
    r, d = Rd(data), {}
    d["unk_0"], d["unk_4"], n1, n2 = r.take("4I")
    d["not_start"] = r.raw(2 * n1).decode("utf-16-le", errors="surrogatepass")
    d["not_end"] = r.raw(2 * n2).decode("utf-16-le", errors="surrogatepass")
    d["_rest"] = r.rest()
    return d


def _forbid_enc(d):
    w = Wr()
    a, b = (x.encode("utf-16-le", errors="surrogatepass") for x in (d["not_start"], d["not_end"]))
    w.put("4I", d["unk_0"], d["unk_4"], len(a) // 2, len(b) // 2)
    w.raw(a + b)
    w.raw(d.get("_rest", b""))
    return bytes(w.b)


CODECS[94] = (_forbid_dec, _forbid_enc)
# 변경 추적 내용 — 앞 4바이트 + 시각(연·월·일·시·분·초 UINT16) + 나머지(뜻 미확인)
CODECS[96] = simple([("type", "I"), ("time", "6H")])


# 변경 추적 작성자 — 글자 수 UINT32 + 이름 + 나머지
def _author_dec(data):
    r, d = Rd(data), {}
    n = r.take("I")
    d["name"] = r.raw(2 * n).decode("utf-16-le", errors="surrogatepass")
    d["_rest"] = r.rest()
    return d


def _author_enc(d):
    w = Wr()
    e = d["name"].encode("utf-16-le", errors="surrogatepass")
    w.put("I", len(e) // 2)
    w.raw(e + d.get("_rest", b""))
    return bytes(w.b)


CODECS[97] = (_author_dec, _author_enc)
CODECS[32] = _array("I", "values")                # 변경 추적 정보 1032바이트 — UINT32 258개로만 둔다(뜻 미확인)
CTX_TAGS = {72, 76}


def decode(tag, data, ctx=None):
    """ctx: walk()가 붙인 부모 맥락. LIST_HEADER·SHAPE_COMPONENT는 맥락이 있어야 바르게 풀린다."""
    c = CODECS.get(tag)
    if not c:
        return None
    return c[0](data, ctx) if tag in CTX_TAGS else c[0](data)


def encode(tag, d):
    return CODECS[tag][1](d)


def roundtrip_corpus(paths, limit=None):
    """파일들의 레코드를 풀고 다시 묶어 원래 바이트와 견준다. → {태그: {레코드, 같음, _rest 있음, 실패}}"""
    import collections
    import zlib
    import olefile
    from hwp5_direct import read_records
    stat = collections.defaultdict(lambda: collections.Counter())
    rest_sizes = collections.defaultdict(collections.Counter)
    for path in paths[:limit]:
        try:
            ole = olefile.OleFileIO(path)
        except Exception:
            continue
        try:
            fh = ole.openstream("FileHeader").read()
            flags = struct.unpack_from("<I", fh, 36)[0]
            if flags & 6:
                continue
            for st in ["DocInfo"] + ["/".join(e) for e in ole.listdir() if e[0] == "BodyText"]:
                raw = ole.openstream(st).read()
                for tag, lv, data, ctx in walk(read_records(zlib.decompress(raw, -15) if flags & 1 else raw)):
                    if tag not in CODECS:
                        continue
                    s = stat[tag]
                    s["레코드"] += 1
                    try:
                        d = decode(tag, data, ctx)
                        if encode(tag, d) == bytes(data):
                            s["같음"] += 1
                        if d.get("_rest"):
                            s["_rest 있음"] += 1
                            rest_sizes[tag][len(d["_rest"])] += 1
                    except Exception:
                        s["실패"] += 1
        finally:
            ole.close()
    return {t: dict(v) for t, v in stat.items()}, {t: dict(v) for t, v in rest_sizes.items()}
