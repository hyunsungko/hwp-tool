"""Make the test fixtures from made-up text, without Hancom:
tests/fixtures/sample.hwpx (direct path) and tests/fixtures/sample.hwp (HWPX -> HWP; needs Windows + pywin32).

    python tests/make_fixtures.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "skills", "hwp-tool", "scripts"))

import hwpx_direct as X  # noqa: E402

OPS = [
    {"op": "p", "text": "예시 문서", "size": 18, "bold": True, "align": "center", "after_pt": 8},
    {"op": "p", "text": "이 문서는 hwp-tool 시험용으로 지어낸 글입니다.", "size": 11},
    {"op": "p", "text": "둘째 문단: 글자 모양과 문단 모양을 바꿔 봅니다.", "size": 11, "after_pt": 6},
    {"op": "table.create", "at": "end", "data": [["항목", "값", "비고"], ["사과", "3", "빨강"], ["배", "5", ""], ["합계", "8", ""]],
     "header": True, "align": "center"},
    {"op": "table.merge", "table": 1, "cells": "B4:C4"},
    {"op": "p", "text": "표 아래 문단입니다.", "size": 11},
    {"op": "char", "find": "글자 모양", "bold": True, "color": "#1F3864"},
    {"op": "header", "text": "예시 머리말", "align": "right"},
    {"op": "footer", "text": "예시 꼬리말", "align": "center"},
    {"op": "footnote", "after": "지어낸 글입니다", "text": "각주 예시입니다."},
]


def main():
    out = os.path.join(HERE, "fixtures")
    os.makedirs(out, exist_ok=True)
    hwpx = os.path.join(out, "sample.hwpx")
    for line in X.edit(None, hwpx, OPS):
        if "실패" in line:
            sys.exit(line)
    print(hwpx)
    try:
        import hwpx2hwp
        hwp = os.path.join(out, "sample.hwp")
        for w in hwpx2hwp.convert(hwpx, hwp):
            print("warning:", w)
        print(hwp)
    except RuntimeError as e:
        print("sample.hwp not written:", e)


if __name__ == "__main__":
    main()
