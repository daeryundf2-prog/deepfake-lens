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
- **파일 이름은 분석 입력**이므로 이름이 일치하는 형식의 id 목록이 스캔 캐시 키에 들어간다(`CACHE_KEY_VERSION` `content-v5`). 행은 이름 자체를 인용하지 않으므로 같은 형식(또는 둘 다 형식 없음)이면 이름이 달라도 같은 행을 재생한다.
  웹 업로드 행은 서버 임시 이름으로 분석되어 이름 형식을 보지 못한다(잔여 — 업로드 경로 수정은 재구성 브랜치 범위).
- **대역**: 4 kHz 이하(협대역 전화·AMR-NB), 8 kHz 이하(AMR-WB·16 kHz 표본화), 12 kHz 이하(24 kHz 표본화 — 24 kHz 출력 음성 합성기도 같음). 대역이 표본화 상한의
  98%보다 낮을 때만 적는다(16 kHz 파일의 8 kHz 대역은 정상이라 적지 않음). 전화·코덱·저가 마이크·낮은 표본화 원본을 올린 경우 모두 같으므로 판정하지 않는다.
- MP4/3GP의 코덱 상자(samr·sawb·mp4a)와 인코더 태그는 컨테이너 메타데이터 계층(재구성 브랜치)의 일이라 여기서 읽지 않는다.
- 흔적이 하나라도 있으면 한계 문구 「메신저·SNS·화면 캡처·통화 경로의 흔적이 관찰되었습니다 — … 그 부재는 무정보이며, 원본 파일(메신저의 '파일로 보내기' 전송본 등)을 …」가 붙는다.
- 모든 항목은 중립·약(층 `platform`). 테스트 `test_platform_traces.py`.

## 3. 기기 AI 편집 라벨 (`device_ai_label`, TB-3 — S-GALAXY-AI, S-GOOGLE-PHOTOS)

표: `deepfake_lens/models/device-labels.json`(`device-labels-v1`, 판 `2026-10-17.1`, 판·sha256이 근거·사유와 캐시 키에 들어감). 모든 이미지에서 실행.

- **삼성 SEFT 트레일러**: JPEG EOI·PNG IEND 뒤 `SEFT`로 끝나는 블록. 배치는 ExifTool `Samsung.pm` 1.62(`ProcessSamsung`, 2026-02-07 커밋 e9609a9)와 같다:
  리틀엔디언 `SEFH` 디렉터리(형식·음의 오프셋·크기 12바이트 항목), 각 블록 `[형식][이름 길이][이름][데이터]`. 항목 이름 목록이 중립·약 사실
  「삼성 기기 편집·촬영 기록(SEFT 트레일러)」(ExifTool이 뜻을 기록한 이름은 한국어 뜻을 덧붙임).
  AI 관련 **후보**: `PhotoEditor_Re_Edit_Data` JSON의 `isScaleAI`·`isAIFilterReEditOnly`, `PEg_Info`(`genImageVersion`·`connectorType`) — 의미를 확인한
  공개 자료가 없어 중립·약 「삼성 편집 기록의 AI 관련 후보 필드(의미 미확인)」로만 적는다. **갤럭시 AI 생성형 편집 전용 항목 이름은 공개 자료에 없어
  등록하지 않았다**(ExifTool에도 없음; 조사에서 언급된 `Generative_Edit`는 근거 없음). S25 이후 AI 편집본의 C2PA(보도)는 `c2pa` 검사·기존 C2PA 규칙이 읽는다.
- **Google AI 라벨**: XMP `photoshop:Credit`이 정확히 「Edited with Google AI」(Google 포토 AI 편집 — Google 공지 2024-10-24는 필드 이름을 적지 않음,
  9to5google 보도), 「Made with Google AI」(Gemini — IPTC 공지 2024-10-28), 「Made by Google AI」(보도) → 생성·**중** 자기 신고(규칙 5만 막고 결론 없음).
  같은 파일의 IPTC 디지털 출처 유형(`compositeWithTrainedAlgorithmicMedia` 등)은 기존 XMP 규칙이 따로 읽는다(그 세기는 이 작업에서 바꾸지 않음 — 재구성 브랜치 결정).
- coverage `ran` 사유: 「삼성 SEFT 트레일러 항목 N개 / 없음; Google AI 라벨 없음 / XMP 라벨 「…」(기기 라벨 표 …)」. 테스트 `test_device_labels.py`.

## 4. 스크린샷·재촬영 사실 (`screen_capture`, TB-5 — M-IMG-07)

표: `deepfake_lens/models/screen-traits.json`(`screen-traits-v1`, 판 `2026-10-17.1`). 모든 이미지에서 실행. 재촬영(모아레·서브픽셀) 탐지는 분류 B라 범위 밖.

- **캡처 주석**: XMP `exif:UserComment`(`http://ns.adobe.com/exif/1.0/`) 또는 EXIF `UserComment`(0x9286)가 「Screenshot」 — iOS PNG 스크린샷 원본(위키미디어 공용,
  iPhone 16 Pro Max 2025·iPhone XR 2020)과 macOS 대화형 스크린샷에서 확인. macOS `screencapture` 명령줄 도구는 쓰지 않는다(macOS 15.7.4에서 직접 확인).
  → 중립·약 「화면 캡처 표시(운영체제가 쓴 주석)」. 다시 저장하면 사라지므로 없다고 스크린샷이 아닌 것은 아니다.
- **화면 해상도**: 이미지 크기(가로·세로 어느 방향이든)가 표의 기기 기본 화면 해상도와 같으면 중립·약 「기기 화면 해상도와 같은 크기」(기기 목록 포함).
  iPhone 11–17·Air·SE, iPad 일부(support.apple.com 기술 사양), 갤럭시 S22–S26·A35/36/55/56·Z 폴드5–7·플립6–7(samsung.com 사양), Pixel 8–11
  (support.google.com/pixelphone/answer/7158570). 같은 크기를 여러 기종이 쓰므로(예: 1170×2532는 7개 기종) 기기를 특정하지 않는다. 삼성의 기본 FHD+ 화면 설정
  크기는 공식 자료가 없어 넣지 않았다.
- IPTC `screenCapture`는 기존 XMP·C2PA 규칙이 읽는다(중립·중).
- **재촬영 문구**: 신뢰 서명자의 유효 C2PA 매니페스트가 `digitalCapture`·`computationalCapture`를 선언하면 한계 문구
  「카메라 C2PA 서명은 이 기기가 이 장면을 찍었다는 것만 증명하며, 장면이 실제였다는 것(화면·인쇄물을 다시 찍은 것이 아님)은 증명하지 않습니다.」가 붙는다.
- 테스트 `test_screen_capture.py`.
