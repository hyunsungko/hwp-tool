---
name: hwp-tool
description: "HWP·HWPX(한글 문서)를 읽고·고치고·새로 쓰고·바꾸는 CLI. Markdown을 보고서 모양 한글 문서로, 글자 모양·문단 모양·스타일·표(줄·칸 넣고 빼기, 합치기·나누기, 배경·테두리)·그림·머리말/꼬리말·쪽 번호·쪽 설정·누름틀·각주·목록·범위 찾아바꾸기·문서 합치기를 HWP와 HWPX 둘 다에서. 쪽 그림 비교 검증, 시험지 조판. 'hwp 읽어', '한글 파일 고쳐', 'hwp에서 ~를 ~로 바꿔', '표에 줄 추가', '머리말 넣어', '보고서 hwp로 만들어', 'hwpx 만들어', '누름틀', 'hwp를 pdf로' 등에 사용. Read, edit, create and convert Korean HWP/HWPX documents. Works without Hancom Office for HWPX; uses Hancom only when needed, on a hidden desktop with no window on screen."
---

# hwp-tool — 한글 문서를 자유롭게

실행: `python "${CLAUDE_SKILL_DIR}/scripts/hwp_tool.py" <명령> ...` (윈도에서 `python`이 없으면 `py -3`)
필요: Python 3.10+, `pip install lxml olefile pillow` (윈도에서 HWP 쓰기·한컴 경로는 `pywin32`도).

## 원칙
- 기본은 **직접 경로**: 한글 프로그램 없이 HWPX는 XML로, HWP는 레코드로 읽고 고친다. 윈도·리눅스·맥에서 돈다.
- 한컴오피스가 있으면 **한컴 경로**도 쓴다(쪽 그림, PDF·DOCX 변환, 누름틀, 한컴 액션). 이때 CLI가 저절로 **숨긴 데스크톱**으로 옮겨 가 한글을 띄우므로 사람 화면에 한글 창·트레이 아이콘·대화상자가 뜨지 않고, 전경 창과 키보드 포커스도 바뀌지 않는다. 한글을 부르는 스크립트를 직접 짤 때는 `python hwp_hidden.py 스크립트.py …`로 실행한다.
- 원본은 고치지 않는다. `-o`가 원본과 같으면 멈춘다. 암호·배포용 문서는 열지 않는다.

## 만든 문서는 검수하고 쪽 그림을 직접 본다
1. `write`·`edit`는 저장한 뒤 저절로 검수한다: 빈 쪽·제목만 있는 쪽·쪽 끝에 홀로 남은 제목·본문 여백 밖으로 넘친 글(한컴이 있을 때),
   남은 마크다운 기호(`**`·`#`·`|`·`` ` ``·`[글](주소)`)·깨진 따옴표(`&글&`·`&quot;`)·기본 바탕 글꼴로 떨어진 글·머리줄과 첫 칸 서식이
   없는 표·본문보다 넓은 표, 쪽 아래가 반쯤 빈 쪽(경고).
2. 오류가 있으면 종료 코드 3으로 멈춘다(파일은 저장된다). 고친 뒤 다시 만든다. `--no-check`는 사람이 결과를 따로 볼 때만.
3. 통과해도 출력 끝 "쪽 그림:" 폴더의 PNG를 직접 열어 표 칸 너비·쪽 나뉨·그림 위치를 보고 넘긴다.

## 명령
| 하고 싶은 일 | 명령 | 한컴 필요 |
|---|---|---|
| 내용 읽기(Markdown, 표 유지) | `read 파일 [-o 결과.md] [--format md\|text\|json]` | 아니오 |
| 문단·표 번호 보기(고칠 때 쓰는 번호) | `outline 파일` | 아니오 |
| **Markdown → 보고서 모양 한글 문서** | `write 글.md -o 보고서.hwpx\|.hwp [--header 머리말] [--check 폴더] [--no-polish] [--no-check]` | 아니오(.hwp 쓰기는 윈도, 한컴이 있으면 줄 배치 다듬기·쪽 그림 검수) |
| **Markdown → 2단 논문 모양** | `write 글.md -o 논문.hwp --style paper` | 아니오 |
| 줄 끝 낱말 잘림·벌어진 줄 다듬기 | `write`가 한컴이 있으면 저절로(잘린 문단만 자간·장평을 조금씩 줄임). 끄려면 `--no-polish` | 예 |
| **고치기·새로 쓰기**(ops) | `edit 파일\|new -o 새파일 --ops ops.json [--op '{...}'] [--engine auto\|com\|direct] [--check 폴더]` | HWPX·새 문서는 아니오 |
| 형식 바꾸기 | `convert 파일 -o 새파일.hwpx\|.hwp\|.md\|.pdf\|.docx\|.html\|.txt [--engine auto\|com\|direct]` | `.md`·HWPX ↔ HWP는 아니오 |
| 쪽 그림 / 두 문서 쪽 나란히 비교 | `render 파일 -o 폴더` / `compare 원본 새파일 -o 폴더` | 예 |
| 한컴으로 다시 열기·HWP↔HWPX 왕복 검사 | `check 파일 [--roundtrip 폴더]` | 예 |
| 누름틀 보기·채우기, 한컴 JSON 왕복 | `fields`·`fill`·`export-json`·`import-json` | 예 |

ops 목록과 인자: `${CLAUDE_SKILL_DIR}/reference/ops.md`. 예:
```json
[{"op":"p","text":"주간 보고","size":18,"bold":true,"align":"center"},
 {"op":"table.create","at":"end","data":[["항목","값"],["매출","120"]],"header":true},
 {"op":"table.insert_row","table":1,"cell":"A2","where":"below"},
 {"op":"char","find":"매출","bold":true,"color":"#1F3864"},
 {"op":"header","text":"예시 회사","align":"right"},
 {"op":"page_number","pos":"bottom-center","side":"-"}]
```

## Markdown 쓰기 (`write`)
- 보고서(`--style report`, 기본): `# 제목` → 표지 제목 상자, `## 1. 장` → 번호 띠, `### 소제목` → □, `- 항목` → ○, 표 → 좌우 바깥 테두리 없는 표(머리줄 음영·굵게·가운데, 첫 칸 굵게·옅은 음영·같은 값은 세로로 합침, 숫자 열 오른쪽, 합계·소계 줄 굵게. 2열 표의 `항목|내용` 머리줄은 빼고 왼쪽 칸을 이름표로. 낮은 표는 쪽에서 나누지 않고, 긴 표는 행 단위로 다음 쪽·머리 줄 반복), `> 인용` → 강조 상자, `![캡션](그림.png)` → 그림 + <그림 n>, ```kpi 블록(`값 | 이름 | 설명`) → 표 안에 표로 만든 지표 상자.
- 논문(`--style paper`): 앞머리 `---` 사이에 `title`·`title_en`·`author`·`running`·`abstract`·`keywords`·`abstract_en`·`keywords_en`. 본문 2단, `[^1]` 각주(`[^1]: 내용`), 표 바로 위 `<표 n> 제목`, 표 바로 아래 `자료: …`, 3선 표, `## 참고문헌` 아래 목록은 내어쓰기. 예: `examples/sample_paper.md`.
- 글꼴: 제목 HY헤드라인M · 보고서 본문·표 맑은 고딕(왼쪽 정렬) · 논문 본문 휴먼명조(없는 PC에서는 한컴이 대체 글꼴로 보인다).
- Markdown은 언제나 이 모양으로 쓴다. 한컴 HTML 가져오기(HTML 입력, `--engine com`)는 바탕 글꼴·맨 표가 나와 검수가 막는다.

## 엔진
- **직접 경로**(`--engine direct`, 한컴이 없으면 저절로): HWPX를 XML로 고친다. `action` 명령은 없다.
  - HWP를 받아 HWPX로 저장: `hwp5_to_hwpx`로 바꾼 뒤 모든 명령을 쓸 수 있다.
  - HWP를 받아 HWP로 저장: `replace`·`fill`만이면 레코드를 직접 고치고(서식 그대로), 다른 명령은 HWPX로 바꿔 고친 뒤 `hwpx2hwp`로 다시 HWP를 쓴다. HWP 파일 쓰기는 지금 윈도(pywin32)에서만 된다.
  - 줄 배치: 직접 경로 결과는 한컴이 있으면 한 번 열어 저장해 줄 배치를 써 둔다(`--layout auto`). `--layout none`이면 줄 배치 없이 두고, 한컴 데스크톱은 열 때 다시 짠다.
- **한컴 경로**(한컴오피스가 있는 윈도): HWP·HWPX 모두, 한컴 액션까지. 숨긴 데스크톱에서 한글을 새로 띄우고 그 프로세스만 닫는다.
- `--engine auto`는 HWP가 끼면 한컴 경로를 고른다. 한컴 없이 하려면 `--engine direct`.

## 시험지 (`exam`, 한컴 줄 배치를 쓰므로 한컴이 있는 윈도)
| 하고 싶은 일 | 명령 |
|---|---|
| 문항을 양식에 배정해 초안(조판까지) | `exam build 문항.json --form 양식설정.json --template 양식.hwpx -o 시험지.hwp [--report 보고서.md]` |
| 점검(10가지, 보고서에는 문항 글 없이 번호·위치만) | `exam check 시험지.hwp --form 양식설정.json [-o 보고서.md]` |
| 편집본 자동 고침 + 다시 조판 + 점검 | `exam fix 편집본.hwp --form 양식설정.json -o 고친본.hwp` |
| 편집 전·후 바뀐 곳 | `exam diff 전.hwp 후.hwp --form 양식설정.json [-o 이력.csv]` |

- 예시 양식: `scripts/forms/example.json` + `scripts/forms/example_template.hwpx`(만드는 스크립트 `examples/make_exam_template.py`), 예시 문항 `examples/exam_items.json`. 다른 양식은 설정 파일을 복사해 구역·단 수·표 크기·칸 위치·과목 띠·쪽수 표기를 맞춘다.
- 문항.json: `{"과목": [[{"문두": 글, "상자": [줄…]|null, "표": {"제목", "머리", "칸", "아래 줄"}|null, "보기": [글×4]}, …], …]}`.

## 한계
- 직접 경로가 아직 옮기지 못하는 개체: OLE·글맵시·차트·비디오·개체 연결선·캔버스·그림 효과·기울어진 그림자 여백 — 만나면 경고로 알린다.
- HWP 파일 쓰기(HWPX→HWP, HWP 직접 고치기)는 윈도에서만. 읽기와 HWP→HWPX는 모든 OS.
- 한컴 '모두 바꾸기'는 자동화에서 선택 범위를 무시한다 → 범위 바꾸기는 찾은 곳을 하나씩 바꾼다.
- 한글 줄 나눔 값 이름이 거꾸로다(`BREAK_WORD`=어절, `KEEP_WORD`=글자). 좁은 표 칸에서 낱말이 끊기면 `word_wrap:"word"`.
- 한컴 2024 COM은 Markdown 형식 저장을 받지 않는다(Markdown은 직접 경로 `write`로).
