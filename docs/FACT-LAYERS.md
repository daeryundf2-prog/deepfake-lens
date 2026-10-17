# 결정적 사실 계층 (techniques-2026b, TB)

위협 카탈로그(2026-10-17)의 분류 A 항목 가운데 서명·워터마크가 아닌 **구조·기록 사실**을 모은다. 공통 규칙(0단계):

- 모든 항목은 결정적·**중립**·약(또는 중) 사실이며 판정 규칙(decision.py)을 바꾸지 않는다. 조작·원본 어느 쪽 결론도 내리지 않는다.
- 파일·기기·앱이 스스로 적은 값은 「자기 신고」로 적고 값은 「…」 안에 그대로 보인다.
- 검사마다 coverage에 `ran`/`skipped`/`failed`와 한국어 사유가 남는다. 분석 중 네트워크를 쓰지 않는다.
- 사실이 없다는 것은 무정보다(원본성 근거 아님).

## 1. 문서 구조 (`document_structure`, TB-6 — M-IMG-06, M-DOC-01)

대상: pdf·docx·xlsx·pptx(hwp·doc·xls·ppt는 `skipped` 「… 구조 검사는 이 버전에서 하지 않음(실제 표본으로 검증한 판독기 없음)」).
의존성 없음 — PDF도 pymupdf 없이 바이트에서 읽는다(64 MB 상한, 넘으면 `skipped`; `%PDF-` 머리가 없으면 `skipped`).

| 형식 | 사실 | 근거 항목(중립·약, 층 `document_structure`) |
| --- | --- | --- |
| PDF | `%%EOF` 개수 = 저장 개정 수(선형화 파일의 첫 페이지 구획은 제외), 교차 참조 표·스트림, `/Prev` 연결 (ISO 32000-1 §7.5.6, §7.5.8, 부록 F) | 개정 2개 이상: 「PDF 증분 저장 구조」 |
| PDF | 서명 `/ByteRange [a b c d]`의 끝(c+d) 뒤 바이트 수 (§12.8.1) | 「PDF 전자서명 범위 뒤의 후속 저장」 — 장기 검증 정보·추가 서명도 같은 구조를 만들어 내용 변경 여부는 판단하지 않음, 서명 검증 안 함 |
| PDF | 마지막 `%%EOF` 뒤 공백 아닌 바이트 | 「PDF 끝 표식 뒤의 덧붙은 바이트」 |
| PDF | Info `/Producer`·`/Creator`(리터럴·16진 문자열, UTF-16BE BOM), XMP `pdf:Producer`·`xmp:CreatorTool`·`stEvt:softwareAgent`를 파일 순서로 | 같은 항목에 서로 다른 값이 둘 이상: 「PDF 작성 도구 기록 이력」(자기 신고) |
| PDF | `/Subtype /application#2Fc2pa` 내장 파일 | 「PDF 내장 C2PA 매니페스트 저장소 표기」 — 검증은 `c2pa` 검사 |
| OOXML | `docProps/core.xml` revision·생성/수정/인쇄 시각, `docProps/app.xml` Application·AppVersion·TotalTime·Template, `word/settings.xml`의 `w:rsid` 개수, 변경 추적 삽입·삭제, 메모 (ECMA-376) | 「문서 수정 메타데이터(자기 신고)」, 생성 > 수정이면 「문서 생성 시각이 수정 시각보다 늦음」 |

- 압축 객체 스트림(`/ObjStm`) 안의 값은 읽지 않는다(coverage 사유에 적음). 구성 파일이 16 MB를 넘으면 `skipped`.
- 한계 문구: 「문서 구조 사실(…)은 편집 이력의 흔적일 뿐 위조나 원본성의 근거가 아닙니다 …」.
- 문서 행은 텍스트 규칙(규칙 1)으로 늘 "참고" 등급이다. 테스트 `test_document_structure.py`.
