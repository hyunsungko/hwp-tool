"""Build the example exam template (skills/hwp-tool/scripts/forms/example_template.hwpx) without Hancom.

The template is what `exam build` fills: a cover section, then a two-column body section with a subject band
paragraph and three prototype items - one with a vertical choice table (4x2), one with a horizontal choice table
(2x4), and one with a 「보기」 box (3x3) above its choices - and a second subject band. All text is made up.

    python examples/make_exam_template.py [output.hwpx]
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, os.pardir, "skills", "hwp-tool", "scripts")
sys.path.insert(0, SCRIPTS)

import hwpx_direct as X  # noqa: E402

NONE = {"line": "none", "sides": "all"}


def item(n, first_table, stem, inner):
    """One prototype item: a 2x2 table (number | stem / content spanning both columns) and tables in the content cell."""
    ops = [
        {"op": "table.create", "at": "end", "data": [[f"{n}.", stem], ["", ""]], "col_widths_mm": [7, 68], "size": 10},
        {"op": "table.merge", "table": first_table, "cells": "A2:B2"},
        {"op": "table.border", "table": first_table, "cells": "A1:B2", **NONE},
    ]
    k = first_table
    for spec in inner:
        k += 1
        ops.append({"op": "table.create", "table": first_table, "cell": "A2", "size": 10, **spec["create"]})
        ops += [dict(o, table=k) for o in spec.get("then", [])]
    return ops, k + 1


def choices_vertical(texts):
    return {"create": {"data": [[m, t] for m, t in zip("①②③④", texts)], "col_widths_mm": [6, 60]},
            "then": [{"op": "table.border", "cells": "A1:B4", **NONE}]}


def main():
    out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                          os.path.join(SCRIPTS, "forms", "example_template.hwpx"))
    tmp = tempfile.mkdtemp()
    body = os.path.join(tmp, "body.hwpx")

    band = {"op": "p", "size": 12, "bold": True, "after_pt": 6, "keep_with_next": True}
    ops, nxt = [dict(band, text="제 1 과목  예시 과목 가")], 1
    o, nxt = item(1, nxt, "다음 중 한글 문서 파일 형식이 아닌 것은?",
                  [choices_vertical(["HWP", "HWPX", "HWPML", "PNG"])])
    ops += o
    o, nxt = item(2, nxt, "HWPX 파일을 이루는 압축 형식은?",
                  [{"create": {"data": [["①", "ZIP", "②", "RAR"], ["③", "7z", "④", "TAR"]],
                               "col_widths_mm": [6, 27, 6, 27]},
                    "then": [{"op": "table.border", "cells": "A1:D2", **NONE}]}])
    ops += o
    o, nxt = item(3, nxt, "<보기>에서 옳은 것만 고른 것은?",
                  [{"create": {"data": [["", "보기", ""], ["", "", ""], ["ㄱ. 예시 글입니다.", "", ""]],
                               "col_widths_mm": [27, 12, 27]},
                    "then": [{"op": "table.merge", "cells": "A2:C2"}, {"op": "table.merge", "cells": "A3:C3"}]},
                   choices_vertical(["ㄱ", "ㄴ", "ㄱ, ㄴ", "ㄴ, ㄷ"])])
    ops += o
    ops.append(dict(band, text="제 2 과목  예시 과목 나"))     # 2과목부터의 띠는 build가 떼어 과목 앞마다 넣는다
    ops.append({"op": "page_break", "before": "제 2 과목"})   # 과목마다 새 쪽(단 나누기는 쪽 나눔을 과목 경계로 본다)
    for _ in X.edit(None, body, ops):
        pass

    d = X.Hwpx(body)                          # 본문은 2단(구역 첫 문단의 단 정의)
    col = next(d.sections[0].iter(X.HP + "colPr"))
    col.set("colCount", "2")
    col.set("sameGap", "1134")
    d.save(body)

    cover = [
        {"op": "p", "text": "2026년 예시 자격시험", "size": 22, "bold": True, "align": "center", "before_pt": 120, "after_pt": 12},
        {"op": "p", "text": "필기시험 문제지", "size": 16, "align": "center", "after_pt": 40},
        {"op": "p", "text": "문제지는 표지를 제외한 2면입니다.", "size": 11, "align": "center", "after_pt": 6},
        {"op": "p", "text": "시험일: 2026년 ○월 ○일", "size": 11, "align": "center"},
        {"op": "merge", "file": body, "break": "section"},
        {"op": "footer", "text": "총 2쪽", "align": "center"},
        {"op": "page_hide", "header": True, "footer": True, "page_number": True},
    ]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    for line in X.edit(None, out, cover):
        if "실패" in line:
            sys.exit(line)
    print(out)


if __name__ == "__main__":
    main()
