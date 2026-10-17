# 함께 쓰는 외부 코드·꾸러미

이 저장소에는 외부 코드를 묶어 넣지 않았다. 아래 꾸러미는 사용자가 `pip`으로 설치한다.

| 꾸러미 | 쓰는 곳 | 라이선스 | 필수 여부 |
|---|---|---|---|
| lxml | HWPX(XML) 읽기·쓰기 전부 | BSD-3-Clause | 필수 |
| olefile | HWP(복합 문서 파일) 읽기 | BSD-2-Clause | HWP를 다룰 때 |
| Pillow | 그림 넣기(크기 읽기), 쪽 그림 비교 | MIT-CMU (HPND) | 그림·비교를 쓸 때 |
| pywin32 | 윈도에서 HWP 파일 쓰기(구조화 저장소), 한컴 COM | PSF-2.0 | 윈도에서 HWP 쓰기·한컴 경로 |
| Python 표준 라이브러리 | 전부 | PSF-2.0 | 필수 |

## 선택: 예비 HWP → HWPX 변환기

`hwpx_direct.py`는 우리 변환기(`hwp5_to_hwpx.py`)가 실패할 때만 `vendor/hwp2hwpx`를 찾는다. 이 저장소에는 넣지 않았다.

| 꾸러미 | 라이선스 | 비고 |
|---|---|---|
| [hwp2hwpx-python-refactor](https://github.com/jkf87/hwp2hwpx-python-refactor) | Apache-2.0 | 쓰려면 `skills/hwp-tool/scripts/vendor/hwp2hwpx/`에 직접 받는다 |
| pyhwp (`hwp5`, 위 변환기가 씀) | AGPL-3.0-or-later | 그래서 묶어 배포하지 않고 선택 설치로 둔다 |

## 한컴오피스

한컴 경로(쪽 그림, PDF·DOCX 변환, 누름틀, 한컴 액션, 시험지 조판)는 사용자 PC에 설치된 한컴오피스를 COM으로 부른다. 한컴 프로그램이나 그 파일은 이 저장소에 들어 있지 않다.

## 형식 문서

HWP 5.0·OWPML(HWPX) 구조는 한컴이 공개한 형식 문서와, 같은 문서를 한컴이 저장한 결과를 견주어 맞췄다. 형식 문서 원본은 이 저장소에 넣지 않았다.
