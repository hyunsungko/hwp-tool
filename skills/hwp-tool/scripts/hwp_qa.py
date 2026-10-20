"""검수 — write·edit가 저장한 문서를 넘기기 전에 자동으로 본다(2026-10-21).

문서 XML로 보는 것(한컴 없이도):
  남은 마크다운 기호(** __ # | ` [글](주소)), 깨진 엔티티(&quot; &취업상담&), 기본 바탕 글꼴로 떨어진 글,
  머리줄·이름표 서식 없는 표, 본문 폭보다 넓은 표
쪽 그림으로 보는 것(한컴이 있을 때, 숨긴 데스크톱):
  빈 쪽, 제목만 있는 쪽, 쪽 끝에 홀로 남은 제목, 본문 여백 밖으로 넘친 글

inspect()는 {"문제": [{"수준": "오류"|"경고", "종류", "쪽", "내용"}], "쪽수", "쪽 그림": [경로]}를 돌려준다.
"""
import collections
import os
import re
import tempfile

import hwpx_direct as X

HP, HH, HC = X.HP, X.HH, X.HC
DEFAULT_FONTS = {"바탕", "바탕체", "함초롬바탕", "HCR Batang", "Batang", "BatangChe", "새바탕", "한컴바탕"}
LEFTOVER = [
    ("**", re.compile(r"\*\*")),
    ("__", re.compile(r"(^|\s)__\S|\S__(\s|$)")),
    ("# 제목 기호", re.compile(r"^\s*#{1,6}\s")),
    ("| 표 기호", re.compile(r"^\s*\|.*\|\s*$|\S\s\|\s\S.*\s\|\s\S")),
    ("` 코드 기호", re.compile(r"`[^`]+`")),
    ("[글](주소) 링크", re.compile(r"\[[^\]]+\]\((https?://|/|\.)[^)]*\)")),
    ("HTML 엔티티", re.compile(r"&(quot|amp|lt|gt|nbsp|apos|#\d+|#x[0-9a-fA-F]+);")),
    ("& 로 깨진 따옴표", re.compile(r"&[^\s&]{1,20}&")),
]


def _load(path):
    """HWPX면 그대로, HWP면 우리 변환기로 HWPX를 만들어 연다."""
    if path.lower().endswith(".hwp"):
        import hwp5_to_hwpx
        tmp = os.path.join(tempfile.mkdtemp(prefix="hwpqa_"), "doc.hwpx")
        hwp5_to_hwpx.convert(path, tmp)
        return X.Hwpx(tmp)
    return X.Hwpx(path)


def _fill(d, bf_id):
    bf = next((b for b in d.reflist("borderFills") if b.get("id") == bf_id), None)
    wb = bf.find(f"{HC}fillBrush/{HC}winBrush") if bf is not None else None
    return wb is not None and wb.get("faceColor", "none").lower() not in ("none", "#ffffff")


def xml_checks(d, strict=True):
    out = []
    lv = "오류" if strict else "경고"
    # 글자 모양 → (한글 글꼴, 굵게)
    faces = {}
    for ff in d.reflist("fontfaces"):
        if ff.get("lang") == "HANGUL":
            faces = {f.get("id"): f.get("face") for f in ff.findall(HH + "font")}
    cps = {}
    for cp in d.reflist("charProperties"):
        ref = cp.find(HH + "fontRef")
        cps[cp.get("id")] = (faces.get(ref.get("hangul")) if ref is not None else None, cp.find(HH + "bold") is not None)
    seen = set()
    for p in d.all_paras():
        t = X.para_text(p).replace(X.OBJ, "")
        for name, rx in LEFTOVER:
            m = rx.search(t)
            if m and (name, t[:30]) not in seen:
                seen.add((name, t[:30]))
                a = max(0, m.start() - 12)
                out.append({"수준": "오류", "종류": "남은 기호", "내용": f"{name}: …{t[a:m.end() + 12]}…"})
    # 기본 바탕 글꼴로 떨어진 글
    fallen = collections.Counter()
    for run in (r for sec in d.sections for r in sec.iter(HP + "run")):
        n = sum(len(tt.text or "") + sum(len(x.tail or "") for x in tt) for tt in run.findall(HP + "t"))
        face = cps.get(run.get("charPrIDRef"), (None, False))[0]
        if n and face in DEFAULT_FONTS:
            fallen[face] += n
    for face, n in fallen.items():
        out.append({"수준": lv, "종류": "기본 바탕 글꼴", "내용": f"'{face}'로 찍힌 글 {n}자 — 글꼴을 지정하지 않은 글"})
    # 표: 머리줄·이름표 서식, 폭
    width = d.text_width(d.secprs()[0]) if d.secprs() else None
    for n, tbl in enumerate(d.tables(), 1):
        rows = tbl.findall(HP + "tr")
        cells = [tc for tr in rows for tc in tr.findall(HP + "tc")]
        ncol = max((len(tr.findall(HP + "tc")) for tr in rows), default=0)
        if width and int(tbl.find(HP + "sz").get("width", 0)) > width + X.mm(2):
            out.append({"수준": lv, "종류": "넓은 표", "내용": f"표 {n}: 폭 {int(tbl.find(HP + 'sz').get('width')) / 283.46:.0f}mm"
                                                       f" > 본문 {width / 283.46:.0f}mm"})
        if len(rows) < 2 or ncol < 2 or not strict:
            continue

        def styled(tcs):
            return any(_fill(d, tc.get("borderFillIDRef")) or tc.get("header") == "1" or
                       any(cps.get(r.get("charPrIDRef"), (None, False))[1] for r in tc.iter(HP + "run")) for tc in tcs)
        first_row = rows[0].findall(HP + "tc")
        first_col = [tc for tc in cells if tc.find(HP + "cellAddr") is not None and tc.find(HP + "cellAddr").get("colAddr") == "0"]
        if not styled(first_row) and not styled(first_col):
            out.append({"수준": lv, "종류": "서식 없는 표", "내용": f"표 {n}({len(rows)}줄): 머리줄·첫 칸 모두 음영·굵게가 없음"})
    return out


def page_checks(path, folder, d, dpi=70):
    """한컴으로 쪽 그림(PNG)과 PDF를 만들고, PDF 글자 자리로 쪽을 본다."""
    import pdfplumber
    from PIL import Image
    from hwp_com import Session
    import hwp_check
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    imgs, out = [], []
    with tempfile.TemporaryDirectory() as td, Session() as hwp:
        hwp_check._open(hwp, path)
        n = hwp.PageCount
        pdf = os.path.join(td, "doc.pdf")
        ok = hwp.SaveAs(pdf, "PDF", "")
        npdf = 0
        if ok:
            with pdfplumber.open(pdf) as doc:
                npdf = len(doc.pages)
        if ok and npdf == n:                     # 쪽 그림은 PDF에서(한컴 CreatePageImage는 양식 문서에서 17px 높이로 깨졌다 — 2026-10-21)
            import fitz
            with fitz.open(pdf) as doc:
                for i, pg in enumerate(doc):
                    p = os.path.join(folder, f"{stem}_p{i + 1:03d}.png")
                    pg.get_pixmap(dpi=int(dpi * 1.4)).save(p)
                    imgs.append(p)
        else:                                    # PDF가 인쇄 설정(모아 찍기)으로 쪽을 합치면 한컴 쪽 그림으로
            for i in range(n):
                bmp = os.path.join(td, f"{i}.bmp")
                if hwp.CreatePageImage(bmp, i, dpi, 24, "bmp"):
                    p = os.path.join(folder, f"{stem}_p{i + 1:03d}.png")
                    with Image.open(bmp) as im:
                        im.save(p)
                    imgs.append(p)
        if not ok:
            return n, imgs, [{"수준": "경고", "종류": "검수 못 함", "내용": "PDF를 만들지 못해 쪽 검사를 건너뜀"}]
        with pdfplumber.open(pdf) as doc:
            if len(doc.pages) != n:
                return n, imgs, [{"수준": "경고", "종류": "검수 못 함",
                                  "내용": f"PDF 쪽수({len(doc.pages)})가 문서 쪽수({n})와 달라 쪽 검사를 건너뜀(인쇄 모아 찍기 설정?)"}]
            m = d.secprs()[0].find(f"{HP}pagePr/{HP}margin")
            g = {k: int(m.get(k, 0)) / 100 for k in ("left", "right", "top", "bottom", "header", "footer")}  # HWPUNIT → pt
            pages = []
            sizes = collections.Counter()
            for pg in doc.pages:
                top, bot = g["top"] + g["header"] - 4, pg.height - g["bottom"] - g["footer"] + 4
                lines = collections.OrderedDict()
                for ch in pg.chars:
                    if not ch["text"].strip() or not top <= ch["top"] <= bot:
                        continue
                    key = round(ch["top"] / 3)
                    lines.setdefault(key, []).append(ch)
                    sizes[round(ch["size"])] += 1
                over = [ch for ch in pg.chars if ch["text"].strip() and top <= ch["top"] <= bot and
                        (ch["x1"] > pg.width - g["right"] + 6 or ch["x0"] < g["left"] - 6)]
                # PDF 글자 차례는 읽는 차례가 아니다(표 글이 뒤에 나온다) → 위에서 아래로
                pages.append((pg, [lines[k] for k in sorted(lines)], over, top, bot))
        body = sizes.most_common(1)[0][0] if sizes else 10
        heading = max(body + 1, 12)
        for i, (pg, lines, over, top, bot) in enumerate(pages, 1):
            texts = ["".join(c["text"] for c in sorted(ln, key=lambda c: c["x0"])) for ln in lines]
            is_head = [max(c["size"] for c in ln) >= heading - 0.2 for ln in lines]
            if not lines:
                out.append({"수준": "오류", "종류": "빈 쪽", "쪽": i, "내용": "본문에 글이 없는 쪽"})
            elif all(is_head):
                out.append({"수준": "오류", "종류": "제목만 있는 쪽", "쪽": i, "내용": " / ".join(texts)[:60]})
            elif i < len(pages) and is_head[-1]:
                out.append({"수준": "오류", "종류": "쪽 끝 제목", "쪽": i, "내용": f"'{texts[-1][:40]}'가 쪽 끝에 홀로 남고 내용은 다음 쪽"})
            elif i < len(pages) and max(c["bottom"] for c in lines[-1]) < top + 0.55 * (bot - top):
                out.append({"수준": "경고", "종류": "쪽 아래 빈 곳", "쪽": i,
                            "내용": "쪽 아래 절반 가까이가 빔 — 다음 쪽 표·그림이 통째로 넘어갔는지 쪽 그림으로 확인"})
            if over:
                s = "".join(c["text"] for c in over[:20])
                out.append({"수준": "오류", "종류": "여백 밖 글", "쪽": i, "내용": f"본문 여백 밖으로 나간 글 {len(over)}자: {s}"})
    return n, imgs, out


def inspect(path, folder=None, strict=True, pages=True):
    import hwp_tool
    d = _load(path)
    rep = {"문제": xml_checks(d, strict), "쪽수": None, "쪽 그림": []}
    if pages and hwp_tool.hancom_installed():
        folder = folder or os.path.join(tempfile.gettempdir(), "hwp-tool-검수",
                                        os.path.splitext(os.path.basename(path))[0])
        try:
            n, imgs, probs = page_checks(path, folder, d)
        except ImportError as e:                 # pdfplumber·pymupdf가 없으면 쪽 검사만 건너뛴다
            n, imgs, probs = None, [], [{"수준": "경고", "종류": "검수 못 함", "내용": f"쪽 검사에 필요한 모듈이 없음({e.name}) — "
                                                                             "pip install pdfplumber pymupdf"}]
        rep.update({"쪽수": n, "쪽 그림": imgs, "쪽 그림 폴더": folder})
        rep["문제"] += probs
    elif pages:
        rep["문제"].append({"수준": "경고", "종류": "검수 못 함", "내용": "한컴이 없어 쪽 그림 검사(빈 쪽·쪽 끝 제목·넘친 글)를 못 함"})
    return rep


def summary(rep):
    """사람이 읽을 줄들."""
    errs = [p for p in rep["문제"] if p["수준"] == "오류"]
    lines = [f"검수: 오류 {len(errs)}건 · 경고 {len(rep['문제']) - len(errs)}건" + (f" · {rep['쪽수']}쪽" if rep["쪽수"] else "")]
    for p in rep["문제"]:
        lines.append(f"  [{p['수준']}] {p['종류']}" + (f" ({p['쪽']}쪽)" if p.get("쪽") else "") + f": {p['내용']}")
    if rep.get("쪽 그림 폴더"):
        lines.append(f"  쪽 그림: {rep['쪽 그림 폴더']} — 넘기기 전에 직접 열어 본다")
    return lines
