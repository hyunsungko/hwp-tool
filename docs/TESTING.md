# 시험

## 한컴 없이 (모든 OS)

```
pip install -r requirements.txt
python -m unittest discover -s tests -v
python tests/bench.py          # 이 PC에서의 속도(숫자만)
```

GitHub Actions(`.github/workflows/test.yml`)가 윈도·리눅스·맥 × Python 3.10·3.13에서 위 시험과 `examples/sample_report.md` → HWPX(윈도는 HWP도) 쓰기를 돌립니다.

## 한컴오피스가 있는 윈도

| 시험 | 명령 | 확인하는 것 |
|---|---|---|
| 창 띄우지 않기 | `python skills/hwp-tool/scripts/selftest_windows.py 시험용.hwp` | 사람 화면에 한컴 창 0, 포커스 이동 0, 끝난 뒤 남은 Hwp.exe 0, 종료 코드 0 |
| 쓰기 결과 눈으로 보기 | `hwp_tool.py write examples/sample_paper.md -o paper.hwp --engine direct --style paper --polish` 뒤 `hwp_tool.py render paper.hwp -o pages` | 2단·각주·3선 표·참고문헌, 줄 끝 낱말 잘림 |
| 두 문서 쪽 비교 | `hwp_tool.py compare 원본.hwp 새파일.hwp -o 비교` | 쪽마다 다른 화소 비율 |

## 정답지 대조 (`tools/oracle/`)

같은 문서를 한컴이 저장한 결과(정답지)와 우리 변환기 결과를 요소·레코드 단위로 견줍니다. 정답지와 시험 문서는 직접 준비합니다(이 저장소에는 없습니다).

| 스크립트 | 견주는 것 |
|---|---|
| `test_hwpx2hwp.py <정답지 폴더> <HWPX…>` | HWPX → HWP: 레코드 필드 |
| `test_hwp2hwpx_oracle.py <정답지 폴더> <HWP…>` | HWP → HWPX: 요소·속성 |
| `test_hwp2hwpx.py <HWP…>` | HWP → HWPX → HWP 왕복 |
| `test_record_roundtrip.py <HWP…>` | 레코드 코덱 풀기·다시 쓰기 왕복 |
