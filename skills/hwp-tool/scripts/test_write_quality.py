"""write·edit 문서 품질과 검수 회귀 시험(2026-10-21: 보고서가 날것 같은 문서로 나간 사고).
python test_write_quality.py [출력 폴더]

1. write(보고서): 검수 오류 0, 바탕 글꼴 0, 2열 표 이름표 칸, 숫자 열 오른쪽, 같은 분류 세로 합침, 합계 굵게, 따옴표 그대로
2. write --engine com(한컴 HTML 가져오기, 한컴이 있을 때): 검수가 '서식 없는 표'로 막고(종료 코드 3), '&'로 깨진 따옴표는 없음
3. edit new: 앞 문단의 page_break_before가 표·다음 문단으로 새지 않음(2쪽, 검수 오류 0)
4. edit new: 제목만 있는 쪽을 검수가 잡음(종료 코드 3, 한컴이 있을 때)
5. 정부24 별지 제27호서식(공개 빈 양식, 짧은 개체 레코드): convert --engine direct·read가 됨
"""
import json, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hwp_tool  # noqa: E402
import hwpx_direct as X  # noqa: E402
import hwp_qa  # noqa: E402

HANCOM = hwp_tool.hancom_installed()


def run(*args):
    p = subprocess.run([sys.executable, os.path.join(HERE, "hwp_tool.py"), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=0x08000000)
    return p.returncode, p.stdout, p.stderr


def cell_info(d, tbl):
    """(행, 열) → (글, 행합침, 가로 정렬, 굵게)"""
    aligns = {pp.get("id"): pp.find(X.HH + "align").get("horizontal") for pp in d.reflist("paraProperties")}
    bolds = {cp.get("id"): cp.find(X.HH + "bold") is not None for cp in d.reflist("charProperties")}
    out = {}
    for tc in tbl.iter(X.HP + "tc"):
        a, s = tc.find(X.HP + "cellAddr"), tc.find(X.HP + "cellSpan")
        p = tc.find(f"{X.HP}subList/{X.HP}p")
        runs = [r for r in tc.iter(X.HP + "run") if "".join(t.text or "" for t in r.findall(X.HP + "t")).strip()]
        out[(int(a.get("rowAddr")), int(a.get("colAddr")))] = (
            "".join(X.para_text(q) for q in tc.iter(X.HP + "p")), int(s.get("rowSpan")), aligns.get(p.get("paraPrIDRef")),
            any(bolds.get(r.get("charPrIDRef")) for r in runs))
    return out


def main(out):
    os.makedirs(out, exist_ok=True)
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("통과 " if cond else "실패 ") + name + (f" — {detail}" if detail and not cond else ""))
        ok &= bool(cond)

    # 1. write 보고서
    doc = os.path.join(out, "write_sample.hwpx")
    code, _, err = run("write", os.path.join(HERE, "data", "write_sample.md"), "-o", doc, "--check", os.path.join(out, "qa1"))
    check("write 종료 코드 0·검수 오류 0", code == 0 and "검수: 오류 0건" in err, err[-600:])
    d = X.Hwpx(doc)
    check("서식 검사(바탕 글꼴·서식 없는 표·남은 기호) 0", not hwp_qa.xml_checks(d, strict=True), hwp_qa.xml_checks(d))
    text = d.plain_text()
    check("따옴표 그대로", '"상담", "추천", ‘지원’' in text and "&" not in text)
    tables = d.tables()

    def table_with(s):
        return next(t for t in tables if s in "".join(X.para_text(p) for p in t.iter(X.HP + "p")))
    kv = cell_info(d, table_with("사업장 소재지"))
    check("2열 표: 이름뿐인 머리줄 뺌·이름표 굵게 가운데", kv[(0, 0)][0] == "상호" and kv[(0, 0)][2] == "CENTER" and kv[(0, 0)][3],
          kv.get((0, 0)))
    budget = table_with("광고 수입")
    b = cell_info(d, budget)
    check("같은 분류 세로 합침", b[(1, 0)][0] == "수입" and b[(1, 0)][1] == 3 and b[(4, 0)][0] == "지출" and b[(4, 0)][1] == 4,
          {k: v for k, v in b.items() if k[1] == 0})
    check("숫자 열 오른쪽", all(b[(r, 2)][2] == "RIGHT" for r in range(1, 8)), [b[(r, 2)] for r in range(1, 8)])
    check("합계 줄 굵게", b[(3, 1)][3] and b[(3, 2)][3] and b[(7, 2)][3])
    check("머리줄 가운데·굵게", b[(0, 0)][2] == "CENTER" and b[(0, 2)][3])

    # 2. 한컴 HTML 가져오기는 검수가 막는다
    if HANCOM:
        code, _, err = run("write", os.path.join(HERE, "data", "write_sample.md"), "-o", os.path.join(out, "com.hwpx"),
                           "--engine", "com")
        check("한컴 HTML 길: 검수가 막음(코드 3, 서식 없는 표)", code == 3 and "서식 없는 표" in err, err[-400:])
        check("한컴 HTML 길: & 로 깨진 따옴표 없음", "& 로 깨진" not in err and "&" not in X.Hwpx(os.path.join(out, "com.hwpx")).plain_text())

    # 3. page_break_before가 새지 않음
    leak = [{"op": "p", "text": "첫 장 본문 문단", "font": "맑은 고딕", "size": 11},
            {"op": "p", "text": "2. 둘째 장", "font": "맑은 고딕", "size": 14, "bold": True, "page_break_before": True,
             "keep_with_next": True},
            {"op": "table.create", "at": "end", "data": [["항목", "내용"], ["가", "나"], ["다", "라"]], "font": "맑은 고딕",
             "size": 10, "header": True},
            {"op": "p", "text": "표 다음 문단", "font": "맑은 고딕", "size": 11}]
    ops = os.path.join(out, "leak_ops.json")
    json.dump(leak, open(ops, "w", encoding="utf-8"), ensure_ascii=False)
    code, _, err = run("edit", "new", "-o", os.path.join(out, "leak.hwpx"), "--ops", ops)
    d = X.Hwpx(os.path.join(out, "leak.hwpx"))
    pbb = {pp.get("id") for pp in d.reflist("paraProperties")
           if pp.find(X.HH + "breakSetting") is not None and pp.find(X.HH + "breakSetting").get("pageBreakBefore") == "1"}
    marked = [X.para_text(p).replace(X.OBJ, "[표]") for p in d.body() if p.get("paraPrIDRef") in pbb]
    check("쪽 나눔은 제목 문단에만", marked == ["2. 둘째 장"], marked)
    if HANCOM:
        check("새는 쪽 없음(2쪽·검수 오류 0)", code == 0 and re.search(r"검수: 오류 0건 · 경고 \d+건 · 2쪽", err), err[-400:])

        # 4. 제목만 있는 쪽은 검수가 잡는다
        bad = [{"op": "p", "text": "본문 하나", "font": "맑은 고딕", "size": 11},
               {"op": "p", "text": "외톨이 장 제목", "font": "맑은 고딕", "size": 16, "bold": True, "page_break_before": True},
               {"op": "p", "text": "다음 쪽 본문", "font": "맑은 고딕", "size": 11, "page_break_before": True}]
        json.dump(bad, open(ops, "w", encoding="utf-8"), ensure_ascii=False)
        code, _, err = run("edit", "new", "-o", os.path.join(out, "bad.hwpx"), "--ops", ops)
        check("제목만 있는 쪽을 검수가 잡음(코드 3)", code == 3 and "제목만 있는 쪽" in err, err[-400:])

    # 5. 정부24 양식(짧은 개체 레코드)
    form = os.path.join(HERE, "data", "gov24_별지27_빈양식.hwp")
    code, _, err = run("convert", form, "-o", os.path.join(out, "form.hwpx"), "--engine", "direct")
    check("정부24 양식 direct 변환", code == 0, err[-400:])
    code, md, err = run("read", form)
    check("정부24 양식 읽기", code == 0 and "직업정보제공 사업신고서" in md, err[-400:])

    # 6. 도장: 글 앞으로 띄워 '(서명 또는 인)' 위에 겹치기
    from PIL import Image, ImageDraw
    seal = os.path.join(out, "seal.png")
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((8, 8, 192, 192), outline=(210, 30, 30, 255), width=12)
    im.save(seal)
    sealed = os.path.join(out, "sealed.hwpx")
    code, _, err = run("edit", form, "-o", sealed, "--op", json.dumps({"op": "image", "path": seal, "width_mm": 15, "over": "인)"}))
    pics = X.Hwpx(sealed).sections[0].iter(X.HP + "pic")
    pic = next(pics, None)
    pos = pic.find(X.HP + "pos") if pic is not None else None
    check("도장 글 앞 띄움", code == 0 and pos is not None and pos.get("treatAsChar") == "0" and
          pic.get("textWrap") == "IN_FRONT_OF_TEXT" and "위에 겹침" in err, err[-400:])
    print("모두 통과" if ok else "실패 있음")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="hwp_write_test_")))
