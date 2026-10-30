"""두 문서 글 비교(diff). 문단과 표 칸을 단위로, 빈칸을 뺀 글자로 맞대어 띄어쓰기만 다른 곳은 바뀜으로 세지 않는다.

- 단위: 문단 한 줄, 표 칸 하나(「표2 3행 1칸」). 표 안 변경도 칸 주소로 잡는다.
- 맞대기: 단위를 빈칸 뺀 글로 먼저 맞추고, 다른 묶음만 빈칸 뺀 글자 단위로 비교한 뒤 바뀐 글자가 걸린 낱말을 원래 모양으로 돌려 보인다.
- 띄어쓰기만 다른 단위는 「띄어쓰기만」으로 따로 센다(「합의하였다 ↔ 합의 하였다」).
"""
import re
from difflib import SequenceMatcher

_ws = re.compile(r"\s+")


def units(blocks):
    """read 결과 블록 → [(자리, 글)]"""
    out, para, tbl = [], 0, 0
    for kind, value in blocks:
        if kind in ("p", "md"):
            for line in str(value).split("\n"):
                para += 1
                if line.strip():
                    out.append((f"문단 {para}", line.strip()))
        else:
            tbl += 1
            for r, row in enumerate(value, 1):
                for c, cell in enumerate(row, 1):
                    if cell and cell.strip():
                        out.append((f"표{tbl} {r}행 {c}칸", _ws.sub(" ", cell).strip()))
    return out


def _words(us):
    """단위 목록 → 낱말 [(자리, 낱말)], 빈칸 뺀 글자열, 글자마다 낱말 번호"""
    words, chars, owner = [], [], []
    for where, text in us:
        for w in text.split():
            words.append((where, w))
            chars.append(w)
            owner += [len(words) - 1] * len(w)
    return words, "".join(chars), owner


def _join(ws):
    return " ".join(w for _, w in ws)


def diff(a_blocks, b_blocks):
    A, B = units(a_blocks), units(b_blocks)
    key = lambda u: _ws.sub("", u[1])
    sm = SequenceMatcher(None, [key(u) for u in A], [key(u) for u in B], autojunk=False)
    changes, spacing = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for (wa, ta), (wb, tb) in zip(A[i1:i2], B[j1:j2]):
                if ta != tb:
                    spacing.append({"앞 자리": wa, "뒤 자리": wb, "앞": ta, "뒤": tb})
            continue
        wa, sa, oa = _words(A[i1:i2])
        wb, sb, ob = _words(B[j1:j2])
        cm = SequenceMatcher(None, sa, sb, autojunk=False)
        for t, x1, x2, y1, y2 in cm.get_opcodes():
            if t == "equal":
                continue
            ga = sorted({oa[k] for k in range(x1, x2)})
            gb = sorted({ob[k] for k in range(y1, y2)})
            near = lambda o, x: o[min(x, len(o) - 1)] if o else None   # 한쪽이 비면 자리는 이웃 낱말에서
            pa, pb = (ga[0] if ga else near(oa, x1)), (gb[0] if gb else near(ob, y1))
            changes.append({"앞 자리": wa[pa][0] if pa is not None else "", "뒤 자리": wb[pb][0] if pb is not None else "",
                            "앞": _join(wa[k] for k in ga), "뒤": _join(wb[k] for k in gb)})
    return {"요약": {"바뀜": len(changes), "띄어쓰기만": len(spacing), "앞 단위": len(A), "뒤 단위": len(B)},
            "바뀜": changes, "띄어쓰기만": spacing}


def to_text(res):
    s = res["요약"]
    lines = [f"바뀜 {s['바뀜']}곳, 띄어쓰기만 다름 {s['띄어쓰기만']}곳 (단위 {s['앞 단위']} → {s['뒤 단위']})"]
    for c in res["바뀜"]:
        where = c["앞 자리"] if c["앞 자리"] == c["뒤 자리"] else f"{c['앞 자리'] or '-'} → {c['뒤 자리'] or '-'}"
        lines.append(f"- [{where}] {c['앞'] or '(없음)'}  →  {c['뒤'] or '(없음)'}")
    return "\n".join(lines)
