"""HWP 5 직접 쓰기 — 한컴 없이 HWP 파일 안 글을 바꾼다. 범위: 글자 바꾸기(replace)·누름틀 채우기(fill)까지.

방법: 복합 문서(CFB)를 olefile로 읽고, BodyText/Section* 레코드에서 문단 글(PARA_TEXT)을 고친 뒤
문단 머리(PARA_HEADER)의 글자 수, 글자 모양 위치(PARA_CHAR_SHAPE), 줄 배치 위치(PARA_LINE_SEG), 범위 태그(PARA_RANGE_TAG)를
같은 만큼 옮긴다. 저장은 윈도 구조화 저장소(pythoncom.StgCreateDocfile)로 새 파일에. 미리보기 글(PrvText)에도 같은 바꾸기.
새로 쓰기·표·서식 바꾸기·줄 다시 짜기는 하지 않는다(한계 — 한컴 경로나 HWPX 직접 경로를 쓴다).
"""
import os
import re
import struct
import zlib

PARA_HEADER, PARA_TEXT, PARA_CHAR_SHAPE, PARA_LINE_SEG, PARA_RANGE_TAG, CTRL_HEADER, CTRL_DATA = 66, 67, 68, 69, 70, 71, 87
EXT = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}   # 8글자(16바이트) 차지하는 제어
OBJ = "￼"


# ───────── 레코드 ─────────
def read_records(buf):
    out, i = [], 0
    while i + 4 <= len(buf):
        h, = struct.unpack_from("<I", buf, i)
        i += 4
        tag, level, size = h & 0x3FF, (h >> 10) & 0x3FF, h >> 20
        if size == 0xFFF:
            size, = struct.unpack_from("<I", buf, i)
            i += 4
        out.append([tag, level, bytearray(buf[i:i + size])])
        i += size
    return out


def write_records(recs):
    out = bytearray()
    for tag, level, data in recs:
        n = len(data)
        if n >= 0xFFF:
            out += struct.pack("<I", tag | (level << 10) | (0xFFF << 20)) + struct.pack("<I", n)
        else:
            out += struct.pack("<I", tag | (level << 10) | (n << 20))
        out += data
    return bytes(out)


# ───────── 문단 글 ─────────
def units(data):
    """PARA_TEXT → 단위 목록 [(시작 WCHAR 위치, 길이 WCHAR, 코드, 원래 바이트)]."""
    out, i = [], 0
    while i + 1 < len(data):
        c, = struct.unpack_from("<H", data, i)
        if c < 32 and c in EXT:
            out.append((i // 2, 8, c, bytes(data[i:i + 16])))
            i += 16
        else:
            out.append((i // 2, 1, c, bytes(data[i:i + 2])))
            i += 2
    return out


def visible(us):
    """찾기용 글: 글자는 그대로, 제어는 OBJ(탭 9는 '\t', 줄바꿈 10은 '\n'). 각 글자의 단위 번호도 돌려준다."""
    s, idx = [], []
    for k, (_, n, c, _) in enumerate(us):
        if c >= 32:
            s.append(chr(c))
        elif c == 9:
            s.append("\t")
        elif c == 10:
            s.append("\n")
        elif c == 13:
            continue
        else:
            s.append(OBJ)
        idx.append(k)
    return "".join(s), idx


class Para:
    """문단 머리 레코드 번호와 딸린 레코드(글·글자 모양·줄 배치·범위 태그)."""

    def __init__(self, recs, hi):
        self.recs, self.hi = recs, hi
        lv = recs[hi][1]
        self.ti = self.ci = self.li = self.ri = None
        j = hi + 1
        while j < len(recs) and recs[j][1] == lv + 1 and recs[j][0] in (PARA_TEXT, PARA_CHAR_SHAPE, PARA_LINE_SEG, PARA_RANGE_TAG):
            setattr(self, {PARA_TEXT: "ti", PARA_CHAR_SHAPE: "ci", PARA_LINE_SEG: "li", PARA_RANGE_TAG: "ri"}[recs[j][0]], j)
            j += 1

    def splice(self, start, end, new_text):
        """WCHAR 위치 [start, end)를 new_text로 바꾸고 딸린 위치들을 옮긴다."""
        recs = self.recs
        data = recs[self.ti][2]
        enc = new_text.encode("utf-16le")
        recs[self.ti][2] = bytearray(data[:start * 2] + enc + data[end * 2:])
        delta = len(enc) // 2 - (end - start)
        h = recs[self.hi][2]
        n, = struct.unpack_from("<I", h, 0)
        struct.pack_into("<I", h, 0, (n & 0x80000000) | ((n & 0x7FFFFFFF) + delta))

        def shift(p):
            if p >= end:
                return p + delta
            if p > start:
                return start + len(enc) // 2 if p > start + len(enc) // 2 else p
            return p
        if self.ci is not None:          # (위치 UINT32, 글자 모양 UINT32) 쌍
            d = recs[self.ci][2]
            pairs = [struct.unpack_from("<II", d, o) for o in range(0, len(d) - 7, 8)]
            fixed, seen = [], set()
            for pos, sid in pairs:
                np_ = shift(pos)
                if np_ in seen:
                    fixed[-1] = (np_, sid)
                    continue
                seen.add(np_)
                fixed.append((np_, sid))
            recs[self.ci][2] = bytearray(b"".join(struct.pack("<II", p, s) for p, s in fixed))
        if self.li is not None:          # 36바이트 항목, 첫 UINT32 = 글 시작 위치
            d = recs[self.li][2]
            for o in range(0, len(d) - 35, 36):
                p, = struct.unpack_from("<I", d, o)
                struct.pack_into("<I", d, o, shift(p))
        if self.ri is not None:          # 12바이트 항목: 시작, 끝, 태그
            d = recs[self.ri][2]
            for o in range(0, len(d) - 11, 12):
                a, b = struct.unpack_from("<II", d, o)
                struct.pack_into("<II", d, o, shift(a), shift(b))
        return delta


def paras(recs):
    return [Para(recs, i) for i, r in enumerate(recs) if r[0] == PARA_HEADER]


# ───────── 누름틀 ─────────
def field_name(ctrl_data):
    """CTRL_DATA(파라미터셋) 끝의 (UINT16 길이, WCHAR×길이) = 누름틀 이름."""
    d = bytes(ctrl_data)
    for o in range(4, len(d) - 1, 2):
        n, = struct.unpack_from("<H", d, o)
        if n and o + 2 + 2 * n == len(d):
            try:
                return d[o + 2:].decode("utf-16le")
            except UnicodeDecodeError:
                return None
    return None


def fields_in(recs, p):
    """문단 p 안의 누름틀 [(이름, 시작 제어 뒤 WCHAR 위치, 끝 제어 WCHAR 위치)]."""
    if p.ti is None:
        return []
    lv = recs[p.hi][1]
    heads = []          # 이 문단의 컨트롤 머리(레벨 lv+1) 순서대로 (id, 이름)
    j = p.hi + 1
    while j < len(recs) and recs[j][1] > lv:
        if recs[j][1] == lv + 1 and recs[j][0] == CTRL_HEADER:
            cid = bytes(recs[j][2][:4])[::-1].decode("latin-1")
            name = None
            if j + 1 < len(recs) and recs[j + 1][0] == CTRL_DATA and recs[j + 1][1] == lv + 2:
                name = field_name(recs[j + 1][2])
            heads.append((cid, name))
        j += 1
    out, hk, stack = [], 0, []
    for pos, n, c, raw in units(recs[p.ti][2]):
        if n != 8:
            continue
        if c == 4:                       # 누름틀 끝은 컨트롤 머리가 없다
            if stack:
                name, s = stack.pop()
                out.append((name, s, pos))
            continue
        cid, name = heads[hk] if hk < len(heads) else ("?", None)
        hk += 1
        if c == 3 and cid == "%clk":
            stack.append((name, pos + 8))
    return out


# ───────── 파일 ─────────
class Hwp5:
    def __init__(self, path):
        import olefile
        if not olefile.isOleFile(path):
            raise ValueError("HWP 5 형식이 아닙니다: " + path)
        ole = olefile.OleFileIO(path)
        try:
            self.root_clsid = ole.root.clsid
            self.streams = {"/".join(e): ole.openstream(e).read() for e in ole.listdir(streams=True, storages=False)}
            self.storages = ["/".join(e) for e in ole.listdir(streams=False, storages=True)]
        finally:
            ole.close()
        fh = self.streams["FileHeader"]
        if not fh.startswith(b"HWP Document File"):
            raise ValueError("HWP 5 형식이 아닙니다")
        self.flags, = struct.unpack_from("<I", fh, 36)
        if self.flags & 2:
            raise PermissionError("암호가 걸린 문서는 열지 않습니다")
        if self.flags & 4:
            raise PermissionError("배포용 문서(본문 암호화)는 한컴으로만 고칠 수 있습니다")
        self.compressed = bool(self.flags & 1)
        self.sections = {}
        for n in sorted((n for n in self.streams if n.startswith("BodyText/Section")), key=lambda n: int(n[16:] or 0)):
            raw = self.streams[n]
            self.sections[n] = read_records(zlib.decompress(raw, -15) if self.compressed else raw)
        self.prv_subs = []

    def all_paras(self):
        for n, recs in self.sections.items():
            for p in paras(recs):
                if p.ti is not None:
                    yield recs, p

    def replace(self, find, to, regex=False, match_case=True):
        rx = re.compile(find if regex else re.escape(find), 0 if match_case else re.I)
        n = 0
        for recs, p in self.all_paras():
            us = units(recs[p.ti][2])
            s, idx = visible(us)
            spans = [(m.start(), m.end(), m.expand(to) if regex else to) for m in rx.finditer(s)
                     if m.end() > m.start() and OBJ not in m.group(0)]
            for a, b, new in reversed(spans):
                start = us[idx[a]][0]
                last = us[idx[b - 1]]
                p.splice(start, last[0] + last[1], new)
                n += 1
        if n and not regex:
            self.prv_subs.append((find, to))
        return n

    def fields(self):
        out = {}
        for recs, p in self.all_paras():
            for name, a, b in fields_in(recs, p):
                out.setdefault(name, []).append((recs, p, a, b))
        return out

    def fill(self, values):
        have = self.fields()
        miss = [k for k in values if k not in have]
        if miss:
            raise ValueError(f"없는 누름틀: {miss}. 있는 누름틀: {sorted(k for k in have if k)}")
        n = 0
        for name, val in values.items():
            for recs, p, a, b in reversed(self.fields()[name]):
                p.splice(a, b, str(val))
                n += 1
        return n

    def save(self, out):
        try:
            import pythoncom
            import pywintypes
        except ImportError as e:
            raise RuntimeError("HWP 파일 쓰기는 지금 윈도(pywin32의 구조화 저장소)에서만 됩니다. 다른 OS에서는 HWPX로 저장하세요") from e
        if os.path.exists(out):
            os.remove(out)
        streams = dict(self.streams)
        for n, recs in self.sections.items():
            data = write_records(recs)
            if self.compressed:
                c = zlib.compressobj(9, zlib.DEFLATED, -15)
                data = c.compress(data) + c.flush()
            streams[n] = data
        if "PrvText" in streams and self.prv_subs:
            t = streams["PrvText"].decode("utf-16le", "replace")
            for f, r in self.prv_subs:
                t = t.replace(f, r)
            streams["PrvText"] = t.encode("utf-16le")
        mode = 0x1000 | 0x2 | 0x10        # STGM_CREATE | STGM_READWRITE | STGM_SHARE_EXCLUSIVE
        root = pythoncom.StgCreateDocfile(os.path.abspath(out), mode, 0)
        if self.root_clsid and self.root_clsid.strip("0-{}"):
            root.SetClass(pywintypes.IID("{" + self.root_clsid.strip("{}") + "}"))
        stg_cache = {"": root}

        def storage(path):
            if path in stg_cache:
                return stg_cache[path]
            parent, _, name = path.rpartition("/")
            s = storage(parent).CreateStorage(name, mode, 0, 0)
            stg_cache[path] = s
            return s
        for st in self.storages:
            storage(st)
        for path, data in streams.items():
            parent, _, name = path.rpartition("/")
            stm = storage(parent).CreateStream(name, mode, 0, 0)
            stm.Write(data)
            stm = None
        for k in sorted(stg_cache, key=len, reverse=True):
            stg_cache[k].Commit(0)
        stg_cache.clear()
        root = None


def edit(src, out, ops):
    """HWP를 한컴 없이 고친다(replace·fill만). 진행 줄을 yield."""
    d = Hwp5(src)
    for i, o in enumerate(ops, 1):
        if o["op"] == "replace":
            if o.get("scope"):
                raise ValueError("한컴 없는 HWP 바꾸기는 범위 지정(scope)을 하지 않습니다(문서 전체)")
            n = d.replace(o["find"], o.get("to", ""), o.get("regex", False), o.get("match_case", True))
            yield f"[{i}] replace: {n}곳 바꿈"
        elif o["op"] == "fill":
            yield f"[{i}] fill: 누름틀 {d.fill(o['fields'])}곳 채움"
        else:
            raise ValueError(f"한컴 없이 HWP로 저장할 때는 replace·fill만 됩니다(받은 명령: {o['op']}). "
                             "다른 명령은 한컴 경로를 쓰거나 -o 를 .hwpx로 주세요")
    d.save(out)
