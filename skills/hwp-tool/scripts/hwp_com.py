"""한컴 COM 경로 — 한컴오피스가 깔린 PC에서 HWP·HWPX를 같은 명령으로 고친다.

근거: 한글 오토메이션 액션 이름과 CreateAction(이름).SetID로 확인한 파라미터셋, 형식 라이브러리 HwpObject.tlb.
"""
import ctypes
import os
import re
import subprocess
import time

HWPUNIT_PER_MM = 7200 / 25.4          # 1mm = 283.46 HWPUNIT
PT = 100                              # 글자 크기 1pt = 100


def mm(v):
    return int(round(float(v) * HWPUNIT_PER_MM))


def color(v):
    """'#RRGGBB'·(r,g,b)·정수 → 한컴 색(0x00BBGGRR)."""
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        v = v.lstrip("#")
        r, g, b = int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    else:
        r, g, b = v
    return r | (g << 8) | (b << 16)


def cell_name(c, r):
    """(열, 행) 0부터 → 'A1'."""
    out, c = "", c + 1
    while c:
        c, k = divmod(c - 1, 26)
        out = chr(65 + k) + out
    return f"{out}{r + 1}"


NO_WINDOW = 0x08000000      # CREATE_NO_WINDOW: tasklist·taskkill이 콘솔 창을 띄우지 않게


def _hwp_pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Hwp.exe", "/FO", "CSV", "/NH"], capture_output=True,
                         text=True, encoding="cp949", errors="replace", creationflags=NO_WINDOW).stdout
    return {int(m.group(1)) for m in re.finditer(r'"Hwp\.exe","(\d+)"', out)}


_SHARED = None              # 한 파이썬 프로세스 안에서 같이 쓰는 한글 하나


def _start():
    import hwp_hidden
    if not hwp_hidden.on_hidden():
        # 한글을 사람 화면에 띄우지 않는다. COM은 부른 프로세스의 데스크톱에 한글을 띄우므로
        # 숨긴 데스크톱에서 돌 때만 시작한다. hwp_tool.py는 저절로 옮겨 가고, 스크립트는 hwp_hidden.py로 실행한다.
        raise RuntimeError("한컴 경로는 숨긴 데스크톱에서만 엽니다: python hwp_hidden.py 스크립트.py … 로 실행하세요")
    import pythoncom
    import win32com.client.dynamic
    pythoncom.CoInitialize()
    before = _hwp_pids()
    clsid = pythoncom.MakeIID("{2291CF00-64A1-4877-A9B4-68CFE89612D6}")   # HWPFrame.HwpObject
    disp = pythoncom.CoCreateInstanceEx(clsid, None, pythoncom.CLSCTX_LOCAL_SERVER, None, (pythoncom.IID_IDispatch,))[0]
    hwp = win32com.client.dynamic.Dispatch(disp)    # 늦은 바인딩: 이름 대소문자 무시, gen_py 캐시 안 만듦
    pid = None
    try:
        win = hwp.XHwpWindows.Item(0)
        h = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(ctypes.c_void_p(win.WindowHandle), ctypes.byref(h))
        pid = h.value or None
    except Exception:
        pass
    if pid is None or pid in before:
        # 사람이 이미 열어 둔 한글에 붙었으면 그 한글은 건드리지 않고(닫지도 숨기지도 않음) 멈춘다
        del hwp, disp
        raise RuntimeError("새 한글을 띄우지 못하고 이미 열린 한글에 붙었습니다. 그 한글은 건드리지 않고 멈춥니다.")
    try:
        win.Visible = False
    except Exception:
        pass
    try:
        hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")   # 파일 열 때 확인 창 안 뜨게
    except Exception:
        pass
    hwp.SetMessageBoxMode(0x1 | 0x10 | 0x1000 | 0x10000)   # 대화상자는 확인·예로 자동 진행
    return hwp, pid


def _stop(hwp, pid):
    try:
        hwp.Clear(1)
        hwp.Quit()
    except Exception:
        pass
    if pid:
        for _ in range(20):
            if pid not in _hwp_pids():
                break
            time.sleep(0.5)
        if pid in _hwp_pids():
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=NO_WINDOW)


def close_shared():
    global _SHARED
    if _SHARED is not None:
        hwp, pid = _SHARED
        _SHARED = None
        _stop(hwp, pid)


class Session:
    """숨긴 데스크톱에서 한글을 띄워 돌려준다(hwp_hidden.py). 같은 파이썬 프로세스 안에서는 한 번 띄운 한글을 다시 쓰고(블록이 끝나면 문서만 닫음),
    프로세스가 끝날 때 그 한글(창 핸들로 찾은 PID)만 닫는다. 이미 열린 한글·다른 작업의 한글은 건드리지 않는다.
    reuse=False면 블록마다 새로 띄우고 블록 끝에 닫는다."""

    def __init__(self, reuse=True):
        self.reuse = reuse

    def __enter__(self):
        global _SHARED
        if self.reuse:
            if _SHARED is not None:
                try:
                    _SHARED[0].XHwpWindows.Count     # 살아 있나
                except Exception:
                    _SHARED = None
            if _SHARED is None:
                import atexit
                _SHARED = _start()
                atexit.register(close_shared)
            self.hwp, self.pid = _SHARED
        else:
            self.hwp, self.pid = _start()
        return self.hwp

    def __exit__(self, *exc):
        if self.reuse:
            try:
                self.hwp.Clear(1)            # 문서만 닫고 한글은 다음 작업에 쓴다
            except Exception:
                close_shared()
        else:
            _stop(self.hwp, self.pid)
        self.hwp = None
        return False


class ComDoc:
    """열린 한글 문서 하나를 고치는 함수 모음. 표 번호·칸 주소는 1부터, 'A1' 방식."""

    def __init__(self, hwp):
        self.hwp = hwp
        self._cells = {}

    # ---------- 기본 ----------
    def run(self, *names):
        ok = True
        for n in names:
            ok = bool(self.hwp.HAction.Run(n)) and ok
        return ok

    def action(self, name, items=None):
        """액션 실행. 파라미터셋이 있으면 기본값을 받은 뒤 items(dict, 하위 셋은 dict 중첩)를 덮어쓴다."""
        a = self.hwp.CreateAction(name)
        sid = a.SetID if a is not None else ""
        if not sid:
            return bool(self.hwp.HAction.Run(name))
        try:
            p = getattr(self.hwp.HParameterSet, "H" + sid)
            self.hwp.HAction.GetDefault(name, p.HSet)
            self._apply(p, items or {})
            return bool(self.hwp.HAction.Execute(name, p.HSet))
        except AttributeError:
            s = a.CreateSet()
            a.GetDefault(s)
            for k, v in (items or {}).items():
                s.SetItem(k, v)
            return bool(a.Execute(s))

    def _apply(self, p, items):
        for k, v in items.items():
            if isinstance(v, dict):
                self._apply(getattr(p, k), v)
            else:
                setattr(p, k, v)

    def text(self, s):
        """지금 자리에 글을 넣는다. 줄바꿈은 문단 나눔."""
        for i, part in enumerate(str(s).split("\n")):
            if i:
                self.run("BreakPara")
            if part:
                self.action("InsertText", {"Text": part})

    def pos(self):
        return tuple(self.hwp.GetPos())

    def body(self):
        """본문으로 돌아간다(머리말·각주·표 안에서 나옴)."""
        for _ in range(4):
            if self.hwp.GetPos()[0] == 0:
                break
            self.run("CloseEx")
        self.run("Cancel")

    # ---------- 찾기·선택 ----------
    def _sel(self):
        r = self.hwp.GetSelectedPos()
        return tuple(r[1:]) if r and r[0] else None

    def find(self, text, nth=1, regex=False, match_case=False, from_start=True):
        """nth번째 찾은 곳을 선택한다. 문서 끝에서 처음으로 돌아오면 멈춘다."""
        if from_start:
            self.body()
            self.run("MoveDocBegin")
        first = None
        for i in range(nth):
            ok = self.action("RepeatFind", {"FindString": text, "Direction": 0, "IgnoreMessage": 1,
                                            "FindRegExp": int(bool(regex)), "MatchCase": int(bool(match_case)),
                                            "ReplaceString": ""})
            sel = self._sel()
            if not ok or sel is None:
                return False
            if first is None:
                first = sel
            elif sel == first:   # 한 바퀴 돎
                return False
        return True

    def find_all(self, text, regex=False, match_case=False, limit=10000):
        """찾은 곳마다 선택한 채로 돌려준다(generator). 한 바퀴 돌면 끝."""
        self.body()
        self.run("MoveDocBegin")
        seen = set()
        for _ in range(limit):
            ok = self.action("RepeatFind", {"FindString": text, "Direction": 0, "IgnoreMessage": 1,
                                            "FindRegExp": int(bool(regex)), "MatchCase": int(bool(match_case))})
            sel = self._sel()
            if not ok or sel is None or sel in seen:
                return
            seen.add(sel)
            yield sel

    def select_paras(self, a, b=None):
        """본문 문단 a~b(0부터)를 선택한다."""
        b = a if b is None else b
        self.body()
        if not self.hwp.SetPos(0, a, 0):
            return False
        self.run("MoveSelParaBegin")
        for _ in range(b - a):
            self.run("MoveSelNextParaBegin")
        self.run("MoveSelParaEnd")
        return True

    # ---------- 표 ----------
    def tables(self):
        out, c = [], self.hwp.HeadCtrl
        while c:
            if c.CtrlID == "tbl":
                out.append(c)
            c = c.Next
        return out

    def enter_table(self, n):
        """n번째 표(1부터)의 A1 칸으로 들어가 칸 주소 → 목록 번호 지도를 만든다."""
        tbls = self.tables()
        if not 1 <= n <= len(tbls):
            raise ValueError(f"표 {n}번이 없습니다(표 {len(tbls)}개)")
        self.body()
        me = self._anchor(tbls[n - 1])
        # 칸마다 목록(list) 번호가 따로 있고 한 표 안에서는 이어진다. 목록 1번부터 훑어 부모 컨트롤이 이 표인 첫 칸을 찾고,
        # 이어서 이 표의 칸을 모은다(칸 안의 표는 부모가 그 칸 목록에 붙어 있으니 건너뛰고 계속, 다른 표가 나오면 멈춘다).
        # 앵커로 커서를 옮겨 FindCtrl 하는 방식은 한 문단에 표가 둘 붙어 있으면 뒤 표를 잡는다.
        L = 1
        while self.hwp.SetPos(L, 0, 0) and self.hwp.GetPos()[0] == L:   # 없는 목록이면 True인 채 목록 0으로 감
            pc = self.hwp.ParentCtrl
            if pc is not None and pc.CtrlID == "tbl" and self._anchor(pc) == me:
                break
            L += 1
        else:
            raise RuntimeError(f"표 {n}번의 칸을 찾지 못했습니다")
        cells, lists = {}, set()
        while self.hwp.SetPos(L, 0, 0) and self.hwp.GetPos()[0] == L:   # 없는 목록이면 True인 채 목록 0으로 감
            pc = self.hwp.ParentCtrl
            anc = self._anchor(pc) if pc is not None else None
            if anc == me:
                m = re.match(r"\(([A-Z]+\d+)\)", self.hwp.KeyIndicator()[-1] or "")
                if m:
                    cells[m.group(1)] = L
                lists.add(L)
            elif not (anc and anc[0] in lists):
                break
            L += 1
        self._cells = cells
        self.hwp.SetPos(cells.get("A1", min(cells.values())), 0, 0)
        return cells

    @staticmethod
    def _anchor(ctrl):
        s = ctrl.GetAnchorPos(0)
        return (s.Item("List"), s.Item("Para"), s.Item("Pos"))

    @staticmethod
    def addr(a):
        m = re.fullmatch(r"([A-Za-z]+)(\d+)", a.strip())
        col = 0
        for ch in m.group(1).upper():
            col = col * 26 + ord(ch) - 64
        return col, int(m.group(2))

    def goto_cell(self, a):
        a = a.upper()
        if a not in self._cells:
            raise ValueError(f"칸 {a}이 없습니다(합친 칸이면 왼쪽 위 주소를 쓰세요). 있는 칸: {', '.join(sorted(self._cells)[:30])}")
        self.hwp.SetPos(self._cells[a], 0, 0)

    def select_cells(self, rng):
        """'A1:C2' 칸 블록 선택(한 칸이면 'B2')."""
        a, _, b = rng.upper().partition(":")
        b = b or a
        self.goto_cell(a)
        self.run("TableCellBlock")
        if b != a:
            (c1, r1), (c2, r2) = self.addr(a), self.addr(b)
            self.run("TableCellBlockExtend")
            for _ in range(max(0, c2 - c1)):
                self.run("TableRightCell")
            for _ in range(max(0, r2 - r1)):
                self.run("TableLowerCell")
        return True

    # ---------- 자리 잡기 ----------
    def goto(self, o):
        """글·그림·각주 등을 넣을 자리로 커서를 옮긴다.
        at: start|end · after: 글 · before: 글 (nth) · table+cell(칸 끝) · para_end: 문단 번호"""
        if "table" in o and "cell" in o:
            self.enter_table(int(o["table"]))
            self.goto_cell(o["cell"])
            self.run("MoveListEnd")
            return True
        if "after" in o or "before" in o:
            key = "after" if "after" in o else "before"
            if not self.find(o[key], nth=int(o.get("nth", 1))):
                raise ValueError(f"'{o[key]}'을(를) 찾지 못했습니다")
            sel = self._sel()
            self.run("Cancel")
            if key == "before":
                self.hwp.SetPos(sel[0], sel[1], sel[2])
            else:
                self.hwp.SetPos(sel[3], sel[4], sel[5])
            return True
        if "para_end" in o:
            self.body()
            if not self.hwp.SetPos(0, int(o["para_end"]), 0):
                raise ValueError(f"문단 {o['para_end']}이 없습니다")
            self.run("MoveParaEnd")
            return True
        self.body()
        self.run("MoveDocBegin" if o.get("at") == "start" else "MoveDocEnd")
        return True

    def each_target(self, o):
        """대상마다 선택해 두고 yield. find(+nth) · para:[a,b] · table+cells · all."""
        if "find" in o:
            if "nth" in o:
                if not self.find(o["find"], nth=int(o["nth"]), regex=o.get("regex", False)):
                    raise ValueError(f"'{o['find']}' {o['nth']}번째를 찾지 못했습니다")
                yield "find"
            else:
                n = 0
                for _ in self.find_all(o["find"], regex=o.get("regex", False)):
                    n += 1
                    yield "find"
                if not n:
                    raise ValueError(f"'{o['find']}'을(를) 찾지 못했습니다")
        elif "para" in o:
            p = o["para"]
            a, b = (p, p) if isinstance(p, int) else (p[0], p[-1])
            if not self.select_paras(a, b):
                raise ValueError(f"문단 {p}이 없습니다")
            yield "para"
        elif "table" in o:
            self.enter_table(int(o["table"]))
            self.select_cells(o.get("cells") or o.get("cell") or "A1")
            yield "cells"
        elif o.get("all"):
            self.body()
            self.run("SelectAll")
            yield "all"
        else:
            raise ValueError("대상이 없습니다(find·para·table+cells·all 중 하나)")
        self.run("Cancel")

    # ---------- 고치기 명령 ----------
    def op(self, o):
        name = o["op"].replace("-", "_").replace(".", "_")
        f = getattr(self, "op_" + name, None)
        if f is None:
            raise ValueError("모르는 명령: " + o["op"])
        return f(o)

    def op_action(self, o):
        """한글 오토메이션 액션 아무거나: {"op":"action","name":"TableCellShadeInc","items":{...}}.
        대상(find·para·table+cells)이나 자리(after·at…)를 같이 주면 먼저 선택·이동한다."""
        def go():
            ok = self.action(o["name"], o.get("items"))
            if not ok and not o.get("allow_false"):
                raise RuntimeError(f"액션 {o['name']} 결과가 False")
        if any(k in o for k in ("find", "para", "cells", "all")):
            for _ in self.each_target(o):
                go()
        elif any(k in o for k in ("at", "after", "before", "para_end", "cell")):
            self.goto(o)
            go()
        else:
            go()
        return "액션 " + o["name"]

    CHAR_KEYS = ("font", "size", "color", "bold", "italic", "underline", "strike", "shade", "spacing", "ratio",
                 "superscript", "subscript")
    PARA_KEYS = ("align", "left_mm", "right_mm", "indent_mm", "before_pt", "after_pt", "line_spacing",
                 "page_break_before", "keep_with_next", "keep_lines", "widow_orphan", "word_wrap")

    def op_p(self, o):
        """문서 끝에 새 문단을 붙인다. 문단 모양(align·indent_mm…)과 글자 모양(font·size·bold…)을 함께 주고,
        runs:[{text, bold…}]로 한 문단 안 글자 모양을 나눌 수 있다. 문서가 비어 있으면 첫 문단을 쓴다."""
        self.body()
        self.run("MoveDocEnd")
        if getattr(self, "_used", False) or not self.hwp.IsEmpty:
            self.run("BreakPara")
        self._used = True
        if "style" in o:
            st = self.styles()
            if o["style"] not in st:
                raise ValueError(f"스타일 '{o['style']}'이 없습니다. 있는 스타일: {', '.join(st)}")
            self.action("Style", {"Apply": st[o["style"]]})
        pit = self._para_items({k: o[k] for k in self.PARA_KEYS if k in o})
        pit.setdefault("HeadingType", 0)
        self.action("ParagraphShape", pit)
        base = {k: o[k] for k in self.CHAR_KEYS if k in o}
        for r in o.get("runs") or [{"text": o.get("text", "")}]:
            look = {"bold": False, "italic": False, "underline": False, "superscript": False, "subscript": False}
            look.update(base)
            look.update({k: r[k] for k in self.CHAR_KEYS if k in r})
            self.action("CharShape", self._char_items(look))
            self.text(r.get("text", ""))
        return "문단"

    def op_text(self, o):
        self.goto(o)
        self.text(o["text"])
        return "글 넣음"

    def op_page_hide(self, o):
        """지금 쪽(기본 첫 쪽)에서 머리말·꼬리말·쪽 번호 등을 감춘다(표지용). PageHiding 셋 Fields 비트."""
        if not any(k in o for k in ("after", "before", "para_end", "cell")):
            o = {**o, "at": o.get("at", "start")}
        self.goto(o)
        bits = {"header": 0x01, "footer": 0x02, "master": 0x04, "border": 0x08, "fill": 0x10, "page_number": 0x20}
        mask = sum(v for k, v in bits.items() if o.get(k))
        if not self.action("PageHiding", {"Fields": mask or (0x01 | 0x02 | 0x20)}):
            raise RuntimeError("감추기 실패")
        return "감추기"

    def op_page_break(self, o):
        self.goto(o)
        self.run("BreakSection" if o.get("section") else "BreakPage")
        return "쪽 나눔"

    def op_delete(self, o):
        n = 0
        for _ in self.each_target(o):
            self.run("Delete")
            n += 1
        return f"{n}곳 지움"

    LANGS = ("Hangul", "Latin", "Hanja", "Japanese", "Other", "Symbol", "User")

    def _char_items(self, o):
        it = {}
        if "font" in o:
            for lang in self.LANGS:
                it["FaceName" + lang] = o["font"]
                it["FontType" + lang] = 1
        if "size" in o:
            it["Height"] = int(round(float(o["size"]) * PT))
        if "color" in o:
            it["TextColor"] = color(o["color"])
        for k, item in (("bold", "Bold"), ("italic", "Italic"), ("superscript", "SuperScript"), ("subscript", "SubScript")):
            if k in o:
                it[item] = int(bool(o[k]))
        if "underline" in o:
            it["UnderlineType"] = 1 if o["underline"] else 0
        if "strike" in o:
            it["StrikeOutType"] = 1 if o["strike"] else 0
        if "shade" in o:
            it["ShadeColor"] = 0xFFFFFFFF if not o["shade"] else color(o["shade"])
        for k, item in (("spacing", "Spacing"), ("ratio", "Ratio")):
            if k in o:
                for lang in self.LANGS:
                    it[item + lang] = int(o[k])
        return it

    def op_char(self, o):
        it = self._char_items(o)
        n = 0
        for _ in self.each_target(o):
            if not self.action("CharShape", it):
                raise RuntimeError("글자 모양 적용 실패")
            n += 1
        return f"{n}곳 글자 모양"

    ALIGN = {"justify": "Justify", "left": "Left", "right": "Right", "center": "Center",
             "distribute": "Distribute", "divide": "DistributeSpace"}

    def _para_items(self, o):
        it = {}
        if "align" in o:
            it["AlignType"] = self.hwp.HAlign(self.ALIGN[o["align"]])
        for k, item in (("left_mm", "LeftMargin"), ("right_mm", "RightMargin"), ("indent_mm", "Indentation")):
            if k in o:
                it[item] = mm(o[k]) * 2          # 문단 모양 셋의 여백은 HWPUNIT의 2배 값
        for k, item in (("before_pt", "PrevSpacing"), ("after_pt", "NextSpacing")):
            if k in o:
                it[item] = int(round(float(o[k]) * PT * 2))
        if "line_spacing" in o:
            it["LineSpacingType"] = 0
            it["LineSpacing"] = int(o["line_spacing"])
        for k, item in (("page_break_before", "PagebreakBefore"), ("keep_with_next", "KeepWithNext"),
                        ("keep_lines", "KeepLinesTogether"), ("widow_orphan", "WidowOrphan")):
            if k in o:
                it[item] = int(bool(o[k]))
        if "word_wrap" in o:          # 한글 줄 나눔: 0 = 어절, 1 = 글자
            it["BreakNonLatinWord"] = 0 if o["word_wrap"] == "word" else 1
        return it

    def op_para(self, o):
        it = self._para_items(o)
        n = 0
        for _ in self.each_target(o):
            if not self.action("ParagraphShape", it):
                raise RuntimeError("문단 모양 적용 실패")
            n += 1
        return f"{n}곳 문단 모양"

    def styles(self):
        import json
        js = json.loads(self.hwp.GetTextFile("JSON", ""))
        return {v["na"]: v["id"] for v in js["st"].values() if isinstance(v, dict)}

    def op_style(self, o):
        st = self.styles()
        if o["name"] not in st:
            raise ValueError(f"스타일 '{o['name']}'이 없습니다. 있는 스타일: {', '.join(st)}")
        n = 0
        for _ in self.each_target(o):
            self.action("Style", {"Apply": st[o["name"]]})
            n += 1
        return f"{n}곳 스타일 '{o['name']}'"

    def op_list(self, o):
        kind = o.get("type", "bullet")
        n = 0
        for _ in self.each_target(o):
            if kind == "bullet":
                self.run("PutBullet")
            elif kind == "number":
                self.run("PutParaNumber")
            else:
                self.action("ParagraphShape", {"HeadingType": 0})
            n += 1
        return f"{n}곳 목록({kind})"

    # 표
    def op_table_create(self, o):
        self.goto(o)
        if o.get("at", "end") == "end" and not any(k in o for k in ("after", "before", "para_end", "cell")):
            self.run("BreakPara")    # 끝에 붙일 때는 새 문단에(앞 문단의 글머리표·번호·들여쓰기를 물려받지 않게)
            self.action("ParagraphShape", {"HeadingType": 0, "LeftMargin": 0, "Indentation": 0, "PrevSpacing": 0,
                                           "NextSpacing": 0, "LineSpacingType": 0, "LineSpacing": 100,
                                           "KeepWithNext": int(bool(o.get("keep_with_next"))),
                                           "AlignType": self.hwp.HAlign(self.ALIGN[o.get("table_align", "center")])})
        data = o.get("data") or []
        rows = int(o.get("rows") or len(data) or 2)
        cols = int(o.get("cols") or max((len(r) for r in data), default=2))
        before = {self._anchor(t) for t in self.tables()}
        p = self.hwp.HParameterSet.HTableCreation
        self.hwp.HAction.GetDefault("TableCreate", p.HSet)
        p.Rows, p.Cols, p.WidthType, p.HeightType = rows, cols, 0, 0     # 기본은 단 폭에 맞춤
        if o.get("col_widths_mm"):
            p.WidthType = 2
            ws = [float(w) for w in o["col_widths_mm"]]
            p.CreateItemArray("ColWidth", cols)
            for i in range(cols):
                p.ColWidth.SetItem(i, mm(ws[i] if i < len(ws) else ws[-1]) - mm(3.6))   # 칸 안 여백(1.8mm×2) 뺀 값
            p.TableProperties.Width = sum(mm(w) for w in ws[:cols])
        if not self.hwp.HAction.Execute("TableCreate", p.HSet):
            raise RuntimeError("표 만들기 실패")
        look = {k: o[k] for k in self.CHAR_KEYS if k in o}
        for r in range(rows):
            for c in range(cols):
                v = data[r][c] if r < len(data) and c < len(data[r]) else ""
                if v != "":
                    self.text(v)
                if not (r == rows - 1 and c == cols - 1):
                    self.run("TableRightCell")
        self.body()
        n = next((i for i, t in enumerate(self.tables(), 1) if self._anchor(t) not in before), len(self.tables()))
        last = cell_name(cols - 1, rows - 1)
        if look:
            self.op_char({"table": n, "cells": "A1:" + last, **look})
        if o.get("pad_pt"):        # 칸 글 위아래 간격(줄 높이 넉넉하게)
            self.op_para({"table": n, "cells": "A1:" + last, "before_pt": o["pad_pt"], "after_pt": o["pad_pt"],
                          "line_spacing": o.get("cell_line_spacing", 130)})
        if o.get("align"):
            self.op_table_align({"table": n, "cells": "A1:" + last, "h": o["align"], "v": "middle"})
        if o.get("word_wrap"):
            self.op_para({"table": n, "cells": "A1:" + last, "word_wrap": o["word_wrap"]})
        if o.get("header"):
            hrow = "A1:" + cell_name(cols - 1, 0)
            self.op_table_fill({"table": n, "cells": hrow, "color": o.get("header_fill", "#D9D9D9")})
            self.op_char({"table": n, "cells": hrow, "bold": True})
            self.op_table_align({"table": n, "cells": hrow, "h": "center", "v": "middle"})
        if o.get("border"):
            self.op_table_border({"table": n, "cells": "A1:" + last, **dict(o["border"])})
        self.body()
        return f"표 {n}번 {rows}×{cols}"

    def _cell_op(self, o, action, items=None):
        self.enter_table(int(o["table"]))
        self.goto_cell(o.get("cell") or o.get("cells", "A1").split(":")[0])
        if not self.action(action, items or {}):
            raise RuntimeError(action + " 실패")

    def op_table_insert_row(self, o):
        self._cell_op(o, "TableInsertUpperRow" if o.get("where") == "above" else "TableInsertLowerRow", {"Count": int(o.get("count", 1))})
        return "줄 넣음"

    def op_table_insert_col(self, o):
        self._cell_op(o, "TableInsertLeftColumn" if o.get("where") == "left" else "TableInsertRightColumn", {"Count": int(o.get("count", 1))})
        return "칸 넣음"

    def op_table_delete_row(self, o):
        self._cell_op(o, "TableDeleteRow")
        return "줄 지움"

    def op_table_delete_col(self, o):
        self._cell_op(o, "TableDeleteColumn")
        return "칸 지움"

    def op_table_split(self, o):
        self._cell_op(o, "TableSplitCell", {"Rows": int(o.get("rows", 1)), "Cols": int(o.get("cols", 2))})
        return "칸 나눔"

    def _block(self, o, fn):
        self.enter_table(int(o["table"]))
        self.select_cells(o["cells"])
        try:
            return fn()
        finally:
            self.run("Cancel")

    def op_table_merge(self, o):
        if not self._block(o, lambda: self.run("TableMergeCell")):
            raise RuntimeError("칸 합치기 실패")
        return "칸 합침 " + o["cells"]

    def op_table_fill(self, o):
        h = self.hwp
        if o.get("color"):
            fa = {"type": h.BrushType("NullBrush|WinBrush"), "WinBrushFaceColor": color(o["color"]),
                  "WinBrushHatchColor": color("#999999"), "WinBrushFaceStyle": h.HatchStyle("None"), "WindowsBrush": 1}
        else:
            fa = {"type": h.BrushType("NullBrush"), "WindowsBrush": 0}
        if not self._block(o, lambda: self.action("CellFill", {"FillAttr": fa})):
            raise RuntimeError("칸 배경 실패")
        return "칸 배경 " + o["cells"]

    LINES = {"solid": "Solid", "dash": "Dash", "dot": "Dot", "double": "DoubleSlim", "none": "None"}

    def op_table_border(self, o):
        h = self.hwp
        line = h.HwpLineType(self.LINES.get(o.get("line", "solid"), "Solid"))
        width = h.HwpLineWidth(o.get("width", "0.12mm"))
        col = color(o.get("color", "#000000"))
        sides = o.get("sides", "all")
        outer = {"left": ("BorderTypeLeft", "BorderWidthLeft", "BorderCorlorLeft"),   # 한컴 셋 이름의 오타(Corlor) 그대로
                 "right": ("BorderTypeRight", "BorderWidthRight", "BorderColorRight"),
                 "top": ("BorderTypeTop", "BorderWidthTop", "BorderColorTop"),
                 "bottom": ("BorderTypeBottom", "BorderWidthBottom", "BorderColorBottom")}
        inner = {"horz": ("TypeHorz", "WidthHorz", "ColorHorz"), "vert": ("TypeVert", "WidthVert", "ColorVert")}
        pick = {"all": list(outer) + list(inner), "outside": list(outer), "inside": list(inner)}.get(sides) or sides.split(",")
        it = {}
        for s in pick:
            t, w, c = {**outer, **inner}[s.strip()]
            it[t], it[w], it[c] = line, width, col
        if not self._block(o, lambda: self.action("CellBorder", it)):
            raise RuntimeError("테두리 실패")
        return "테두리 " + o["cells"]

    def op_table_cell_text(self, o):
        self.enter_table(int(o["table"]))
        self.goto_cell(o["cell"])
        self.run("MoveListBegin", "MoveSelListEnd")
        if o["text"] == "":
            self.run("Delete")
        else:
            self.text(o["text"])
        self.body()
        return "칸 글 " + o["cell"]

    def op_table_align(self, o):
        hv = {"left": "Left", "center": "Center", "right": "Right"}[o.get("h", "center")]
        vv = {"top": "Top", "middle": "Center", "bottom": "Bottom"}[o.get("v", "middle")]
        if not self._block(o, lambda: self.run(f"TableCellAlign{hv}{vv}")):
            raise RuntimeError("칸 정렬 실패")
        return "칸 정렬 " + o["cells"]

    # 그림·머리말·쪽
    def op_image(self, o):
        path = os.path.abspath(o["path"])
        if not os.path.exists(path):
            raise ValueError("그림 파일이 없습니다: " + path)
        w, hgt = o.get("width_mm"), o.get("height_mm")
        if w or hgt:
            from PIL import Image
            with Image.open(path) as im:
                ratio = im.height / im.width
            w = float(w) if w else float(hgt) / ratio
            hgt = float(hgt) if hgt else w * ratio
            size = (1, int(round(w)), int(round(hgt)))
        else:
            size = (0, 0, 0)
        self.goto(o)
        if self.hwp.InsertPicture(path, True, size[0], False, False, 0, size[1], size[2]) is None:
            raise RuntimeError("그림 넣기 실패")
        self.body()
        return "그림 넣음"

    def _headfoot(self, o, kind):
        self.body()
        self.run("MoveDocBegin")
        page = self.hwp.PageType({"both": "Both", "even": "Even", "odd": "Odd"}[o.get("page", "both")])
        if not self.action("HeaderFooter", {"HeaderFooterCtrlType": kind, "Type": page}):
            raise RuntimeError("머리말/꼬리말 실패")
        self.run("MoveListBegin", "MoveSelListEnd")
        self.text(o.get("text", ""))
        if "align" in o:
            self.run("MoveSelListBegin")
            self.action("ParagraphShape", {"AlignType": self.hwp.HAlign(self.ALIGN[o["align"]])})
        self.body()

    def op_header(self, o):
        self._headfoot(o, 0)
        return "머리말"

    def op_footer(self, o):
        self._headfoot(o, 1)
        return "꼬리말"

    POS = {"none": "None", "top-left": "TopLeft", "top-center": "TopCenter", "top-right": "TopRight",
           "bottom-left": "BottomLeft", "bottom-center": "BottomCenter", "bottom-right": "BottomRight",
           "outside-top": "OutsideTop", "outside-bottom": "OutsideBottom", "inside-top": "InsideTop", "inside-bottom": "InsideBottom"}
    NUMFMT = {"digit": "Digit", "circle": "CircledDigit", "roman": "RomanCapital", "roman-small": "RomanSmall",
              "latin": "LatinCapital", "latin-small": "LatinSmall", "hangul": "HangulSyllable", "hanja": "HanjaDigit"}

    def op_page_number(self, o):
        self.body()
        it = {"DrawPos": self.hwp.PageNumPosition(self.POS[o.get("pos", "bottom-center")]),
              "NumberFormat": self.hwp.NumberFormat(self.NUMFMT[o.get("format", "digit")]),
              "SideChar": ord(o["side"]) if o.get("side") else 0}
        if not self.action("PageNumPos", it):
            raise RuntimeError("쪽 번호 실패")
        return "쪽 번호 " + o.get("pos", "bottom-center")

    PAPER = {"A4": (210, 297), "A3": (297, 420), "A5": (148, 210), "B4": (257, 364), "B5": (182, 257),
             "LETTER": (215.9, 279.4), "LEGAL": (215.9, 355.6)}

    def op_page(self, o):
        pd = {}
        if "paper" in o:
            w, hh = self.PAPER[o["paper"].upper()]
            pd["PaperWidth"], pd["PaperHeight"] = mm(w), mm(hh)
        if "width_mm" in o:
            pd["PaperWidth"] = mm(o["width_mm"])
        if "height_mm" in o:
            pd["PaperHeight"] = mm(o["height_mm"])
        if "landscape" in o:
            pd["Landscape"] = int(bool(o["landscape"]))
        for k, item in (("left", "LeftMargin"), ("right", "RightMargin"), ("top", "TopMargin"), ("bottom", "BottomMargin"),
                        ("header", "HeaderLen"), ("footer", "FooterLen"), ("gutter", "GutterLen")):
            if k in o.get("margins_mm", {}):
                pd[item] = mm(o["margins_mm"][k])
        self.body()
        if not self.action("PageSetup", {"PageDef": pd}):
            raise RuntimeError("쪽 설정 실패")
        return "쪽 설정"

    # 누름틀·각주·바꾸기·합치기
    def op_field(self, o):
        self.goto(o)
        if not self.hwp.CreateField(o.get("help", o["name"]), o.get("memo", ""), o["name"]):
            raise RuntimeError("누름틀 만들기 실패")
        if o.get("text"):
            self.hwp.PutFieldText(o["name"], o["text"])
        return "누름틀 " + o["name"]

    def op_fill(self, o):
        have = set(self.hwp.GetFieldList(0, 0).split("\x02")) - {""}
        miss = [k for k in o["fields"] if k not in have]
        if miss:
            raise ValueError(f"없는 누름틀: {miss}. 있는 누름틀: {sorted(have)}")
        for k, v in o["fields"].items():
            self.hwp.PutFieldText(k, str(v))
        return f"누름틀 {len(o['fields'])}개 채움"

    def _note(self, o, action):
        self.goto(o)
        if not self.run(action):
            raise RuntimeError(action + " 실패")
        self.text(o["text"])
        self.body()

    def op_footnote(self, o):
        self._note(o, "InsertFootnote")
        return "각주"

    def op_endnote(self, o):
        self._note(o, "InsertEndnote")
        return "미주"

    def op_replace(self, o):
        find, to = o["find"], o.get("to", "")
        scope, regex, mc = o.get("scope"), bool(o.get("regex")), bool(o.get("match_case"))
        total = sum(1 for _ in self.find_all(find, regex=regex, match_case=mc))
        if not scope and not regex:
            self.body()
            self.action("AllReplace", {"FindString": find, "ReplaceString": to, "IgnoreMessage": 1,
                                       "MatchCase": int(mc), "Direction": self.hwp.FindDir("AllDoc")})
            left = 0 if find in to else sum(1 for _ in self.find_all(find, match_case=mc))
            return f"{total - left}곳 바꿈(남은 {left})"
        # 한컴 '모두 바꾸기'는 자동화에서 선택 범위를 무시한다(2026-10-16 시험 3가지) → 찾은 곳을 하나씩 골라 바꾼다
        inside = lambda sel: True
        if scope and "table" in scope:
            lists = set(self.enter_table(int(scope["table"])).values())
            inside = lambda sel: sel[0] in lists
        elif scope and "para" in scope:
            p = scope["para"]
            a, b = (p, p) if isinstance(p, int) else (p[0], p[-1])
            inside = lambda sel: sel[0] == 0 and a <= sel[1] <= b
        elif scope and ("after" in scope or "before" in scope):
            lo = hi = None
            if "after" in scope:
                if not self.find(scope["after"]):
                    raise ValueError(f"범위 시작 '{scope['after']}'을 찾지 못했습니다")
                s = self._sel(); lo = (s[4], s[5])
            if "before" in scope:
                if not self.find(scope["before"]):
                    raise ValueError(f"범위 끝 '{scope['before']}'을 찾지 못했습니다")
                s = self._sel(); hi = (s[1], s[2])
            inside = lambda sel: sel[0] == 0 and (lo is None or (sel[1], sel[2]) >= lo) and (hi is None or (sel[1], sel[2]) <= hi)
        self.body()
        self.run("MoveDocBegin")
        rx = re.compile(find) if regex else None
        n = 0
        for _ in range(total):
            if not self.action("RepeatFind", {"FindString": find, "Direction": 0, "IgnoreMessage": 1,
                                              "FindRegExp": int(regex), "MatchCase": int(mc)}):
                break
            sel = self._sel()
            if sel is None or not inside(sel):
                continue
            new = rx.sub(to, self.hwp.GetTextFile("UNICODE", "saveblock:true").rstrip("\r\n")) if rx else to
            if new:
                self.action("InsertText", {"Text": new})
            else:
                self.run("Delete")
            n += 1
        return f"{n}곳 바꿈(전체 {total}곳 중 범위 안)"

    def op_merge(self, o):
        path = os.path.abspath(o["file"])
        if not os.path.exists(path):
            raise ValueError("합칠 파일이 없습니다: " + path)
        self.body()
        self.run("MoveDocEnd")
        # KeepSection=1이면 붙는 문서가 제 구역으로 들어와 새 쪽에서 시작한다 → 'page'에 쪽 나눔을 또 넣으면 빈 쪽이 생김
        self.run({"section": "BreakSection"}.get(o.get("break", "page"), "BreakPara"))
        if not self.action("InsertFile", {"filename": path, "KeepSection": 1, "KeepCharshape": 1, "KeepParashape": 1, "KeepStyle": 1}):
            raise RuntimeError("문서 합치기 실패")
        return "합침 " + os.path.basename(path)
