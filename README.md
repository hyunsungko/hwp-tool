# hwp-tool

한글 문서(HWP·HWPX)를 읽고, 고치고, 새로 쓰는 도구입니다. Claude Code 스킬로 쓰거나 명령줄에서 바로 씁니다.

Read, edit, create and convert Korean HWP/HWPX documents — from Claude Code or the command line. HWPX works without Hancom Office on Windows, Linux and macOS.

![hwp-tool 시연: Gemini 딥리서치 Markdown → 한글 보고서·논문](docs/demo/hwp-tool-demo.webp)

[시연 영상 (MP4, 35초)](docs/demo/hwp-tool-demo.mp4)

## 할 수 있는 것

| 하고 싶은 일 | 명령 | 한컴오피스 필요 |
|---|---|---|
| Markdown → 보고서 모양 한글 문서 | `write 글.md -o 보고서.hwp --engine direct` | 아니오 (.hwp 쓰기는 윈도) |
| Markdown → 2단 논문 모양 | `write 글.md -o 논문.hwp --engine direct --style paper` | 아니오 |
| 줄 끝 낱말 잘림 다듬기 | `write … --polish` | 예 |
| 내용 읽기 (Markdown, 표 유지) | `read 파일.hwp` | 아니오 |
| 고치기·새로 쓰기 (글자·문단·표·그림·머리말·쪽 번호·각주·누름틀 등) | `edit 파일 -o 새파일 --ops ops.json` | HWPX는 아니오 |
| HWP ↔ HWPX 변환 | `convert 파일.hwp -o 파일.hwpx` | 아니오 (HWPX → HWP는 윈도) |
| PDF·DOCX 등으로 변환, 쪽 그림, 두 문서 비교 | `convert`·`render`·`compare` | 예 |
| 시험지 조판·점검 | `exam build`·`exam check` | 예 |

명령과 ops 목록 전체: [skills/hwp-tool/SKILL.md](skills/hwp-tool/SKILL.md), [reference/ops.md](skills/hwp-tool/reference/ops.md)

### 보고서 (`--style report`)
- `# 제목` → 표지 제목 상자, `## 1. 장` → 번호 띠, `### 소제목` → □, `- 항목` → ○
- 표 → 좌우 바깥 테두리 없는 표. 긴 표는 행 단위로 다음 쪽에 이어지고 머리 줄이 다시 나옵니다.
- `> 인용` → 강조 상자, `![캡션](그림.png)` → 그림과 <그림 n>
- ```` ```kpi ```` 블록 → 표 안에 표로 만든 지표 상자
- 글꼴: 제목 HY헤드라인M · 본문 휴먼명조 · 표 맑은 고딕

### 논문 (`--style paper`)
- 제목·영문 제목·국문 요약·Abstract는 1단, 본문은 2단
- `[^1]` 각주, `<표 n>` 표 제목(위)·`자료:` 출처(아래), 3선 표, 그림 제목(아래)
- `## 참고문헌` 아래 목록은 내어쓰기, `*기울임*`
- 예시 원고: [examples/sample_paper.md](examples/sample_paper.md)

### 다듬기 (`--polish`, 한컴이 있는 윈도)
- 본문과 표 칸은 어절 단위로 줄을 나눕니다.
- 그래도 줄 끝에서 낱말이 잘리는 문단만 문단 전체의 자간·장평을 조금씩 줄입니다.
- 양쪽 정렬에서 띄어쓰기가 벌어진 줄도 같은 방법으로 줄입니다.
- 쪽 끝에 표 머리 줄만 남으면 그 표를 다음 쪽에서 시작합니다.

## 설치

Claude Code 플러그인:

```
/plugin marketplace add hyunsungko/hwp-tool
/plugin install hwp-tool@hwp-tool
```

명령줄에서 바로:

```
git clone https://github.com/hyunsungko/hwp-tool
cd hwp-tool
pip install -r requirements.txt
python skills/hwp-tool/scripts/hwp_tool.py write examples/sample_report.md -o report.hwpx --engine direct
```

필요: Python 3.10 이상, `lxml`, `olefile`, `pillow`. 윈도에서 HWP 파일을 쓰거나 한컴 경로를 쓰려면 `pywin32`도 필요합니다.

## 한컴오피스가 있을 때: 창을 띄우지 않습니다

쪽 그림, PDF·DOCX 변환, 누름틀, `--polish`처럼 한컴이 필요한 일은 PC에 설치된 한컴오피스를 COM으로 부릅니다. 이때 도구가 **숨긴 데스크톱**을 따로 만들어 그 안에서 한글을 띄웁니다. 그래서 사용자 화면에 한글 창·대화상자·트레이 아이콘이 뜨지 않고, 작업 중인 창의 포커스와 키보드 입력도 빼앗지 않습니다. 끝나면 도구가 띄운 한글 프로세스만 닫습니다.

## 한계

- 직접 경로가 아직 옮기지 못하는 개체: OLE·글맵시·차트·비디오·개체 연결선·캔버스·그림 효과. 만나면 경고로 알립니다.
- HWP 파일 쓰기(HWPX → HWP, HWP 직접 고치기)는 윈도에서만 됩니다. 읽기와 HWP → HWPX는 모든 OS에서 됩니다.
- 암호가 걸린 문서와 배포용 문서는 열지 않습니다.
- 글꼴이 없는 PC에서는 한컴이 대체 글꼴로 보여 줍니다.

## 시험

- `python -m unittest discover -s tests` — 한컴 없이 돕니다. GitHub Actions에서 윈도·리눅스·맥 × Python 3.10·3.13으로 돌립니다.
- 한컴이 있는 윈도에서 더 하는 시험: [docs/TESTING.md](docs/TESTING.md)

## 라이선스

MIT. 함께 쓰는 꾸러미와 그 라이선스: [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md). 이 저장소에는 외부 코드와 한컴 프로그램·형식 문서를 넣지 않았습니다.

"한글", "한컴오피스"는 한글과컴퓨터의 상표입니다. 이 프로젝트는 한글과컴퓨터와 관계가 없습니다.
