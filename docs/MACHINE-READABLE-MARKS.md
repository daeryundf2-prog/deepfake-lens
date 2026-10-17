# 기계판독 표식 검사 (techniques-2026)

근거 자료: `new_techniques_2026-10.md`(2026-10 최신 기법 조사)의 "(A) 지금 추가 가능" 항목. 0단계 규칙(분석 중 네트워크 금지,
fail closed, 모든 검사의 coverage 기록, 재현 가능한 기록 근거(결정적 근거)만 결론에 참여, 부재는 원본성 근거가 아님)은 그대로다.

## 1. C2PA 2.2–2.4 판독 (`c2pa` 검사)

- SDK: `c2pa-python>=0.38`(c2pa-rs 0.91 — 검증 결과에 `specVersion` 2.4.0을 기록). 2026-10-15 PyPI 최신이 0.38.0이며
  linux-aarch64·macOS 휠이 있다(조사 문서의 "0.32.x"는 당시 집계값).
- **오프라인 검증**: 판독기는 `verify.remote_manifest_fetch=false`, `verify.ocsp_fetch=false`로 실행된다(원격 매니페스트·OCSP 조회 없음).
  그래서 인증서 폐기(revocation)는 확인하지 않는다.
- **신뢰 목록**: 이전에는 신뢰 앵커를 넣지 않아 어떤 서명자도 `signingCredential.trusted`가 될 수 없었다(강한 C2PA 규칙 두 개가 도달 불가).
  이제 C2PA 공식 신뢰 목록 스냅샷(`deepfake_lens/models/c2pa-trust-list.pem`, 2026-08-05판, 앵커 30개,
  `models/c2pa-trust.json`에 출처 URL·업스트림 커밋·조회일·sha256)을 쓰며, 사용 전에 sha256을 확인한다(다르면 `c2pa` 검사 failed
  「C2PA 검증 불가: C2PA 신뢰 목록 무결성 불일치…」). R27-5: 확인은 프로세스 안에서 파일 버전마다 한 번이다 — 목록·ITL·운영자 앵커 파일을
  읽을 때 잰 stat(장치·inode·크기·mtime_ns·ctime_ns)이 그대로인 동안만 확인된 내용을 다시 쓰고, 하나라도 바뀌면 다시 읽고 다시 확인한다
  (바뀐 파일은 여전히 failed). 판독기 Context(앵커 PEM을 파싱한 SDK 설정)도 작업 스레드마다, 확인된 sha256 조합마다 한 번 만든다. 운영자는 `DEEPFAKE_LENS_C2PA_TRUST_ANCHORS=<PEM>`으로 앵커를 더한다(읽을 수 없으면 failed).
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
  둘 다 근거 목록에 남고, 중립 항목 「근거 충돌: 서명된 원본성 근거와 생성 근거가 함께 있음」이 더해진다. P0R-10(사용자 결정 2026-10-17):
  결정 규칙도 이 충돌을 따른다 — 강한 생성 근거와 강한 원본성 근거가 함께 있으면 규칙 2가 발동하지 않고 판단 불가(규칙 2의 예외,
  `decision.CONFLICT_RULE` = 7)다.
- **모달리티**: 이미지(기존)에 더해 오디오(wav·mp3·flac·m4a)·영상(mp4·mov·avi)·문서(pdf·docx·xlsx·pptx)도 `c2pa` 검사를 한다.
  문서 결과는 텍스트 규칙상 등급이 늘 "참고"라서 서명된 매니페스트도 결론을 바꾸지 않는다(등급 상향은 사용자 결정 사항).
  `.txt`·`.md`는 `skipped` 「C2PA 텍스트·HTML 임베딩(C2PA 2.3/2.4)은 현재 SDK…가 판독하지 않음…」 — c2pa-rs 0.91은 텍스트/HTML
  임베딩을 읽지 못한다.
- **알려진 SDK 한계**: c2pa-rs 0.91은 2.4 위치(`claim_generator_info[].specVersion`)에 spec 버전을 쓴 매니페스트를 스스로 다시 읽지
  못한다(`claim_cbor: unexpected trailing data`). 그런 파일은 `c2pa` failed로 기록된다(fail closed). 빌더가 구성요소의 `digitalSourceType`을
  기록하지 않아 구성요소 수준 값은 기록된 판독 결과로만 시험했다.
- 픽스처: `fixtures/c2pa-2.4/`(재현 스크립트 `scripts/build_c2pa24_fixtures.py`, 설명 `fixtures/c2pa-2.4/README.md`), 테스트 `test_c2pa24.py`.
- **하드 바인딩 제외범위 검사(P0R-20, 위협 G3 — 2026-10-02 공개)**: `c2pa.hash.data` assertion의 제외 구간(exclusions)은 서명된 콘텐츠 해시에서 빠지는
  바이트 범위다. c2pa-rs(0.91.3까지, 게이트의 c2pa-python 0.38)는 제외 구간 하나가 매니페스트 위치와 같은지만 보고 나머지 구간은 보지 않아,
  매니페스트 구간 + 콘텐츠를 덮는 두 번째 구간(또는 나머지 전부)을 넣은 파일을 **Trusted**로 보고한다. `deepfake_lens/c2pa_binding.py`가
  판독기의 `detailed_json()` assertion 저장소에서 활성 매니페스트의 하드 바인딩을 읽어(파일은 청크·세그먼트 헤더만 읽음 — 전체를 읽지 않음)
  다음이면 신뢰 불가로 본다: 제외 구간이 파일 전체를 덮거나 해시되는 콘텐츠가 64바이트 미만, 매니페스트 저장소(PNG `caBX` 청크, JPEG APP11
  세그먼트) 밖을 64바이트(세그먼트 틀 여유)보다 많이 덮음, 겹치거나 파일 밖·음수·형식이 잘못된 구간, BMFF 제외 경로가 `/uuid`·`/ftyp`·`/mfra`
  밖, PNG·JPEG·BMFF인데 하드 바인딩이 없음. 그러면 판독 기록은 `status: "invalid"`, `error_kind: "hash_exclusion"`, `trusted: false`,
  실패 코드에 `c2pa.hash.exclusion.invalid`가 더해지고 `hard_binding`(ok·kind·reason·file_size·excluded_bytes·excluded_outside_store·
  hashed_bytes·store_located)이 남으며, `c2pa` 검사는 `failed` 「AnalyzerError: C2PA 해시 제외범위 이상 — 서명 대상에서 파일 내용 N바이트 중
  M바이트가 제외됨(매니페스트 저장소 밖 제외 K바이트) — 서명 유효 여부와 무관하게 출처 근거로 쓰지 않습니다」이다(규칙 3: 판단 불가). 픽스처
  `exclusion-twozone.png`·`exclusion-allcontent.png`(SDK가 Trusted로 보고), `exclusion-twozone.jpg`·`exclusion-wholefile.png`·
  `exclusion-paststore.png`(SDK가 이미 Invalid — 회귀용), 재현 스크립트 `scripts/build_c2pa_exclusion_fixtures.py`, 테스트 `test_c2pa_exclusions.py`.
- **신뢰 목록 분리(P0R-21)**: 2026-01-01에 동결된 임시 신뢰 목록(ITL)의 루트 앵커(`deepfake_lens/models/c2pa-itl-anchors.pem`, 27개,
  `models/c2pa-itl.json` — contentauth/verify-site `static/trust/anchors.pem`, 커밋 c2bf16d, Apache-2.0, sha256 확인)를 공식 목록과 따로 둔다.
  서명자는 먼저 공식 목록(+ 운영자 앵커)으로 확인하고, 신뢰되지 않을 때만 ITL 앵커만으로 다시 확인한다. 판독 기록의 `trust_list`는
  `c2pa`(공식 목록) / `c2pa_or_operator`(운영자 앵커가 지정된 경우) / `itl`(동결된 ITL에만 연결) / 없음이다. ITL에만 연결된 유효 서명의 생성형·촬영
  선언은 **보통**(「C2PA 서명(동결된 임시 신뢰 목록 ITL): …」 — ITL 제품은 C2PA 적합성 프로그램의 평가를 받지 않음)이며 결론 근거가 아니다.
  ITL의 최종 개체 허용 목록(`allowed.pem`)은 넣지 않았다.
- **폐기 확인(P0R-21)**: 공식 C2PA 오프라인 폐기 목록(CRL 스냅숏)은 2026-10-17 현재 공개된 것이 없어 동봉하지 않았다. 모든 C2PA 근거의 검증 기준
  문구와 `c2pa` 사유에 「인증서 폐기 여부는 확인하지 않음(오프라인 폐기 목록 미적용·OCSP 조회 안 함 — 폐기된 서명 인증서도 신뢰로 보일 수 있음)」이
  붙고 `trust_basis.revocation`은 `not_checked`다.

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
  메타데이터와 같은 급이다. 불일치는 무정보(coverage `ran` 「등록 고정 메시지 N개와 정확 일치 없음 — 무정보(워터마크 부재를 뜻하지 않음)」 — TB-8에서 문구 변경, §10).
- 범위 밖: 짧은 변 256px 미만(SDXL 워터마커가 표식을 넣지 않음) → `skipped`; 8천만 화소 초과 → `skipped`.
- 의존성 확인(R27-5): numpy·Pillow는 import하고, OpenCV는 설치 위치만 찾는다(없으면 이전과 같이 `skipped` 「의존성 부재: cv2」).
  OpenCV는 범위 안 이미지를 디코드할 때 import한다 — import가 OpenCV의 BLAS 스레드를 띄워 작은 이미지 검사에서 검사 자체보다 비쌌다.
  그래서 설치는 되었으나 import가 실패하는 OpenCV에서는 256px 미만 이미지가 「의존성 부재」 대신 「측정 범위 밖」으로 건너뛰어진다(둘 다 `skipped`).
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

## 7. 컨테이너·비트스트림 메타데이터 (`container_metadata`, P0R-22 — 위협 G2)

`deepfake_lens/container_metadata.py`는 ffprobe 없이 순수 파이썬으로 미디어 컨테이너가 스스로 적은 메타데이터를 **사실**로 읽는다. 영상·음성 행마다, 이미지 행에서는 GB 45438 `AIGC` 라벨에 한해 검사 범위에 `container_metadata`가 남는다.

- 읽는 것: ISO-BMFF(mp4·mov·m4v·m4a·3gp)의 `moov/udta/meta/keys/ilst`(QuickTime `mdta` 키 — `AIGC`, `prompt`, `workflow`, `comment`, `com.apple.quicktime.*`; `©too`/`©swr`/`©enc`), `udta` 텍스트 원자, `hdlr` 이름, `mvhd` 생성 시각, 영상 샘플 항목의 compressor 이름, XMP `uuid`/`XMP_` 패킷, C2PA `uuid` 박스의 존재(검증은 `c2pa` 검사); H.264/HEVC SEI `user_data_unregistered` 문자열(첫 영상 청크의 앞 4 MiB); 오디오 ID3v2 프레임(TXXX·TSSE·TENC·COMM·TIT2 …), RIFF `LIST/INFO`(ISFT·ICMT …)와 WAV/AVI의 `id3` 청크, FLAC Vorbis 주석; 이미지의 PNG 텍스트 청크·XMP의 `AIGC`.
- 상한: 메타데이터 박스 내용 합계 64 MiB, 값 하나 8 MiB, 박스 100 000개, 중첩 16단계. 상한에 닿으면 사유에 부분 판독을 적는다.
- 커버리지: `ran`(사유에 읽은 부분 — 예: `MP4/MOV 메타데이터 판독(udta·meta·keys·ilst·hdlr·mvhd·stsd·uuid·XMP)`), `skipped`(판독하지 않는 형식 — `… 이 형식의 컨테이너 메타데이터는 판독하지 않음(지원: MP4/MOV·RIFF(WAV/AVI)·ID3v2(MP3)·FLAC)`), `failed`(`AnalyzerError: MP4 박스 …의 크기가 0입니다 …`, `… 헤더가 잘렸습니다 …`, `MP4 박스 중첩이 16단계를 넘습니다 …` 등 구조를 걸을 수 없을 때 — 조용히 통과하지 않는다).
- 근거 규칙(`evidence_rules.container_metadata_evidence`): 읽은 필드는 결정적·중립·약함 사실 항목 하나(「컨테이너 메타데이터 판독 사실」)로 적는다 — `Lavf`·`Lavc`·`x264`·`VideoHandler` 같은 인코더·핸들러 이름은 ffmpeg로 변환·편집한 모든 파일에 있으므로 아무것도 뜻하지 않는다고 함께 적는다. 생성 방향 항목은 **등록된 생성기 선언**(`models/container-generators.json`, 버전이 캐시 키에 들어감)에서만 나온다: GB 45438-2025 `AIGC` 라벨 JSON의 `Label`이 AI 생성 값이면 결정적·생성·강함, ComfyUI 노드 그래프(JSON)가 `prompt`/`workflow` 필드에 있으면 결정적·생성·강함, 등록된 생성 서비스 SEI 문자열(`kling-ai`)이면 결정적·생성·강함, 생성 도구 필드(encoder·software·`©too`·TSSE·ISFT)의 생성기 이름 하나는 결정적·생성·**보통**(단독 결론 불가). 자유 텍스트 필드(comment·title·artist …)의 내용은 사실 항목일 뿐이다. 생성 방향 항목은 모두 `self_declared: true`이고 설명에 「자기 선언 메타데이터 — 심거나 지울 수 있음」이 들어간다. 이 계층은 원본성 항목을 만들지 않는다. `ContentProducer`가 등록표에 없으면 서비스를 귀속하지 않는다.
- 한계: 재인코딩·다시 저장하면 메타데이터·SEI가 사라지거나 바뀐다 — 부재는 원본성 근거가 아니다.

P0R-23: 영상 행의 출처 추정 문구가 존재하지 않던 영상 메타데이터 검사를 「…컨테이너 메타데이터에서 출처 단서를 찾지 못했습니다」로 적던 것을 없앴다. 지금 문구는 「영상 출처는 추정하지 않습니다 — 확인한 표식(컨테이너 메타데이터·C2PA·워터마크)의 결과는 근거 항목과 검사 범위에 있습니다.」이다.

## 8. 비가시 유니코드 표식 (`text_invisible_marks`, techniques-2026b TB-1)

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

## 9. Resemble AI PerTh 암묵 워터마크 (`watermark_perth`, techniques-2026b TB-2)

- 위협 카탈로그 O-CHATTERBOX·M-AUD-01. Chatterbox(resemble-ai/chatterbox, MIT)는 모든 출력에 `perth.PerthImplicitWatermarker().apply_watermark`를
  적용한다(`src/chatterbox/tts.py` L126·L271, `vc.py` L29·L103, 커밋 `5de7a54aa4e5`, README 「Built-in PerTh Watermarking」).
- 디코더: `resemble-perth` 1.0.1(PyPI, MIT)의 `get_watermark` 경로를 torch만으로 다시 구현(`wm_perth.py` — 게이트의 torch 2.14용 torchaudio가
  없어 `Spectrogram`을 `torch.stft(center=True, pad_mode="reflect")`와 주기 Hann 창으로 옮김). 처음 300초를 ffmpeg로 32비트 실수 WAV로 풀고
  모노 평균 → `librosa.resample(..., res_type="polyphase")`로 32 kHz → dB 크기 스펙트로그램 정규화 → PerthNet 디코더(2 kHz 이하 대역) → [0, 1] 신뢰도.
  가중치는 휠 안의 `perth_net_250000.pth.tar`(자산 `perth-implicit-250000.pth.tar`, sha256 고정, 디코더 가중치만 `weights_only` 로드).
  패키지 자체(torch 2.11 + torchaudio 2.11 임시 환경)와 242개 파일에서 최대 차이 3.0×10⁻⁷(`fixtures/perth/reference.json`).
- **의미**: 암묵 표식은 메시지가 없는 1비트다.
  - 신뢰도 ≥ 0.5(패키지의 반올림 기준) → 중립·약 사실 「PerTh 워터마크 검출」(측정 오탐률 문구 포함).
  - 등록표 항목 `perth-implicit-chatterbox`(기대값 존재 = 1, `min_confidence` 0.9) 이상 → 생성·**중** 「등록된 생성기 워터마크 일치(PerTh 1비트 존재 표식 — 결론 근거 아님)」.
    Resemble AI 서비스와 공개 라이브러리 사용자도 같은 표식을 넣으므로 Chatterbox를 생성기로 확정하지 않으며, 규칙 2(강)를 일으키지 않고 원본성 결론(규칙 5)만 막는다.
  - 미만 → `ran` 「… 검출 안 됨(신뢰도 …) — 무정보(워터마크 부재를 뜻하지 않음)」. 재녹음·음성 향상·다른 TTS는 표식을 남기지 않는다.
- coverage: torch 없음 → `skipped` 「의존성 부재: torch」(librosa 없음 → 「의존성 부재: librosa」, ffmpeg 없음 → 「의존성 부재: ffmpeg」); 가중치 없음 →
  `skipped` 「가중치 없음: … fetch_watermark_models.py --only perth …」; 핀과 다른 바이트 → `failed` 「AssetPinError: 미고정 모델: …」; 1초 미만 → `skipped`.
- **측정 범위(TB-2 수정)**: 디코더는 음성에서만 측정했다. ffmpeg의 440 Hz 사인파는 무손실·mp3·aac·opus 모두 신뢰도 0.99로 표식을 "주장"했다(220 Hz는 0.01 미만,
  vorbis 220 Hz 10초는 0.92). 그래서 처음 30초의 평균 전력 스펙트럼에서 가장 강한 봉우리 3개(±2 빈)가 에너지의 90% 이상인 순음 위주 신호는 `skipped`
  「측정 범위 밖: … 순음 위주 신호 — PerTh 디코더는 음성에서만 측정되었고 순음에서 오탐이 관찰됨 …」. 측정: LibriSpeech 240개의 비율 중앙값 0.23·최대 0.622,
  합성 유성음 픽스처 0.55·0.67, 사인파 1.0. 같은 LibriSpeech 40개를 mp3 64k·aac 64k·opus 24k로 재압축해도 0.5 이상은 0건(최대 0.118).
  오디오 행과 영상의 음성 트랙에서 실행되고 표식 문맥(`marking_context.checked`)에 `watermark_perth`가 들어간다. 추론은 torch 스레드 1개(잠금).
- 측정(2026-10-17, `docs/WATERMARK-MEASUREMENTS.md`): LibriSpeech dev-clean 240개에서 0.5 이상 3건(1.25%, 95% 상한 3.6%, 최대 0.780), 0.9 이상 0건;
  같은 240개에 패키지로 표식을 넣고 16비트로 저장하면 최소 0.922(240/240).
- 게이트 이미지 `deepfake-lens-gate:tech2`(`scripts/gate/Dockerfile.tech2`, `:tech` 위에 가중치만 추가 — 휠·파일 sha256 확인).

## 10. 공개 파이프라인의 고정 워터마크 확인 (techniques-2026b TB-8 — O-FLUX2, O-SDXL-FAMILY)

2026-10-17, 소스 코드에서 직접 확인(커밋 고정). `watermark-registry.json` 판 `2026-10-17.2`에 들어간 것만 판정에 쓰인다(정확 일치 → 결정적·생성·강, 제목은 항목별).

| 파이프라인 | 저장소@커밋 · 파일 | 메시지 | 기본 적용 | 등록 |
| --- | --- | --- | --- | --- |
| SDXL 계열 | Stability-AI/generative-models@e8cd6576 `sgm/inference/helpers.py` L54-58; diffusers@b800f828 SDXL 파이프라인 18개 | 48비트 `0xB3EC907BB19E` | 저장 경로 전부 / diffusers는 invisible-watermark 설치 시 | 기존 |
| FLUX.1 참조 구현 | black-forest-labs/flux@802fb471 `src/flux/util.py` L733-774, `save_image()` L258 | `0b0010…101110` — `bin()[2:]`가 앞의 0 두 개를 버려 **46비트** | 예(끌 수 없음, 모든 CLI·데모) | 추가 |
| SD 1.x txt2img | CompVis/stable-diffusion@21f890f9 `scripts/txt2img.py` L69-73 | 바이트 「StableDiffusionV1」(136비트, MSB 먼저 — imwatermark `bytes`=`numpy.unpackbits`, tech 이미지에서 확인) | 예(txt2img만) | 추가 |
| SD 2.x 스크립트 | Stability-AI/stablediffusion@f16630a9(GitHub 404, Software Heritage 보관본) `scripts/txt2img.py` L206-209 등 4개 | 바이트 「SDV2」(32비트) | 예 | 추가 |
| **FLUX.2** | black-forest-labs/flux2@50fe5162 `src/flux2/watermark.py`에 FLUX.1과 같은 상수가 있으나 `scripts/cli.py` L27 가져오기·L618 호출이 주석 처리 | — | **확인: 기본 워터마크 없음**(대신 EXIF `Software`=「AI generated;flux2」, TB-7) | 없음 |
| FLUX.3 klein | black-forest-labs/flux-3-image-klein@e53f4ea6 — EXIF만(`src/flux3/safety.py` L121-124) | — | 확인: 기본 워터마크 없음 | 없음 |
| SD 3.5 / Stable Cascade / diffusers의 flux·flux2·flux3·sd3·cascade·kandinsky·wuerstchen·pixart·aura_flow·hunyuandit·qwenimage·z_image·kolors·sana, Kandinsky-2/3, Qwen-Image, Z-Image, PixArt-sigma, HunyuanDiT 원 저장소 | 커밋은 등록표 `no_default_watermark` | — | 확인: 기본 워터마크 없음 | 없음 |

- DeepFloyd IF는 보이는 로고를 붙인다(비가시 메시지 아님 — 범위 밖).
- 32비트(SD 2) 메시지는 균일 비트 모형에서 우연 일치 확률이 이미지당 2×2⁻³²로 48비트보다 높다. 측정값은 `docs/WATERMARK-MEASUREMENTS.md`.
- 테스트 `test_imwatermark_registry.py`(상수 재현·항목별 제목·n/n 일치).
