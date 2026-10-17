"""Speed on whatever machine runs it (used on CI runners): build, edit, read and convert, no Hancom.

    python tests/bench.py
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, os.pardir, "skills", "hwp-tool", "scripts")
sys.path.insert(0, SCRIPTS)

import hwp5_to_hwpx  # noqa: E402
import hwpx_direct as X  # noqa: E402
import md_write  # noqa: E402


def timed(label, fn):
    t = time.perf_counter()
    r = fn()
    print(f"{label}: {time.perf_counter() - t:.3f} s")
    return r


def main():
    tmp = tempfile.mkdtemp()
    md = open(os.path.join(HERE, os.pardir, "examples", "sample_report.md"), encoding="utf-8").read()
    big = "\n\n".join([md] * 10)
    a = os.path.join(tmp, "report.hwpx")
    timed("Markdown -> HWPX (sample report)", lambda: list(md_write.write(md, a)))
    b = os.path.join(tmp, "big.hwpx")
    timed("Markdown -> HWPX (10x sample)", lambda: list(md_write.write(big, b)))
    c = os.path.join(tmp, "edited.hwpx")
    ops = [{"op": "replace", "find": "예시", "to": "보기"}, {"op": "char", "find": "보기", "bold": True}]
    timed("edit: replace + char on 10x sample", lambda: list(X.edit(b, c, ops)))
    timed("read HWPX -> text (10x sample)", lambda: X.Hwpx(c).body())
    hwp = os.path.join(HERE, "fixtures", "sample.hwp")
    timed("HWP -> HWPX (fixture)", lambda: hwp5_to_hwpx.convert(hwp, os.path.join(tmp, "f.hwpx")))
    try:
        import hwpx2hwp
        timed("HWPX -> HWP (10x sample)", lambda: hwpx2hwp.convert(b, os.path.join(tmp, "big.hwp")))
    except RuntimeError as e:
        print("HWPX -> HWP: skipped -", e)


if __name__ == "__main__":
    main()
