"""HWPX → HWP 변환기 시험: 우리 변환 결과와 정답지(같은 HWPX를 한컴이 열어 HWP로 저장한 것)를 레코드 필드로 견준다.
python test_hwpx2hwp.py <정답지 폴더> <HWPX 파일들…> [--out 폴더]
정답지 이름 = <HWPX가 있던 폴더 이름>_<파일 이름>.hwp (make_oracle 방식). 결과: 태그·필드별로 다른 건수, 구조 차이, 예."""
import collections
import json
import os
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "skills", "hwp-tool", "scripts"))
import hwp5_records as R  # noqa: E402
import hwpx2hwp  # noqa: E402
from hwp5_direct import Hwp5, read_records  # noqa: E402

SKIP = {66, 67, 68, 69, 70}


def records(path):
    d = Hwp5(path)
    raw = d.streams["DocInfo"]
    out = {"DocInfo": list(R.walk(read_records(zlib.decompress(raw, -15) if d.compressed else raw)))}
    for k, v in d.sections.items():
        out[k] = list(R.walk(v))
    return out, d


def plain(v):
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v


def fonts_of(recs):
    """DocInfo → 언어별 글꼴 이름 목록(한컴은 그 컴퓨터에 없는 글꼴을 뒤로 옮기므로 번호 대신 이름으로 견준다)."""
    counts = next(R.decode(t, d)["counts"] for t, lv, d, c in recs if t == 17)
    names = [R.decode(t, d)["name"] for t, lv, d, c in recs if t == 19]
    out, i = [], 0
    for n in counts[1:8]:
        out.append(names[i:i + n])
        i += n
    return out


def by_font_name(recs, fonts):
    """FACE_NAME은 (언어, 이름) 순으로 다시 늘어놓고, CHAR_SHAPE의 글꼴 번호는 이름으로 바꾼다."""
    out, faces = [], []
    lang, seen = 0, 0
    for t, lv, d, c in recs:
        if t == 19:
            while lang < 7 and seen >= len(fonts[lang]):
                lang, seen = lang + 1, 0
            faces.append((lang, R.decode(t, d)["name"], (t, lv, d, c)))
            seen += 1
            continue
        if faces:
            out += [x[2] for x in sorted(faces, key=lambda f: (f[0], f[1]))]
            faces = []
        out.append((t, lv, d, c))
    return out


def font_names(x, fonts):
    x = dict(x)
    x["face_ids"] = [fonts[i][f] if f < len(fonts[i]) else f for i, f in enumerate(x["face_ids"])]
    return x


def compare(ours, ref, stat, examples, src):
    fa, fb = fonts_of(ours["DocInfo"]), fonts_of(ref["DocInfo"])
    ours = dict(ours, DocInfo=by_font_name(ours["DocInfo"], fa))
    ref = dict(ref, DocInfo=by_font_name(ref["DocInfo"], fb))
    for st in sorted(set(ours) | set(ref)):
        a, b = ours.get(st, []), ref.get(st, [])
        ka = [(t, lv) for t, lv, _, _ in a]
        kb = [(t, lv) for t, lv, _, _ in b]
        if ka != kb:
            stat[("구조", st.split("/")[0])] += 1
            # 처음 어긋난 자리
            i = next((i for i, (x, y) in enumerate(zip(ka, kb)) if x != y), min(len(ka), len(kb)))
            examples.setdefault(("구조", st.split("/")[0]), (src, i, ka[i:i + 4], kb[i:i + 4], len(ka), len(kb)))
        for (t1, l1, d1, c1), (t2, l2, d2, c2) in zip(a, b):
            if t1 != t2:
                break
            if t1 in (67, 68, 69, 70):
                k = (t1, "바이트")
                stat[(k, "레코드")] += 1
                RECORD[t1][0] += 1
                RECORD[t1][1] += bytes(d1) == bytes(d2)
                if bytes(d1) != bytes(d2):
                    stat[k] += 1
                    examples.setdefault(k, (src, bytes(d1)[:48].hex(), bytes(d2)[:48].hex()))
                continue
            x1, x2 = R.decode(t1, d1, c1), R.decode(t2, d2, c2)
            if x1 is None:
                continue
            if t1 == 21:
                x1, x2 = font_names(x1, fa), font_names(x2, fb)
            sub = x1.get("ctrl_id") or x1.get("kind") or ""
            stat[((t1, sub), "레코드")] += 1
            RECORD[t1][0] += 1
            RECORD[t1][1] += all(x1.get(f) == x2.get(f) for f in set(x1) | set(x2) if f not in ("instance_id",))
            for f in sorted(set(x1) | set(x2)):
                v1, v2 = x1.get(f), x2.get(f)
                if v1 != v2:
                    k = ((t1, sub), f)
                    stat[k] += 1
                    examples.setdefault(k, (src, repr(plain(v1))[:120], repr(plain(v2))[:120]))
                    learn(k, v1, v2)


RECORD = collections.defaultdict(lambda: [0, 0])        # 태그 → [견준 레코드, 모든 필드가 같은 레코드]
PAIRS = collections.defaultdict(collections.Counter)    # (태그, 필드) → (우리 값, 정답 값) 짝 — 값 표 고치기용
BITS = collections.defaultdict(collections.Counter)     # 정수 필드에서 다른 비트: (비트, 우리→정답)


def learn(k, v1, v2):
    if isinstance(v1, list) and isinstance(v2, list) and len(v1) == len(v2):
        for a, b in zip(v1, v2):
            if a != b and not isinstance(a, (list, dict)):
                PAIRS[k][(a, b)] += 1
    elif isinstance(v1, int) and isinstance(v2, int):
        PAIRS[k][(v1, v2)] += 1
        x = v1 ^ v2
        for bit in range(32):
            if x >> bit & 1:
                BITS[k][(bit, v1 >> bit & 1, v2 >> bit & 1)] += 1


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if a != "--pairs"]
    out_dir = None
    if "--out" in args:
        i = args.index("--out")
        out_dir = args[i + 1]
        del args[i:i + 2]
    oracle, files = args[0], args[1:]
    out_dir = out_dir or tempfile.mkdtemp(prefix="x2h_")
    os.makedirs(out_dir, exist_ok=True)
    stat, examples, warns, fails = collections.Counter(), {}, collections.Counter(), []
    n = 0
    for src in files:
        name = os.path.basename(os.path.dirname(src)) + "_" + os.path.splitext(os.path.basename(src))[0] + ".hwp"
        ref = os.path.join(oracle, name)
        if not os.path.exists(ref):
            continue
        ours = os.path.join(out_dir, name)
        try:
            for w in hwpx2hwp.convert(src, ours):
                warns[w] += 1
            a, _ = records(ours)
            b, _ = records(ref)
        except Exception as e:
            fails.append((name, repr(e)[:200]))
            continue
        n += 1
        compare(a, b, stat, examples, name)
    print("문서", n, "실패", len(fails))
    for f in fails[:10]:
        print("  실패", f)
    rec = {k[0]: v for k, v in stat.items() if isinstance(k, tuple) and k[1:] == ("레코드",)}
    rows = sorted(((k, v) for k, v in stat.items() if not (isinstance(k, tuple) and k[1:] == ("레코드",))),
                  key=lambda kv: -kv[1])
    for k, v in rows:
        tot = rec.get(k[0]) if isinstance(k, tuple) else None
        print(f"  {k}: {v}" + (f"/{tot}" if tot else ""), "|", examples.get(k))
    print("경고:")
    for w, c in warns.most_common():
        print(f"  {c} {w}")
    if "--pairs" in sys.argv:
        print("값 짝(우리→정답):")
        for k in sorted(PAIRS, key=lambda k: -sum(PAIRS[k].values()))[:40]:
            print(" ", k, PAIRS[k].most_common(8))
            if BITS[k]:
                print("     비트(자리, 우리, 정답):", BITS[k].most_common(8))
    print("태그별 레코드 일치(모든 필드가 정답지와 같음, 고유 아이디 제외):")
    for t in sorted(RECORD):
        a, b = RECORD[t]
        print(f"  {t}: {b}/{a} = {100 * b / a:.1f}%")
    json.dump({"문서": n, "다름": {str(k): v for k, v in stat.items()}, "경고": dict(warns),
               "레코드 일치": {t: v for t, v in RECORD.items()}},
              open(os.path.join(out_dir, "_비교.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
