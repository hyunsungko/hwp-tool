"""HWP → HWPX 변환기 정답지 대조: 우리 HWPX와 한컴이 같은 HWP를 HWPX로 저장한 것(정답지)을 요소·속성으로 견준다.
python test_hwp2hwpx_oracle.py <정답지 폴더> <HWP들…> [--out 폴더]
정답지 이름 = <HWP가 있던 폴더 이름>_<파일 이름>.hwpx. 결과: 요소별 빠짐·더함, (요소, 속성)별 다른 값 건수와 예."""
import collections
import difflib
import os
import re
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "skills", "hwp-tool", "scripts"))
from lxml import etree  # noqa: E402
import hwp5_to_hwpx as hwp2hwpx  # noqa: E402  (모듈 이름: vendor/hwp2hwpx와 겹치지 않게)

SKIP_ATTR = {("hp:p", "id"), ("hh:head", "version")}


def flat(xml):
    """XML → [(요소 이름, {속성}, 글)] 문서 순서. 이름공간은 접두어로."""
    root = etree.fromstring(xml)
    pre = {"head": "hh", "paragraph": "hp", "core": "hc", "section": "hs", "app": "ha"}
    out = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        q = etree.QName(el)
        p = next((v for k, v in pre.items() if (q.namespace or "").endswith("/" + k)), "x")
        name = f"{p}:{q.localname}"
        a = {etree.QName(k).localname: v for k, v in el.attrib.items()}
        out.append((name, a, (el.text or "").strip() if len(el) == 0 else ""))
    return out


def parts(path):
    z = zipfile.ZipFile(path)
    names = z.namelist()
    out = {"header": z.read("Contents/header.xml")}
    for n in sorted((n for n in names if re.match(r"Contents/section\d+\.xml$", n)), key=lambda n: int(re.findall(r"\d+", n)[-1])):
        out[n.split("/")[-1]] = z.read(n)
    return out


def compare(a, b, stat, ex, src):
    for part in sorted(set(a) | set(b)):
        if part not in a or part not in b:
            stat[("부분 없음", part)] += 1
            continue
        fa, fb = flat(a[part]), flat(b[part])
        sm = difflib.SequenceMatcher(None, [x[0] for x in fa], [x[0] for x in fb], autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                for x, y in zip(fa[i1:i2], fb[j1:j2]):
                    stat[(x[0], "요소")] += 1
                    for k in set(x[1]) | set(y[1]):
                        if (x[0], k) in SKIP_ATTR:
                            continue
                        if x[1].get(k) != y[1].get(k):
                            key = (x[0], k)
                            stat[key] += 1
                            ex.setdefault(key, (src, x[1].get(k), y[1].get(k)))
                    if x[2] != y[2]:
                        stat[(x[0], "#글")] += 1
                        ex.setdefault((x[0], "#글"), (src, x[2][:60], y[2][:60]))
            else:
                for x in fa[i1:i2]:
                    stat[(x[0], "우리만")] += 1
                    ex.setdefault((x[0], "우리만"), (src,))
                for y in fb[j1:j2]:
                    stat[(y[0], "정답지만")] += 1
                    ex.setdefault((y[0], "정답지만"), (src,))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    out_dir = None
    if "--out" in args:
        i = args.index("--out")
        out_dir = args[i + 1]
        del args[i:i + 2]
    oracle, files = args[0], args[1:]
    out_dir = out_dir or tempfile.mkdtemp(prefix="h2xo_")
    os.makedirs(out_dir, exist_ok=True)
    stat, ex, warns, fails = collections.Counter(), {}, collections.Counter(), []
    n = 0
    for src in files:
        name = os.path.basename(os.path.dirname(src)) + "_" + os.path.splitext(os.path.basename(src))[0]
        ref = os.path.join(oracle, name + ".hwpx")
        if not os.path.exists(ref):
            continue
        ours = os.path.join(out_dir, name + ".hwpx")
        try:
            for w in hwp2hwpx.convert(src, ours):
                warns[w] += 1
            compare(parts(ours), parts(ref), stat, ex, name)
        except Exception as e:
            fails.append((name, repr(e)[:200]))
            continue
        n += 1
    print("문서", n, "실패", len(fails))
    for f in fails[:10]:
        print("  실패", f)
    total = {k[0]: v for k, v in stat.items() if k[1] == "요소"}
    rows = sorted(((k, v) for k, v in stat.items() if k[1] != "요소"), key=lambda kv: -kv[1])
    for k, v in rows[:80]:
        print(f"  {k}: {v}" + (f"/{total[k[0]]}" if k[0] in total and k[1] not in ("우리만", "정답지만") else ""), "|", ex.get(k))
    print("경고:")
    for w, c in warns.most_common(20):
        print(f"  {c} {w}")
    same = sum(total.values())
    print("맞춘 요소", same, "· 우리만", sum(v for k, v in stat.items() if k[1] == "우리만"),
          "· 정답지만", sum(v for k, v in stat.items() if k[1] == "정답지만"))


if __name__ == "__main__":
    main()
