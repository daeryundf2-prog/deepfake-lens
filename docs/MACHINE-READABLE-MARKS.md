# 기계판독 표식 검사 (techniques-2026)

근거 자료: `new_techniques_2026-10.md`(2026-10 최신 기법 조사)의 "(A) 지금 추가 가능" 항목. 0단계 규칙(분석 중 네트워크 금지,
fail closed, 모든 검사의 coverage 기록, 결정적 근거만 결론에 참여, 부재는 원본성 근거가 아님)은 그대로다.

## 1. C2PA 2.2–2.4 판독 (`c2pa` 검사)

- SDK: `c2pa-python>=0.38`(c2pa-rs 0.91 — 검증 결과에 `specVersion` 2.4.0을 기록). 2026-10-15 PyPI 최신이 0.38.0이며
  linux-aarch64·macOS 휠이 있다(조사 문서의 "0.32.x"는 당시 집계값).
- **오프라인 검증**: 판독기는 `verify.remote_manifest_fetch=false`, `verify.ocsp_fetch=false`로 실행된다(원격 매니페스트·OCSP 조회 없음).
  그래서 인증서 폐기(revocation)는 확인하지 않는다.
- **신뢰 목록**: 이전에는 신뢰 앵커를 넣지 않아 어떤 서명자도 `signingCredential.trusted`가 될 수 없었다(강한 C2PA 규칙 두 개가 도달 불가).
  이제 C2PA 공식 신뢰 목록 스냅샷(`deepfake_lens/models/c2pa-trust-list.pem`, 2026-08-05판, 앵커 30개,
  `models/c2pa-trust.json`에 출처 URL·업스트림 커밋·조회일·sha256)을 쓰며, 매 사용 전에 sha256을 확인한다(다르면 `c2pa` 검사 failed
  「C2PA 검증 불가: C2PA 신뢰 목록 무결성 불일치…」). 운영자는 `DEEPFAKE_LENS_C2PA_TRUST_ANCHORS=<PEM>`으로 앵커를 더한다(읽을 수 없으면 failed).
  신뢰 기준(목록 id·발행일·sha256, 운영자 앵커 sha256)과 검증기 spec 버전은 C2PA 근거의 `detail`, `c2pa` coverage의 `reason`
  (「매니페스트 있음 · 검증기 C2PA 2.4.0 · 신뢰 목록 C2PA-TRUST-LIST 2026-08-05판(sha256 …)」)에 적혀 서명되는 보고서 본문에 들어가고,
  스캔 캐시 키에도 들어간다.
- **판독 사실**(`validate_c2pa_manifest`의 새 필드): `spec_version`, `trust_list_uri`, `trust_basis`, `sdk_version`,
  `source_type_declarations`(값과 위치 — `action`=활성 매니페스트 자신의 액션, `ingredient`=C2PA 2.4의 구성요소 수준 값),
  `ai_disclosures`(`c2pa.ai-disclosure`: modelType·modelName·modelIdentifier·humanOversightLevel), `soft_bindings`(alg와 블록 값),
  `watermark_actions`(`c2pa.watermarked`/`.bound`/`.unbound`), `embedded_data`(`c2pa.embedded-data*`), `claim_spec_version`, `claim_version`.
  매니페스트는 신뢰할 수 없는 입력이므로 항목 수(16)·문자열 길이(200)를 제한한다.
- **판정 의미**:

| 매니페스트 | 근거 |
| --- | --- |
| 유효 서명 + 신뢰 서명자 + 콘텐츠 해시 일치 + 생성형 출처 유형(아래 표) **또는** `c2pa.ai-disclosure` | 결정적·생성·강 「C2PA 서명: 생성형 AI 출처 선언」 → 조작·생성 근거 있음 |
| 같은 조건 + `digitalCapture`(생성형 선언 없음) | 결정적·원본·강 「C2PA 서명: 카메라 촬영 출처」 → (다른 생성 근거가 없으면) 원본성 근거 있음 |
| 같은 조건 + 그 밖의 유형(예: `screenCapture`) | 중립·중 「C2PA 매니페스트 검증됨(촬영·생성 외 출처 유형 선언)」 + 「C2PA: 화면 캡처 출처 선언」 |
| 신뢰되지 않음·만료·검증 미완료 + 생성형 선언 | 생성·중 「…(검증 미완료)」(실패 코드 `signingCredential.untrusted`/`expired` 표기) — 결론 불참여 |
| 해시 불일치(서명 후 변경) | 중립·강 「C2PA 무결성 불일치」 + 선언은 생성·중 |
| 구성요소(ingredient)의 생성형 유형 | 생성·중 — 입력 자료의 출처라 단독 결론 불가 |
| 소프트 바인딩·워터마크 액션, embedded data | 중립·약 사실 항목 |

  콘텐츠 해시 일치는 `assertion.dataHash|bmffHash|boxesHash|collectionHash.match` 중 하나다.
- **근거 충돌**(조정자 추가): 강한 원본 근거(신뢰 서명된 촬영 매니페스트)와 강한 생성 근거(예: 서명 없는 XMP 자기 신고)가 함께 있으면
  둘 다 근거 목록에 남고, 중립 항목 「근거 충돌: 서명된 원본성 근거와 생성 근거가 함께 있음」이 더해진다. 결정 규칙(규칙 2가 생성 근거를
  우선)은 바꾸지 않았다 — 규칙 변경은 phase0 브랜치에서 결정한다.
- **모달리티**: 이미지(기존)에 더해 오디오(wav·mp3·flac·m4a)·영상(mp4·mov·avi)·문서(pdf·docx·xlsx·pptx)도 `c2pa` 검사를 한다.
  문서 결과는 텍스트 규칙상 등급이 늘 "참고"라서 서명된 매니페스트도 결론을 바꾸지 않는다(등급 상향은 사용자 결정 사항).
  `.txt`·`.md`는 `skipped` 「C2PA 텍스트·HTML 임베딩(C2PA 2.3/2.4)은 현재 SDK…가 판독하지 않음…」 — c2pa-rs 0.91은 텍스트/HTML
  임베딩을 읽지 못한다.
- **알려진 SDK 한계**: c2pa-rs 0.91은 2.4 위치(`claim_generator_info[].specVersion`)에 spec 버전을 쓴 매니페스트를 스스로 다시 읽지
  못한다(`claim_cbor: unexpected trailing data`). 그런 파일은 `c2pa` failed로 기록된다(fail closed). 빌더가 구성요소의 `digitalSourceType`을
  기록하지 않아 구성요소 수준 값은 기록된 판독 결과로만 시험했다.
- 픽스처: `fixtures/c2pa-2.4/`(재현 스크립트 `scripts/build_c2pa24_fixtures.py`, 설명 `fixtures/c2pa-2.4/README.md`), 테스트 `test_c2pa24.py`.

## 2. 디지털 출처 유형 어휘 (`digital_source_type.py`)

IPTC NewsCodes digitalsourcetype 2024-10-23판의 20개 값 전부(퇴역 `minorHumanEdits`·`digitalArt`·`softwareImage` 포함)와 C2PA 이름공간
(`http://c2pa.org/digitalsourcetype/trainedAlgorithmicData`, `…/empty`, 구 `c2pa.trainedAlgorithmicData`). 범주:

- 생성형: `trainedAlgorithmicMedia`, `compositeWithTrainedAlgorithmicMedia`(생성형 AI로 편집), `compositeSynthetic`(생성형 AI 요소 포함 합성),
  `algorithmicMedia`(기존 규칙 유지), `trainedAlgorithmicData`;
- 촬영: `digitalCapture`(서명·신뢰·해시 일치일 때만 원본성 근거);
- 화면 캡처: `screenCapture` — 사실로만 기록(카메라 촬영 근거도, 생성 근거도 아님);
- 그 밖(연산 촬영, 필름·인화 디지털화, 사람 편집, 알고리즘 보정, 디지털 창작, 데이터 기반, 가상 녹화, `composite`, `compositeCapture`, `empty`) — 중립.

IPTC·C2PA 이름공간이 아닌 IRI는 마지막 경로 조각이 같아도 "알 수 없는 값"으로 그대로 표시한다. 서명 없는 XMP
`Iptc4xmpExt:DigitalSourceType`은 "자기 신고"로 표시한다(생성형이면 기존처럼 결정적·생성·강 — WP-A 표의 생성 도구 메타데이터 급,
`screenCapture`는 중립·중).

## 3. SDXL 고정 메시지 워터마크 (`watermark_sdxl`)

- 대상: 모든 이미지(사진/비사진 게이트와 무관 — 표식은 이미지 유형과 관계없는 사실). 의존성: numpy·opencv·Pillow(라이선스 MIT인
  invisible-watermark의 디코더를 `wm_imwatermark.py`에 numpy+OpenCV로 다시 구현 — PyWavelets·torch 불필요, 라이브러리와 비트 단위로 같음).
- 메시지: `0b101100111110110010010000011110111011000110011110`(48비트, diffusers `StableDiffusionXLWatermarker`와 Stability 참조 구현이 같은 값 —
  출처·조회일은 `models/watermark-registry.json`). 문서화된 BGR 순서와 R·B를 바꾼 순서를 모두 시험한다.
- **근거**: 48/48 정확 일치일 때만 결정적·생성·강 「SDXL 공식 파이프라인 워터마크 존재」. 메시지가 공개라 흉내 낼 수 있으므로 생성 도구
  메타데이터와 같은 급이다. 불일치는 무정보(coverage `ran` 「48비트 정확 일치 없음 — 무정보(워터마크 부재를 뜻하지 않음)」).
- 범위 밖: 짧은 변 256px 미만(SDXL 워터마커가 표식을 넣지 않음) → `skipped`; 8천만 화소 초과 → `skipped`.
- 측정: 실사 240장 우연 일치 0건(95% 상한 1.5%), 무손실 재검출률 29.6% — `docs/WATERMARK-MEASUREMENTS.md`.

## 4. Adobe TrustMark (`watermark_trustmark`)

- 디코더: TrustMark JS 디코더가 쓰는 ONNX(`trustmark-decoder_Q/P/C.onnx`, MIT, `cai-watermark.adobe.net`), sha256은 `models/assets.json`.
  `scripts/fetch_watermark_models.py`가 받아 sha256을 확인한다. 로드는 검증한 바이트로만(`model_assets.verified_bytes`), onnxruntime 1스레드.
  BCH 오류 정정은 패키지 원본(`vendor_trustmark_bch.py`, MIT, 수정 없음).
- coverage: onnxruntime 없음 → `skipped` 「의존성 부재: onnxruntime」; 가중치 없음 → `skipped` 「가중치 없음: … — scripts/fetch_watermark_models.py…」;
  핀과 다른 파일 → `failed` 「AssetPinError: 미고정 모델: …」; 짧은 변 128px 미만 → `skipped`.
- **근거**:
  - BCH 통과 패킷 = 중립·약 사실 「TrustMark 워터마크 판독(변형 V)」(스키마·페이로드·소프트 바인딩 표기 `"<스키마>*<비트>"`, 측정 오탐률 문구 포함).
    TrustMark는 사진 출처 표시에도 쓰이므로 AI 생성 사실이 아니다. 실사 오탐률이 변형별 1.3–2.5%라 이 판독만으로는 표식 존재도 확정하지 않는다.
  - 판독값이 같은 변형(`com.adobe.trustmark.V`)의 C2PA 소프트 바인딩 값과 같고 매니페스트가 신뢰 서명자로 검증(하드 바인딩 불일치만 허용)되면
    중립·중 「TrustMark–C2PA 소프트 바인딩 교차검증 일치」.
  - 그 상태에서 **파일 바이트가 서명 후 바뀌어(하드 바인딩 불일치) 매니페스트 자체의 C2PA 항목이 결론을 낼 수 없을 때** 매니페스트가 생성형
    출처(또는 AI 공개)를 선언하면 결정적·생성·강 「C2PA 소프트 바인딩으로 확인된 생성형 AI 출처 선언」 — 판정과 연결되는 유일한 경로.
  - 매니페스트의 TrustMark 소프트 바인딩과 판독값이 다르면 중립 사실 「…불일치」. 서명이 신뢰되지 않거나 assertion이 변조(hashedURI 불일치)되면 연결하지 않는다.
  - 페이로드로 매니페스트를 찾는 온라인 조회(소프트 바인딩 resolver)는 하지 않는다.
- 픽스처: `fixtures/trustmark/`(재현 `scripts/build_trustmark_fixture.py --encoder <encoder_Q.onnx 폴더>`): 워터마크만, 서명+일치, 서명+다른 ID,
  서명 후 픽셀 변경(하드 바인딩 불일치·소프트 바인딩 일치).

## 5. Meta VideoSeal·AudioSeal (`watermark_videoseal`, `watermark_audioseal`)

- 선택 의존성 `watermark_meta`(torch, audioseal 0.2.0 등). 없으면 `skipped` 「의존성 부재: torch」/「의존성 부재: audioseal」.
  가중치: `videoseal-y_256b_img.jit`(VideoSeal 1.0, TorchScript, MIT), `audioseal-detector_base.pth`(HF `facebook/audioseal` 리비전 `3c19eba5…`, MIT) — sha256 고정.
- **VideoSeal**(이미지·영상): 256비트 메시지를 읽지만 존재 확률을 내지 않는다(첫 채널은 검출 점수가 아님) — 어떤 이미지든 256비트가 나오므로
  디코드 메시지만으로는 무정보다. 등록표(`watermark-registry.json`, 규칙 `min_agreement_bits` 기본 243/256)와 일치할 때만 결정적·생성·강.
  2026-10 현재 공개·검증된 생성기 메시지가 없어 등록 0건 — 디코드 메시지는 표식 문맥(`marking_context`)의 사실로만 남는다.
  영상은 프레임을 순서대로 읽어(스트리밍) 처음 600초 중 최대 32프레임을 고르게 골라 로짓을 평균한다. 1024px 초과 이미지·프레임은 줄여서 읽는다(모델 내부 처리 256px; 1280·1024·512px에서 재검출 비트 같음).
- **AudioSeal**(오디오, 영상의 음성 트랙): ffmpeg로 16 kHz 모노(처음 600초)를 만들어 60초 단위로 검출한다. 워터마크 프레임 비율 ≥ 0.5(라이브러리 기본값)이면
  중립·약 사실 「AudioSeal 워터마크 검출」(확률·16비트 메시지). 삽입 주체나 AI 생성 여부는 뜻하지 않는다. 생성기 연결은 등록 16비트 메시지와
  정확 일치일 때만(등록 0건). 오디오 트랙이 없거나 1초 미만이면 `skipped`.
- 결정성: 추론 중 torch 스레드 1개(잠금으로 직렬화), 끝나면 원래 값으로 되돌린다.
- WavMark는 보류: `wavmark` 패키지가 실행 시 Hugging Face에서 가중치를 받는 구조(huggingface-hub·resampy·torchaudio 의존)라 오프라인 핀 로딩을
  따로 만들어야 한다. PixelSeal(1.2 GB 체크포인트, videoseal 패키지 필요)·ChunkySeal(13 GB)도 보류 — `docs/WATERMARK-MEASUREMENTS.md` 끝 참조.

## 6. 법정 표식 문맥 (`marking_context`, 근거 「기계판독 표식 확인 결과…」)

- 이미지·오디오·영상·문서·텍스트 행마다 실제로 실행한 표식 검사를 사실로 정리한다: C2PA 매니페스트(있음/없음/미실행), SDXL·TrustMark·VideoSeal
  (이미지), VideoSeal·AudioSeal(영상), AudioSeal(오디오) — 각각 확인됨/확인 안 됨/미실행(사유)/실패.
- 온라인 전용 검출(구글 SynthID 검출 서비스, OpenAI 콘텐츠 출처 확인, Kling 콘텐츠 검출)은 coverage `online_detection` `skipped`
  「…: 온라인 전용 검출 서비스(검출기·키 비공개) — 분석 중 네트워크를 쓰지 않으므로 실행하지 않음」.
- 결과 JSON의 `result.marking_context`(`legal_basis`, `checked[]`, `online_only[]`, `marks_found`, `absence_statement`)와 결정적·중립·약 근거 항목
  「기계판독 표식 확인 결과(EU 인공지능법 제50조·인공지능기본법 관련)」(근거 목록 맨 뒤 — 모든 보고서에 그대로 출력·서명). 추론은 하지 않는다:
  「표식이 확인되지 않은 것은 원본성 근거가 아닙니다 …」가 늘 함께 적힌다.

## 7. 비가시 유니코드 표식 (`text_invisible_marks`, techniques-2026b TB-1)

위협 카탈로그 M-TXT-03·M-TXT-01. 대상: `.txt`·`.md`가 분석한 본문(앞 `text_bytes`), 문서(pdf·docx·xlsx·pptx·hwp)는 추출한 본문
(추출이 실행되지 않으면 `skipped` 「문서 텍스트 추출이 실행되지 않아 검사하지 않음」). 의존성 없음(표준 라이브러리).

| 찾는 것 | 규칙 | 출처 |
| --- | --- | --- |
| 유니코드 태그 문자 U+E0000–U+E007F | U+E0020–E007E를 ASCII로 풀어 그대로 적는다. U+1F3F4 + 태그 영숫자 + U+E007F(이모지 깃발 태그 시퀀스)는 정상 용법으로 세기만 한다 | Unicode 표준 §23.9, UTS #51 ED-14a |
| 데이터로 쓰인 변형 선택자 | 선택자 2개 이상 연속, 또는 등록 IVS에 쓰이지 않는 U+E0120–U+E01EF 하나. VS1–16 → 0x00–0x0F, VS17–256 → 0x10–0xFF로 읽어 길이·16진수(UTF-8이면 그 글)를 적는다. 기준 문자 + 선택자 하나는 정상 변형 시퀀스 | StandardizedVariants.txt, UTS #37(IVD 2025-07-14: E0100–E011F만 사용), C2PA 2.3 부록 A.7.3 |
| 제로폭 문자 U+200B·200C·200D·2060·FEFF(첫 글자 제외)·180E·2061–2064 | 종류별 개수, 4개 이상 연속열(이진 부호화 형태) 위치. 두 그림 문자 사이의 ZWJ(이모지 ZWJ 시퀀스)는 제외 | Unicode §23.2, §23.8.1 |
| C2PA 비정형 텍스트 래퍼 | U+FEFF 뒤 선택자 열이 `C2PATXT\0`(0x4332504154585400)로 시작 → 버전(uint8)·매니페스트 길이(uint32, 빅엔디언)를 적는다. 길이 뒤 채움 선택자는 허용(MIT `c2pa-text`의 `encode_wrapper_padded`) | C2PA 2.3 부록 A.7.2 = 2.4 부록 A.8.2 |
| C2PA 구조화 텍스트 블록 | `-----BEGIN C2PA MANIFEST-----` … `-----END C2PA MANIFEST-----` | C2PA 2.4 부록 A.9 |

- **의미**: 찾은 것은 모두 결정적·중립·약 사실(층 `text_marks`)이다. 비가시 문자는 누구나 넣거나 지울 수 있고 복사·붙여넣기·편집기·입력기에서
  생기거나 사라지므로 작성 주체나 AI 생성 여부를 뜻하지 않는다. 텍스트 결과는 규칙 1로 늘 "참고" 등급이라 판정에 영향이 없다. 없음은 무정보
  (coverage `ran` 「… — 비가시 유니코드 표식 없음 — 무정보(…)」, 한계 문구 하나).
- **C2PA 텍스트 매니페스트**: 번들 SDK(c2pa-python 0.38 = c2pa-c-ffi c2pa-v0.91.0)는 c2pa-rs의 `unstable_plain_text`·`unstable_structured_text`
  기능 없이 빌드되어 텍스트 임베딩을 읽지 못한다. 그래서 래퍼·블록은 「…(서명 미검증)」 존재 사실로만 적고, `c2pa` 검사는 계속 `skipped`
  (사유가 「…서명·해시를 검증하지 않았습니다(비정형 텍스트 래퍼의 존재는 비가시 유니코드 표식 검사가 사실로 기록)」로 바뀜). 표식 문맥
  (`marking_context.checked`)에 `text_invisible_marks`와, 래퍼가 있으면 `c2pa_text_wrapper`(found, 「서명 검증 미실행」)가 더해진다.
- **Encypher**: `encypher-ai`(AGPL-3.0 — 가져오지 않음)의 C2PA 형식은 위 래퍼이고, 이전 형식(basic/manifest — 머리 없는 JSON+Ed25519 서명 바이트)은
  변형 선택자 데이터열로 잡혀 바이트로만 적힌다(서명 검증 없음). 공개되지 않은 독점 표식은 다루지 않는다.
- 제로폭 스테가노그래피(offdev/zwsp-steg-js, KuroLabs/stegcloak 등)는 형식이 여럿이고 stegcloak은 기본 암호화라 풀지 않고 연속열만 적는다.
- HTML(C2PA 2.4 부록 A.7, `<script type="application/c2pa">`)은 `.html`이 지원 형식이 아니어서 범위 밖이다.
- 출처(2026-10-17 조회): https://spec.c2pa.org/specifications/specifications/2.3/specs/C2PA_Specification.html (부록 A.7),
  …/2.4/specs/C2PA_Specification.html (부록 A.7–A.9), c2pa-rs `sdk/src/asset_handlers/plain_text_io.rs`(커밋 2dcc9114a6, unstable 기능),
  github.com/encypherai/c2pa-text@7e81875(MIT), github.com/encypherai/encypher-ai@15f5878(AGPL-3.0), https://www.unicode.org/reports/tr51/ ,
  https://www.unicode.org/reports/tr37/ , https://paulbutler.org/2025/smuggling-arbitrary-data-through-an-emoji/ . 테스트 `test_text_marks.py`.
