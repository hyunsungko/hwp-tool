"""회귀 시험: 한컴을 쓰는 명령을 사람 화면(기본 데스크톱)에서 불러도 한글 창이 보이지 않고 포커스도 옮겨 가지 않는지 확인한다.
사용: python selftest_windows.py <시험용 .hwp(공개 문서)> [출력 폴더]
통과 기준: 사람 화면의 한컴 창 0(숨은 창·트레이·대화상자 포함), 한컴·이 도구로 간 전경 창·키보드 포커스 0,
끝난 뒤 새로 남은 Hwp.exe 0, 명령 모두 종료 코드 0."""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hwp_hidden  # noqa: E402
from hwp_com import _hwp_pids  # noqa: E402

NO_WINDOW = 0x08000000


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    src = os.path.abspath(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp(prefix="hwptool_selftest_")
    os.makedirs(out, exist_ok=True)
    ops = os.path.join(out, "ops.json")
    json.dump([{"op": "replace", "find": "2024", "to": "2026"},
               {"op": "header", "text": "시험 머리말", "align": "right"},
               {"op": "page_number", "pos": "bottom-center"}], open(ops, "w", encoding="utf-8"), ensure_ascii=False)
    new_ops = os.path.join(out, "new_ops.json")
    json.dump([{"op": "p", "text": "새 문서 시험", "size": 14, "bold": True, "font": "맑은 고딕"},   # 글꼴 없으면 검수가 막는다(바탕)
               {"op": "table.create", "data": [["가", "나"], ["1", "2"]], "header": True, "font": "맑은 고딕"}],
              open(new_ops, "w", encoding="utf-8"), ensure_ascii=False)
    tool = os.path.join(HERE, "hwp_tool.py")
    steps = [
        ("읽기", ["read", src, "-o", os.path.join(out, "read.md")]),
        ("HWP 고치기(한컴)", ["edit", src, "-o", os.path.join(out, "edit.hwpx"), "--ops", ops]),
        ("HWP 바꾸기(직접)", ["edit", src, "-o", os.path.join(out, "replace.hwp"), "--op", '{"op":"replace","find":"2024","to":"2026"}']),
        ("새 HWPX(직접)", ["edit", "new", "-o", os.path.join(out, "new.hwpx"), "--ops", new_ops]),
        ("쪽 그림", ["render", os.path.join(out, "edit.hwpx"), "-o", os.path.join(out, "png"), "--pages", "1-2"]),
        ("다시 열기·왕복", ["check", os.path.join(out, "edit.hwpx"), "--roundtrip", os.path.join(out, "rt")]),
    ]
    before = _hwp_pids()
    rows = []
    with hwp_hidden.WindowWatch() as w:
        for name, args in steps:
            t = time.time()
            r = subprocess.run([sys.executable, tool] + args, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", creationflags=NO_WINDOW)
            rows.append({"단계": name, "종료 코드": r.returncode, "초": round(time.time() - t, 1),
                         "오류": r.stderr.strip().splitlines()[-1][:160] if r.returncode else ""})
    time.sleep(1)
    left = _hwp_pids() - before
    res = w.result()
    # 사람이 시험 중에 다른 창을 눌러 생긴 바뀜('그 밖')은 실패로 치지 않는다. 한컴·이 도구로 간 전경·포커스는 0이어야 한다.
    ok = (res["보이는 한컴 창(최대)"] == 0 and res["사람 화면의 한컴 창(숨은 창 포함, 최대)"] == 0
          and res["한컴·도구로 간 전경"] == 0 and res["한컴·도구로 간 포커스"] == 0
          and not left and all(x["종료 코드"] == 0 for x in rows))
    rep = {"통과": ok, "단계": rows, **{k: v for k, v in res.items() if k != "자세히"}, "남은 Hwp.exe": sorted(left),
           "자세히": res["자세히"], "출력": out}
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
