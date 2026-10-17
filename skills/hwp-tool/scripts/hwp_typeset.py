"""조판 다듬기 — 한컴이 실제로 짠 줄 배치를 읽어 사람 편집자가 하던 마무리를 자동으로 한다.

- 낱말 끊김 맞춤(fit_words): 줄 끝에서 낱말이 갈리거나("프로그/램") 마지막 줄에 한두 글자만 남은 문단을 찾아,
  그 줄의 글자만 자간을 조금 줄여 끌어올리거나(최대 -10%) 늘려 내린다(최대 +10%). 한글 줄 나눔은 글자 단위 그대로.
- 단 채우기(balance_columns): 단마다 아래 빈 높이를 재서 그 단 문항들 사이 간격으로 나눈다 → 단 끝·가운데 선이 쪽 끝까지.
- 부정어 강조(emphasize): '옳지 않은·아닌·틀린…' 뒤에 '것'이 오면 밑줄·굵게.
- 여기까지 내어쓰기(hang_markers): '가. ', '• ' 같은 머리 글 뒤에서 한컴 액션 ParagraphShapeIndentAtCaret(Ctrl+Shift+Tab)을
  실제 배치 위에서 실행 → 둘째 줄이 머리 글 뒤 첫 글자에 화소 단위로 맞는다.

줄 배치는 한컴이 다시 저장한 HWPX의 linesegarray(textpos=줄 첫 글자 위치, vertpos·vertsize=단 안 세로 위치·높이)로 읽는다.
한컴을 쓰는 함수(relayout·hang_markers)는 hwp_com.Session으로 숨긴 데스크톱에서만 돈다.
"""
import math
import os
import re
import shutil
import tempfile

from hwpx_direct import HC, HH, HP, Hwpx, explode, implode

OPEN_OPT = "forceopen:true;versionwarning:false"

NEGATIVE_RX = re.compile(
    r"((?<![가-힣])[가-힣]*지\s?(?:않은|않는)|(?<![가-힣])(?:거리가|관계가|관련이)\s?(?:먼|없는)|관계없는|관련없는|"
    r"잘못\s?연결된|잘못된|틀린|아닌|없는)(?=\s?(?:것|[가-힣]{1,4}[은는을를이가]\s?\?))")     # 뒤에 '것' 또는 '자는?·경우는?' 같은 짧은 물음

MARKER_RX = re.compile(r"^(\s*(?:[•·▪■□○●◦ㆍ※\-–]|[ⓐ-ⓩ①-⑳㉠-㉭㉮-㉻]|[가-하]\.|[ㄱ-ㅎ]\.|\([가-하ㄱ-ㅎ]\)|"
                       r"[가-하ㄱ-ㅎ]\)|\(\d{1,2}\)|\d{1,2}\)|\d{1,2}\.)\s+)")


# ───────────────────────── 줄 배치 읽기 ─────────────────────────
def lines_of(p):
    """문단 p 자신의 줄(첫 글자 위치 순). 각 줄: textpos·vertpos·vertsize·spacing·horzpos·horzsize."""
    ls = p.find(HP + "linesegarray")
    if ls is None:
        return []
    out = [{k: int(l.get(k, 0)) for k in ("textpos", "vertpos", "vertsize", "spacing", "horzpos", "horzsize")}
           for l in ls.findall(HP + "lineseg")]
    return sorted(out, key=lambda x: x["textpos"])


def plain_text(p):
    """글자만 든 문단이면 그 글, 탭·개체가 섞였으면 None(줄 위치 단위가 달라 다루지 않음)."""
    items = explode(p)
    if any(i.kind not in ("ch", "empty") for i in items):
        return None
    return "".join(i.val for i in items if i.kind == "ch")


def _w(ch):
    if ch == " ":
        return 0.5
    return 0.55 if ord(ch) < 128 else 1.0


def word_breaks(text, lines):
    """줄 사이에서 낱말이 갈린 곳(split)과 마지막 줄에 한두 글자만 남은 곳(widow). 각 항목:
    kind, line(아래 줄 번호), s(위 줄 시작), pos(아래 줄 시작), a·b(갈린 낱말 처음·끝)."""
    starts = [l["textpos"] for l in lines]
    blanks = [(m.start(), m.end()) for m in re.finditer(r"\(\s*\)", text)]
    out = []
    for i in range(1, len(starts)):
        pos = starts[i]
        if not 0 < pos < len(text):
            continue
        blank = next(((b0, b1) for b0, b1 in blanks if b0 < pos < b1), None)
        if blank:
            a, b = blank[0], blank[1]
            while a > starts[i - 1] and not text[a - 1].isspace():
                a -= 1
            while b < len(text) and not text[b].isspace():
                b += 1
            out.append({"kind": "split", "line": i, "s": starts[i - 1], "pos": pos, "a": a, "b": b})
            continue
        if not text[pos - 1].isspace() and not text[pos].isspace():
            a, b = pos, pos
            while a > starts[i - 1] and not text[a - 1].isspace():
                a -= 1
            while b < len(text) and not text[b].isspace():
                b += 1
            for b0, b1 in blanks:             # 낱말이 빈칸 괄호에 붙어 있으면 괄호까지 한 덩어리
                if b0 < a <= b1:
                    a = b0
                    while a > starts[i - 1] and not text[a - 1].isspace():
                        a -= 1
                if b0 <= b < b1:
                    b = b1
            out.append({"kind": "split", "line": i, "s": starts[i - 1], "pos": pos, "a": a, "b": b})
    if len(starts) >= 2 and 0 < starts[-1] < len(text):
        last = text[starts[-1]:].strip()
        if 0 < len(last) <= 2 and not any(x["line"] == len(starts) - 1 for x in out):
            out.append({"kind": "widow", "line": len(starts) - 1, "s": starts[-2], "pos": starts[-1],
                        "a": starts[-1], "b": len(text)})
    return out


# ───────────────────────── 한컴(숨긴 데스크톱) ─────────────────────────
def relayout(hwp, src, out):
    """한컴으로 열어 HWPX로 다시 저장(줄 배치가 채워짐). → 쪽수"""
    if not hwp.Open(os.path.abspath(src), "", OPEN_OPT):
        raise RuntimeError("한컴이 열지 못함: " + src)
    pages = hwp.PageCount
    hwp.SaveAs(os.path.abspath(out), "HWPX", "")
    hwp.Clear(1)
    return pages


def hang_markers(hwp, src, out, texts):
    """texts: [(문단 글, 머리 글 길이)]. 한컴에서 그 문단을 찾아 머리 글 끝에서 '여기까지 내어쓰기'를 실행하고 HWPX로 저장.
    같은 글이 여러 번 나오면 나온 순서대로 모두. → 실행한 문단 수"""
    from hwp_com import ComDoc
    if not hwp.Open(os.path.abspath(src), "", OPEN_OPT):
        raise RuntimeError("한컴이 열지 못함: " + src)
    d = ComDoc(hwp)
    done, seen = 0, {}
    for text, mlen in texts:
        key = text[:60]
        seen[key] = seen.get(key, 0) + 1
        if not d.find(key, nth=seen[key]):
            continue
        sel = d._sel()
        d.run("Cancel")
        if not hwp.SetPos(sel[0], sel[1], sel[2] + mlen):
            continue
        if d.run("ParagraphShapeIndentAtCaret"):
            done += 1
    d.body()
    hwp.SaveAs(os.path.abspath(out), "HWPX", "")
    hwp.Clear(1)
    return done


def marker_paras(d, paras):
    """머리 글('가. ', '• ' …)로 시작하는 글자 문단 → [(문단, 글, 머리 글 길이)]."""
    out = []
    for p in paras:
        t = plain_text(p)
        if t:
            m = MARKER_RX.match(t)
            if m and len(t) > len(m.group(1)):
                out.append((p, t, len(m.group(1))))
    return out


# ───────────────────────── 부정어 강조 ─────────────────────────
def emphasize(d, paras, rx=NEGATIVE_RX, look=None):
    """paras 안에서 rx에 맞는 글을 밑줄·굵게. → (바꾼 문단 수, 바꾼 곳 수)"""
    look = look or {"bold": True, "underline": True}
    np_, nm = 0, 0
    for p in paras:
        items = explode(p)
        idx = [k for k, it in enumerate(items) if it.kind == "ch"]
        text = "".join(items[k].val for k in idx)
        ms = list(rx.finditer(text))
        if not ms:
            continue
        for m in ms:
            for pos in range(m.start(), m.end()):
                it = items[idx[pos]]
                it.run = {**it.run, "charPrIDRef": d.charpr(it.run.get("charPrIDRef", "0"), look)}
        implode(p, items)
        d.touch(p)
        np_ += 1
        nm += len(ms)
    return np_, nm


# ───────────────────────── 낱말 끊김 자간 맞춤 ─────────────────────────
def _apply_spacing(d, p, entries):
    """entries [(시작, 끝, 자간 증감%)]를 글자에 적용(겹치면 뒤 항목이 이김)."""
    items = explode(p)
    idx = [k for k, it in enumerate(items) if it.kind == "ch"]
    delta = [0] * len(idx)
    for s, e, dv in entries:
        for k in range(max(0, s), min(e, len(idx))):
            delta[k] = dv
    for pos, dv in enumerate(delta):
        if dv:
            it = items[idx[pos]]
            it.run = {**it.run, "charPrIDRef": d.charpr(it.run.get("charPrIDRef", "0"), {"spacing_add": dv})}
    implode(p, items)
    d.touch(p)


def _plan(text, iss, prev, max_pull, max_push):
    """한 문단의 첫 문제에 대한 조정 하나 (시작, 끝, 자간%, 방식). prev = 같은 줄에 전에 쓴 조정."""
    s, pos, a, b = iss["s"], iss["pos"], iss["a"], iss["b"]
    W = sum(_w(c) for c in text[s:pos]) + 0.3          # 위 줄 너비(em, 남는 틈 0.3으로 봄)
    pull_lo = None
    if b > s:
        cnt = b - s
        pull_lo = (sum(_w(c) for c in text[s:b]) - W) / cnt * 100
    push_lo = None
    if iss["kind"] == "split" and a - 1 > s:
        cnt = a + 1 - s
        push_lo = (W - sum(_w(c) for c in text[s:a + 1])) / cnt * 100
        push_lo = max(push_lo, 0) + 1
    if prev:
        ps, pe, dv, how = prev
        if how == "pull":
            if -dv + 2 <= max_pull:
                return (s, max(pe, b), dv - 2, "pull")
            if push_lo is not None and push_lo <= max_push:
                return (s, pos, math.ceil(push_lo + 1.5), "push")
            return None
        if dv + 2 <= max_push:
            return (s, pos, dv + 2, "push")
        return None
    pull = max(math.ceil(pull_lo + 1.5), 2) if pull_lo is not None else None
    push = max(math.ceil(push_lo + 1.5), 2) if push_lo is not None else None
    ok_pull = pull is not None and pull <= max_pull
    ok_push = push is not None and push <= max_push
    tail = text[b:].strip()
    short_tail = 0 < len(tail) <= 3 and " " not in tail        # 끌어올리면 마지막 줄에 짧은 꼬리만 남는다
    if ok_pull and ok_push:
        if short_tail or push < pull:
            return (s, pos, push, "push")
        return (s, b, -pull, "pull")
    if ok_pull:
        return (s, b, -pull, "pull")
    if ok_push:
        return (s, pos, push, "push")
    if pull is not None:
        return (s, b, -max_pull, "pull")
    return None


def word_wrapped(d, p):
    """문단이 어절 단위 줄 나눔(BREAK_WORD)이면 True — 낱말이 갈리지 않으니 짧은 마지막 줄도 온전한 낱말이다."""
    pid = p.get("paraPrIDRef")
    for pr in d.header.iter(HH + "paraPr"):
        if pr.get("id") == pid:
            bs = pr.find(HH + "breakSetting")
            return bs is not None and bs.get("breakNonLatinWord") == "BREAK_WORD"
    return False


def issues_of(d, p):
    t = plain_text(p)
    ls = lines_of(p)
    if not t or len(ls) < 2:
        return t, []
    iss = word_breaks(t, ls)
    if word_wrapped(d, p):
        iss = [x for x in iss if x["kind"] != "widow"]
    return t, iss


def count_breaks(d, idxs):
    paras = d.all_paras()
    n = {"split": 0, "widow": 0}
    where = []
    for i in idxs:
        for x in issues_of(d, paras[i])[1]:
            n[x["kind"]] += 1
            where.append((i, x["kind"], x["line"]))
    return n, where


def fit_words(hwp, base, out, scope, max_rounds=12, max_pull=10, max_push=10, log=None):
    """base(한컴이 저장한 HWPX)에서 scope(d)가 돌려준 문단 번호(d.all_paras() 기준)의 낱말 끊김을 자간으로 맞춘다.
    조정은 문단마다 [(시작, 끝, 자간%)]로 모아 매 바퀴 base에 새로 입혀 한컴으로 다시 짠다. → 결과 dict"""
    work = tempfile.mkdtemp(prefix="hwpfit_")
    cur = base
    adj = {}             # 문단 번호 → [(s, e, dv, how)]
    stuck = set()
    first = None
    for rnd in range(max_rounds + 1):
        m = Hwpx(cur)
        idxs = scope(m)
        paras = m.all_paras()
        n, where = count_breaks(m, idxs)
        if first is None:
            first = n
        if log:
            log(f"낱말 맞춤 {rnd}바퀴: 끊김 {n['split']} · 한두 글자 줄 {n['widow']}")
        if not where or rnd == max_rounds:
            break
        changed = False
        for i in sorted({w[0] for w in where}):
            t, found = issues_of(m, paras[i])
            iss = found[0]
            key = (i, iss["s"])
            if key in stuck:
                continue
            prev = next((e for e in adj.get(i, []) if e[0] == iss["s"]), None)
            new = _plan(t, iss, prev, max_pull, max_push)
            if new is None:
                stuck.add(key)
                continue
            lst = [e for e in adj.get(i, []) if e[0] != iss["s"]]
            lst.append(new)
            adj[i] = sorted(lst)
            changed = True
        if not changed:
            break
        d = Hwpx(base)
        bp = d.all_paras()
        for i, lst in adj.items():
            _apply_spacing(d, bp[i], [(s, e, dv) for s, e, dv, _ in lst])
        tmp = os.path.join(work, f"r{rnd}.hwpx")
        d.save(tmp)
        cur = os.path.join(work, f"r{rnd}_laid.hwpx")
        relayout(hwp, tmp, cur)
    shutil.copyfile(cur, out)
    shutil.rmtree(work, ignore_errors=True)
    dvs = [dv for lst in adj.values() for _, _, dv, _ in lst]
    return {"전": first, "후": n, "조정한 문단": len(adj), "조정 줄": len(dvs),
            "자간 범위": [min(dvs), max(dvs)] if dvs else [], "못 맞춘 곳": [list(w) for w in where]}


# ───────────────────────── 단 채우기 ─────────────────────────
def body_height(sec):
    pp = sec.find(f".//{HP}secPr/{HP}pagePr")
    m = pp.find(HP + "margin")
    h = int(pp.get("height")) if pp.get("landscape", "WIDELY") == "WIDELY" else int(pp.get("width"))
    return h - sum(int(m.get(k, 0)) for k in ("top", "bottom", "header", "footer"))


def columns(sec, is_item=None):
    """본문 문단을 단별로 묶는다(세로 위치가 줄거나 같으면, 또는 쪽·단 나눔이면 새 단). → [[(문단 번호, 위, 아래, 문항인가, 쪽 나눔)], …]"""
    is_item = is_item or (lambda p: any(el.tag == HP + "tbl" for r in p.findall(HP + "run") for el in r))
    cols, prev = [], None
    for i, p in enumerate(sec.findall(HP + "p")):
        ls = lines_of(p)
        if not ls:
            continue
        top = min(l["vertpos"] for l in ls)
        bot = max(l["vertpos"] + l["vertsize"] for l in ls)
        brk = p.get("pageBreak") == "1"
        if prev is None or top <= prev or brk or p.get("columnBreak") == "1":
            cols.append([])
        cols[-1].append((i, top, bot, bool(is_item(p)), brk))
        prev = top
    return cols


def pages_of(cols, ncol=2):
    """단 묶음을 쪽으로: 쪽 나눔이 있거나 단 ncol개가 차면 새 쪽. → [[단 번호…], …]"""
    pages = []
    for ci, col in enumerate(cols):
        if not pages or col[0][4] or len(pages[-1]) >= ncol:
            pages.append([])
        pages[-1].append(ci)
    return pages


def _para_after(d, ppid):
    for pr in d.header.iter(HH + "paraPr"):
        if pr.get("id") == ppid:
            m = pr.find(f".//{HP}case/{HH}margin")
            if m is None:
                m = pr.find(f".//{HH}margin")
            return int(m.find(HC + "next").get("value"))
    return 0


def _partition(hs, avail, gap, margin=300):
    """문항 높이 hs를 차례대로 min(단 수, 문항 수)개 단에 빈 단 없이 나눈다.
    채울 때 문항 사이 한 칸에 더해질 높이(남는 높이 ÷ 문항 사이 수, 문항 하나뿐인 단은 남는 높이 전부)의
    가장 큰 값이 가장 작게, 같으면 제곱 합이 작게 → 과목 전체에서 문항 간격이 고르다. → 단 시작 문항 번호 목록(없으면 None)"""
    K, C = len(hs), min(len(avail), len(hs))
    pre = [0]
    for h in hs:
        pre.append(pre[-1] + h)

    def used(i, j):
        return pre[j] - pre[i] + gap * (j - i - 1)
    INF = (float("inf"), float("inf"))
    best = [[INF] * (K + 1) for _ in range(C + 1)]
    back = [[0] * (K + 1) for _ in range(C + 1)]
    best[0][0] = (0, 0)
    for c in range(1, C + 1):
        for j in range(c, K + 1):
            for i in range(c - 1, j):
                if best[c - 1][i] == INF:
                    continue
                u = used(i, j)
                if u > avail[c - 1] - margin:
                    continue
                free = avail[c - 1] - u
                g = free / (j - i - 1) if j - i > 1 else free
                pm, ps = best[c - 1][i]
                v = (max(pm, g), ps + g * g)
                if v < best[c][j]:
                    best[c][j], back[c][j] = v, i
    if best[C][K] == INF:
        return None
    starts, j = [], K
    for c in range(C, 0, -1):
        i = back[c][j]
        starts.append(i)
        j = i
    return sorted(starts)


def plan_columns(sec, is_item, ncol=2):
    """과목(쪽 나눔으로 시작하는 묶음)마다 문항을 그 과목 쪽들의 단 전부에 높이가 고르게 나눌 단 나눔 자리. → ({문단 번호}, 기록)"""
    H = body_height(sec)
    cols = columns(sec, is_item)
    segs = []
    for ci, col in enumerate(cols):
        if not segs or col[0][4]:
            segs.append([])
        segs[-1].append(ci)
    breaks, notes = set(), []
    for seg in segs:
        scols = [cols[ci] for ci in seg]
        npages = len(pages_of(scols, ncol))
        items = [c for col in scols for c in col if c[3]]
        if len(items) < 2:
            continue
        gaps = []
        for col in scols:
            its = [c for c in col if c[3]]
            gaps += [b[1] - a[2] for a, b in zip(its, its[1:])]
        gaps.sort()
        gap = gaps[len(gaps) // 2] if gaps else 2200
        avail = []
        for k in range(npages * ncol):
            if k < len(scols):
                first = next((c for c in scols[k] if c[3]), scols[k][0])
                avail.append(H - first[1])
            else:
                avail.append(H)
        starts = _partition([c[2] - c[1] for c in items], avail, gap)
        if starts is None:
            notes.append(f"문단 {items[0][0]}부터: 고르게 나눌 수 없어 그대로 둠")
            continue
        for k in starts[1:]:
            breaks.add(items[k][0])
        sizes = [b - a for a, b in zip(starts, starts[1:] + [len(items)])]
        notes.append(f"문단 {items[0][0]}부터: 문항 {len(items)}개 → 단 {len(starts)}개 {sizes}")
    return breaks, notes


def balance_columns(hwp, base, out, section=1, is_item=None, ncol=2, max_rounds=5, log=None):
    """① 과목마다 문항을 그 과목 쪽들의 단 전부에 높이가 고르게 나눈다(단 나눔).
    ② 단마다 아래 빈 높이를 그 단 문항 사이 간격에 고르게 나눈다(단 끝·가운데 선이 쪽 끝까지). → 결과 dict"""
    work = tempfile.mkdtemp(prefix="hwpbal_")
    sec0 = Hwpx(base).sections[section]
    H = body_height(sec0)
    breaks, notes = plan_columns(sec0, is_item, ncol)
    d = Hwpx(base)
    for i, p in enumerate(d.sections[section].findall(HP + "p")):
        if p.get("pageBreak") == "1":
            continue
        want = "1" if i in breaks else "0"
        if p.get("columnBreak", "0") != want:
            p.set("columnBreak", want)
            d.touch(p)
    tmp = os.path.join(work, "breaks.hwpx")
    d.save(tmp)
    start = os.path.join(work, "breaks_laid.hwpx")
    relayout(hwp, tmp, start)
    plan_cols = columns(Hwpx(start).sections[section], is_item)
    member = [[c[0] for c in col] for col in plan_cols]
    reserve = [0] * len(plan_cols)
    cur = start
    for rnd in range(max_rounds):
        d = Hwpx(start)
        body = d.sections[section].findall(HP + "p")
        for ci, col in enumerate(plan_cols):
            items = [c for c in col if c[3]]
            if len(items) < 2:
                continue
            free = H - max(c[2] for c in col) - reserve[ci]
            if free <= 0:
                continue
            extra = free // (len(items) - 1)
            for c in items[:-1]:
                p = body[c[0]]
                base_after = _para_after(d, p.get("paraPrIDRef"))
                p.set("paraPrIDRef", d.parapr(p.get("paraPrIDRef"), {"after_pt": (base_after + extra) / 100}))
                d.touch(p)
        tmp = os.path.join(work, f"b{rnd}.hwpx")
        d.save(tmp)
        cur = os.path.join(work, f"b{rnd}_laid.hwpx")
        relayout(hwp, tmp, cur)
        gm = [[c[0] for c in col] for col in columns(Hwpx(cur).sections[section], is_item)]
        if gm == member:
            break
        bad = [ci for ci, col in enumerate(member) if ci >= len(gm) or gm[ci] != col]
        for ci in bad:
            reserve[ci] += 300
        if log:
            log(f"단 채우기 {rnd}바퀴: 단 {len(bad)}개가 넘침 → 여유를 늘려 다시")
    final = columns(Hwpx(cur).sections[section], is_item)
    shutil.copyfile(cur, out)
    shutil.rmtree(work, ignore_errors=True)
    return {"본문 높이": H, "단 수": len(final), "단 나눔": len(breaks), "나눔 기록": notes,
            "단 아래 빈 높이(mm)": [round((H - max(c[2] for c in col)) / 283.46, 1) for col in final],
            "단 구성 같음": [[c[0] for c in col] for col in final] == member,
            "단별 문항 수": [sum(1 for c in col if c[3]) for col in final]}
