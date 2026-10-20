"""캡션 달린 표 회귀 시험(2026-10-20 실태조사 보고서 HWP read가 메모리 49GB를 잡은 사고).
python test_caption_table.py           시험: data/caption_table.hwp를 메모리 1GB·60초 상한 안에서 read → 캡션·표 확인
python test_caption_table.py --make    시험 문서를 다시 만든다(한컴 없이: data/blank.hwpx → 표 3개 → hwpx2hwp)

시험 문서: 위 캡션 표, 아래 캡션 표, 캡션 없는 표. 캡션 속성(caption_attr)의 위쪽 비트는 한컴이 실제로 채우는 알 수 없는 값을
사고 문서에서 그대로 옮겼다(0x3351C738 — 옛 해석이 이 바이트를 칸 주소 51002×13137로 읽었다)."""
import os, re, struct, subprocess, sys, time, zipfile, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DOC = os.path.join(HERE, "data", "caption_table.hwp")
HIGH = 0x3351C738          # 사고 문서 캡션 속성의 위쪽 비트(아래 3비트 = 방향·크기는 0)
CAPS = {"TOP": "[ 그림 1 ] 위 캡션 시험 글", "BOTTOM": "[ 표 2 ] 아래 캡션 시험 글"}
LIMIT_MB, LIMIT_S = 1024, 60


def cell(r, c, text):
    return (f'<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="1">'
            f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" '
            f'linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">{para(text)}</hp:subList>'
            f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
            f'<hp:cellSz width="10000" height="1000"/><hp:cellMargin left="510" right="510" top="141" bottom="141"/></hp:tc>')


def para(text, inner=""):
    t = f"<hp:t>{text}</hp:t>" if text else "<hp:t/>"
    return (f'<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0">{inner}{t}</hp:run></hp:p>')


def table(n, side=None):
    rows = "".join("<hp:tr>" + "".join(cell(r, c, f"표{n} {r}-{c}") for c in range(3)) + "</hp:tr>" for r in range(2))
    cap = "" if side is None else (
        f'<hp:caption side="{side}" fullSz="0" width="8504" gap="850" lastWidth="30000">'
        f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" linkListIDRef="0" '
        f'linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">{para(CAPS[side])}</hp:subList></hp:caption>')
    return (f'<hp:tbl id="{1000 + n}" zOrder="{n}" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" '
            f'lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" rowCnt="2" colCnt="3" cellSpacing="0" '
            f'borderFillIDRef="1" noAdjust="0"><hp:sz width="30000" widthRelTo="ABSOLUTE" height="2000" '
            f'heightRelTo="ABSOLUTE" protect="0"/><hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" '
            f'allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" '
            f'vertOffset="0" horzOffset="0"/><hp:outMargin left="283" right="283" top="283" bottom="283"/>{cap}'
            f'<hp:inMargin left="510" right="510" top="141" bottom="141"/>{rows}</hp:tbl>')


def make():
    import hwpx2hwp
    src = os.path.join(HERE, "data", "blank.hwpx")
    tmp = DOC + ".tmp.hwpx"
    with zipfile.ZipFile(src) as z, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as o:
        for info in z.infolist():
            data = z.read(info)
            if info.filename == "Contents/section0.xml":
                s = data.decode("utf-8")
                body = (para("캡션 표 회귀 시험 문서") + para("", table(1, "TOP")) + para("가운데 문단")
                        + para("", table(2, "BOTTOM")) + para("", table(3)) + para("끝 문단"))
                s = s.replace("</hs:sec>", body + "</hs:sec>")
                data = s.encode("utf-8")
            o.writestr(info, data)

    class Conv(hwpx2hwp.Conv):        # 캡션 속성 위쪽 비트를 사고 문서 값으로
        def caption(self, e, lv):
            recs = super().caption(e, lv)
            if recs:
                attr, = struct.unpack_from("<I", recs[0][2], 8)
                struct.pack_into("<I", recs[0][2], 8, attr | HIGH)
            return recs

    c = Conv(tmp)
    c.save(DOC)
    c.z.close()
    os.remove(tmp)
    print("저장:", DOC, *c.warnings)


def records_check():
    """시험 문서가 사고 모양을 담고 있는지: 표 제어 바로 뒤 LIST_HEADER(캡션)의 8바이트째를 옛 해석처럼 읽으면 거대 주소."""
    import olefile
    from hwp_tool import hwp5_records, CTRL_HEADER, LIST_HEADER, MAX_CELLS
    ole = olefile.OleFileIO(DOC)
    raw = ole.openstream("BodyText/Section0").read()
    ole.close()
    recs = hwp5_records(zlib.decompress(raw, -15))
    big = [struct.unpack_from("<HH", recs[i + 1][2], 8) for i, (t, _, d) in enumerate(recs)
           if t == CTRL_HEADER and d[:4] == b" lbt" and recs[i + 1][0] == LIST_HEADER]
    assert len(big) == 2 and all(c * r > MAX_CELLS for c, r in big), f"시험 문서에 사고 모양 캡션이 없음: {big}"


def run_capped():
    """자식 프로세스로 read, 윈도 Job Object로 메모리 상한. (종료 코드, 출력, 최대 MB, 초)"""
    import ctypes
    from ctypes import wintypes as w
    k = ctypes.WinDLL("kernel32", use_last_error=True)

    class BASIC(ctypes.Structure):
        _fields_ = [("a", ctypes.c_longlong), ("b", ctypes.c_longlong), ("LimitFlags", w.DWORD),
                    ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", w.DWORD), ("f", ctypes.c_size_t),
                    ("g", w.DWORD), ("h", w.DWORD)]

    class EXT(ctypes.Structure):
        _fields_ = [("Basic", BASIC), ("Io", ctypes.c_ulonglong * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k.CreateJobObjectW.restype = w.HANDLE
    k.OpenProcess.restype = w.HANDLE
    job = k.CreateJobObjectW(None, None)
    info = EXT()
    info.Basic.LimitFlags = 0x200 | 0x2000          # JOB_MEMORY_LIMIT | KILL_ON_JOB_CLOSE
    info.JobMemoryLimit = LIMIT_MB * 2**20
    assert k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    t = time.time()
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "hwp_tool.py"), "read", "--engine", "direct", DOC],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=0x4 | 0x08000000)  # 멈춘 채·창 없이
    h = k.OpenProcess(0x1F0FFF, False, p.pid)
    assert k.AssignProcessToJobObject(job, h)
    ctypes.WinDLL("ntdll").NtResumeProcess(w.HANDLE(h))
    try:
        out, err = p.communicate(timeout=LIMIT_S)
    except subprocess.TimeoutExpired:
        p.kill(); out, err = p.communicate()
    q = EXT()
    k.QueryInformationJobObject(job, 9, ctypes.byref(q), ctypes.sizeof(q), None)
    return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), q.PeakJobMemoryUsed / 2**20, time.time() - t


def test():
    records_check()
    code, out, err, mb, sec = run_capped()
    assert code == 0, f"read 실패(코드 {code}, {mb:.0f}MB, {sec:.1f}초): {err[-300:]}"
    assert sec < LIMIT_S and mb < LIMIT_MB, (mb, sec)
    lines = out.splitlines()
    for side, text in CAPS.items():
        assert text in lines, f"캡션 글 없음: {text}"
    tables = [i for i, l in enumerate(lines) if re.fullmatch(r"\|(---\|)+", l)]
    assert len(tables) == 3 and all(lines[i] == "|---|---|---|" for i in tables), f"표 모양: {[lines[i] for i in tables]}"
    assert "| 표1 0-0 | 표1 0-1 | 표1 0-2 |" in lines and "| 표3 1-0 | 표3 1-1 | 표3 1-2 |" in lines
    top, bottom = lines.index(CAPS["TOP"]), lines.index(CAPS["BOTTOM"])
    assert top < tables[0] and bottom > tables[1], "캡션 자리(위 캡션은 표 앞, 아래 캡션은 표 뒤)"
    print(f"통과: 캡션 2·표 3(2×3), {mb:.0f}MB·{sec:.1f}초 (상한 {LIMIT_MB}MB·{LIMIT_S}초)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    make() if "--make" in sys.argv else test()
