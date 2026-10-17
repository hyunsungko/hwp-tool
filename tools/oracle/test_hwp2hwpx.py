"""HWP → HWPX 변환기 시험(한컴 없이): HWP → hwp2hwpx → HWPX → hwpx2hwp → HWP'로 돌아와 원래 HWP와 레코드 필드를 견준다.
hwpx2hwp가 정답지와 99% 맞으므로, 되돌아온 값이 원래와 같으면 hwp2hwpx의 값 표가 맞다는 뜻이다.
python test_hwp2hwpx.py HWP들… [--out 폴더] [--pairs]"""
import collections
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "skills", "hwp-tool", "scripts"))
import hwp5_to_hwpx as hwp2hwpx  # noqa: E402  (모듈 이름: vendor/hwp2hwpx와 겹치지 않게)
import hwpx2hwp  # noqa: E402
import test_hwpx2hwp as T  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if a != "--pairs"]
    out_dir = None
    if "--out" in args:
        i = args.index("--out")
        out_dir = args[i + 1]
        del args[i:i + 2]
    out_dir = out_dir or tempfile.mkdtemp(prefix="h2x_")
    os.makedirs(out_dir, exist_ok=True)
    stat, examples, warns, fails = collections.Counter(), {}, collections.Counter(), []
    n = 0
    for src in args:
        name = os.path.basename(os.path.dirname(src)) + "_" + os.path.splitext(os.path.basename(src))[0]
        x = os.path.join(out_dir, name + ".hwpx")
        back = os.path.join(out_dir, name + "_back.hwp")
        try:
            for w in hwp2hwpx.convert(src, x):
                warns["hwp2hwpx: " + w] += 1
            for w in hwpx2hwp.convert(x, back):
                warns["hwpx2hwp: " + w] += 1
            a, _ = T.records(back)
            b, _ = T.records(src)
        except Exception as e:
            fails.append((name, repr(e)[:200]))
            continue
        n += 1
        T.compare(a, b, stat, examples, name)
    print("문서", n, "실패", len(fails))
    for f in fails[:10]:
        print("  실패", f)
    rec = {k[0]: v for k, v in stat.items() if isinstance(k, tuple) and k[1:] == ("레코드",)}
    for k, v in sorted(((k, v) for k, v in stat.items() if not (isinstance(k, tuple) and k[1:] == ("레코드",))),
                       key=lambda kv: -kv[1])[:60]:
        tot = rec.get(k[0]) if isinstance(k, tuple) else None
        print(f"  {k}: {v}" + (f"/{tot}" if tot else ""), "|", examples.get(k))
    print("경고:")
    for w, c in warns.most_common(20):
        print(f"  {c} {w}")
    print("태그별 레코드 일치(돌아온 HWP = 원래 HWP):")
    for t in sorted(T.RECORD):
        a, b = T.RECORD[t]
        print(f"  {t}: {b}/{a} = {100 * b / a:.1f}%")
    if "--pairs" in sys.argv:
        for k in sorted(T.PAIRS, key=lambda k: -sum(T.PAIRS[k].values()))[:30]:
            print(" ", k, T.PAIRS[k].most_common(6))


if __name__ == "__main__":
    main()
