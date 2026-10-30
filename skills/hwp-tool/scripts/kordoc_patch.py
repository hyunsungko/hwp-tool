"""고정판 kordoc(4.24.6) render에 덧댐을 넣는다. hwp_tool.kordoc_argv가 부를 때마다 확인하고, 이미 들어 있으면 그대로 둔다.

kordoc render가 한컴과 다르게 그리는 곳을 고친다.
1. 쪽 번호 새로 매기기(hp:newNum)를 홀짝 머리말·꼬리말 고르기에 쓰지 않음 → 홀짝 머리말 좌우가 바뀜
2. 머리말·꼬리말 글 안 쪽 번호 자동 번호(hp:autoNum numType=PAGE)를 그리지 않음 → 쪽 번호 숫자가 빔
3. HTML 안 SVG에서 글 앞 빈칸이 사라짐(xml:space가 HTML에서는 안 먹음)
4. 다단(newspaper)에서 단 끝을 넘는 표의 남은 조각을 다음 쪽에 그리고, 그 뒤 문단을 앞 단에 겹쳐 그림
   (표 조각 k를 시작 단 + k번째 단으로 옮기고, 뒤 문단은 표가 넘어간 단 수만큼 다음 단에서 시작)
   한계: 한 칸 안의 글이 단 경계에서 나뉘는 경우는 칸 글의 가로 위치를 옮기지 않는다.
5. 밑줄 친 빈칸(답 쓰는 줄)을 그리지 않음
6. 다단 구분선이 표 조각만 있는 단에서 짧거나 빠짐

원본은 같은 폴더의 <파일>.orig로 한 번 남기고, 덧댐은 언제나 원본에서 새로 만든다(덧댐 판이 바뀌어도 그대로 다시 씀).
고칠 자리를 못 찾으면(kordoc 판이 다름) 덧대지 않고 알리기만 한다.
"""
import hashlib
import os
import shutil
import sys
CHUNK = "chunk-KR4UKOX2.js"

PATCHES = [
    # 1) 머리말·꼬리말: newNum을 따른 쪽 번호로 홀짝을 고르고, 그 번호를 story에 붙인다
    ("""          if (kind === "header" || kind === "footer") declarations.push({ el, page, kind });""",
     """          if (kind === "header" || kind === "footer") declarations.push({ el, page, kind });
          else if (kind === "newNum" && (el.getAttribute("numType") ?? "PAGE") === "PAGE") restarts.push({ page, num: num(el, "num", 1) }); """),
    ("""  const declarations = [];
  const hidden = /* @__PURE__ */ new Map();""",
     """  const declarations = [];
  const restarts = [];
  const hidden = /* @__PURE__ */ new Map();"""),
    ("""    const n = pageBase + page + 1;
    const hides = (name) =>""",
     """    const restart = restarts.filter((r) => r.page <= page).pop();
    const n = restart ? restart.num + page - restart.page : pageBase + page + 1;
    const hides = (name) =>"""),
    ("""      if (sub) out.push({ sub, page, x: geom.ML, y: kind === "header" ? num(margin, "top", 5668) : geom.PH - num(margin, "bottom", 4252) - num(margin, "footer"), width: geom.BODY_W });""",
     """      if (sub) out.push({ sub, page, n, x: geom.ML, y: kind === "header" ? num(margin, "top", 5668) : geom.PH - num(margin, "bottom", 4252) - num(margin, "footer"), width: geom.BODY_W });"""),
    ("""    const draw = () => {
      ctx.page = story.page;""",
     """    const draw = () => {
      ctx.page = story.page;
      (ctx.pageNumbers ??= {})[story.page] = story.n;"""),
    # 2) 글 안 쪽 번호: 자리는 "00"으로 재고, 그릴 때 그 쪽 번호로
    ("""            const t = an ? noteAutoNumOf(an) : note ?""",
     """            if (an && an.getAttribute("numType") === "PAGE") { slot.pageAuto = findChildByLocalName(an, "autoNumFormat")?.getAttribute("type") ?? "DIGIT"; slot.ctrlText = "00"; }
            const t = an ? noteAutoNumOf(an) : note ?"""),
    ("""        const ct = m.chars[i].ctrlText;
        if (ct) {""",
     """        const ct = m.chars[i].pageAuto ? formatHeadNumber2(ctx.pageNumbers?.[ctx.page] ?? pageNo(ctx), m.chars[i].pageAuto) : m.chars[i].ctrlText;
        if (ct) {"""),
    # 3) HTML 안 SVG 글 앞 빈칸
    (""".kordoc-page > svg { display: block; width: 100%; height: 100%; }""",
     """.kordoc-page > svg { display: block; width: 100%; height: 100%; }
.kordoc-page text { white-space: pre; }"""),
    # 5) 밑줄 친 빈칸(답 쓰는 줄)은 글이 없어 그리지 않음 → 밑줄만 긋는다
    ("""        return sw;
      };
      const merged = [];""",
     """        if (!text.trim().length && st.underline && sw > 0) {
          const uy = pt2(glyphY(st.height) + Math.round(st.height * 0.12));
          emit(ctx, `<line x1="${pt2(cx)}" y1="${uy}" x2="${pt2(cx + sw)}" y2="${uy}" stroke="${escapeXml(st.color || "#000000")}" stroke-width="${pt2(Math.max(40, Math.round(st.height * 0.06)))}"/>`);
        }
        return sw;
      };
      const merged = [];"""),
    # 줄 끝 빈칸은 그리지 않고 건너뛰는데, 뒤에 밑줄 친 빈칸이 있으면 남긴다(답 쓰는 줄 앞 빈칸 폭도 지킴)
    ("""      if (i >= plan.textEnd && m.chars[i].ch === " ") {""",
     """      if (i >= plan.textEnd && m.chars[i].ch === " " && !m.chars.slice(i, plan.end).some((c) => c.ch === " " && ctx.styles.charPr.get(c.prId)?.underline)) {"""),
    ("""!(j >= plan.textEnd && m.chars[j].ch === " ")""",
     """!(j >= plan.textEnd && m.chars[j].ch === " " && !m.chars.slice(j, plan.end).some((c) => c.ch === " " && ctx.styles.charPr.get(c.prId)?.underline))"""),
    # 6) 단 구분선: 쪽 안 문단 줄 끝까지만 그어 표 조각만 있는 단에서는 짧거나 빠짐 → 섹션 마지막 쪽이 아니면 단 높이 끝까지
    ("""    const bottom = pageBottoms.get(page);
    if (colLine""",
     """    const bottom = page < ctx.pages.length - 1 ? ctx.geom.BODY_H : pageBottoms.get(page) ?? (ctx.pages[page]?.length ? ctx.geom.BODY_H : 0);
    if (colLine"""),
    # 4) 다단: 단 끝을 넘는 표
    ("""    let cur = 0, col = 0;""",
     """    let cur = 0, col = 0, colCarry = 0;"""),
    ("""        if (localCols) {
          if (v < prevV ||""",
     """        if (localCols) {
          if (paraFirst && colCarry > 0) {
            col += colCarry;
            cur += Math.floor(col / nCol);
            col %= nCol;
            colCarry = 0;
            prevV = Number.NEGATIVE_INFINITY;
          }
          if (v < prevV ||"""),
    ("""      paraSegPages.set(p, pagesOf);""",
     """      paraSegPages.set(p, pagesOf);
      if (localCols && multiCol && segEls.length) {
        for (const o of buildPara(p).objs) {
          if (o.inline || o.tag !== "tbl" || (o.el.getAttribute("textWrap") ?? "TOP_AND_BOTTOM") !== "TOP_AND_BOTTOM") continue;
          const pb = o.el.getAttribute("pageBreak");
          const pos = findChildByLocalName(o.el, "pos"), om = findChildByLocalName(o.el, "outMargin");
          if (pb !== "CELL" && pb !== "TABLE" || (pos?.getAttribute("vertRelTo") ?? "PARA") !== "PARA") continue;
          const ty = MT + num(segEls[0], "vertpos") + num(om, "top") + Math.max(0, toInt32(pos?.getAttribute("vertOffset") ?? void 0, 0));
          const sp = tableContPages(o.el, ty, MT, BODY_H, ctxBase.extentMemo, PH - MT - BODY_H, cellFloatSpan(MT, BODY_H, ctxBase.extentMemo, PH - MT - BODY_H));
          if (sp.pages > colCarry) colCarry = sp.pages;
        }
        if (colCarry) pendingEnd = Math.max(pendingEnd, cur + Math.floor((col + colCarry) / nCol));
      }"""),
    ("""  const ctx = {
    ...ctxBase,""",
     """  const ctx = {
    ...ctxBase,
    colLayout: localCols && multiCol ? { n: nCol, step: colW + colGap, ML } : void 0,"""),
    ("""  while (ctx.pages.length <= Math.max(startPage, ...contPages)) ctx.pages.push([]);""",
     """  const colOf = depth === 1 && ctx.colLayout && contPages.length ? Math.max(0, Math.min(ctx.colLayout.n - 1, Math.floor((tx - ctx.colLayout.ML + 50) / ctx.colLayout.step))) : -1;
  while (ctx.pages.length <= Math.max(startPage, ...contPages.map((pg) => colOf < 0 ? pg : startPage + Math.floor((colOf + pg - startPage) / ctx.colLayout.n)))) ctx.pages.push([]);"""),
    # 표 조각 k(쪽 단위로 나뉜 것)를 시작 단 + k번째 단으로 옮긴다. 머리 줄 되풀이 칸까지 넣은 뒤에 옮긴다
    ("""  for (const page of contPages) for (const c of cells) if (c.ra < hdrRows) geom.push({ c, page, ...xw(c), y: contBase + num(findChildByLocalName(tbl, "outMargin"), "top") + rowY[c.ra], h: rowY[Math.min(c.ra + c.rs, nRows)] - rowY[c.ra] });
  for (const g of geom) {
    ctx.page = g.page;
    const bf = g.c.bfId""",
     """  for (const page of contPages) for (const c of cells) if (c.ra < hdrRows) geom.push({ c, page, ...xw(c), y: contBase + num(findChildByLocalName(tbl, "outMargin"), "top") + rowY[c.ra], h: rowY[Math.min(c.ra + c.rs, nRows)] - rowY[c.ra] });
  if (colOf >= 0) {
    for (const g of geom) {
      const cc = colOf + g.page - startPage;
      g.page = startPage + Math.floor(cc / ctx.colLayout.n);
      g.x += (cc % ctx.colLayout.n - colOf) * ctx.colLayout.step;
    }
  }
  for (const g of geom) {
    ctx.page = g.page;
    const bf = g.c.bfId"""),
]


# 표지는 덧댐 목록의 해시: 덧댐을 고치면 다음 실행 때 원본에서 다시 만든다
MARK = "/* hwp-tool patch " + hashlib.sha1(repr(PATCHES).encode()).hexdigest()[:10] + " */"
PATCHES[0] = (PATCHES[0][0], PATCHES[0][1] + " " + MARK)


def apply(dist_dir):
    path = os.path.join(dist_dir, CHUNK)
    if not os.path.exists(path):
        return False
    s = open(path, encoding="utf-8").read()
    if MARK in s:
        return True
    orig = path + ".orig"
    if not os.path.exists(orig):
        if "hwp-tool" in s:
            print("kordoc에 옛 hwp-tool 덧댐이 있는데 원본(.orig)이 없음 — kordoc을 다시 깔아 주세요", file=sys.stderr)
            return False
        shutil.copyfile(path, orig)
    s = open(orig, encoding="utf-8").read()
    for old, _ in PATCHES:
        if s.count(old) != 1:
            print(f"kordoc 덧댐을 못 넣음(판이 다름: {old.strip()[:50]}…) — kordoc 원래대로 그림", file=sys.stderr)
            return False
    for old, new in PATCHES:
        s = s.replace(old, new)
    tmp = path + ".tmp"
    open(tmp, "w", encoding="utf-8", newline="").write(s)
    os.replace(tmp, path)
    return True
