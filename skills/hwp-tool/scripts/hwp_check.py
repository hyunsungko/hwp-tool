"""검증 — 쪽 그림(원본·바꾼 뒤 나란히), 한컴으로 다시 열기, HWP↔HWPX 왕복. 모두 한컴오피스가 있어야 한다."""
import os
import tempfile

from hwp_com import Session, ComDoc


def _open(hwp, path):
    if not hwp.Open(os.path.abspath(path), "", "forceopen:true;suspendpassword:true;versionwarning:false"):
        raise RuntimeError("한글이 파일을 열지 못함: " + path)


def to_pdf(path, pdf):
    with Session() as hwp:
        _open(hwp, path)
        if not hwp.SaveAs(os.path.abspath(pdf), "PDF", ""):
            raise RuntimeError("PDF 저장 실패: " + path)
        return hwp.PageCount


def render(path, outdir, dpi=80, pages=None, prefix=None):
    """쪽마다 PNG(한컴 CreatePageImage — 쪽 번호 0부터, bmp로만 저장돼 PNG로 바꾼다).
    PDF로 그리면 인쇄 설정(모아 찍기 등)을 따라 쪽이 합쳐질 수 있어 쓰지 않는다."""
    from PIL import Image
    os.makedirs(outdir, exist_ok=True)
    prefix = prefix or os.path.splitext(os.path.basename(path))[0]
    out = []
    with tempfile.TemporaryDirectory() as td, Session() as hwp:
        _open(hwp, path)
        for i in range(hwp.PageCount):
            if pages and (i + 1) not in pages:
                continue
            bmp = os.path.join(td, f"{i}.bmp")
            if not hwp.CreatePageImage(bmp, i, dpi, 24, "bmp"):
                raise RuntimeError(f"{i + 1}쪽 그림 실패")
            p = os.path.join(outdir, f"{prefix}_p{i + 1:03d}.png")
            with Image.open(bmp) as im:
                im.save(p)
            out.append(p)
    return out


def compare(a, b, outdir, dpi=70):
    """두 문서의 쪽 그림을 나란히 붙여 저장하고, 쪽마다 바뀐 화소 비율을 돌려준다."""
    from PIL import Image, ImageChops, ImageDraw
    ra = render(a, os.path.join(outdir, "_a"), dpi, prefix="a")
    rb = render(b, os.path.join(outdir, "_b"), dpi, prefix="b")
    rows = []
    for i in range(max(len(ra), len(rb))):
        ia = Image.open(ra[i]).convert("RGB") if i < len(ra) else None
        ib = Image.open(rb[i]).convert("RGB") if i < len(rb) else None
        w = max(x.width for x in (ia, ib) if x)
        h = max(x.height for x in (ia, ib) if x)
        canvas = Image.new("RGB", (w * 2 + 30, h + 28), "white")
        d = ImageDraw.Draw(canvas)
        for k, (img, label) in enumerate(((ia, "원본"), (ib, "바꾼 뒤"))):
            x = k * (w + 30)
            if img:
                canvas.paste(img, (x, 28))
            d.text((x + 4, 6), f"{label} {i + 1}쪽" if img else f"{label} (쪽 없음)", fill="black")
        p = os.path.join(outdir, f"비교_p{i + 1:03d}.png")
        canvas.save(p)
        changed = None
        if ia and ib and ia.size == ib.size:
            diff = ImageChops.difference(ia, ib).convert("L").point(lambda v: 255 if v > 40 else 0)
            changed = round(sum(1 for v in diff.getdata() if v) / (ia.width * ia.height) * 100, 2)
        rows.append({"쪽": i + 1, "그림": p, "바뀐 화소 %": changed})
    return rows


def reopen(path):
    """한컴으로 다시 열어 쪽수·글자 수·표·누름틀 수를 센다(깨졌으면 예외)."""
    with Session() as hwp:
        _open(hwp, path)
        d = ComDoc(hwp)
        text = hwp.GetTextFile("TEXT", "") or ""
        fields = [n for n in (hwp.GetFieldList(0, 0) or "").split("\x02") if n]
        return {"열림": True, "쪽수": hwp.PageCount, "글자": len(text.replace("\r\n", "")),
                "표": len(d.tables()), "누름틀": len(fields)}


def roundtrip(path, outdir):
    """HWP→HWPX→HWP(또는 HWPX→HWP→HWPX)로 저장해 다시 연 결과가 원본과 같은지 본다."""
    os.makedirs(outdir, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(path))
    other = ".hwpx" if ext.lower() == ".hwp" else ".hwp"
    fmt = {".hwp": "HWP", ".hwpx": "HWPX"}
    mid = os.path.join(outdir, stem + "_왕복1" + other)
    back = os.path.join(outdir, stem + "_왕복2" + ext.lower())
    with Session() as hwp:
        _open(hwp, path)
        t0 = hwp.GetTextFile("TEXT", "")
        hwp.SaveAs(os.path.abspath(mid), fmt[other], "")
    with Session() as hwp:
        _open(hwp, mid)
        t1 = hwp.GetTextFile("TEXT", "")
        hwp.SaveAs(os.path.abspath(back), fmt[ext.lower()], "")
    with Session() as hwp:
        _open(hwp, back)
        t2 = hwp.GetTextFile("TEXT", "")
    r0, r1, r2 = reopen(path), reopen(mid), reopen(back)
    return {"원본": r0, other.lstrip("."): r1, "되돌림": r2,
            "글 같음": t0 == t1 == t2, "쪽수 같음": r0["쪽수"] == r1["쪽수"] == r2["쪽수"], "파일": [mid, back]}
