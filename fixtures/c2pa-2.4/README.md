# C2PA 2.x 테스트 픽스처 (techniques-2026 item 1)

`scripts/build_c2pa24_fixtures.py --force`로 처음부터 다시 만든다(openssl 3.4 이상, c2pa-python 0.38 이상).
서명 바이트는 매번 달라지므로(ECDSA 난수, UUID) 테스트는 바이트가 아니라 판독한 사실을 확인한다.
개인 키는 임시 폴더에서 지워지고 CA 인증서만 남는다.

| 파일 | 서명자 | 매니페스트 내용 |
| --- | --- | --- |
| `ai-generated.png` | 테스트 CA의 서명자 | `c2pa.created` + digitalSourceType `trainedAlgorithmicMedia`, `c2pa.watermarked.bound`, `c2pa.soft-binding`(alg `com.adobe.trustmark.Q`, 값 `2*0101…`), `c2pa.ai-disclosure`(2.4) |
| `ai-disclosure-only.png` | 테스트 CA의 서명자 | `c2pa.created` + C2PA 이름공간 `http://c2pa.org/digitalsourcetype/empty`, `c2pa.ai-disclosure` |
| `camera-capture.png` | 테스트 CA의 서명자 | `c2pa.created` + `digitalCapture`, 편집 없음 |
| `screen-capture.png` | 테스트 CA의 서명자 | `c2pa.created` + `screenCapture` |
| `composite-ai.png` | 테스트 CA의 서명자 | `digitalCapture` 생성 + `c2pa.edited` `compositeWithTrainedAlgorithmicMedia` |
| `expired-signer.png` | 만료된 서명자(빌드 후 2분에 만료, 시간 도장 없음) | `trainedAlgorithmicMedia` |
| `other-ca.png` | 신뢰하지 않는 다른 CA의 서명자 | `trainedAlgorithmicMedia` |

- `test-ca-cert.pem` — 테스트 CA. 테스트는 `DEEPFAKE_LENS_C2PA_TRUST_ANCHORS`를 이 파일로 지정해 서명자를 신뢰시킨다.
  지정하지 않으면 동봉된 C2PA 공식 신뢰 목록(`deepfake_lens/models/c2pa-trust-list.pem`)에 없으므로 신뢰되지 않는다.
- `other-ca-cert.pem` — 신뢰 목록에 넣지 않는 다른 CA(참고용).
- 매니페스트에 C2PA 2.4 위치의 `claim_generator_info[].specVersion`을 넣지 않는다: c2pa-rs 0.91이 그 필드를 쓰지만
  다시 읽지 못한다(`claim_cbor: unexpected trailing data`). 그런 실제 파일은 c2pa 검사 실패로 기록된다.
- c2pa-rs 0.91의 빌더는 구성요소(ingredient)의 `digitalSourceType`(2.4)을 기록하지 않으므로, 구성요소 수준 출처 유형은
  기록된 판독 결과(dict)로만 시험한다(`test_c2pa24.py`).
- 폐기(revocation) 확인은 OCSP 조회가 필요해 분석 중에는 하지 않는다(오프라인 설정 `ocsp_fetch: false`).

## 하드 바인딩 제외범위 공격 픽스처 (P0R-20)

`scripts/build_c2pa_exclusion_fixtures.py --force`로 만든다. 일회용 CA(`exclusion-attack-ca-cert.pem`)로 서명한 뒤 활성 매니페스트의
`c2pa.hash.data` assertion을 바꾸고(제외 구간·해시·pad), claim의 해시 URI를 고치고, COSE 서명을 다시 해 저장소 길이를 늘린 파일이다.

| 파일 | 제외 구간 | SDK(c2pa-python 0.38) |
| --- | --- | --- |
| `exclusion-twozone.png` | 매니페스트 위치 그대로 + 그 뒤 콘텐츠 2000바이트 | Trusted(실패 코드 없음) — 공격 재현 |
| `exclusion-allcontent.png` | 매니페스트 위치 그대로 + 나머지 모든 바이트 | Trusted — 서명이 아무 콘텐츠도 덮지 않음 |
| `exclusion-twozone.jpg` | JPEG 두 구간 | Invalid(`assertion.dataHash.mismatch`) — 회귀용 |
| `exclusion-wholefile.png` | 파일 전체 하나 | Invalid — 회귀용 |
| `exclusion-paststore.png` | 매니페스트 + 뒤 콘텐츠를 한 구간으로 | Invalid — 회귀용 |

다섯 파일 모두 `c2pa` 검사가 「C2PA 해시 제외범위 이상 …」으로 실패해야 한다(`test_c2pa_exclusions.py`).
