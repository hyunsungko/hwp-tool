"""Markdown → HWPX/HWP, 한컴 없이(직접 경로). 공공기관·기업 보고서 모양으로 쓴다.

  python hwp_tool.py write 보고서.md -o 보고서.hwp --engine direct [--header 머리말]

글꼴: 제목 HY헤드라인M · 본문 휴먼명조 · 표 맑은 고딕.
- `# 제목` → 표지 제목 상자(위·아래 굵은 선), 바로 아래 짧은 문단은 부제
- `## 1. 장 제목` → 번호 띠(남색 번호 칸 + 밑줄 친 제목 칸)
- `### 소제목` → □ 소제목,  `- 항목` → ○ 항목(내어쓰기),  `1. 항목` → 번호 그대로
- 표 → 좌우 바깥 테두리 없음, 위·아래 굵은 남색 선, 머리줄 음영, 첫 칸 굵게
- `> 인용` → 강조 상자(옅은 바탕 + 왼쪽 굵은 선)
- `![캡션](그림.png)` → 그림(폭 150mm) + <그림 n> 캡션
- ```kpi 블록(줄마다 `값 | 이름 | 설명`) → 「핵심 지표」 상자: 바깥 표 안에 지표 표(표 안에 표)
"""
import os
import re

import hwpx_direct as X

NAVY, ACCENT, GRAY, LIGHT = "#1F3864", "#C55A11", "#8C96A8", "#DCE6F2"
HEAD, BODYF, TABLEF = "HY헤드라인M", "휴먼명조", "맑은 고딕"
BODY = {"font": BODYF, "size": 12, "align": "justify", "line_spacing": 160, "after_pt": 4, "word_wrap": "word"}
STYLE = {
    "subtitle": {"font": TABLEF, "size": 11, "align": "right", "after_pt": 16, "color": "#595959"},
    3: {"font": BODYF, "size": 13, "bold": True, "before_pt": 10, "after_pt": 4, "keep_with_next": True, "word_wrap": "word"},
    4: {"font": BODYF, "size": 12, "bold": True, "before_pt": 6, "after_pt": 3, "keep_with_next": True, "word_wrap": "word"},
    "bullet": {**BODY, "left_mm": 6, "indent_mm": -4},
    "number": {**BODY, "left_mm": 6, "indent_mm": -5},
    "caption": {"font": TABLEF, "size": 10, "align": "center", "after_pt": 10, "color": "#404040"},
}
INLINE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|`([^`]+)`|\[([^\]]+)\]\([^)]+\)|(?<![\w*])\*([^*\s][^*]*?)\*(?![\w*])")
NONE = {"line": "none", "sides": "all"}


def runs(text):
    """한 줄 글 → [{text, bold}]: **굵게**·`코드`·[링크](주소)를 푼다."""
    out, pos = [], 0
    for m in INLINE.finditer(text):
        if m.start() > pos:
            out.append({"text": text[pos:m.start()]})
        if m.group(1) or m.group(2):
            out.append({"text": m.group(1) or m.group(2), "bold": True})
        elif m.group(5):
            out.append({"text": m.group(5), "italic": True})
        else:
            out.append({"text": m.group(3) or m.group(4)})
        pos = m.end()
    if pos < len(text):
        out.append({"text": text[pos:]})
    return [r for r in out if r["text"]] or [{"text": ""}]


def plain(s):
    s = re.sub(r"\*\*(.+?)\*\*|__(.+?)__|`([^`]+)`", lambda m: m.group(1) or m.group(2) or m.group(3), s)
    return re.sub(r"(?<![\w*])\*([^*\s][^*]*?)\*(?![\w*])", r"\1", s)


def blocks(md):
    """Markdown → [(종류, 값)]. 종류: h1~h3, p, bullet, number, quote, table, image, kpi."""
    lines = md.replace("\r\n", "\n").split("\n")
    out, i, para = [], 0, []

    def flush():
        if para:
            out.append(("p", " ".join(s.strip() for s in para)))
            para.clear()
    while i < len(lines):
        s = lines[i].rstrip()
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if s.strip().startswith("```kpi"):
            flush()
            rows = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                if lines[i].strip():
                    rows.append([c.strip() for c in lines[i].split("|")])
                i += 1
            out.append(("kpi", rows))
        elif m:
            flush()
            out.append((f"h{min(len(m.group(1)), 3)}", m.group(2).strip().strip("#").strip()))
        elif re.match(r"^\s*!\[[^\]]*\]\([^)]+\)\s*$", s):
            flush()
            mm = re.match(r"^\s*!\[([^\]]*)\]\(([^)]+)\)", s)
            out.append(("image", (mm.group(1), mm.group(2))))
        elif re.match(r"^\s*\|.*\|\s*$", s):
            flush()
            rows = []
            while i < len(lines) and re.match(r"^\s*\|.*\|\s*$", lines[i]):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.match(r"^:?-{2,}:?$", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            n = max(len(r) for r in rows)
            out.append(("table", [r + [""] * (n - len(r)) for r in rows]))
            continue
        elif re.match(r"^\s*[-*+]\s+", s):
            flush()
            out.append(("bullet", re.sub(r"^\s*[-*+]\s+", "", s)))
        elif re.match(r"^\s*\d+[.)]\s+", s):
            flush()
            out.append(("number", s.strip()))
        elif s.startswith(">"):
            flush()
            out.append(("quote", s.lstrip("> ").strip()))
        elif re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", s) or not s.strip():
            flush()
        else:
            para.append(s)
        i += 1
    flush()
    return out


def col_widths(rows, total_mm):
    """칸 너비: 칸마다 글 길이에 비례(최소 16mm)."""
    n = len(rows[0])
    need = [max(min(len(r[c]), 60) for r in rows) + 3 for c in range(n)]
    # 칸 최소 너비: 본문 줄(머리 줄 빼고)의 가장 긴 어절이 한 줄에 들어가게(9.5pt 한글 약 3.6mm·영숫자 약 2mm + 칸 여백 5mm, 40mm까지)
    def word_mm(wd):
        return sum(3.6 if ord(ch) > 0x2E80 else 2.0 for ch in wd)
    low = [min(40.0, max(16.0, max((word_mm(wd) for r in rows[1:] for wd in r[c].split()), default=0) + 5)) for c in range(n)]
    w = [max(low[c], total_mm * need[c] / sum(need)) for c in range(n)]
    over = sum(w) - total_mm                 # 넘친 만큼은 최소보다 넓은 칸에서 덜어 낸다
    room = [w[c] - low[c] for c in range(n)]
    if over > 0 and sum(room) > over:
        w = [w[c] - over * room[c] / sum(room) for c in range(n)]
    k = total_mm / sum(w)
    return [round(x * k, 1) for x in w]


def cell(c, r):
    return f"{chr(65 + c)}{r + 1}"


class Ops:
    """ops와 표 번호(문서 순서·칸 안 표 포함)를 함께 센다."""
    def __init__(self, width_mm, base_dir):
        self.ops, self.n, self.w, self.dir, self.fig = [], 0, width_mm, base_dir, 0
        self.page_break = False                  # 장마다 새 쪽으로 넘기지 않는다(이어 쓴다)

    def add(self, *o):
        self.ops.extend(o)

    def table(self, data, widths, **kw):
        self.n += 1
        kw.setdefault("inline", True)
        self.add({"op": "table.create", "at": "end", "data": data, "col_widths_mm": widths, **kw})
        return self.n

    def nested(self, outer, cell_ref, data, widths, **kw):
        self.n += 1
        kw.setdefault("inline", True)
        self.add({"op": "table.create", "table": outer, "cell": cell_ref, "data": data, "col_widths_mm": widths, **kw})
        return self.n

    # ── 꾸밈 ──
    def title(self, text, sub=None):
        t = self.table([[text]], [self.w], font=HEAD, size=22, color=NAVY, align="center", pad_pt=10, table_align="center")
        self.add({"op": "table.border", "table": t, "cells": "A1", **NONE},
                 {"op": "table.border", "table": t, "cells": "A1", "line": "solid", "width": "0.7mm", "color": NAVY, "sides": "top,bottom"},
                 {"op": "table.fill", "table": t, "cells": "A1", "color": "#F3F6FB"})
        if sub:
            self.add({"op": "p", "runs": runs(sub), **STYLE["subtitle"]})

    def chapter(self, text):
        m = re.match(r"^(\d+)[.)]?\s*(.*)$", text)
        num, name = (m.group(1), m.group(2)) if m else ("■", text)
        t = self.table([[num, plain(name)]], [12, self.w - 12], font=HEAD, size=14, color=NAVY, pad_pt=3,
                       table_align="left", keep_with_next=True, page_break=self.page_break)
        self.add({"op": "table.border", "table": t, "cells": "A1:B1", **NONE},
                 {"op": "table.fill", "table": t, "cells": "A1", "color": NAVY},
                 {"op": "char", "table": t, "cells": "A1", "color": "#FFFFFF"},
                 {"op": "table.align", "table": t, "cells": "A1", "h": "center", "v": "middle"},
                 {"op": "table.align", "table": t, "cells": "B1", "h": "left", "v": "middle"},
                 {"op": "table.border", "table": t, "cells": "B1", "line": "solid", "width": "0.5mm", "color": NAVY, "sides": "bottom"})

    def data_table(self, rows):
        data = [[plain(c) for c in r] for r in rows]
        nr, nc = len(data), len(data[0])
        last = cell(nc - 1, nr - 1)
        t = self.table(data, col_widths(data, self.w), font=TABLEF, size=9.5, header=True, header_fill=LIGHT,
                       align="left", word_wrap="word", pad_pt=2.5, table_align="center", inline=False)
        r = 1                                    # 첫 칸이 빈 줄은 위 분류 칸과 세로로 합친다(원문의 세로 합친 칸)
        while r < nr:
            k = r + 1
            while k < nr and not data[k][0].strip():
                k += 1
            if data[r][0].strip() and k - 1 > r:
                self.add({"op": "table.merge", "table": t, "cells": f"A{r + 1}:A{k}"},
                         {"op": "table.align", "table": t, "cells": f"A{r + 1}", "h": "center", "v": "middle"})
            r = k
        self.add({"op": "table.border", "table": t, "cells": f"A1:{last}", "line": "solid", "width": "0.12mm", "color": GRAY, "sides": "all"},
                 {"op": "table.border", "table": t, "cells": f"A1:{last}", "line": "solid", "width": "0.5mm", "color": NAVY, "sides": "top,bottom"},
                 {"op": "table.border", "table": t, "cells": f"A1:{last}", "line": "none", "sides": "left,right"},
                 {"op": "table.border", "table": t, "cells": f"A1:{cell(nc - 1, 0)}", "line": "solid", "width": "0.3mm", "color": NAVY, "sides": "bottom"},
                 {"op": "char", "table": t, "cells": f"A1:{cell(nc - 1, 0)}", "color": NAVY})
        if nr > 1:
            self.add({"op": "char", "table": t, "cells": f"A2:{cell(0, nr - 1)}", "bold": True},
                     {"op": "table.fill", "table": t, "cells": f"A2:{cell(0, nr - 1)}", "color": "#F5F7FA"})

    def callout(self, text):
        t = self.table([[" ", text]], [2.5, self.w - 2.5], font=BODYF, size=11.5, pad_pt=4, word_wrap="word", table_align="center")
        self.add({"op": "table.border", "table": t, "cells": "A1:B1", **NONE},
                 {"op": "table.fill", "table": t, "cells": "A1", "color": ACCENT},
                 {"op": "table.fill", "table": t, "cells": "B1", "color": "#FDF3EA"},
                 {"op": "char", "table": t, "cells": "B1", "color": "#3B2A1A"})

    def kpi(self, rows):
        """「핵심 지표」 상자: 바깥 표(제목 줄 + 칸) 안에 지표 표를 넣는다."""
        n = len(rows)
        outer = self.table([["핵심 지표"], [""]], [self.w], font=HEAD, size=13, color="#FFFFFF", pad_pt=4, table_align="center")
        self.add({"op": "table.fill", "table": outer, "cells": "A1", "color": NAVY},
                 {"op": "table.border", "table": outer, "cells": "A1:A2", "line": "solid", "width": "0.5mm", "color": NAVY, "sides": "outside"},
                 {"op": "table.fill", "table": outer, "cells": "A2", "color": "#F3F6FB"})
        vals = [r[0] for r in rows]
        names = [r[1] if len(r) > 1 else "" for r in rows]
        notes = [r[2] if len(r) > 2 else "" for r in rows]
        w = round((self.w - 8) / n, 1)
        inner = self.nested(outer, "A2", [vals, names, notes], [w] * n, font=TABLEF, size=10, align="center", pad_pt=1.5)
        self.add({"op": "table.border", "table": inner, "cells": f"A1:{cell(n - 1, 2)}", **NONE},
                 {"op": "table.border", "table": inner, "cells": f"A1:{cell(n - 1, 2)}", "line": "solid", "width": "0.12mm", "color": "#C9D3E3", "sides": "vert"},
                 {"op": "char", "table": inner, "cells": f"A1:{cell(n - 1, 0)}", "font": HEAD, "size": 22, "color": ACCENT},
                 {"op": "char", "table": inner, "cells": f"A2:{cell(n - 1, 1)}", "bold": True, "color": NAVY},
                 {"op": "char", "table": inner, "cells": f"A3:{cell(n - 1, 2)}", "size": 9, "color": "#595959"})
        self.add({"op": "p", "text": "", "size": 6})

    def image(self, cap, path):
        p = path if os.path.isabs(path) else os.path.join(self.dir, path)
        self.fig += 1
        self.add({"op": "p", "text": "", "align": "center", "size": 4, "keep_with_next": True},     # 그림은 캡션과 같은 쪽에
                 {"op": "image", "at": "end", "path": p, "width_mm": min(self.w, 150)},
                 {"op": "p", "runs": [{"text": f"<그림 {self.fig}> ", "bold": True}, {"text": cap}], **STYLE["caption"]})


def to_ops(md, header=None, base_dir=".", width_mm=148):
    bs = blocks(md)
    o, title = Ops(width_mm, base_dir), None
    if bs and bs[0][0] == "h1":
        title = plain(bs.pop(0)[1])
        sub = bs.pop(0)[1] if bs and bs[0][0] == "p" and len(bs[0][1]) < 80 else None
        o.title(title, sub)
    for kind, v in bs:
        if kind in ("h1", "h2"):
            o.chapter(v)
        elif kind == "h3":
            o.add({"op": "p", "runs": [{"text": "□ "}] + [dict(r, bold=True) for r in runs(plain(v))], **STYLE[3]})
        elif kind == "p":
            o.add({"op": "p", "runs": runs(v), **BODY})
        elif kind == "bullet":
            o.add({"op": "p", "runs": [{"text": "○ "}] + runs(v), **STYLE["bullet"]})
        elif kind == "number":
            o.add({"op": "p", "runs": runs(v), **STYLE["number"]})
        elif kind == "quote":
            o.callout(plain(v))
        elif kind == "table":
            o.data_table(v)
        elif kind == "image":
            o.image(*v)
        elif kind == "kpi":
            o.kpi(v)
    o.add({"op": "header", "text": header or title or "", "align": "right"})
    o.add({"op": "page_number", "pos": "bottom-center", "format": "digit", "side": "-"})
    return o.ops


# ── 논문 모양(--style paper) ──
# 앞머리(--- 사이 `키: 값`): title, title_en, author, running, abstract, keywords, abstract_en, keywords_en
# 본문은 2단. `## 1. 서론` 절, `### 1.1 ...` 소절, `[^1]` 각주(`[^1]: 내용`), 표 바로 위 `<표 n> 제목` 줄은 표 제목,
# 표 바로 아래 `자료: ...` 줄은 출처, `![제목](그림.png)` 그림 제목은 아래, `## 참고문헌` 아래 목록은 내어쓰기.
PAPER_BODY = {"font": BODYF, "size": 10, "align": "justify", "line_spacing": 160, "indent_mm": 3.5, "word_wrap": "word"}
PAPER_W, PAPER_GAP = 170, 8
NOTE_REF = re.compile(r"\[\^(\w+)\]")


def front_matter(md):
    md = md.replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n", md, re.S)
    if not m:
        return {}, md
    meta = {}
    for line in m.group(1).split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, md[m.end():]


class PaperOps(Ops):
    col_w = (PAPER_W - PAPER_GAP) / 2

    def abstract(self, head, text, kw_label, kw):
        rows = [[head], [text]] + ([[f"{kw_label}: {kw}"]] if kw else [])
        t = self.table(rows, [PAPER_W], font=BODYF, size=9.5, pad_pt=1.5, word_wrap="word", table_align="center")
        last = f"A{len(rows)}"
        self.add({"op": "table.border", "table": t, "cells": f"A1:{last}", **NONE},
                 {"op": "table.border", "table": t, "cells": f"A1:{last}", "line": "solid", "width": "0.4mm", "color": "#000000", "sides": "top,bottom"},
                 {"op": "char", "table": t, "cells": "A1", "bold": True, "size": 10.5},
                 {"op": "table.align", "table": t, "cells": "A1", "h": "center"},
                 {"op": "para", "table": t, "cells": "A2", "align": "justify", "line_spacing": 150, "indent_mm": 3.5})
        if kw:
            self.add({"op": "char", "table": t, "cells": last, "font": TABLEF, "size": 9})
        self.add({"op": "p", "text": "", "size": 6})

    def booktabs(self, rows, caption, source):
        """3선 표: 위·아래 굵은 선, 머리줄 아래 가는 선, 세로선 없음. 표 제목은 위, 출처는 아래."""
        data = [[plain(c) for c in r] for r in rows]
        nr, nc = len(data), len(data[0])
        last = cell(nc - 1, nr - 1)
        head = f"A1:{cell(nc - 1, 0)}"
        if caption:
            self.add({"op": "p", "runs": caption, "font": TABLEF, "size": 9, "align": "center", "before_pt": 6, "after_pt": 3,
                      "keep_with_next": True, "keep_lines": True, "word_wrap": "word"})
        t = self.table(data, col_widths(data, self.col_w), font=TABLEF, size=8.5, align="left", word_wrap="word", pad_pt=1.2,
                       table_align="center", inline=False)
        self.add({"op": "table.border", "table": t, "cells": f"A1:{last}", **NONE},
                 {"op": "table.border", "table": t, "cells": f"A1:{last}", "line": "solid", "width": "0.4mm", "color": "#000000", "sides": "top,bottom"},
                 {"op": "table.border", "table": t, "cells": head, "line": "solid", "width": "0.12mm", "color": "#000000", "sides": "bottom"},
                 {"op": "char", "table": t, "cells": head, "bold": True},
                 {"op": "table.align", "table": t, "cells": head, "h": "center", "v": "middle"})
        if source:
            self.add({"op": "p", "text": source, "font": TABLEF, "size": 8, "after_pt": 8, "word_wrap": "word"})
        else:
            self.add({"op": "p", "text": "", "size": 6})

    def figure(self, cap, path):
        p = path if os.path.isabs(path) else os.path.join(self.dir, path)
        self.fig += 1
        self.add({"op": "p", "text": "", "align": "center", "size": 4, "keep_with_next": True},
                 {"op": "image", "at": "end", "path": p, "width_mm": self.col_w - 1},
                 {"op": "p", "runs": [{"text": f"<그림 {self.fig}> ", "bold": True}, {"text": cap}], "font": TABLEF, "size": 9,
                  "align": "center", "after_pt": 8, "word_wrap": "word", "keep_lines": True})


def to_paper_ops(md, header=None, base_dir="."):
    meta, md = front_matter(md)
    notes = {}

    def take_note(m):
        notes[m.group(1)] = m.group(2).strip()
        return ""
    md = re.sub(r"^\[\^(\w+)\]:\s*(.+)$", take_note, md, flags=re.M)
    o = PaperOps(PAPER_W, base_dir)
    # 각주는 전 단 가로지르기: 단마다(EACH_COLUMN)로 두면 1단 구역에 각주가 있을 때 한컴이 2단 구역을 다음 쪽으로 넘긴다(실측)
    o.add({"op": "page", "paper": "A4", "margins_mm": {"left": 20, "right": 20, "top": 20, "bottom": 18, "header": 10, "footer": 8},
           "footnote_place": "merged"})

    def para(text, style, columns=None):
        """글 한 문단 + 그 안의 각주들. 각주 자리 = 표시 바로 앞 글(문서에서 마지막으로 나온 곳)."""
        parts = NOTE_REF.split(text)
        op = {"op": "p", "runs": runs("".join(parts[0::2])), **style}
        if columns:
            op["columns"] = columns
        o.add(op)
        done = ""
        for k in range(1, len(parts), 2):
            done += parts[k - 1]
            before = plain(re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", done))[-14:]
            if parts[k] in notes and before.strip():
                o.add({"op": "footnote", "after": before, "nth": -1, "text": notes[parts[k]], "font": BODYF, "size": 8.5})

    o.add({"op": "p", "runs": [{"text": meta.get("title", "")}], "font": HEAD, "size": 18, "align": "center",
           "line_spacing": 140, "after_pt": 4, "word_wrap": "word"})
    if meta.get("title_en"):
        o.add({"op": "p", "text": meta["title_en"], "font": TABLEF, "size": 10.5, "align": "center", "color": "#404040",
               "after_pt": 10, "word_wrap": "word"})
    if meta.get("author"):
        para(meta["author"], {"font": BODYF, "size": 10.5, "align": "center", "after_pt": 14})
    if meta.get("abstract"):
        o.abstract("요     약", meta["abstract"], "주제어", meta.get("keywords"))
    if meta.get("abstract_en"):
        o.abstract("Abstract", meta["abstract_en"], "Keywords", meta.get("keywords_en"))

    bs = blocks(md)
    first, refs, caption = True, False, None
    i = 0
    while i < len(bs):
        kind, v = bs[i]
        nxt = bs[i + 1] if i + 1 < len(bs) else (None, "")
        cols = {"count": 2, "gap_mm": PAPER_GAP} if first else None
        if kind in ("h1", "h2"):
            refs = plain(v).replace(" ", "") in ("참고문헌", "References")
            para(plain(v), {"font": BODYF, "size": 11.5, "bold": True, "align": "center" if refs else "left",
                            "indent_mm": 0, "before_pt": 0 if first else 10, "after_pt": 5, "keep_with_next": True,
                            "word_wrap": "word"}, cols)
            first = False
        elif kind == "h3":
            para(plain(v), {"font": BODYF, "size": 10, "bold": True, "before_pt": 6, "after_pt": 3,
                            "keep_with_next": True, "word_wrap": "word"}, cols)
            first = False
        elif kind == "p" and re.match(r"^<표\s*\d+>", v) and nxt[0] == "table":
            m = re.match(r"^(<표\s*\d+>)\s*(.*)$", v)
            caption = [{"text": m.group(1) + " ", "bold": True}, {"text": plain(m.group(2))}]
        elif kind == "table":
            src = None
            if nxt[0] == "p" and nxt[1].startswith("자료"):
                src = plain(nxt[1])
                i += 1
            o.booktabs(v, caption, src)
            caption = None
        elif kind == "image":
            o.figure(*v)
        elif kind in ("bullet", "number") and refs:
            para(v if kind == "bullet" else re.sub(r"^\d+[.)]\s*", "", v),
                 {"font": BODYF, "size": 9, "align": "left", "line_spacing": 140, "left_mm": 6, "indent_mm": -6,
                  "after_pt": 3, "word_wrap": "word"}, cols)
            first = False
        elif kind == "bullet":
            para("· " + v, {**PAPER_BODY, "left_mm": 4, "indent_mm": -3}, cols)
            first = False
        elif kind == "number":
            para(v, {**PAPER_BODY, "left_mm": 5, "indent_mm": -4}, cols)
            first = False
        elif kind == "quote":
            para(v, {**PAPER_BODY, "size": 9.5, "left_mm": 5, "right_mm": 5, "indent_mm": 0}, cols)
            first = False
        else:
            para(v, PAPER_BODY, cols)
            first = False
        i += 1
    # 끝에 1단 구역을 새로 열면 한컴이 마지막 쪽의 두 단 높이를 맞춘다(다단 설정 나누기)
    o.add({"op": "p", "text": "", "size": 2, "columns": {"count": 1, "gap_mm": 0}})
    o.add({"op": "header", "text": header or meta.get("running") or meta.get("title", ""), "align": "right"})
    o.add({"op": "page_number", "pos": "bottom-center", "format": "digit"})
    return o.ops


def write(md, out_hwpx, header=None, base_dir=".", style="report"):
    """md 글 → HWPX 파일. style: report(보고서) · paper(2단 논문). 진행 줄을 yield."""
    ops = to_paper_ops(md, header, base_dir) if style == "paper" else to_ops(md, header, base_dir)
    yield from X.edit(None, out_hwpx, ops)
