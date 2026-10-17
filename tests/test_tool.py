"""hwp-tool tests that run without Hancom Office (Windows, Linux, macOS).

    python -m unittest discover -s tests -v

HWP writing (HWPX -> HWP) uses Windows structured storage, so those tests run only on Windows with pywin32.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
SCRIPTS = os.path.join(ROOT, "skills", "hwp-tool", "scripts")
TOOL = os.path.join(SCRIPTS, "hwp_tool.py")
FIX = os.path.join(HERE, "fixtures")
sys.path.insert(0, SCRIPTS)

import hwp5_records as R  # noqa: E402
import hwp5_to_hwpx  # noqa: E402
import hwpx_direct as X  # noqa: E402
from hwp5_direct import Hwp5, read_records  # noqa: E402

try:
    import pythoncom  # noqa: F401
    HWP_WRITE = True
except ImportError:
    HWP_WRITE = False


def cli(*args):
    r = subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise AssertionError(f"hwp_tool {' '.join(args)} failed ({r.returncode}):\n{r.stderr[-2000:]}")
    return r.stdout


def texts(hwpx):
    d = X.Hwpx(hwpx)
    return [X.para_text(p).replace(X.OBJ, "").strip() for sec in d.sections for p in sec.iter(X.HP + "p")]


class DirectHwpx(unittest.TestCase):
    """HWPX: new documents and edits as XML, no Hancom."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="hwptool_test_")

    def path(self, name):
        return os.path.join(self.tmp, name)

    def edit(self, src, out, ops):
        lines = list(X.edit(src, out, ops))
        bad = [x for x in lines if "실패" in x]
        self.assertEqual(bad, [], bad)
        return out

    def test_new_document(self):
        out = self.edit(None, self.path("new.hwpx"), [
            {"op": "p", "text": "제목", "size": 16, "bold": True, "align": "center"},
            {"op": "p", "text": "본문 문단"},
            {"op": "table.create", "at": "end", "data": [["a", "b"], ["c", "d"]]},
        ])
        t = texts(out)
        self.assertIn("제목", t)
        self.assertIn("본문 문단", t)
        o = json.loads(cli("outline", out))
        self.assertEqual(len(o["표"]), 1)
        self.assertEqual((o["표"][0]["행"], o["표"][0]["열"]), (2, 2))

    def test_read_markdown_keeps_table(self):
        md = cli("read", os.path.join(FIX, "sample.hwpx"))
        self.assertIn("| 항목 | 값 | 비고 |", md)
        self.assertIn("표 아래 문단입니다.", md)

    def test_table_ops(self):
        src = os.path.join(FIX, "sample.hwpx")
        out = self.edit(src, self.path("tbl.hwpx"), [
            {"op": "table.insert_row", "table": 1, "cell": "A2", "where": "below"},
            {"op": "table.insert_col", "table": 1, "cell": "A1", "where": "right"},
            {"op": "table.delete_col", "table": 1, "cell": "B1"},
            {"op": "table.fill", "table": 1, "cells": "A1:C1", "color": "#DCE6F2"},
            {"op": "table.border", "table": 1, "cells": "A1:C2", "line": "solid", "width": "0.5mm", "color": "#C00000", "sides": "outside"},
            {"op": "table.cell_text", "table": 1, "cell": "A3", "text": "새 줄"},
        ])
        t = X.Table(X.Hwpx(out), X.Hwpx(out).tables()[0])
        self.assertEqual((t.rows, t.cols), (5, 3))
        self.assertIn("새 줄", texts(out))

    def test_replace_and_char(self):
        out = self.edit(os.path.join(FIX, "sample.hwpx"), self.path("rep.hwpx"), [
            {"op": "replace", "find": "사과", "to": "자두"},
            {"op": "char", "find": "자두", "bold": True},
        ])
        t = " ".join(texts(out))
        self.assertIn("자두", t)
        self.assertNotIn("사과", t)

    def test_original_untouched(self):
        src = os.path.join(FIX, "sample.hwpx")
        before = open(src, "rb").read()
        self.edit(src, self.path("copy.hwpx"), [{"op": "replace", "find": "배", "to": "감"}])
        self.assertEqual(open(src, "rb").read(), before)


class HwpRead(unittest.TestCase):
    """HWP 5 binary: reading, record codec, HWP -> HWPX, no Hancom."""

    hwp = os.path.join(FIX, "sample.hwp")

    def test_read(self):
        out = cli("read", self.hwp)
        for s in ("예시 문서", "| 사과 | 3 | 빨강 |", "표 아래 문단입니다."):
            self.assertIn(s, out)

    def test_record_codec_round_trip(self):
        d = Hwp5(self.hwp)
        raw = d.streams["DocInfo"]
        streams = [zlib.decompress(raw, -15) if d.compressed else raw] + list(d.sections.values())
        n = 0
        for buf in streams:
            recs = read_records(buf) if isinstance(buf, (bytes, bytearray)) else buf
            for tag, lv, data, ctx in R.walk(recs):
                if tag in R.CODECS:
                    self.assertEqual(bytes(R.encode(tag, R.decode(tag, data, ctx))), bytes(data), f"tag {tag}")
                    n += 1
        self.assertGreater(n, 20)

    def test_hwp_to_hwpx(self):
        out = os.path.join(tempfile.mkdtemp(), "conv.hwpx")
        hwp5_to_hwpx.convert(self.hwp, out)
        t = texts(out)
        for s in ("예시 문서", "표 아래 문단입니다.", "사과"):
            self.assertIn(s, t)

    def test_edit_hwp_saves_hwpx(self):
        out = os.path.join(tempfile.mkdtemp(), "edit.hwpx")
        cli("edit", self.hwp, "-o", out, "--engine", "direct", "--layout", "none",
            "--op", json.dumps({"op": "replace", "find": "사과", "to": "자두"}, ensure_ascii=False))
        self.assertIn("자두", " ".join(texts(out)))


@unittest.skipUnless(HWP_WRITE, "HWP writing needs Windows + pywin32")
class HwpWrite(unittest.TestCase):
    def test_hwpx_to_hwp_and_back(self):
        tmp = tempfile.mkdtemp()
        hwp = os.path.join(tmp, "out.hwp")
        cli("convert", os.path.join(FIX, "sample.hwpx"), "-o", hwp, "--engine", "direct")
        back = os.path.join(tmp, "back.hwpx")
        hwp5_to_hwpx.convert(hwp, back)
        self.assertEqual([x for x in texts(back) if x], [x for x in texts(os.path.join(FIX, "sample.hwpx")) if x])


class Exam(unittest.TestCase):
    """exam build without typesetting: fill the example template (pure XML)."""

    def test_assemble(self):
        import hwp_exam
        cfg = hwp_exam.load_form("example.json")
        data = json.load(open(os.path.join(ROOT, "examples", "exam_items.json"), encoding="utf-8"))
        out = os.path.join(tempfile.mkdtemp(), "exam.hwpx")
        n = hwp_exam.assemble(os.path.join(SCRIPTS, "forms", "example_template.hwpx"), data, cfg, out)
        self.assertEqual(n, sum(len(g) for g in data["과목"]))
        its = hwp_exam.items_in(X.Hwpx(out), cfg)
        self.assertEqual(len(its), n)

    def test_template_builder(self):
        out = os.path.join(tempfile.mkdtemp(), "t.hwpx")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "examples", "make_exam_template.py"), out],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertTrue(os.path.getsize(out) > 1000)


if __name__ == "__main__":
    unittest.main()
