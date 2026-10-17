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

## 2. 플랫폼·메신저 경로 흔적 (TB-4 — M-IMG-08, M-VID-09, M-AUD-06)

참조 표: `deepfake_lens/models/platform-traces.json`(스키마 `platform-traces-v1`, 판 `2026-10-17.1`, 버전·sha256이 근거·coverage 사유와 스캔 캐시 키에
들어감). 표의 모든 값에 출처와 신뢰 수준이 붙는다. **표를 지어내지 않는다** — 인용할 수 있는 표가 없는 플랫폼은 항목이 없다.

| 검사 | 대상 | 사실 |
| --- | --- | --- |
| `platform_recompression` | JPEG(아니면 `skipped` 「JPEG 아님 …」) | DQT(지그재그 → 자연 순서), 크로마 서브샘플링, 기저/점진, EXIF 유무를 적고, 등록 표와 64개 값이 모두 같으면 「플랫폼 재압축 양자화표와 일치」 |
| `file_name_pattern` | 이미지·영상·오디오 | 파일 이름이 등록 형식(메신저 저장·스크린샷·화면 녹화·통화 녹음)과 같으면 「파일 이름 형식: …」 |
| `audio_channel_trace` | 오디오 | AMR/AMR-WB 원시 저장 머리(RFC 4867 §5), Ogg Opus 머리(RFC 7845: 입력 표본화·채널·인코더 문자열), 처음 60초의 유효 대역폭(ffmpeg 디코드, Blackman-Harris 창 약 12 Hz 간격 평균 전력이 최대 대비 −60 dB 이상인 가장 높은 주파수)과 일치하는 문서화된 대역 |

- **등록 표**(2026-10-17): WhatsApp 안드로이드 재압축 표(2020 석사 논문 Risemberg, UC Denver NCMF, 그림 20–22 + 2026 독립 블로그 재현 — 광도·색차 같음),
  Instagram 광도 표(단일 블로그, 색차 미공개 — 광도만 대조). WhatsApp 데스크톱(IJG 품질 92)·iOS(IJG 품질 75)는 아무 libjpeg 프로그램이나 쓰는 표준 표라 넣지 않았다.
  **카카오톡·LINE·네이버 밴드·텔레그램·현재 Facebook/X**: 인용할 수 있는 표가 없어 항목이 없다(학술 자료는 해상도 상한·품질 추정치만 공개 —
  Giudice 외 arXiv:1610.06347, Chuman 외 arXiv:1810.02062, TrueFake arXiv:2504.20658, FODB arXiv:2011.02241; ExifTool JPEGDigest·JPEGsnoop 서명표에도 없음).
- **표를 더하는 절차**(재현 가능한 캡처): 알려진 원본 JPEG(카메라 사진, EXIF 있음)을 해당 앱으로 보내고 받는 쪽 기기에 저장한 뒤
  `exiftool -v3`(또는 JPEGsnoop)로 DQT·SOF0을 덤프해, 앱 버전·OS·전송 화질 설정(카카오톡 '저용량/일반/원본')·날짜와 함께 `jpeg_tables`에 넣는다.
  같은 설정으로 서로 다른 원본 3장 이상에서 같은 표가 나와야 한다. 표 판(`version`)을 올리면 출력 형식 세대도 올린다.
- **이름 형식**: AOSP 스크린샷 `Screenshot_YYYYMMDD-HHMMSS`(ImageExporter.java)·화면 녹화 `screen-YYYYMMDD-HHMMSS.mp4`(ScreenMediaRecorder.java),
  삼성 앱 접미사, macOS 영어·한국어 스크린샷, 카카오톡 저장 `KakaoTalk_YYYYMMDD_HHMMSSmmm`(블로그 2건), WhatsApp 안드로이드 `IMG-YYYYMMDD-WA####`·데스크톱
  `WhatsApp Image … at …`, 텔레그램 데스크톱 `photo_YYYY-MM-DD_HH-MM-SS`(tdesktop 소스), Facebook·Instagram 배포 이름, 삼성 통화 녹음 — 출처는 표에 있다.
  iOS 스크린샷(`IMG_####.PNG`)은 일반 사진과 같은 이름이라 넣지 않았다.
- **파일 이름은 분석 입력**이므로 스캔 캐시 키에 들어간다(`CACHE_KEY_VERSION` `content-v5`): 같은 내용이라도 이름이 다르면 다시 분석한다(폴더 이름 변경은 그대로 적중).
  웹 업로드 행은 서버 임시 이름으로 분석되어 이름 형식을 보지 못한다(잔여 — 업로드 경로 수정은 재구성 브랜치 범위).
- **대역**: 4 kHz 이하(협대역 전화·AMR-NB), 8 kHz 이하(AMR-WB·16 kHz 표본화), 12 kHz 이하(24 kHz 표본화 — 24 kHz 출력 음성 합성기도 같음). 대역이 표본화 상한의
  98%보다 낮을 때만 적는다(16 kHz 파일의 8 kHz 대역은 정상이라 적지 않음). 전화·코덱·저가 마이크·낮은 표본화 원본을 올린 경우 모두 같으므로 판정하지 않는다.
- MP4/3GP의 코덱 상자(samr·sawb·mp4a)와 인코더 태그는 컨테이너 메타데이터 계층(재구성 브랜치)의 일이라 여기서 읽지 않는다.
- 흔적이 하나라도 있으면 한계 문구 「메신저·SNS·화면 캡처·통화 경로의 흔적이 관찰되었습니다 — … 그 부재는 무정보이며, 원본 파일(메신저의 '파일로 보내기' 전송본 등)을 …」가 붙는다.
- 모든 항목은 중립·약(층 `platform`). 테스트 `test_platform_traces.py`.
