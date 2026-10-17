"""시험지 만들기·점검·고치기 — 양식(설정 파일 forms/*.json)에 문항을 배정해 초안을 만들고, 사람(편집위원)이 한글에서 고친
편집본을 점검하고, 자동으로 고칠 수 있는 것은 고쳐 다시 조판한다. 사람이 고친 글·순서는 되돌리지 않는다.

  hwp_tool.py exam build 문항.json --form 양식설정.json --template 양식.hwp -o 시험지.hwp
  hwp_tool.py exam check 시험지.hwp --form 양식설정.json [-o 보고서.md]
  hwp_tool.py exam fix   편집본.hwp --form 양식설정.json -o 고친본.hwp [--report 보고서.md]
  hwp_tool.py exam diff  전.hwp 후.hwp --form 양식설정.json [-o 이력.csv]

보고서·이력에는 문항 글을 넣지 않고 번호·위치만 쓴다. 한컴이 필요한 단계(줄 배치 재기·저장)는 숨긴 데스크톱에서만 돈다.
"""
import argparse
import collections
import copy
import csv
import difflib
import json
import os
import re
import shutil
import sys
import tempfile
import time

from lxml import etree

from hwpx_direct import HH, HP, Hwpx, Item, explode, implode
import hwp_typeset as ts

HERE = os.path.dirname(os.path.abspath(__file__))
OPEN_OPT = ts.OPEN_OPT
MM = 283.46


# ───────────────────────── 설정 ─────────────────────────
def load_form(path):
    if not os.path.exists(path) and os.path.exists(os.path.join(HERE, "forms", path)):
        path = os.path.join(HERE, "forms", path)
    cfg = json.load(open(path, encoding="utf-8"))
    cfg["_num"] = re.compile(cfg["문항 번호"])
    cfg["_band"] = re.compile(cfg["과목 띠"])
    return cfg


# ───────────────────────── 표 도우미 ─────────────────────────
def band_text(p):
    """과목 띠 글: 글상자(사각형) 안 글 + 문단 자체 글(표 안 글은 빼고). 글상자 없는 양식도 띠 문단으로 과목을 나눈다."""
    rects = "".join(x.text or "" for rc in p.iter(HP + "rect") for x in rc.iter(HP + "t"))
    return rects + "".join(t.text or "" for r in p.findall(HP + "run") for t in r.findall(HP + "t"))


def cells(t):
    return [tc for tc in t.iter(HP + "tc") if next(tc.iterancestors(HP + "tbl")) is t]


def cell_at(t, c, r):
    for tc in cells(t):
        a = tc.find(HP + "cellAddr")
        if int(a.get("colAddr")) == c and int(a.get("rowAddr")) == r:
            return tc
    return None


def nested(tc):
    return [t for t in tc.iter(HP + "tbl") if next(t.iterancestors(HP + "tc")) is tc]


def text_of(el):
    return "".join(x.text or "" for x in el.iter(HP + "t")) if el is not None else ""


def paras_of(tc):
    return [p for p in tc.iter(HP + "p") if next(p.iterancestors(HP + "tc")) is tc]


def set_text(tc, lines):
    """칸 글을 lines(문단 목록)로 바꾼다. 첫 문단·첫 글 run의 모양을 쓴다."""
    sub = tc.find(HP + "subList")
    ps = sub.findall(HP + "p")
    proto = ps[0]
    run0 = next((r for r in proto.findall(HP + "run") if r.find(HP + "t") is not None), proto.find(HP + "run"))
    rattr = dict(run0.attrib) if run0 is not None else {"charPrIDRef": "0"}
    for p in ps:
        sub.remove(p)
    for line in lines:
        p = etree.SubElement(sub, HP + "p", dict(proto.attrib))
        r = etree.SubElement(p, HP + "run", rattr)
        etree.SubElement(r, HP + "t").text = line


def set_cell_text_keep(tc, s):
    """칸의 첫 문단 글만 s로(글자 모양 유지)."""
    p = paras_of(tc)[0]
    items = explode(p)
    ra = next((i.run for i in items if i.kind == "ch"), items[0].run if items else {"charPrIDRef": "0"})
    implode(p, [Item("ch", ch, ra) for ch in s] or [Item("empty", None, ra)])


# ───────────────────────── 문서 안 문항 읽기 ─────────────────────────
def items_in(d, cfg):
    """본문 구역의 문항(번호 칸이 '숫자.'인 표). 과목은 쪽 나눔 문단으로 나눈다. 편집본에서도 같은 방법으로 읽는다."""
    sec = d.sections[cfg["본문 구역"]]
    nc, sc, cc = cfg["문항 표"]["번호 칸"], cfg["문항 표"]["문두 칸"], cfg["문항 표"]["내용 칸"]
    out, subj = [], 1
    for bi, p in enumerate(sec.findall(HP + "p")):
        band = cfg["_band"].search(band_text(p))
        if band:
            subj = int(band.group(1))
        for r in p.findall(HP + "run"):
            for t in r.findall(HP + "tbl"):
                tn = cell_at(t, *nc)
                m = cfg["_num"].match(text_of(tn).strip()) if tn is not None else None
                if not m:
                    continue
                cont = cell_at(t, *cc)
                inner = nested(cont) if cont is not None else []
                vrc, hrc = tuple(cfg["보기 표"]["세로"]), tuple(cfg["보기 표"]["가로"])
                choice = next((x for x in inner if (int(x.get("rowCnt")), int(x.get("colCnt"))) in (vrc, hrc)), None)
                brc = tuple(cfg["상자 표"]["크기"])
                box = next((x for x in inner if (int(x.get("rowCnt")), int(x.get("colCnt"))) == brc
                            and cfg["상자 표"]["제목"] in text_of(cell_at(x, *cfg["상자 표"]["제목 칸"]))), None)
                data = [x for x in inner if x is not choice and x is not box]
                out.append({"para": p, "bi": bi, "table": t, "num": int(m.group(1)), "subject": subj,
                            "num_cell": tn, "stem": paras_of(cell_at(t, *sc)), "content": cont,
                            "choice": choice, "box": box, "data": data})
    by = collections.defaultdict(int)
    for it in out:
        by[it["subject"]] += 1
        it["order"] = by[it["subject"]]
    return out


def choices_of(it, cfg):
    t = it["choice"]
    if t is None:
        return []
    rc = (int(t.get("rowCnt")), int(t.get("colCnt")))
    out = []
    if rc == tuple(cfg["보기 표"]["세로"]):
        for i in range(rc[0]):
            out.append((text_of(cell_at(t, 0, i)).strip(), text_of(cell_at(t, 1, i)).strip()))
    else:
        for i in range(rc[0] * rc[1] // 2):
            out.append((text_of(cell_at(t, (i % 2) * 2, i // 2)).strip(), text_of(cell_at(t, (i % 2) * 2 + 1, i // 2)).strip()))
    return out


def label(it):
    return f"{it['subject']}과목 {it['order']}번"


def is_item_para(cfg):
    nc = cfg["문항 표"]["번호 칸"]

    def f(p):
        for r in p.findall(HP + "run"):
            for t in r.findall(HP + "tbl"):
                tn = cell_at(t, *nc)
                if tn is not None and cfg["_num"].match(text_of(tn).strip()):
                    return True
        return False
    return f


def scope_fn(cfg):
    def f(d):
        keep = [q for it in items_in(d, cfg) for q in it["table"].iter(HP + "p")]
        s = set(keep)
        return [k for k, p in enumerate(d.all_paras()) if p in s]
    return f


def box_paras(it):
    return list(it["box"].iter(HP + "p")) if it["box"] is not None else []


# ───────────────────────── 글자 모양 ─────────────────────────
def cp_info(d, cpid):
    cache = d.__dict__.setdefault("_cpinfo", {})
    key = cpid
    if key in cache:
        return cache[key]
    faces = {}
    for ff in d.header.iter(HH + "fontface"):
        if ff.get("lang") == "HANGUL":
            faces = {f.get("id"): f.get("face") for f in ff.findall(HH + "font")}
    info = None
    for c in d.header.iter(HH + "charPr"):
        if c.get("id") == cpid:
            fr = c.find(HH + "fontRef")
            u = c.find(HH + "underline")
            info = {"face": faces.get(fr.get("hangul")) if fr is not None else None, "size": int(c.get("height")),
                    "spacing": int(c.find(HH + "spacing").get("hangul")), "bold": c.find(HH + "bold") is not None,
                    "underline": u is not None and u.get("type") not in (None, "NONE"), "italic": c.find(HH + "italic") is not None}
    cache[key] = info
    return info


def role_paras(it):
    """역할별 문단: 번호·문두·보기 글·상자 글."""
    out = {"번호": paras_of(it["num_cell"]), "문두": it["stem"], "보기": [], "상자": box_paras(it)}
    t = it["choice"]
    if t is not None:
        rc = (int(t.get("rowCnt")), int(t.get("colCnt")))
        for tc in cells(t):
            a = tc.find(HP + "cellAddr")
            c = int(a.get("colAddr"))
            is_text = c == 1 if rc[1] == 2 else c % 2 == 1
            if is_text:
                out["보기"] += paras_of(tc)
    return out


def role_majority(d, its):
    """역할마다 가장 많이 쓴 (글꼴, 크기, 자간)과 그 글자 모양 id."""
    cnt = {r: collections.Counter() for r in ("번호", "문두", "보기", "상자")}
    ids = {r: {} for r in cnt}
    for it in its:
        for role, ps in role_paras(it).items():
            for p in ps:
                for i in explode(p):
                    if i.kind == "ch" and not i.val.isspace():
                        cp = i.run.get("charPrIDRef", "0")
                        inf = cp_info(d, cp)
                        k = (inf["face"], inf["size"], inf["spacing"])
                        cnt[role][k] += 1
                        if not inf["bold"] and not inf["underline"]:
                            ids[role].setdefault(k, cp)
    out = {}
    for role, c in cnt.items():
        if c:
            k = c.most_common(1)[0][0]
            out[role] = {"face": k[0], "size": k[1], "spacing": k[2], "cp": ids[role].get(k)}
    return out


# ───────────────────────── 만들기(초안) ─────────────────────────
def grid(title, heads, cells_, label_row=False):
    span = [len(c) if label_row else 1 for c in cells_]
    n = sum(span)
    mk = lambda text, cs=1, rs=1, fill=False: {"text": text, "cs": cs, "rs": rs, "fill": fill}
    rows = []
    if title:
        rows.append([mk([title], cs=n, fill=True)])
    rows.append([mk([h], cs=s, fill=True) for h, s in zip(heads, span)])
    if not label_row:
        rows.append([mk(c) for c in cells_])
    else:
        top, low = [], []
        for c in cells_:
            if len(c) == 1:
                top.append(mk(c, rs=2))
                continue
            for part in c:
                m = re.match(r"^(.*\S)\s+([가-하])$", part)
                top.append(mk([m.group(1)]))
                low.append(mk([m.group(2)]))
        rows += [top, low]
    return {"rows": rows, "ncol": n}


def _col_widths(placed, n, width, margins=(141, 85), sizes=(9, 8.5, 8, 7.5)):
    def wlen(s):
        return sum(0.55 if ord(ch) < 128 else 1 for ch in s)
    longest, total = [1] * n, [1] * n
    for r, c, x in placed:
        if x["cs"] == 1:
            for line in x["text"]:
                longest[c] = max([longest[c]] + [wlen(w) for w in line.split()])
                if not x["fill"]:
                    total[c] += wlen(line)
    for size in sizes:
        for margin in margins:
            mins = [l * size * 100 + 2 * margin for l in longest]
            if sum(mins) <= width:
                break
        else:
            continue
        break
    extra = width - sum(mins)
    cw = [int(m + extra * t / sum(total)) if extra > 0 else int(m * width / sum(mins)) for m, t in zip(mins, total)]
    cw[-1] += width - sum(cw)
    return cw, size, margin


def make_table(d, spec, width, cp, pp, head_fill="#F2F2F2"):
    """상자 대신 넣는 자료 표(합친 칸 가능). 칸 글은 가운데 정렬·어절 단위 줄 나눔, 열 너비는 내용에 맞춤."""
    from hwpx_direct import rid
    rows, n = spec["rows"], spec["ncol"]
    taken, placed = set(), []
    for r, row in enumerate(rows):
        c = 0
        for x in row:
            while (r, c) in taken:
                c += 1
            placed.append((r, c, x))
            taken.update((r + i, c + j) for i in range(x["rs"]) for j in range(x["cs"]))
            c += x["cs"]
    cws, size, margin = _col_widths(placed, n, width)
    cp = d.charpr(cp, {"size": size})
    pp = d.parapr(pp, {"align": "center", "word_wrap": "word"})
    bf = d.solid_borderfill()
    hbf = d.borderfill(bf, fill=head_fill)
    tbl = etree.Element(HP + "tbl", {"id": rid(), "zOrder": "0", "numberingType": "TABLE", "textWrap": "TOP_AND_BOTTOM",
                                     "textFlow": "BOTH_SIDES", "lock": "0", "dropcapstyle": "None", "pageBreak": "CELL",
                                     "repeatHeader": "0", "rowCnt": str(len(rows)), "colCnt": str(n), "cellSpacing": "0",
                                     "borderFillIDRef": bf, "noAdjust": "0"})
    etree.SubElement(tbl, HP + "sz", {"width": str(sum(cws)), "widthRelTo": "ABSOLUTE", "height": str(282 * len(rows)),
                                      "heightRelTo": "ABSOLUTE", "protect": "0"})
    etree.SubElement(tbl, HP + "pos", {"treatAsChar": "1", "affectLSpacing": "0", "flowWithText": "1", "allowOverlap": "0",
                                       "holdAnchorAndSO": "0", "vertRelTo": "PARA", "horzRelTo": "PARA", "vertAlign": "TOP",
                                       "horzAlign": "LEFT", "vertOffset": "0", "horzOffset": "0"})
    etree.SubElement(tbl, HP + "outMargin", {"left": "0", "right": "0", "top": "141", "bottom": "141"})
    etree.SubElement(tbl, HP + "inMargin", {"left": str(margin), "right": str(margin), "top": "141", "bottom": "141"})
    trs = [etree.SubElement(tbl, HP + "tr") for _ in rows]
    for r, c, x in placed:
        tc = etree.SubElement(trs[r], HP + "tc", {"name": "", "header": "0", "hasMargin": "0", "protect": "0", "editable": "0",
                                                  "dirty": "0", "borderFillIDRef": hbf if x["fill"] else bf})
        sub = etree.SubElement(tc, HP + "subList", {"id": "", "textDirection": "HORIZONTAL", "lineWrap": "BREAK",
                                                    "vertAlign": "CENTER", "linkListIDRef": "0", "linkListNextIDRef": "0",
                                                    "textWidth": "0", "textHeight": "0", "hasTextRef": "0", "hasNumRef": "0"})
        for line in x["text"]:
            p = etree.SubElement(sub, HP + "p", {"id": "0", "paraPrIDRef": pp, "styleIDRef": "0", "pageBreak": "0",
                                                 "columnBreak": "0", "merged": "0"})
            ra = {"charPrIDRef": cp}
            implode(p, [Item("ch", ch, ra) for ch in line] or [Item("empty", None, ra)])
        etree.SubElement(tc, HP + "cellAddr", {"colAddr": str(c), "rowAddr": str(r)})
        etree.SubElement(tc, HP + "cellSpan", {"colSpan": str(x["cs"]), "rowSpan": str(x["rs"])})
        etree.SubElement(tc, HP + "cellSz", {"width": str(sum(cws[c:c + x["cs"]])), "height": str(282 * x["rs"])})
        etree.SubElement(tc, HP + "cellMargin", {"left": str(margin), "right": str(margin), "top": "141", "bottom": "141"})
    return tbl


def _template_parts(d, cfg):
    """양식에서 문항 원형(글자처럼 취급하는 문항 표 + 그 문단), 세로·가로 보기 표, 「보기」 상자, 과목 띠를 찾는다."""
    sec = d.sections[cfg["본문 구역"]]
    nc = cfg["문항 표"]["번호 칸"]
    vrc, hrc, brc = tuple(cfg["보기 표"]["세로"]), tuple(cfg["보기 표"]["가로"]), tuple(cfg["상자 표"]["크기"])
    blocks = []
    for t in sec.iter(HP + "tbl"):
        if any(a.tag == HP + "tbl" for a in t.iterancestors()):
            continue
        tn = cell_at(t, *nc)
        if tn is not None and cfg["_num"].match(text_of(tn).strip()):
            blocks.append(t)
    proto = cv = ch = box = None
    for t in blocks:
        cont = cell_at(t, *cfg["문항 표"]["내용 칸"])
        for nt in nested(cont) if cont is not None else []:
            rc = (int(nt.get("rowCnt")), int(nt.get("colCnt")))
            if rc == vrc and cv is None and t.find(HP + "pos").get("treatAsChar") == "1":
                cv, proto = nt, t
            if rc == hrc and ch is None:
                ch = nt
            if rc == brc and box is None and cfg["상자 표"]["제목"] in text_of(cell_at(nt, *cfg["상자 표"]["제목 칸"])):
                box = nt
    if not (proto is not None and cv is not None and ch is not None and box is not None):
        raise ValueError("양식에서 문항 원형·보기 표·「보기」 상자를 찾지 못했습니다(설정 파일의 표 크기·칸 위치를 확인)")
    return blocks, proto, cv, ch, box


def assemble(template_hwpx, data, cfg, out):
    """양식(HWPX)에 문항을 배정해 초안을 만든다(조판 전). data = {"과목": [[문항, …], …]}.
    문항 = {"문두": 글, "상자": [줄…] | null, "표": {"제목", "머리", "칸", "아래 줄"} | null, "보기": [글×4]}"""
    d = Hwpx(template_hwpx)
    sec = d.sections[cfg["본문 구역"]]
    blocks, proto, cv, ch, box = _template_parts(d, cfg)
    proto_para = next(proto.iterancestors(HP + "p"))
    tc_ = cfg["상자 표"]["글 칸"]
    box_p = paras_of(cell_at(box, *tc_))[0]
    box_pp_plain = d.parapr(box_p.get("paraPrIDRef"), {"indent_mm": 0, "left_mm": 0})
    box_cp = next(r for r in box_p.iter(HP + "run")).get("charPrIDRef")
    banners = {}
    for p in sec.findall(HP + "p"):
        m = cfg["_band"].search(band_text(p))
        if m and int(m.group(1)) >= 2:
            b = copy.deepcopy(p)
            for r in b.findall(HP + "run"):
                for el in list(r):
                    if el.tag == HP + "tbl":
                        r.remove(el)
            banners[int(m.group(1))] = b
            if not any(t in blocks for t in p.iter(HP + "tbl")):
                p.getparent().remove(p)
    first_slot = None
    for p in sec.findall(HP + "p"):
        bt = [t for t in p.iter(HP + "tbl") if t in blocks]
        if not bt:
            continue
        if any(el.tag in (HP + "secPr", HP + "ctrl") for r in p.findall(HP + "run") for el in r):
            for t in bt:
                t.getparent().remove(t)
            first_slot = p
        else:
            if first_slot is None:
                first_slot = p.getprevious()
            p.getparent().remove(p)
    body = sec.findall(HP + "p")
    for p in body[body.index(first_slot) + 1:-1]:
        if not text_of(p).strip() and p.find(f".//{HP}tbl") is None and not any(
                el.tag in (HP + "ctrl", HP + "secPr") for r in p.findall(HP + "run") for el in r):
            sec.remove(p)
    item_pp = d.parapr(proto_para.get("paraPrIDRef"), {"after_pt": cfg["문항 간격 pt"]})
    marks = cfg["보기 기호"]
    short_n = cfg["보기 표"]["가로로 둘 보기 글자 수"]
    anchor, n = first_slot, 0
    for k, group in enumerate(data["과목"], 1):
        if k in banners:
            b = copy.deepcopy(banners[k])
            anchor.addnext(b)
            anchor = b
        for kn, it in enumerate(group, 1):
            n += 1
            t = copy.deepcopy(proto)
            for tr in t.findall(HP + "tr")[2:]:
                t.remove(tr)
            t.set("rowCnt", "2")
            t.set("pageBreak", "NONE")
            t.set("id", str(1000000 + n))
            for tc in cells(t):
                tc.find(HP + "cellSz").set("height", "282")
            t.find(HP + "sz").set("height", "564")
            set_text(cell_at(t, *cfg["문항 표"]["번호 칸"]), [f"{kn}."])
            set_text(cell_at(t, *cfg["문항 표"]["문두 칸"]), [it["문두"]])
            cont = cell_at(t, *cfg["문항 표"]["내용 칸"])
            sub = cont.find(HP + "subList")
            cps = sub.findall(HP + "p")
            pproto = next((p for p in cps if p.find(f".//{HP}tbl") is not None), cps[0])
            for p in cps:
                sub.remove(p)

            def holder(obj):
                ph = copy.deepcopy(pproto)
                for r in ph.findall(HP + "run"):
                    for el in list(r):
                        if el.tag == HP + "tbl":
                            r.remove(el)
                ph.find(HP + "run").insert(0, obj)
                sub.append(ph)
            if it.get("표"):
                tb = it["표"]
                spec = grid(tb.get("제목"), tb["머리"], tb["칸"], tb.get("아래 줄", False))
                holder(make_table(d, spec, int(box.find(HP + "sz").get("width")), box_cp, box_pp_plain))
            elif it.get("상자"):
                bx = copy.deepcopy(box)
                set_text(cell_at(bx, *tc_), it["상자"])
                for bp in paras_of(cell_at(bx, *tc_)):
                    if not ts.MARKER_RX.match(text_of(bp)):
                        bp.set("paraPrIDRef", box_pp_plain)
                holder(bx)
            choices = it["보기"]
            short = max(len(c) for c in choices) <= short_n
            ct = copy.deepcopy(ch if short else cv)
            for i, c in enumerate(choices):
                if short:
                    set_text(cell_at(ct, (i % 2) * 2 + 1, i // 2), [c])
                    set_text(cell_at(ct, (i % 2) * 2, i // 2), [marks[i]])
                else:
                    set_text(cell_at(ct, 0, i), [marks[i]])
                    set_text(cell_at(ct, 1, i), [c])
            for tc in cells(ct):
                tc.find(HP + "cellSz").set("height", "282")
            holder(ct)
            np_ = copy.deepcopy(proto_para)
            for r in np_.findall(HP + "run"):
                for el in list(r):
                    if el.tag in (HP + "tbl", HP + "ctrl", HP + "secPr"):
                        r.remove(el)
            np_.set("pageBreak", "0")
            np_.set("columnBreak", "0")
            np_.set("paraPrIDRef", item_pp)
            np_.find(HP + "run").insert(0, t)
            anchor.addnext(np_)
            anchor = np_
    for ls in list(sec.iter(HP + "linesegarray")):
        ls.getparent().remove(ls)
    for s_ in d.sections:
        for tt in s_.iter(HP + "t"):
            if tt.text:
                for a, b in cfg.get("표지 바꿀 글", []):
                    tt.text = tt.text.replace(a, b)
    d.save(out)
    return n


# ───────────────────────── 조판 ─────────────────────────
def set_totals(d, cfg, total):
    pt = cfg["쪽수 표기"]
    n = 0
    for sec in d.sections:
        for f in sec.iter(HP + "footer"):
            for tt in f.iter(HP + "t"):
                if tt.text and re.match(pt["쪽 아래"], tt.text):
                    tt.text = re.sub(r"\d+", str(total), tt.text)
                    n += 1
        for tt in sec.iter(HP + "t"):
            if tt.text and re.search(pt["표지"], tt.text):
                tt.text = re.sub(pt["표지"], lambda m: m.group(0).replace(m.group(1), str(total)), tt.text)
                n += 1
    return n


def typeset(hwp, src, out, cfg, work, note=lambda s: None):
    """부정어 강조 → 여기까지 내어쓰기 → 낱말 끊김 자간 맞춤 → 내어쓰기 다시 → 단 채우기 → 쪽수 표기. out은 한컴이 저장한 HWPX."""
    res = {}
    d = Hwpx(src)
    its = items_in(d, cfg)
    res["부정어 강조"] = dict(zip(("문항", "곳"), ts.emphasize(d, [p for it in its for p in it["stem"]])))
    marks = [(t, n) for it in its for _, t, n in ts.marker_paras(d, box_paras(it))]
    neg = os.path.join(work, "t_neg.hwpx")
    d.save(neg)
    scope = scope_fn(cfg)
    lim = cfg.get("자간 한도", {})
    kw = {"max_pull": lim.get("줄이기", 10), "max_push": lim.get("늘리기", 10), "log": note}
    cur = os.path.join(work, "t_hang1.hwpx")
    res["내어쓰기"] = ts.hang_markers(hwp, neg, cur, marks)
    for rnd in (1, 2):
        nxt = os.path.join(work, f"t_fit{rnd}.hwpx")
        res[f"낱말 맞춤 {rnd}"] = {k: v for k, v in ts.fit_words(hwp, cur, nxt, scope, **kw).items() if k != "못 맞춘 곳"}
        cur = os.path.join(work, f"t_hang{rnd + 1}.hwpx")
        ts.hang_markers(hwp, nxt, cur, marks)
        n, _ = ts.count_breaks(Hwpx(cur), scope(Hwpx(cur)))
        if not n["split"] and not n["widow"]:
            break
    bal = os.path.join(work, "t_bal.hwpx")
    r = ts.balance_columns(hwp, cur, bal, section=cfg["본문 구역"], is_item=is_item_para(cfg), ncol=cfg["단 수"], log=note)
    res["단 채우기"] = {k: r[k] for k in ("단 수", "단 나눔", "나눔 기록", "단 구성 같음")}
    pages = ts.relayout(hwp, bal, os.path.join(work, "t_pages.hwpx"))
    d = Hwpx(bal)
    res["쪽수 표기"] = set_totals(d, cfg, pages - cfg["쪽수 표기"]["본문 밖 쪽"])
    tot = os.path.join(work, "t_total.hwpx")
    d.save(tot)
    res["쪽수"] = ts.relayout(hwp, tot, out)
    return res


def save_as(hwp, src, out):
    if not hwp.Open(os.path.abspath(src), "", OPEN_OPT):
        raise RuntimeError("한컴이 열지 못함: " + src)
    fmt = "HWP" if out.lower().endswith(".hwp") else "HWPX"
    hwp.SaveAs(os.path.abspath(out), fmt, "")
    hwp.Clear(1)


# ───────────────────────── 점검 ─────────────────────────
def where_map(d, cfg):
    """본문 문단 번호 → (쪽, 단). 쪽은 본문 첫 쪽이 1."""
    sec = d.sections[cfg["본문 구역"]]
    cols = ts.columns(sec, is_item_para(cfg))
    pages = ts.pages_of(cols, cfg["단 수"])
    out = {}
    for pi, pg in enumerate(pages, 1):
        for k, ci in enumerate(pg, 1):
            for c in cols[ci]:
                out[c[0]] = (pi, k)
    return out, cols, pages


def split_pages(hwp):
    """한컴 목록을 훑어 문항 표마다 처음·끝 쪽. → {본문 문단 번호: (처음 쪽, 끝 쪽)}"""
    def anchor(ctrl):
        s = ctrl.GetAnchorPos(0)
        return (s.Item("List"), s.Item("Para"), s.Item("Pos"))
    owner, where = {}, {}
    L = 1
    while hwp.SetPos(L, 0, 0) and hwp.GetPos()[0] == L:
        pc = hwp.ParentCtrl
        if pc is not None and pc.CtrlID == "tbl":
            a = anchor(pc)
            key = a if a[0] == 0 else owner.get(a[0])
            if key is not None:
                owner[L] = key
                where.setdefault(key, []).append(hwp.KeyIndicator()[3])
        L += 1
    return {k: (min(v), max(v)) for k, v in where.items()}


def check_laid(hwp, laid, cfg, work, with_indent=True, pages_total=None):
    """laid = 한컴이 저장한 HWPX(줄 배치 있음). → 점검 결과 dict(항목 → {통과, 수, 위치[], 자동})"""
    d = Hwpx(laid)
    its = items_in(d, cfg)
    sec = d.sections[cfg["본문 구역"]]
    wm, cols, pages = where_map(d, cfg)
    H = ts.body_height(sec)
    thr = cfg.get("단 아래 허용 mm", 3) * MM
    loc = lambda it: f"{label(it)}({wm.get(it['bi'], ('?', '?'))[0]}쪽 {wm.get(it['bi'], ('?', '?'))[1]}단)"
    R = {}

    def put(name, bad, auto, note=""):
        R[name] = {"통과": not bad, "수": len(bad), "위치": bad[:60], "자동 수정": auto, "참고": note}
    # 1 낱말 끊김
    allp = d.all_paras()
    pos = {p: k for k, p in enumerate(allp)}
    bad = []
    for it in its:
        for q in it["table"].iter(HP + "p"):
            for x in ts.issues_of(d, q)[1]:
                bad.append(f"{loc(it)} {'낱말 끊김' if x['kind'] == 'split' else '한두 글자 줄'}")
    put("낱말 끊김·한두 글자 줄", bad, True)
    # 2 문항 잘림
    bad = []
    for it in its:
        ls = ts.lines_of(it["para"])
        if ls and max(l["vertpos"] + l["vertsize"] for l in ls) > H:
            bad.append(loc(it) + " 단 넘침")
        if it["table"].get("pageBreak") != "NONE":
            bad.append(loc(it) + " 쪽 나눔 허용(잘릴 수 있음)")
    if hwp is not None:
        if hwp.Open(os.path.abspath(laid), "", OPEN_OPT):
            sp = split_pages(hwp)
            hwp.Clear(1)
            off = sum(len(d.sections[k].findall(HP + "p")) for k in range(cfg["본문 구역"]))   # 본문 목록 0은 구역을 이어 센다
            by_para = collections.defaultdict(list)
            for k, v in sp.items():
                if k[0] == 0:
                    by_para[k[1]].append(v)
            for it in its:
                if any(a != b for a, b in by_para.get(off + it["bi"], [])):
                    bad.append(loc(it) + " 쪽에 걸쳐 잘림")
    put("문항 잘림", bad, True)
    # 3·4 단 아래 여백, 가운데 선
    bad, line_bad = [], []
    for pi, pg in enumerate(pages, 1):
        fulls = []
        for k, ci in enumerate(pg, 1):
            free = H - max(c[2] for c in cols[ci])
            fulls.append(free <= thr)
            if free > thr:
                bad.append(f"{pi}쪽 {k}단 아래 {free / MM:.1f}mm")
        if not any(fulls):
            line_bad.append(f"{pi}쪽")
    put("단 아래 빈 여백", bad, True)
    put("가운데 선 쪽 끝까지", line_bad, True)
    # 5 부정어
    bad = []
    for it in its:
        for p in it["stem"]:
            items = explode(p)
            idx = [k for k, i in enumerate(items) if i.kind == "ch"]
            text = "".join(items[k].val for k in idx)
            for m in ts.NEGATIVE_RX.finditer(text):
                if any(not (cp_info(d, items[idx[j]].run.get("charPrIDRef", "0"))["bold"]
                            and cp_info(d, items[idx[j]].run.get("charPrIDRef", "0"))["underline"])
                       for j in range(m.start(), m.end()) if not text[j].isspace()):
                    bad.append(loc(it))
                    break
    put("부정어 밑줄·굵게", bad, True)
    # 6 내어쓰기
    bad = []
    marks = [(it, p, t, n) for it in its for p, t, n in ts.marker_paras(d, box_paras(it))]
    if hwp is not None and with_indent and marks:
        probe = os.path.join(work, "c_hang.hwpx")
        ts.hang_markers(hwp, laid, probe, [(t, n) for _, _, t, n in marks])
        d2 = Hwpx(probe)
        allp2 = d2.all_paras()
        for it, p, t, n in marks:
            q = allp2[pos[p]]
            a, b = _intent(d, p), _intent(d2, q)
            if abs(a - b) > 20:
                bad.append(f"{loc(it)} 상자 머리 글 줄")
    put("내어쓰기 시작 칸", bad, True, f"머리 글 문단 {len(marks)}개")
    # 7 번호
    bad = []
    by = collections.defaultdict(list)
    for it in its:
        by[it["subject"]].append(it)
    for k, group in by.items():
        for n, it in enumerate(group, 1):
            if it["num"] != n:
                bad.append(f"{label(it)} 번호가 {it['num']}")
    want = cfg.get("과목 문항 수")
    if want:
        for k, w in enumerate(want, 1):
            if len(by.get(k, [])) != w:
                bad.append(f"{k}과목 문항 수 {len(by.get(k, []))}(양식 {w})")
    put("문항 번호(과목별 1번부터·연속)", bad, True)
    # 8 보기
    bad = []
    marks_ok = cfg["보기 기호"]
    for it in its:
        ch = choices_of(it, cfg)
        if len(ch) != len(marks_ok):
            bad.append(f"{loc(it)} 보기 {len(ch)}개")
        elif "".join(m for m, _ in ch) != marks_ok:
            bad.append(f"{loc(it)} 보기 기호 순서")
        elif any(not t for _, t in ch):
            bad.append(f"{loc(it)} 빈 보기")
    put("보기 ①~④ 개수·순서", bad, False)
    # 9 쪽수 표기
    bad = []
    body_pages = pages_total - cfg["쪽수 표기"]["본문 밖 쪽"] if pages_total else len(pages)
    pt = cfg["쪽수 표기"]
    for s_ in d.sections:
        for tt in s_.iter(HP + "t"):
            if tt.text:
                m = re.search(pt["표지"], tt.text)
                if m and int(m.group(1)) != body_pages:
                    bad.append(f"표지 {m.group(1)}면(본문 {body_pages}쪽)")
        for f in s_.iter(HP + "footer"):
            for tt in f.iter(HP + "t"):
                m = re.match(pt["쪽 아래"], tt.text or "")
                if m and int(m.group(1)) != body_pages:
                    bad.append(f"쪽 아래 / {m.group(1)}(본문 {body_pages}쪽)")
    put("표지·쪽 아래 쪽수", sorted(set(bad)), True)
    # 10 글꼴·크기
    maj = role_majority(d, its)
    bad = []
    for it in its:
        for role, ps in role_paras(it).items():
            if role not in maj:
                continue
            want_ = maj[role]
            off = False
            for p in ps:
                for i in explode(p):
                    if i.kind == "ch" and not i.val.isspace():
                        inf = cp_info(d, i.run.get("charPrIDRef", "0"))
                        if inf["face"] != want_["face"] or inf["size"] != want_["size"]:
                            off = True
            if off:
                bad.append(f"{loc(it)} {role}")
    put("글꼴·크기", bad, True, "역할(번호·문두·보기·상자)마다 가장 많이 쓴 글꼴·크기와 비교")
    return R


def _intent(d, p):
    pid = p.get("paraPrIDRef")
    for pr in d.header.iter(HH + "paraPr"):
        if pr.get("id") == pid:
            m = pr.find(f".//{HP}case/{HH}margin")
            if m is None:
                m = pr.find(f".//{HH}margin")
            from hwpx_direct import HC
            return int(m.find(HC + "intent").get("value"))
    return 0


def report_md(title, R, extra=None):
    lines = [f"# {title}", "", "| 점검 | 결과 | 수 | 자동 수정 |", "|---|---|---|---|"]
    for k, v in R.items():
        lines.append(f"| {k} | {'통과' if v['통과'] else '실패'} | {v['수']} | {'예' if v['자동 수정'] else '아니오(사람이 볼 것)'} |")
    for k, v in R.items():
        if not v["통과"]:
            lines += ["", f"## {k}", ""] + [f"- {x}" for x in v["위치"]]
            if v["수"] > len(v["위치"]):
                lines.append(f"- … 외 {v['수'] - len(v['위치'])}곳")
    if extra:
        lines += ["", "## 한 일", ""] + [f"- {x}" for x in extra]
    lines += ["", "문항 글은 넣지 않았다(번호·위치만)."]
    return "\n".join(lines) + "\n"


# ───────────────────────── 고치기 ─────────────────────────
def normalize(d, cfg):
    """사람이 고친 글·순서는 그대로 두고, 조판 값만 깨끗이 되돌린다: 번호(과목별 1부터), 문항 간격·단 나눔,
    문항 안 자간(역할 기본값), 글꼴·크기(역할 다수값), 문항 표 쪽 나눔 금지. → 한 일 목록"""
    its = items_in(d, cfg)
    done = collections.Counter()
    by = collections.defaultdict(list)
    for it in its:
        by[it["subject"]].append(it)
    for group in by.values():
        for n, it in enumerate(group, 1):
            if it["num"] != n:
                set_cell_text_keep(it["num_cell"], f"{n}.")
                d.touch(paras_of(it["num_cell"])[0])
                done["번호 고침"] += 1
    gap = int(cfg["문항 간격 pt"] * 100)
    for it in its:
        p = it["para"]
        if ts._para_after(d, p.get("paraPrIDRef")) != gap:
            p.set("paraPrIDRef", d.parapr(p.get("paraPrIDRef"), {"after_pt": cfg["문항 간격 pt"]}))
        if p.get("columnBreak") == "1":
            p.set("columnBreak", "0")
        if it["table"].get("pageBreak") != "NONE":
            it["table"].set("pageBreak", "NONE")
            done["문항 표 쪽 나눔 금지"] += 1
        d.touch(p)
    maj = role_majority(d, its)
    for it in its:
        for role, ps in role_paras(it).items():
            if role not in maj or not maj[role]["cp"]:
                continue
            w = maj[role]
            for p in ps:
                items = explode(p)
                changed = False
                for i in items:
                    if i.kind != "ch":
                        continue
                    cp = i.run.get("charPrIDRef", "0")
                    inf = cp_info(d, cp)
                    if inf["face"] != w["face"] or inf["size"] != w["size"]:
                        new = d.charpr(w["cp"], {"bold": inf["bold"], "underline": inf["underline"], "italic": inf["italic"]})
                        done["글꼴·크기 맞춤(글자)"] += 1
                    elif inf["spacing"] != w["spacing"]:
                        new = d.charpr(cp, {"spacing": w["spacing"]})
                    else:
                        continue
                    i.run = {**i.run, "charPrIDRef": new}
                    changed = True
                if changed:
                    implode(p, items)
                    d.touch(p)
    for sec in d.sections:
        for ls in list(sec.iter(HP + "linesegarray")):
            ls.getparent().remove(ls)
    return dict(done)


def to_laid(hwp, src, work, name):
    """한컴으로 열어 HWPX로 다시 저장(줄 배치 채움). → (경로, 쪽수)"""
    out = os.path.join(work, name)
    return out, ts.relayout(hwp, src, out)


def cmd_check(a, cfg):
    from hwp_com import Session
    work = tempfile.mkdtemp(prefix="hwpexam_")
    with Session() as hwp:
        laid, pages = to_laid(hwp, a.file, work, "c_in.hwpx")
        R = check_laid(hwp, laid, cfg, work, pages_total=pages)
    shutil.rmtree(work, ignore_errors=True)
    md = report_md(f"점검: {os.path.basename(a.file)}", R)
    if a.output:
        open(a.output, "w", encoding="utf-8").write(md)
    print(md)
    return 0 if all(v["통과"] for v in R.values()) else 1


def cmd_fix(a, cfg):
    from hwp_com import Session
    t0 = time.time()
    work = tempfile.mkdtemp(prefix="hwpexam_")
    note = lambda s: print(s, file=sys.stderr)
    with Session() as hwp:
        laid, pages = to_laid(hwp, a.file, work, "f_in.hwpx")
        before = check_laid(hwp, laid, cfg, work, pages_total=pages)
        d = Hwpx(laid)
        done = normalize(d, cfg)
        norm = os.path.join(work, "f_norm.hwpx")
        d.save(norm)
        final = os.path.join(work, "f_final.hwpx")
        res = typeset(hwp, norm, final, cfg, work, note)
        after = check_laid(hwp, final, cfg, work, pages_total=res["쪽수"])
        save_as(hwp, final, a.output)
    shutil.rmtree(work, ignore_errors=True)
    extra = [f"{k}: {v}" for k, v in done.items()] + [
        f"부정어 강조 {res['부정어 강조']['문항']}문항", f"내어쓰기 {res['내어쓰기']}문단",
        f"쪽수 {res['쪽수']}쪽", f"걸린 시간 {time.time() - t0:.1f}초"]
    rows = ["| 점검 | 고치기 전 | 고친 뒤 |", "|---|---|---|"] + [
        f"| {k} | {'통과' if before[k]['통과'] else '실패 ' + str(before[k]['수'])} | {'통과' if after[k]['통과'] else '실패 ' + str(after[k]['수'])} |"
        for k in after]
    md = report_md(f"고친 뒤 점검: {os.path.basename(a.output)}", after, extra)
    md = md.replace("| 점검 | 결과 | 수 | 자동 수정 |", "\n".join(rows) + "\n\n| 점검 | 결과 | 수 | 자동 수정 |", 1)
    if a.report:
        open(a.report, "w", encoding="utf-8").write(md)
    print(md)
    return 0 if all(v["통과"] or not v["자동 수정"] for v in after.values()) else 1


def cmd_build(a, cfg):
    from hwp_com import Session
    t0 = time.time()
    data = json.load(open(a.items, encoding="utf-8"))
    work = tempfile.mkdtemp(prefix="hwpexam_")
    note = lambda s: print(s, file=sys.stderr)
    with Session() as hwp:
        thx = os.path.join(work, "양식.hwpx")
        save_as(hwp, a.template, thx)
        draft = os.path.join(work, "b_draft.hwpx")
        n = assemble(thx, data, cfg, draft)
        final = os.path.join(work, "b_final.hwpx")
        res = typeset(hwp, draft, final, cfg, work, note)
        R = check_laid(hwp, final, cfg, work, pages_total=res["쪽수"])
        save_as(hwp, final, a.output)
    shutil.rmtree(work, ignore_errors=True)
    extra = [f"문항 {n}개", f"부정어 강조 {res['부정어 강조']['문항']}문항", f"내어쓰기 {res['내어쓰기']}문단",
             f"단 나눔 {res['단 채우기']['나눔 기록']}", f"쪽수 {res['쪽수']}쪽", f"걸린 시간 {time.time() - t0:.1f}초"]
    md = report_md(f"초안 점검: {os.path.basename(a.output)}", R, extra)
    if a.report:
        open(a.report, "w", encoding="utf-8").write(md)
    print(md)
    return 0


# ───────────────────────── 편집 전·후 비교 ─────────────────────────
def snapshot(d, cfg):
    out = []
    for it in items_in(d, cfg):
        out.append({"label": label(it), "subject": it["subject"], "order": it["order"],
                    "stem": " ".join(text_of(p) for p in it["stem"]).strip(),
                    "choices": [t for _, t in choices_of(it, cfg)],
                    "marks": "".join(m for m, _ in choices_of(it, cfg)),
                    "box": "\n".join(text_of(p) for p in box_paras(it)),
                    "data": "\n".join(text_of(t) for t in it["data"])})
    return out


def diff_items(A, B):
    """편집 전 A·후 B 문항 목록 → [(후 위치, 종류, 전 위치)]. 같은 문항은 문두·보기로 짝짓는다."""
    key = lambda x: (x["stem"], tuple(x["choices"]))
    used, pairs = set(), {}
    for j, b in enumerate(B):
        for i, a in enumerate(A):
            if i not in used and key(a) == key(b) and a["box"] == b["box"] and a["data"] == b["data"] and a["marks"] == b["marks"]:
                used.add(i)
                pairs[j] = (i, [])
                break
    for j, b in enumerate(B):
        if j in pairs:
            continue
        best, bi = 0, None
        for i, a in enumerate(A):
            if i in used:
                continue
            r = difflib.SequenceMatcher(None, a["stem"] + "".join(a["choices"]), b["stem"] + "".join(b["choices"])).ratio()
            if r > best:
                best, bi = r, i
        if bi is not None and best >= 0.6:
            a = A[bi]
            kinds = [k for k, f in (("문두 바뀜", "stem"), ("보기 바뀜", "choices"), ("보기 기호 바뀜", "marks"), ("상자 바뀜", "box"),
                                    ("표 바뀜", "data"))
                     if a[f] != b[f]]
            used.add(bi)
            pairs[j] = (bi, kinds)
    rows = []
    seq = [pairs[j][0] for j in sorted(pairs)]
    # 순서: 짝지은 전 번호의 가장 긴 증가 부분열에 없는 것은 옮겨진 것
    lis_keep = _lis(seq)
    for j, b in enumerate(B):
        if j not in pairs:
            rows.append((b["label"], "새 문항", ""))
            continue
        i, kinds = pairs[j]
        if i not in lis_keep:
            kinds = ["순서 바뀜"] + kinds
        for k in kinds:
            rows.append((b["label"], k, A[i]["label"]))
    for i, a in enumerate(A):
        if i not in used:
            rows.append(("", "빠진 문항", a["label"]))
    return rows


def _lis(seq):
    import bisect
    tails, idx, prev = [], [], [None] * len(seq)
    for k, x in enumerate(seq):
        p = bisect.bisect_left(tails, x)
        if p == len(tails):
            tails.append(x)
            idx.append(k)
        else:
            tails[p] = x
            idx[p] = k
        prev[k] = idx[p - 1] if p else None
    keep, k = set(), idx[-1] if idx else None
    while k is not None:
        keep.add(seq[k])
        k = prev[k]
    return keep


def cmd_diff(a, cfg):
    from hwp_com import Session
    work = tempfile.mkdtemp(prefix="hwpexam_")
    with Session() as hwp:
        A = snapshot(Hwpx(to_laid(hwp, a.before, work, "d_a.hwpx")[0]), cfg)
        B = snapshot(Hwpx(to_laid(hwp, a.after, work, "d_b.hwpx")[0]), cfg)
    shutil.rmtree(work, ignore_errors=True)
    rows = diff_items(A, B)
    if a.output:
        with open(a.output, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["편집 후 위치", "종류", "편집 전 위치"])
            w.writerows(rows)
    c = collections.Counter(r[1] for r in rows)
    print(json.dumps({"바뀐 곳": len(rows), "종류별": dict(c)}, ensure_ascii=False))
    for r in rows:
        print(" · ".join(x for x in r if x))
    return 0


def main(argv):
    ap = argparse.ArgumentParser(prog="hwp_tool.py exam", description="시험지 만들기·점검·고치기·비교")
    sub = ap.add_subparsers(dest="act", required=True)
    p = sub.add_parser("build")
    p.add_argument("items")
    p.add_argument("--form", required=True)
    p.add_argument("--template", required=True)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--report")
    p = sub.add_parser("check")
    p.add_argument("file")
    p.add_argument("--form", required=True)
    p.add_argument("-o", "--output")
    p = sub.add_parser("fix")
    p.add_argument("file")
    p.add_argument("--form", required=True)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--report")
    p = sub.add_parser("diff")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--form", required=True)
    p.add_argument("-o", "--output")
    a = ap.parse_args(argv)
    cfg = load_form(a.form)
    if a.act in ("build", "fix") and os.path.abspath(a.output) in (os.path.abspath(getattr(a, "file", "") or ""),):
        sys.exit("출력이 원본과 같습니다. 다른 이름으로 주세요(원본은 고치지 않습니다).")
    return {"build": cmd_build, "check": cmd_check, "fix": cmd_fix, "diff": cmd_diff}[a.act](a, cfg)
