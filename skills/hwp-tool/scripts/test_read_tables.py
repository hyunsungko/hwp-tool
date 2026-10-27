"""읽기 표 구조 회귀 시험(2026-10-26 0032): 병합 칸은 HTML 표(rowspan·colspan), JSON에 병합 정보, 한 칸 겉치레 표는 글로,
kordoc 감싸기(버전 고정·diff). python test_read_tables.py"""
import json, os, re, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hwp_tool  # noqa: E402

FORM = os.path.join(HERE, "data", "gov24_별지27_빈양식.hwp")


def run(*a):
    p = subprocess.run([sys.executable, os.path.join(HERE, "hwp_tool.py"), *a], capture_output=True)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def main():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("통과 " if cond else "실패 ") + name + (f" — {detail}" if detail and not cond else ""))
        ok &= bool(cond)

    code, md, err = run("read", FORM)
    check("병합 칸 표는 HTML 표", code == 0 and '<td rowspan="5">신고인</td>' in md and 'colspan="10"' in md, md[:300])
    check("병합 칸 수 = 한컴 저장본(55)", len(re.findall(r"<t[dh][^>]*\b(?:rowspan|colspan)=", md)) == 55)
    code, js, err = run("read", FORM, "--format", "json")
    blocks = json.loads(js)["블록"]
    check("JSON에 병합 정보", any(b.get("병합") for b in blocks) and [3, 0, 5, 1] in next(b["병합"] for b in blocks if b.get("병합")))
    # 한 칸 겉치레 표는 글로: 제목 상자(1×1 표) 안 글이 Markdown 표 없이 나온다
    T = hwp_tool.Table([["가", "나"]], [(0, 0, 1, 1, [("p", "가")]), (0, 1, 1, 1, [("p", "나")])])
    check("병합 없는 표는 Markdown 표", "| 가 | 나 |" in hwp_tool.to_markdown([("table", T)]))
    with tempfile.TemporaryDirectory() as td:
        import hwpx_direct as X
        out = os.path.join(td, "box.hwpx")
        list(X.edit(None, out, [{"op": "table.create", "at": "end", "data": [["제목 상자 글"]]}, {"op": "p", "text": "본문"}]))
        code, md, err = run("read", out)
        check("한 칸 표는 글로 꺼냄", code == 0 and "제목 상자 글" in md and "|" not in md, md)
    code, out, err = run("kordoc", "--version")
    check(f"kordoc {hwp_tool.KORDOC_VERSION} 고정", code == 0 and out.strip() == hwp_tool.KORDOC_VERSION, out + err)
    code, out, err = run("diff", FORM, FORM)
    check("diff(같은 문서) 차이 없음", code == 0 and "변경 0" in out, out + err)
    print("모두 통과" if ok else "실패 있음")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
