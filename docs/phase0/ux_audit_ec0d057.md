# UI/UX·접근성·AX 점검 (HEAD ec0d057, 비게이트)

## 1) 영역별 총평

**A. 조사관 UX.** 결론 체계(세 가지 결론, 검사 범위, 실패 사유, "원본이라는 뜻이 아닙니다", in-sample·신경망 미탑재 배너)는 CLI·GUI·HTML·증거설명서에 일관되게 들어가 있고 fail-closed 문구도 대체로 정확합니다. 그런데 오독을 부르는 표현이 세 군데 반복됩니다.
- 「결정적 근거」: 한국어 일상어로는 "결정타가 된 근거"로 읽힙니다.
- 「감정 근거로 사용 가능」 등급 라벨: 판단 불가인 행, 깨진 파일 행에도 붙습니다.
- 증거설명서 기본값: 특정 죄명과 원고 측 문안이 미리 채워져 있고, 판단 불가·미지원 파일까지 갑호증으로 나열됩니다.

운영 흐름도 약합니다. 대용량 폴더에서 진행률(n/N)이 CLI·GUI 모두 없고, GUI는 30분이 지나면 결과를 포기하며, 취소된 검사는 나머지 파일이 흔적 없이 사라집니다. 검토 표시는 사건을 넘어 파일명으로 공유되어 섞입니다.

**B. 접근성(WCAG 2.2 AA 기준).** 다음은 기본기가 갖춰져 있습니다: `lang="ko"`, `:focus-visible` 윤곽선, `aria-live` 일부, radiogroup·aria-pressed 일부, CLI가 색을 전혀 쓰지 않음(색만으로 의미 전달 없음).

AA 미달 항목:
- 라이트 모드 결론 배지·주황 텍스트 대비 2.2–3.8:1
- 375px에서 가로 넘침·제목 세로 줄바꿈(1.4.10 리플로)
- 결과 카드가 role·aria-expanded 없는 div
- 스캔 시작 시 포커스가 body로 유실됨
- 전역 단일키 단축키(j/k, 화살표)가 페이지 스크롤을 가로챔(2.1.4)
- 진행률 값 없는 progressbar
- 오류가 5초 토스트로만 표시됨
- 토큰 모달에 포커스 트랩·복귀 없음
- reduced-motion 미대응
- PDF가 태그 없는 PDF(insert_textbox, /Lang·구조 없음)

**C. AX(에이전트·자동화).** 기계 인터페이스는 이렇습니다.
- 강점: scan JSON은 `schema_version: 2`와 2020-12 JSON 스키마(실제 출력이 오류 0으로 통과)를 갖고, stdout(JSON)과 stderr(경고)가 분리되며, 입력 오류는 exit 2로 문서화되어 있습니다. FastAPI `/api/scan/stream`의 SSE 진행 이벤트(index/total/verdict_code)는 쓸 만합니다.
- 약점:
  - 오류가 한국어 문장뿐이고 기계 코드가 없습니다. HTTP 오류 봉투도 3종(`detail` / `status+message` / `error`)으로 갈립니다.
  - coverage.check 식별자가 불안정합니다(`external_model`과 `model:<영문 표시이름>` 혼재). reason에 코드가 없습니다.
  - 단일 파일 명령·doctor 출력에 schema_version과 스키마가 없습니다.
  - CLI에 `--version`·NDJSON 스트림이 없습니다.
  - 명령마다 기본 출력 형식이 다르고(18개 json 기본, scan은 table), 서버 구현이 둘(stdlib webapp, FastAPI)이라 경로·파라미터·기본값이 다릅니다.
  - `ai_agent.py`는 에이전트 인터페이스가 아니라 키워드 휴리스틱입니다. 사람 글에도 `confidence: "high"`, 원점수 100을 냅니다.
  - MCP·도구 정의·capability 매니페스트는 없습니다.

---

## 2) 지적 사항

### UX

**U-1 (High) 「결정적 근거」가 "결정타"로 읽힘**
- 증거: CLI 표 `판단 불가 … # [결정적 근거] 메타데이터 부재`, HTML 보고서 "결정적 근거" 칸 아래 `메타데이터 부재 (중립·약)`, GUI 범례 "결정적 근거 없음".
- 한국어 "결정적"은 decisive의 뜻이 강해서, 판단 불가 행에 "결정적 근거: 메타데이터 부재"가 찍히면 법정 독자는 메타데이터 부재가 결정타라고 읽기 쉽습니다.
- 제안: 표시어를 「기록 근거(결정론적)」나 「재현 가능 근거」로 바꾸고, 방향(중립·약)을 제목 앞에 둡니다. 코드값 `deterministic`은 유지합니다.

**U-2 (High) 판단 불가 행에도 「감정 근거로 사용 가능」 표시**
- 증거:
  - HTML `판단 불가 [감정 근거로 사용 가능]`(broken.jpg, empty.png 포함)
  - GUI 상세의 `판단 불가 | 감정 근거로 사용 가능`
  - CLI `[결론] 판단 불가 · 등급: 감정 근거로 사용 가능`
  - 증거설명서 `자동 분석 결론: 판단 불가 / 등급: 감정 근거로 사용 가능`
- 등급은 모달리티 성격(텍스트=참고)인데 행 단위 판정처럼 읽힙니다.
- 제안: 라벨을 「감정 대상 모달리티」/「참고 전용 모달리티」로 바꾸거나, 판단 불가일 때는 등급 표시를 숨깁니다. SPEC이 라벨 문구를 정하고 있으므로 1단계 용어 개정 항목입니다.

**U-3 (High) 증거설명서 기본값에 특정 죄명·원고 측 문안이 들어감**
- 증거: 사건 정보 없이 `scan --evidence-statement-out es.md`를 실행하면 다음이 그대로 나옵니다.
  - `**사건**: (사건번호 입력) 성폭력처벌법위반(허위영상물편집등) 및 정보통신망법위반`
  - "원고(고소인)의 대리인은 … 입증하기 위하여"
  - `원고(고소인) 소송대리인: 디지털포렌식 감정센터`
- 그라데이션 픽스처, 깨진 파일, 미지원 `notes.xyz`까지 모두 「갑 제N호증」이 됩니다.
- GUI 사건 정보도 placeholder가 같은 죄명이고, 「의뢰인」 placeholder는 "(의뢰사 상호명 입력) 귀하"입니다.
- 영향: 빈칸을 놓친 채 제출하면 엉뚱한 죄명이 적히고 당사자 측이 단정됩니다. 미분석 파일이 서증으로 올라갑니다.
- 제안:
  - 필수 사건 정보가 없으면 생성을 거부하거나 「미기재」로 표시합니다.
  - 죄명 기본값을 없애고 원고/피고 측을 선택하게 합니다.
  - 호증 포함 대상을 사용자가 고르게 하고, 기본값은 분석 행만으로 합니다.

**U-4 (High) 검토 표시(★, 메모)가 사건을 넘어 파일명으로 공유됨**
- 증거: `gui.js:28` `itemKey(item){ return item.path || item.name }`. scan 행의 `path`는 상대 경로(`a1111-metadata-marker.png`)이고, 저장소는 전역 `~/.deepfake-lens/reviews.json`(`reviews.py:17`)입니다.
- 실제 관찰: 새 사건 폴더를 처음 열었는데 a1111-metadata-marker.png에 이미 「검토됨」이 표시되었습니다. 다른 세션이 남긴 기록이고, 저장소에는 `['a1111-metadata-marker.png','photo.png']`가 있었습니다.
- 영향: 다른 사건의 동명 파일(IMG_0001.jpg 등)에 검토·메모가 섞여 증거 관리 연속성이 훼손됩니다.
- 제안: 키를 sha256과 사건(스캔 루트) 조합으로 바꾸고, 사건별 저장소를 둡니다.

**U-5 (High) 취소된 검사에서 남은 파일이 기록 없이 사라짐 [0단계 해당 후보 — 파일별 기록·unrecorded_files 원칙 위반 여부를 게이트에서 판단 요망]**
- 재현: `webapp_api._scan_payload(..., should_stop=5번째 호출 후 True)`로 adv(110개, recursive)를 검사했습니다.
- 결과:
  - `summary.total: 3`, `unrecorded_files.total_files: 1(기타 건너뜀)`이고 취소 플래그가 없습니다.
  - 107개 파일은 행도 「기록되지 않은 파일」 사유도 없습니다.
  - 순차 경로는 `core.py:858` `if should_stop(): break`입니다.
  - GUI는 5초 토스트 "취소 요청됨 — 분석된 항목까지만 표시됩니다"만 띄웁니다. 이후 HTML·PDF·증거설명서 내보내기는 완전한 폴더 보고서처럼 보입니다.
- 제안:
  - `summary.cancelled=true`를 넣고 `unrecorded_files`에 `cancelled` 사유와 개수를 추가합니다.
  - 보고서 머리에 "부분 결과(취소됨)" 배너를 표시합니다.

**U-6 (High) C2PA 의존성이 없어도 실행 단위 경고가 없음**
- 증거: c2pa가 없는 환경(.venv-api)에서 scan을 돌리면 머리글은 "신경망 미탑재"·in-sample만 경고합니다.
  - signed-c2pa.png 행은 `[결정적 근거] 메타데이터 부재` / 판단 불가로 나옵니다.
  - 미실행 사유(`C2PA 출처 검증: 미실행 — 의존성 부재: c2pa`)는 `explain` 같은 행 상세에서만 보입니다.
- C2PA는 「원본성 근거」와 AI 서명 탐지의 핵심 결정 검사이므로 빠지면 결론 공간 자체가 줄어듭니다.
- 덧붙여 `pyproject` `dependencies = []`입니다. Pillow·c2pa·pymupdf·fastapi는 dev에만 있거나 아예 extras에 없습니다(pymupdf·fastapi는 어느 extra에도 없음).
- 제안:
  - 결정 검사(metadata/c2pa/image_class) 중 하나라도 의존성 부재면 "!! 결정 검사 미설치" 배너를 CLI·GUI·보고서에 표시합니다.
  - `[investigator]`, `[report]`, `[api]` extras를 만듭니다.

**U-7 (Med) 진행률·장시간 작업 상태가 없음**
- 증거:
  - CLI `--progress`는 "검사 중…"과 "검사 완료" 두 줄뿐입니다(110개 실측).
  - GUI는 값 없는 indeterminate 바와 경과 시간만 보입니다. `/api/scan-status`가 n/N을 반환하지 않습니다(`webapp_api.py:302-315`).
  - GUI 폴링은 30분이 지나면 "분석 시간 초과"로 결과를 포기합니다(`gui.js:542`). 서버 작업은 계속 돌아가는데 결과를 받을 방법이 없습니다.
- 제안:
  - CLI `--progress`에 파일별 n/N과 ETA를 표시합니다.
  - status에 `done/total/current`를 넣습니다.
  - 데드라인을 없애고 job 재접속 기능을 둡니다.

**U-8 (Med) `--folder`가 GUI 입력란에 반영되지 않음**
- `web --folder case1`로 시작해도 입력란이 비어 있고 placeholder는 `C:\Users\test\images`입니다.
- 그대로 "폴더 스캔"을 누르면 "폴더 경로를 입력하세요" 토스트가 뜹니다.
- 서버는 `default_folder`를 쓸 수 있는데 GUI가 모릅니다.
- 입력란에서 Enter를 눌러도 제출되지 않습니다.
- 범위 밖 경로는 "분석 중 오류: 허용되지 않은 경로"만 표시합니다. 허용 폴더 목록이나 `--allow-root` 안내가 없어 조치할 수 없습니다.

**U-9 (Med) 「미지원」과 「분석 실패」 혼동**
- GUI는 notes.xyz를 `미지원·실패 … 알 수 없음 / 분석 실패`로 표시합니다. CLI·JSON은 `unsupported — 지원 형식이 아닙니다`입니다.
- `batch` 명령은 미지원·검사 실패 파일을 모두 `"status":"success"`, `success_rate: 1.0`, `failed_files: 0`으로 요약합니다.
- 제안: GUI는 상태별 라벨을 분리하고, batch는 scan으로 흡수하거나 verdict별 집계를 출력합니다.

**U-10 (Med) 섞인 메시지와 노이즈**
- verdict 문자열 `이미지(측정 범위 밖(해상도) — 생성 탐지 비적용): 조작·생성 근거 있음`에서 "비적용"과 "근거 있음"이 한 문장에 함께 나옵니다.
- 텍스트 행 목록에 `참고: AI 어시스턴트 문체 유사`(source_guess)가 결론 바로 옆에 노출됩니다(사람 글 human-01.txt).
- 깨진 파일 상세에 측정 게이트 문구가 모델 6개 × 같은 문장으로 반복됩니다.
- 빠른 검사(붙여넣은 텍스트)에 "C2PA 판독 불가(NotSupported)"가 표시됩니다.
- "영어 기준으로 보정되어"에서 「보정」이 calibration 의미와 충돌합니다.
- 제안: 범위 밖 안내는 별도 줄로 빼고, 동일 사유는 묶고, 텍스트에는 C2PA를 실행하지 않습니다.

**U-11 (Med) 집계 단위 불일치**
- 같은 실행에서 stderr는 "처리 오류 4건", 표는 "판단 불가 6건(검사 실패 포함 2건)"입니다.
- 앞의 4는 ERROR 로그 레코드 수(`cli_logging.py:81`)이고 뒤의 2는 파일 수입니다. 같은 「건」이라 혼동됩니다.
- 제안: 「파일 n개, 실패 검사 m개」처럼 단위를 명시합니다.

**U-12 (Med) [0단계 해당 후보, G1/R-OUT-4 잔여] 실행되지 않은 모델의 원점수를 0으로 표시**
- `/api/check`(GUI 빠른 검사)의 `model_analysis`가 `{"available":false,"score":0,…}`입니다.
- GUI는 `외부 모델 원점수(미보정, 결론 불참여) — 0`으로 렌더링합니다(`gui.js:723`, `score != null`이면 숫자를 출력).
- 측정 게이트 때문에 돌지도 않은 모델이 "0점"으로 보입니다.
- 제안: available=false면 score를 null로 두고, 표시는 "미실행"으로 합니다.

**U-13 (Low) 용어 불일치**
- CLI `--plaintiff 원고(고소인)` / GUI 「의뢰인」, CLI `--court 제출처` / GUI 「수권법원」.
- `--include-low`(이름은 남아 있으나 의미 변경), `scan`/`batch`/`forensic`/`classify`/`explain` 등 단일·다중 명령이 겹칩니다.
- 45개 명령 중 계층 진단 16개가 최상위에 나열되어 조사관 시작점이 흐립니다.
- GUI 문구가 소비자용입니다("카톡·Threads에서 받은 글", "AI 생성 콘텐츠 스크리닝").
- "뉴럴 모델" 토글은 실행 가능한 프로필이 0개인데 기본 ON입니다.
- macOS에서도 "Windows에서 접속: http://localhost:8765"를 출력합니다.

**U-14 (Low) CSV 상단에 `#` 주석 3줄**
- Excel·pandas 기본 파싱이 깨집니다. 열 이름은 한글과 영문이 섞여 있습니다.
- 제안: 메타는 별도 파일이나 마지막 열로 옮기고 BOM을 넣습니다.

**U-15 (Low) 문서**
- README에 조사관 시작 안내가 없습니다(개발 검증 명령뿐).
- `docs/deepfake-lens-cli.md`는 2,000자 넘는 줄이 있고 라운드 ID(R16-13 등)가 28회 섞인 변경 이력형 문서라 운영 매뉴얼로 읽기 어렵습니다.

### 접근성

**A11Y-1 (High) 라이트 모드 결론 배지·경고 텍스트 대비 미달 (1.4.3)**
- `gui.css` 값으로 계산한 결과, 11px 굵은 글씨 기준:
  - 판단 불가 `.band-medium`(#f08c00 on 15% 주황 틴트) 2.2:1
  - 원본성 근거 있음 `.band-low` 2.78:1
  - 조작·생성 근거 있음 `.band-high` 3.75:1
  - `--orange`/`.rev-badge` on white 2.48:1, `--green` on white 3.13:1
  - `--muted` on card2 4.31:1(11–12.5px 본문)
- 다크 모드는 대부분 통과하지만 `.nn-badge` 흰 글씨 on #cf7df2는 2.67:1입니다.
- 판단 불가에 주황(경고색)을 쓰는 점(`VERDICT_CLS undetermined:'medium'`, `gui.js:666`)도 "중간 위험"으로 읽히게 합니다.
- 제안: 텍스트용 진한 색 토큰(예: #b35c00, #1e7e34)을 따로 두고, 판단 불가는 중립색을 씁니다.

**A11Y-2 (High) 375px 리플로 실패 (1.4.10)**
- 모바일 프리셋 실측: `scrollWidth 473 > clientWidth 375`. 사용법 카드 타일 폭이 390px이고, 「분석 결과」 h2가 한 글자씩 세로로 줄바꿈되며, 내보내기 버튼(JSON/CSV/HTML…)과 배너 문장이 오른쪽에서 잘립니다(스크린샷).
- 제안: 결과 툴바를 wrap하거나 메뉴화하고, 가이드 범례의 min-content를 해소합니다.

**A11Y-3 (Med) 결과 카드 의미 구조 없음**
- `.res-main`은 tabindex만 있는 div입니다(`gui.js:1121`). role=button도 aria-expanded도 없습니다.
- ★ 버튼의 이름이 "★"뿐입니다(title만 "검토 표시 토글").
- 통계 필터 pill 4개가 같은 title "클릭하면 이 결론만 표시"를 씁니다.
- `<main>` 랜드마크와 건너뛰기 링크가 없습니다. h2에 이모지가 들어가 "나침반 사용법"처럼 읽힙니다.
- 업로드용 `input[type=file]` 2개는 이름이 없습니다.

**A11Y-4 (Med) 포커스 관리**
- 「폴더 스캔」을 누르면 `setBusy`가 포커스된 버튼을 disabled로 바꿔 `document.activeElement`가 BODY가 됩니다(실측).
- 완료 후에도 결과로 포커스가 이동하지 않습니다.
- 토큰 모달(`gui.html:200`)은 포커스 트랩·배경 inert·닫을 때 포커스 복귀가 없습니다.

**A11Y-5 (Med) 문자 단축키·화살표 가로채기 (2.1.4)**
- `gui.js:1245`의 document keydown이 결과가 있으면 j/k/화살표를 preventDefault합니다. 페이지 화살표 스크롤과 스크린리더 가상 커서 조작이 깨집니다.
- 「kb-focus」는 시각 클래스일 뿐 실제 포커스가 아니라 보조기기가 따라가지 못합니다.
- 끄거나 재지정할 방법이 없습니다.
- 픽셀 모드 radiogroup은 버튼 3개가 모두 Tab 정지점이고 화살표 이동을 지원하지 않습니다.

**A11Y-6 (Med) 상태 전달**
- `role=progressbar`에 aria-valuenow·min·max가 없습니다.
- 오류는 5초(성공 2.8초) 뒤 사라지는 토스트로만 표시됩니다(`gui.js:102`).
- `#qc-out`과 `#stat-pills` 전체가 aria-live라서 빠른 검사 결과 수십 줄이 한꺼번에 낭독됩니다.
- 제안: 요약 한 줄만 live로 두고, 오류는 지속 표시 영역에 둡니다.

**A11Y-7 (Med) 보고서 접근성**
- PDF는 `pdf_layout.py`의 `insert_textbox` 렌더링이라 태그 없는 PDF입니다. /Lang, 구조 트리, 제목 메타데이터가 없습니다(정적 확인, 호스트와 Docker 모두 pymupdf가 없어 실제 렌더링은 못 함).
- HTML 보고서: `lang="ko"`·h1/h2는 있지만 표에 `scope`·`caption`이 없고, 히트맵 alt가 일괄 "히트맵"입니다(`reports.py:807`).
- 제안: PDF/UA 태깅(또는 HTML 원본 병행 제공), th scope, 히트맵 alt에 파일명·계층명을 넣습니다.

**A11Y-8 (Low)**
- `prefers-reduced-motion` 미대응(무한 progress slide, 진입 애니메이션).
- 사건 정보 입력란은 label로 감싸 이름은 붙지만, placeholder가 실제 값처럼 보입니다(U-3과 연결).
- CLI 표는 한글 전각 폭 때문에 열 정렬이 깨집니다. 스크린리더 사용자에게는 `--format json`이나 행별 문장 출력 옵션이 더 낫습니다.
- 반대로 CLI가 ANSI 색을 쓰지 않고 `!!` 표식을 쓰는 점은 좋습니다(NO_COLOR 불필요).

### AX

**AX-1 (High) 기계용 오류 코드 없음, HTTP 오류 봉투 3종**
- `/openapi.json` 404 → `{"detail":"찾을 수 없는 경로입니다"}`
- 헤더 누락 401 → `{"status":"error","message":…}`
- 범위 밖 403 → `{"error":"허용되지 않은 경로"}`
- CLI는 `오류: …` 한국어 문장과 exit 2뿐입니다.
- 에이전트는 문자열 매칭으로만 분기할 수 있고, 문구가 바뀌면 깨집니다.
- 제안: `{"error":{"code":"READ_ROOT_DENIED","message_ko":…,"hint":…}}`로 통일하고, CLI에 `--format json` 오류 객체(stderr)를 둡니다.

**AX-2 (High) coverage 식별자·사유 코드 불안정**
- 같은 실행에서 `check`가 `external_model`(범위 밖)과 `model:SBI-trained EfficientNet-B0 v2 (local, enriched self-blend: polygon/affine masks + diverse portraits)`(게이트 미충족)처럼 서로 다르게 나옵니다.
- 스키마는 `check: string`, `reason`은 자유 한국어 문장입니다(`$defs/coverage_entry`).
- 「측정 범위 밖 / 사진 아님 / 의존성 부재 / 측정 게이트 / 미고정 모델」을 기계적으로 구분할 수 없습니다.
- 제안: `check_id`(enum과 profile 파일명), `reason_code` enum, `exception_class` 필드를 추가하고 스키마 v2.1로 올립니다.

**AX-3 (Med) 출력 계약이 scan에만 있음**
- `forensic`/`classify`/`explain`/`agent`/`doctor` JSON에는 `schema_version`도 스키마도 없습니다.
- 단일 파일 출력은 `kind`가 `"analysis_result"`(출력 종류)이고 모달리티는 `file_kind`입니다. scan 행은 `kind`가 모달리티입니다. 이름이 충돌합니다.
- 최상위 `coverage`(가중치 집계)와 행의 `coverage`(검사 범위)도 이름이 같습니다.
- 제안: 공통 `item` 스키마로 단일·다중을 통일하고, doctor 스키마를 둡니다.

**AX-4 (Med) CLI 일관성**
- `--version`이 없습니다. `deepfake-lens --version`은 scan으로 넘어가 "<폴더> 필요" 오류, exit 2를 냅니다.
- 기본 출력 형식이 명령마다 다릅니다(26개 형식 옵션 중 18개가 json 기본, scan은 table, explain은 text|json). help에 기본값이 표시되지 않습니다.
- 에이전트가 scan 대상으로 파일을 주면 거부됩니다("단일 파일은 forensic/classify를 사용").
- scan의 종료 코드는 결론·실패와 무관하게 항상 0입니다. `--fail-on manipulation|failed` 같은 게이트 옵션이 없습니다.
- `verify-report`만 2의 의미가 다릅니다(키 ID 불일치, 사용 오류는 4).

**AX-5 (Med) 스트리밍**
- FastAPI SSE `/api/scan/stream`은 좋지만, CLI에 NDJSON(`--format ndjson`, 행 단위 결과·진행)이 없습니다.
- `/api/scan`의 파라미터 `folder`와 `/api/scan/stream`의 `directory`가 다르고, 기본 `max_files`도 200(stream)과 1000(CLI·GUI)으로 다릅니다.
- 상태를 바꾸는 GET(`/api/scan`이 작업 시작, `/api/scan-cancel`)도 있습니다.

**AX-6 (Med) 서버가 두 개**
- `webapp.py`(stdlib)와 `api_server.py`(FastAPI)의 라우트가 다릅니다. `/api/jobs/*`, `/api/scan/stream`, `/api/analyze/*`는 FastAPI에만 있습니다.
- `/api/health`는 `{"status":"healthy"}`뿐이라 버전, 스키마 버전, 실행 가능 프로필, 의존성 상태를 알 수 없습니다.
- FastAPI 자동 문서 비활성은 합의된 결정이라 재지적하지 않습니다. 대신 정적 OpenAPI 파일을 `contracts/`에 커밋하는 방안을 제안합니다.
- 토큰을 `--token` 인자로만 받아 프로세스 목록에 노출됩니다. 환경 변수 대안이 없습니다.

**AX-7 (Med) `ai_agent.py`의 정체와 품질**
- "AI 에이전트가 만든 콘텐츠" 키워드 탐지기입니다(AGENT_MARKERS + 정규식). 에이전트가 도구를 쓰는 인터페이스가 아닙니다. 이름 때문에 에이전트 연동 기능으로 오해받기 쉽습니다.
- 실측: 사람 글 "The operator said the pipeline was done. Step 1 … Devin and Cursor AI helped."에 `agent_type: openai_agent`, `confidence: "high"`, `raw_score 100/100`이 나옵니다(`operator`, ` done`, ` processing` 같은 부분 문자열 매칭).
- 결론에는 참여하지 않습니다(layer_diagnostic, verdict 판단 불가·참고). 그러나 `confidence: high`는 D1의 "band/verdict 없음" 취지와 어긋나는 등급형 필드입니다. 게이트에서 D1 위반 여부를 판단해 볼 만합니다(0단계 해당 가능성 낮음, 참고).
- 제안: 이름을 `agent_markers`로 바꾸고, confidence를 없애거나 「참고」로 고정하고, 단어 경계 매칭을 씁니다. 1단계에서 퇴출을 검토합니다.

**AX-8 (Med) LLM 에이전트 연동 수단 없음**
- MCP 서버, 도구 스키마, capability 매니페스트가 없습니다.
- 에이전트는 다음을 스스로 조합해야 합니다: 한국어 help 파싱, 명령별 기본 형식 차이, 파일·폴더 명령 구분, 오류 문장 파싱.
- 제안: `deepfake-lens mcp` 또는 `tools.json`을 만들고 도구를 둡니다(`scan_folder`, `analyze_file`, `explain`, `doctor`, `verify_report`). 각 도구의 출력은 스키마 참조 JSON으로 하고 결론 3값 enum을 명시합니다. 도구 설명에 "판단 불가≠원본", "text=참고" 같은 해석 규칙을 넣어 LLM의 오독을 막습니다.

**AX-9 (Low) 결정성**
- 같은 폴더·같은 입력 재실행은 바이트 동일했습니다(두 번째 실행 차이는 파일 추가 때문).
- `path`는 루트 기준 상대 경로, `path_b64`가 동반되어 좋습니다.
- 다만 레거시 `signals[].weight: 60`과 `score: 0`/`ai_score: 0`이 `band: high`와 함께 나옵니다. 구 소비자에게는 "0점인데 high"로 보입니다. SPEC에 따른 호환 필드이므로 1단계에서 폐기 일정을 문서화하기를 권합니다.

---

## 3) 1단계 권고 (우선순위 상위 10)

| # | 항목 | 관련 | 공수 |
|---|---|---|---|
| 1 | 취소·부분 결과를 기록: `summary.cancelled`, unrecorded 사유 `cancelled`, 보고서 배너 (0단계 게이트 판단 선행) | U-5 | S |
| 2 | 검토 저장소 키를 sha256+사건으로 바꾸고 사건별 파일로 분리. 기존 전역 기록 이전·경고 | U-4 | S–M |
| 3 | 증거설명서 기본값 정리: 죄명·당사자 측 기본값 제거, 필수 정보 없으면 거부, 호증 대상 선택 | U-3 | M |
| 4 | 용어 개정: 「결정적 근거」→「기록(결정론적) 근거」, 등급 라벨 재정의·판단 불가 시 숨김, 판단 불가 중립색. CLI·GUI·보고서·문서 동시 | U-1, U-2, A11Y-1 | M |
| 5 | 결정 검사 의존성 부재 배너와 설치 extras 정비(`investigator`/`report`/`api`) | U-6 | S |
| 6 | 진행률: CLI `--progress` n/N·ETA, `/api/scan-status`의 done/total, GUI 30분 데드라인 제거·재접속 | U-7 | M |
| 7 | 오류·coverage 기계 코드: 통일 오류 봉투, `reason_code`/`check_id` enum, 스키마 v2.1과 단일 파일·doctor 스키마 | AX-1–3 | M |
| 8 | GUI 접근성 일괄 수정: 대비 토큰, 375px 리플로, 카드 role/aria-expanded, 포커스 관리, 단축키 토글, progressbar 값, 지속 오류 영역, reduced-motion | A11Y-1–6 | M |
| 9 | 에이전트 인터페이스: `--version`, 공통 기본 형식(json), CLI `--format ndjson`, `--fail-on`, health에 capability 포함, MCP 서버 또는 tool 정의와 정적 OpenAPI | AX-4–6, AX-8 | M–L |
| 10 | 보고서 접근성: 태그 PDF(/Lang, 구조), HTML th scope·caption, 히트맵 alt. CSV 주석 제거·BOM | A11Y-7, U-14 | M |

차순위: `ai_agent.py` 정리 또는 퇴출(AX-7, S), 명령 정리(계층 진단을 `diag` 하위로 내리고 batch를 scan에 흡수, U-13, M), 조사관용 README·운영 매뉴얼 분리(U-15, S), `/api/check`의 미실행 모델 score null 처리(U-12, S — 게이트에서 0단계 여부 판단).

---

## 4) 부록: 실행·확인한 것

- 환경: 개인 클론 `/private/tmp/claude-501/uxa/repo`(HEAD ec0d057, 수정 없음). 호스트 `.venv`(PYTHONPATH=클론), `.venv-api`(+ .venv site-packages로 Pillow 보충, 단독 실행도 시험). Docker는 PDF 확인용으로 1회만 `--rm --cpus 1` 실행했고, pymupdf가 없어 실패했습니다.
- 테스트 폴더 `/private/tmp/claude-501/uxa/work/case1`: a1111·gradient·real-like 픽스처, signed-c2pa.png, human-01.txt, 잘린 JPEG, 빈 PNG, notes.xyz, adversarial gradient, screenshot 하위 폴더. `scripts/make_adversarial_fixtures.py --out work/adv`로 110개 생성.
- CLI:
  - `--help`, `scan` table/json/`--progress`/`--html-out`/`--csv-out`/`--evidence-statement-out`, `--pdf-out`(exit 2, pymupdf 없음)
  - `scan /nonexistent`, `--bogus`, `--format jsn`, `--version`, 파일을 scan 대상으로 지정
  - `explain`, `forensic`, `classify`, `agent`(--text/--file, table/json), `face`, `batch`(--output), `doctor` table/json
  - adv 110개 `--recursive`, 동일 입력 재실행 비교
  - 출력 JSON을 `contracts/deepfake-lens-scan-result-v2.schema.json`으로 jsonschema 검증(오류 0)
  - `webapp_api._scan_payload`에 should_stop을 주입해 취소 재현
- GUI(`deepfake-lens web --folder case1 --host 127.0.0.1 --port 8765`, Claude Browser):
  - 빈 경로 스캔, 정상 스캔, 범위 밖 경로(/etc)와 없는 경로 오류
  - 결과 카드 열기(broken.jpg), 빠른 텍스트 검사, `/api/check` 응답 확인
  - read_page 접근성 트리, Tab 순서·포커스 표시, 스캔 중 activeElement 추적
  - 1280px 라이트·다크, 375px 모바일 넘침 측정, 계산 스타일 확인
  - 진행 중 서버는 모두 종료했고 뷰포트도 원래대로 되돌렸습니다. 검토 표시는 누르지 않았습니다(기존 reviews.json은 다른 세션이 남긴 것).
- API(`api-serve --port 8766 --allow-root case1`): `/api/health`, `/openapi.json`, `/docs`, `/api/scan`(헤더 유무), 범위 밖 403, `POST /api/scan/stream` SSE.
- 정적 검토:
  - `gui.html`, `gui.css`(대비 계산 스크립트 `/private/tmp/claude-501/uxa/contrast.py`), `gui.js`(리뷰 키, 단축키, 토스트, 폴링, 모달)
  - `webapp.py`, `webapp_api.py`, `api_server.py` 라우트, `ai_agent.py` 전문, `cli_logging.py`, `core.py` 취소 경로, `reports.py`, `pdf_layout.py`, `pyproject.toml` extras
  - `docs/deepfake-lens-cli.md`(종료 코드), `docs/deepfake-lens-json-contract.md`, README, SPEC, HANDOFF §7(합의 사항은 재지적하지 않음)
