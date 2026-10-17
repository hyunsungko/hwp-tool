"""코덱 왕복 검사: 태그별 + (71 컨트롤 ID / 72 종류 / 76 개체 종류)별 같음·_rest 크기·실패 예.
python test_record_roundtrip.py HWP파일들…"""
import collections
import glob
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "skills", "hwp-tool", "scripts"))
import olefile  # noqa: E402
import hwp5_records as R  # noqa: E402
from hwp5_direct import read_records  # noqa: E402

paths = sorted(p for a in sys.argv[1:] for p in glob.glob(a))
stat = collections.defaultdict(collections.Counter)
rest = collections.defaultdict(collections.Counter)
fails = {}
restex = {}
nfiles = 0
for p in paths:
    try:
        ole = olefile.OleFileIO(p)
    except Exception:
        continue
    try:
        flags = struct.unpack_from("<I", ole.openstream("FileHeader").read(), 36)[0]
        if flags & 6:
            continue
        nfiles += 1
        for st in ["DocInfo"] + ["/".join(e) for e in ole.listdir() if e[0] == "BodyText"]:
            raw = ole.openstream(st).read()
            for tag, lv, data, ctx in R.walk(read_records(zlib.decompress(raw, -15) if flags & 1 else raw)):
                if tag not in R.CODECS:
                    stat[(tag, "코덱 없음")]["레코드"] += 1
                    continue
                key = tag
                try:
                    d = R.decode(tag, data, ctx)
                    if tag == 71:
                        key = (71, d["ctrl_id"] if not d["ctrl_id"].startswith("%") else "%필드")
                    elif tag == 72:
                        key = (72, d["kind"], ctx.get("ctrl"))
                    elif tag == 76:
                        key = (76, d["ctrl_id"])
                    s = stat[key]
                    s["레코드"] += 1
                    if R.encode(tag, d) == bytes(data):
                        s["같음"] += 1
                    else:
                        fails.setdefault(key, (p.split("\\")[-1], bytes(data).hex()[:200]))
                    if d.get("_rest"):
                        s["_rest"] += 1
                        rest[key][len(d["_rest"])] += 1
                        restex.setdefault((key, len(d["_rest"])), (p.split("\\")[-1], d["_rest"][:40].hex()))
                except Exception as e:
                    s = stat[key]
                    s["레코드"] += 1
                    s["실패"] += 1
                    fails.setdefault(key, (p.split("\\")[-1], repr(e)[:120]))
    finally:
        ole.close()
print("파일", nfiles)
for k in sorted(stat, key=str):
    v = stat[k]
    print(k, dict(v), dict(sorted(rest[k].items(), key=lambda x: -x[1])[:6]) if k in rest else "")
print("--- 실패")
for k, v in fails.items():
    print(k, v)
print("--- _rest 예")
for (k, n), v in sorted(restex.items(), key=str):
    print(k, n, v)
