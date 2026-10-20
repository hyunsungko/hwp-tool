# hwp-tool 고치기 명령(ops) 설명서

`edit <파일|new> -o <새 파일> --ops ops.json` (또는 `--op '{...}'` 여러 번). ops는 위에서부터 차례로 적용한다.
한 명령이라도 실패하면 저장하지 않는다(`--keep-going`이면 실패만 알리고 계속). 같은 ops가 두 엔진에서 같은 뜻으로 돈다.

## 대상 고르기 (글자·문단·스타일·목록·지우기·action)
| 키 | 뜻 |
|---|---|
| `"find": "글"` | 그 글이 나오는 곳 모두. `"nth": 2`면 두 번째만. `"regex": true`면 정규식 |
| `"para": 3` / `"para": [3, 8]` | 본문 문단 번호(0부터, `outline`으로 확인) |
| `"table": 2, "cells": "A1:C1"` | 2번 표(1부터, 문서 순서·칸 안 표 포함)의 칸 범위 |
| `"all": true` | 문서 전체 |

## 넣을 자리 (글·그림·표·누름틀·각주)
`"at": "start"|"end"` · `"after": "글"` · `"before": "글"`(+`"nth"`) · `"para_end": 5` · `"table": 1, "cell": "B2"`(칸 끝)

## 명령
| op | 인자 | 하는 일 |
|---|---|---|
| `p` | `text` 또는 `runs:[{text, bold…}]`, 문단 모양 키, 글자 모양 키, `style` | 끝에 새 문단(서식 함께). 문서를 처음부터 쓸 때 |
| `text` | 자리, `text`(줄바꿈=문단) | 글 넣기 |
| `char` | 대상, `font` `size`(pt) `color` `bold` `italic` `underline` `strike` `shade`(배경색) `spacing`(%) `ratio`(%) `superscript` `subscript` | 글자 모양 |
| `para` | 대상, `align`(left·center·right·justify·distribute·divide) `left_mm` `right_mm` `indent_mm`(음수=내어쓰기) `before_pt` `after_pt` `line_spacing`(%) `page_break_before` `keep_with_next` `keep_lines` `widow_orphan` `word_wrap`(한글 줄 나눔: `word`=어절·`char`=글자) | 문단 모양 |
| `style` | 대상, `name` | 스타일 적용(없는 이름이면 있는 스타일 목록을 알려 줌) |
| `list` | 대상, `type`: bullet·number·none | 글머리표·번호 |
| `replace` | `find` `to`, `regex`, `match_case`, `scope`: `{"table":n}`·`{"para":[a,b]}`·`{"after":"글","before":"글"}` | 찾아 바꾸기(범위 지정) |
| `delete` | 대상 | 글 지우기(표·그림 같은 개체는 남김) |
| `page_break` | 자리, `section`: true면 구역 나누기 | 쪽 나누기 |
| `table.create` | 자리, `data`(2차원) 또는 `rows`·`cols`, `col_widths_mm`, `font` `size` `color` `bold`, `header`(머리줄 굵게·가운데·음영) `header_fill`, `align`, `pad_pt`(칸 위아래 여백), `word_wrap`(칸 글 한글 줄 나눔 `word`·`char`), `keep_with_next`, `table_align` | 표 만들기 |
| `table.insert_row` / `table.insert_col` | `table`, `cell`, `where`(above·below / left·right), `count` | 줄·칸 넣기 |
| `table.delete_row` / `table.delete_col` | `table`, `cell` | 줄·칸 지우기 |
| `table.merge` | `table`, `cells` | 칸 합치기 |
| `table.split` | `table`, `cell`, `rows`, `cols` | 칸 나누기 |
| `table.fill` | `table`, `cells`, `color`(없으면 배경 지움) | 칸 배경 |
| `table.border` | `table`, `cells`, `line`(solid·dash·dot·double·none) `width`("0.4mm") `color` `sides`(all·outside·inside·left,top…) | 칸 테두리 |
| `table.align` | `table`, `cells`, `h`(left·center·right) `v`(top·middle·bottom) | 칸 정렬 |
| `table.cell_text` | `table`, `cell`, `text` | 칸 글 바꾸기 |
| `image` | 자리, `path`, `width_mm`/`height_mm`(하나만 주면 비율 유지), `float`("front"·"behind"), `over`(이 글 위에 겹침, `nth`), `dx_mm`·`dy_mm` | 그림 넣기. 도장·서명: `{"op":"image","path":"도장.png","width_mm":15,"over":"인)"}`(위치는 어림이라 쪽 그림으로 확인) |
| `header` / `footer` | `text`, `page`(both·odd·even), `align` | 머리말·꼬리말(있으면 글을 바꿈) |
| `page_number` | `pos`(bottom-center 등 11가지), `format`(digit·circle·roman·roman-small·latin·latin-small·hangul·hanja), `side`("-") | 쪽 번호 |
| `page_hide` | 자리(기본 첫 쪽), `header` `footer` `page_number` `master` `border` `fill` | 그 쪽 감추기(표지) |
| `page` | `paper`(A4·A3·A5·B4·B5·Letter·Legal) 또는 `width_mm`·`height_mm`, `landscape`, `margins_mm`{left,right,top,bottom,header,footer,gutter} | 쪽 설정 |
| `field` | 자리, `name`, `text`, `help` | 누름틀 만들기 |
| `fill` | `fields`: {이름: 값} | 누름틀 채우기 |
| `footnote` / `endnote` | 자리, `text` | 각주·미주 |
| `merge` | `file`, `break`(page·section·para) | 다른 문서를 끝에 합치기(붙는 문서의 구역 설정 유지) |
| `action` | `name`, `items`(파라미터셋 항목, 하위 셋은 dict), 대상이나 자리 | **한컴 경로 전용.** 한글 오토메이션 액션 아무거나. 이름·파라미터셋은 한컴이 공개한 액션 테이블 문서 |

## 단위
글자 크기는 pt, 여백·너비는 mm, 문단 위아래 간격은 pt, 줄 간격은 %. 색은 `"#RRGGBB"`.
