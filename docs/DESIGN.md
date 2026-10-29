# 캡처 이미지 디자인 규격 (슬라이드용)

대상: "현재 화면 복사"(shmoo, 로그 뷰)와 리포트 섹션 "복사"로 나오는 PNG.
목표: PowerPoint 16:9 슬라이드에 **붙여 넣고 크기를 바꾸지 않아도** 발표용으로 바로 쓸 수 있는 이미지. 지금은 웹 대시보드(DXP) 화면을 잘라 붙인 느낌이다.

목업(검증용 렌더, Noto Sans CJK KR로 그림): `/tmp/claude-1000/design-scratch/`
`slide-full.png`, `slide-halves.png`, `shmoo-single-L.png`, `shmoo-all-M.png`, `log-L.png`, `report-categories-M.png`, `report-summary-S.png` (생성 스크립트 `mocks.py`).

---

## 1. 진단

기준: 16:9 슬라이드는 13.333 × 7.5 in이고, PowerPoint는 이를 96 dpi 기준 **1280 × 720 px**로 다룬다 [1][2]. 아래 "슬라이드 pt"는 이미지를 본문 영역 폭(1180 px)에 맞춰 넣었을 때의 크기다.

| 캡처 | 문제 |
|---|---|
| `shmoo-capture.png` (590×464 CSS px, 2x) | ① 크기가 격자 칸 수(`CW=30, CH=22`)에 따라 달라지고 비율이 1.27:1이라 슬라이드 어느 자리(전폭/반/1/3)에도 맞지 않는다. ② 1180 px 폭에 맞추면 2배 확대: 눈금 11px → 16.5pt로 괜찮지만, 반쪽(576 px)에 넣으면 약 8pt가 돼 읽을 수 없다. ③ 제목이 `Shmoo: VDD_V x FREQ_MHZ`, 즉 컬럼 이름일 뿐 결론이 없다. 축 이름 `VDD_V`, `FREQ_MHZ`는 단위가 없는 원시 이름이다. ④ 진한 청록/주황 두 색이 면적 전체를 같은 무게로 칠해서 정작 봐야 할 **구멍(1.20–1.26 V × 1.9–2.1 GHz)**이 묻힌다. 구멍·spec·동작점 표시가 없다. ⑤ 선택한 칸의 검은 테두리(UI 상태)가 캡처에 들어간다. ⑥ 범례가 아래에 따로 있고 `FREQ_MHZ` 라벨과 겹친다. 미측정 회색 `#e7e5e4` 칸은 흰 배경에서 거의 보이지 않는다. ⑦ 청록/주황 쌍은 명도 차가 작다(`#0e7490` vs `#c2410c`, 대비 약 1.2:1). 흑백 인쇄나 프로젝터에서 구분이 약하다. |
| `logview-capture.png` (720×300, 2x) | ① 2.4:1의 납작한 띠라 전폭에 넣으면 높이가 300 px뿐이고, 반쪽에 넣으면 글자가 6pt대로 줄어든다. ② 제목은 파일명/아티팩트 제목이고, 부제는 마스킹된 패턴 원문(`<n>ms ... <ip>`)이다. 결론("03:10–03:24 ERROR 급증")이 없다. ③ INFO가 청록 `#0e7490`이라 배경이어야 할 정상 트래픽이 가장 눈에 띄고, ERROR는 막대 꼭대기에 얹혀 높이를 읽기 어렵다. ④ 장애 구간 음영이나 주석이 없다. ⑤ x 눈금이 `01:05, 02:10…`처럼 버킷 개수로 잘린 어색한 시각이다. ⑥ 범례가 하단 왼쪽에 있고 "건수 / 5분"이 하단 오른쪽 구석에 있다. |
| `report-*.png` (720 폭 섹션, 2x) | ① 카드 테두리, 둥근 모서리, 회색 페이지 여백(`#f5f5f4`)이 그대로 찍혀 웹 카드 스크린샷처럼 보인다. ② 모든 섹션 제목이 `2026년 9월 카드A 지출 · 카테고리별 지출 2026-09` 식으로 반복되고 기간 정보가 중복된다. ③ 막대 색 의미(주황 = 예산 초과)가 범례 문장("검은 눈금 = 예산, 주황 막대 = 예산 초과")에만 있다. ④ 금액이 막대에서 멀리 떨어진 오른쪽 열에 있다(`603,800`). ⑤ 높이가 섹션 내용마다 제각각이다(206–620 px). 메모 섹션은 텍스트라 이미지일 이유가 없다. |
| 공통 | ① 글꼴이 렌더 환경에 따라 다르다. 프레임 캡처는 **사용자 브라우저**의 시스템 글꼴(Windows면 맑은 고딕)을 쓰고, 서버 캡처는 headless Chrome의 Noto Sans CJK KR을 쓴다. 같은 앱인데 결과가 다르다. 앱 CSS의 `IBM Plex Sans KR`은 이 머신에 설치돼 있지 않다(`fc-list`). ② 크기 선택이 없다. 언제나 "보이는 그대로"만 찍는다. ③ 파일명이 `art123-v1-1-3-2_85.png`처럼 내용을 알 수 없다. |

## 2. 원칙

1. **제목은 결론을 말한다.** "무엇을 그렸나"가 아니라 "무엇을 봐야 하나"를 쓴다(FT/Economist/Datawrapper 공통) [4][5][6]. 무엇을 그렸는지는 부제가, 데이터 출처는 출처 줄이 맡는다.
2. **슬라이드 크기로 설계한다.** 캡처는 슬라이드 본문의 전폭/반/1/3 자리에 맞춘 고정 캔버스에 그린다. 1 논리 px = 1/96 in = 0.75 pt이므로, 붙여 넣은 뒤 크기를 바꾸지 않으면 글자 크기가 규격 그대로 유지된다.
3. **강조 1색 + 중립 배경.** 봐야 할 것(실패 구멍, ERROR, 예산 초과)만 진한 색으로 칠하고, 나머지는 회색이나 연한 색으로 둔다 [3][7].
4. **범례 대신 직접 라벨.** 계열 3개 이하면 마크 옆에 이름을 쓴다. 범례가 필요할 때는 플롯 안 좌상단이나 오른쪽 여백에 한 줄로 둔다 [6][8].
5. **찾은 것에 주석을 단다.** shmoo 구멍, spec 박스, 동작점, 장애 구간처럼 분석(`core.analyze`, 로그 버킷)이 이미 찾아낸 것은 그림 위에 표시하고 라벨을 붙인다.
6. **잉크를 아낀다.** 카드 테두리, 그림자, 둥근 프레임, 진한 격자를 쓰지 않는다. 격자는 가는 1 px 연회색이고 축선은 기준선 하나만 둔다 [3].
7. **색만으로 의미를 전하지 않는다.** pass/fail은 명도 차 + 색상 차(파랑/살구)로 구분하고, 상태색에는 항상 텍스트 라벨이 붙는다. 프로젝터 물빠짐과 흑백 인쇄를 견뎌야 한다 [9][10][11].
8. **UI 상태는 찍지 않는다.** 선택 테두리, hover, 버튼은 캡처에서 뺀다. 캡처는 같은 데이터로 다시 그린 "발표용 렌더"다.

## 3. 캡처 규격

### 3.1 캔버스 (논리 px, 1 px = 0.75 pt)

슬라이드 1280×720, 좌우 여백 50, 위 140(슬라이드 자체 제목 자리), 아래 40 → 본문 영역 **1180 × 540**.

| 프리셋 | 캔버스 | 슬라이드 배치 | 용도 |
|---|---|---|---|
| `L` 전폭 | **1180 × 540** | x=50, y=140 | 기본값. 단일 shmoo, 로그 타임라인 |
| `M` 반쪽 | **576 × 540** | 50 / 654 (간격 28) | 2개 나란히: 장치 vs 전체, 차트 + 표 |
| `S` 1/3 | **376 × 540** | 50 / 452 / 854 (간격 26) | KPI 숫자, 작은 비교 |

- **배율 2x**로 내보낸다. 결과 PNG는 L 2360×1080, M 1152×1080, S 752×1080이고, 배치 크기에서 192 dpi다. 1080p 프로젝터는 슬라이드 전체를 약 1.5x로 보여 주므로 2x면 선명하고, 4K에서도 약간 확대되는 정도로 견딘다. 3x는 파일만 커진다.
- PNG에 `pHYs` = 192 dpi(7559 px/m)를 기록한다. "저장 → 삽입" 경로에서 PowerPoint가 이 값을 쓰면 L 이미지가 정확히 12.29 in(1180 px 상당)로 들어간다. 클립보드 경로는 브라우저가 DPI를 버릴 수 있으니 구현 후 실제 PowerPoint(Windows/Mac)에서 붙여넣기 크기를 확인한다. 너무 크게 들어오면 PowerPoint가 슬라이드에 맞춰 줄이므로, 그때는 "너비 12.29 cm/in 맞추기" 안내를 툴팁에 넣는다.
- **안쪽 여백**(캔버스 가장자리 → 내용): L 상 28 / 좌우 32 / 하 22, M 24/24/22, S 20/20/22.
- **세로 구조**(위에서부터): 제목(1–2줄) → 부제 1줄 → 간격 24 → 플롯 → 출처 줄(바닥에서 22). 플롯 높이는 나머지 전부다.
- **배경**은 불투명 `#FFFFFF`. 투명 PNG는 어두운 템플릿에 놓이면 진한 잉크가 사라지고, 회사 템플릿은 대부분 흰 바탕이다. 투명 옵션은 두지 않는다.
- **형식**은 PNG만. SVG는 PowerPoint 365가 삽입·"도형으로 변환"을 지원하지만 [12], 한글 `<text>`가 받는 쪽 PC의 글꼴로 바뀌고 변환 기능도 버전에 따라 다르다는 보고가 있다 [13]. 편집용 SVG 저장은 후순위 옵션으로만 둔다.
- **크기 선택 UI**: "현재 화면 복사"를 분할 버튼으로 바꾼다. `복사 ▾` 메뉴는 `전폭 L / 반쪽 M / 1/3 S`와 체크박스 `제목 포함`(기본 켜짐)이다. 마지막 선택은 `localStorage`에 기억한다. 뷰마다 기본 프리셋: shmoo 단일 L, shmoo 전체 M, 로그 L, 리포트 요약 S, 카테고리 M, 큰 거래 M.
- **제목 끄기**: 슬라이드 제목에 이미 결론을 쓰는 사용자를 위한 옵션이다. 끄면 제목·부제 영역을 지우고 플롯이 그 높이를 가져간다(캔버스 크기는 그대로).
- **파일명**: `{뷰}_{대상}_{조건}_{프리셋}_{YYYYMMDD-HHmm}.png`. 예: `shmoo_1-3-2_TEMP85_L_20261030-1412.png`, `log_order-svc_ERROR_L_….png`, `report_2026-09_categories_M_….png`. `[\w.-]` 이외 문자는 `_`로 바꾸고 80자에서 자른다.

### 3.2 내용 밀도 규칙 (글자를 줄이지 말고 내용을 줄인다)

글자 크기는 프리셋과 무관하게 고정한다. 작은 프리셋에서는 다음을 줄인다.

- x 눈금 라벨 간격이 최소 72 px가 되도록 눈금을 솎는다(2, 5, 10 배수 값만).
- y 눈금 라벨 간격은 최소 40 px.
- 제목은 2줄까지(L은 한 줄 약 52자, M 약 24자, S 약 15자). 넘으면 자동 제목의 짧은 형태를 쓴다(4장 참고).
- 범례 항목이 폭을 넘으면 출처 줄 위에 두 줄로 놓는다. 항목 수는 줄이지 않는다.

## 4. 디자인 토큰

### 4.1 색

모든 캡처 공통이며 라이트 전용이다(슬라이드는 흰 바탕).

| 토큰 | hex | 흰 바탕 대비 | 용도 |
|---|---|---|---|
| `--ink` | `#1A1A1A` | 17.4:1 | 제목, 직접 라벨, 주석 |
| `--ink-2` | `#5C5C5C` | 6.7:1 | 부제, 눈금 숫자, 축 이름 |
| `--muted` | `#767676` | 4.5:1 | 출처 줄(4.5:1 하한) |
| `--grid` | `#E6E6E6` | — | 격자선 1 px |
| `--axis` | `#BDBDBD` | — | 기준선(0선) 1 px |
| `--band` | `#F4F2EE` | — | 주석 구간 음영(장애 구간) |
| `--bg` | `#FFFFFF` | — | 캔버스 |
| `--accent` | `#B42318` | 6.6:1 | 강조 1색: 실패 구멍 테두리, ERROR, 핵심 주석 |
| `--pass` | `#2A78D6` | 4.4:1 | Pass(=동작함, shmoo의 "내용") |
| `--fail` | `#F2A88A` | 1.95:1 | Fail(배경 성격의 면). pass와 2.3:1, CVD ΔE 30 이상 |
| `--untested` | `#FFFFFF` + 테두리 `#D0CEC8` 1 px | — | 미측정. 빈 칸으로 보여야 한다 |
| `--over` | `#D4572A` | 4.0:1 | 예산 초과 막대 |
| `--over-text` | `#A33A14` | 6.4:1 | 초과·증가 텍스트(▲ 기호와 함께) |
| `--neutral-bar` | `#C9C7C1` | — | 비강조 막대(예산 내, INFO) |

**pass/fail 쌍 근거**: 빨강/초록은 적록색약에게 구분되지 않는다 [9][10]. 파랑/주황 계열은 Okabe–Ito가 권하는 CVD 안전 쌍이다 [9]. fail을 연한 살구색으로 둔 것은 "fmax 위의 실패"가 정상적인 배경이기 때문이다. 명도 차가 커서 흑백에서도 구분된다. `--fail`은 흰 바탕 대비가 3:1 미만이라 relief 규칙에 따라 라벨을 반드시 붙인다(직접 라벨 범례 + 출처 줄의 판정 기준) [11]. 검증: `validate_palette.js "#2a78d6,#f2a88a"`에서 CVD/정상시 분리는 PASS다. lightness/chroma "FAIL"은 범주 계열용 규칙이라 의도된 배경 면에는 적용하지 않는다.

**shmoo 통과율 (전체 장치)**: 구간을 나눈 발산형이다. 파랑 팔은 통과, 살구 팔은 실패, 중간은 중립 회색이다 [7][14]. 연속 보간을 쓰지 않는 이유는 엔지니어가 읽는 값이 "100%냐, 아니냐"이기 때문이다.

| 구간 | 100% | 95–99% | 80–94% | 50–79% | 1–49% | 0% | 미측정 |
|---|---|---|---|---|---|---|---|
| hex | `#2A78D6` | `#6DA7EC` | `#B7D3F6` | `#ECEAE4` | `#F8CDB9` | `#F2A88A` | `#FFFFFF`+테두리 |

양 끝 색은 단일 장치의 pass/fail과 같아서 두 그림을 나란히 놓아도 의미가 맞는다.

**로그 레벨**: 정상 트래픽은 회색으로 둔다.

| 레벨 | hex | 비고 |
|---|---|---|
| FATAL | `#7A1A12` | ERROR보다 어둡게, 같은 계열 |
| ERROR | `#B42318` | = `--accent` |
| WARN | `#E8A317` | 흰 바탕 2.2:1이므로 라벨 필수 |
| INFO | `#CFCDC7` | 배경 |
| DEBUG | `#E6E4DF` | 배경 |
| 레벨 없음 `-` | `#DAD8D2` | 배경 |

상태색(빨강=오류, 주황/노랑=경고)은 Carbon의 alert 관례를 따른다 [7]. 쌓는 순서는 **FATAL·ERROR가 기준선 쪽**, 그 위에 WARN, INFO/DEBUG가 맨 위다. 심각한 레벨의 높이가 0선에서 바로 읽혀야 한다.

### 4.2 타이포그래피

**글꼴: Pretendard** (OFL 1.1, Inter + 본고딕 기반, 9 weight) [15]. 숫자·영문이 Inter 계열이라 `1.20 V`, `2.0 GHz`가 깔끔하다. 탭형 숫자(`tnum`)는 구현 시 렌더로 확인한다.

```
font-family: "Pretendard", "Noto Sans CJK KR", "Noto Sans KR", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
font-variant-numeric: tabular-nums;   /* 눈금·표·금액 */
```

- 이 머신에 있는 한글 글꼴은 `Noto Sans CJK KR` Regular/Bold 두 굵기뿐이다(`fc-list :lang=ko`). Pretendard, IBM Plex Sans KR, Spoqa Han Sans Neo는 없다.
- **배포 방식이 제약 조건이다.** 렌더 프레임은 네트워크가 없다. CSP는 `font-src {CDN} data:`이고, 직렬화한 SVG를 `<img>`로 그리는 순간 외부 리소스는 아예 로드되지 않는다 [16]. 따라서 글꼴은 **data: URI로 인라인**해야 한다.
  - 저장소에 `assets/fonts/Pretendard-{Regular,SemiBold}.otf`를 둔다(OFL 사본 포함).
  - 뷰를 만들 때(`build`) 데이터와 템플릿 문자열에 실제로 쓰이는 글자만 `fontTools.subset`으로 잘라 woff2로 만든다. 예상 크기는 굵기당 수십 KB다. 이것을 페이지 CSS와 **캡처용 SVG 안의 `<style>@font-face`** 양쪽에 넣는다.
  - 의존성 `fonttools[woff]`(brotli) 추가가 필요하다. 구현자 결정 사항이며, 이 문서에서는 설치하지 않았다.
  - `MAX_CHARS` 예산(900,000)에서 글꼴 몫을 뺀다.
- 서버 캡처(리포트)도 같은 인라인 `@font-face`를 쓴다. `page.route("**/*", abort)`는 data: URI를 막지 않는다. 그러면 서버와 클라이언트 캡처의 글꼴이 같아진다.
- 굵기는 **400 / 600** 두 개만 쓴다. 폴백 Noto CJK KR에서는 600이 Bold로 그려진다.
- 대안 비교: Noto Sans KR/CJK(OFL, 이미 설치됨, 폴백으로 충분) [17]; IBM Plex Sans KR(OFL, 자간이 넓어 좁은 M/S에서 불리) [18]; Spoqa Han Sans Neo(OFL, 굵기 5종) [19]. 단일 글꼴 하나를 번들한다면 숫자 품질 때문에 Pretendard를 고른다.

**글자 크기** (캡처 논리 px → 슬라이드 pt, 배치 크기 그대로일 때):

| 역할 | px | 슬라이드 pt | 굵기 | 색 |
|---|---|---|---|---|
| 제목 | 30 | 22.5 | 600 (700 폴백) | `--ink` |
| 부제 | 20 | 15 | 400 | `--ink-2` |
| 직접 라벨·주석 | 18 | 13.5 | 400/600 | `--ink` / `--ink-2` |
| 눈금·축 이름·범례 | 16 | 12 | 400 | `--ink-2` |
| 출처 줄 | 14 | 10.5 | 400 | `--muted` |
| KPI 숫자 | 56 / 36 | 42 / 27 | 600 | `--ink` |

- **하한**: 읽어야 하는 글자는 16 px(12 pt) 이상이고, 출처 줄만 14 px다. Microsoft 접근성 가이드는 슬라이드 본문을 18 pt 이상으로 권한다 [2]. 차트 라벨은 본문이 아니라서 12 pt를 하한으로 둔다. 제목·주석은 13.5 pt 이상이다.
- 행간: 제목 1.3, 나머지 1.4. 자간 0. 대문자 강조와 이탤릭은 쓰지 않는다 [2].

### 4.3 선·간격

| 토큰 | 값 |
|---|---|
| 격자선 | 1 px `--grid`, y 방향만(막대/시계열). shmoo는 격자선 없음(칸 간격이 격자 역할) |
| 기준선 | 1 px `--axis` |
| shmoo 칸 간격 | 1.5 px 흰색(칸 크기와 무관) |
| 막대 간격 | 막대 폭의 ≥ 2 px 또는 10% |
| 강조 테두리(구멍) | 3 px `--accent`, 바깥으로 2 px, `rx=3` |
| spec 박스 | 2 px `--ink`, 대시 `6 4` |
| 동작점 | 반지름 7, 흰 채움 + 2.5 px `--ink` 테두리 |
| 예산 눈금 | 2.5 px `--ink`, 막대 위아래 5 px 돌출 |
| 리더선(주석) | 1.2 px `--ink` |
| 간격 단위 | 4의 배수: 4 / 8 / 12 / 16 / 24 / 32 |

## 5. 뷰별 사양

### 5.1 Shmoo: 단일 장치 (기본 L)

목업: `shmoo-single-L.png`, `slide-full.png`

```
┌──────────────────────────────────────────────────────────────────────┐
│ 1-3-2: 동작점(2.0 GHz, 1.20 V)이 실패 구멍 안 — 1.20–1.26 V × 1.9–2.1 GHz │  제목 30/600
│ VDD × 주파수 shmoo · 장치 1-3-2 (WAFER 1, DIE 3,2) · TEMP 85°C        │  부제 20
│ VDD (V)                                                                │
│ 1.28 ███████████████████████████████████░░░░  ■ Pass                 │
│ 1.24 ┆███░░░█████████████┆█████░░░░░░░░░░░░  ■ Fail                 │
│ 1.20 ┆███░○░█████████████┆████░░░░░░░░░░░░░  □ 실패 구멍(빨강 테두리)│
│ 1.16 ┆██████████████░░░░░┆░░░░░░░░░░░░░░░░░  ┆ Spec 영역             │
│ 1.12 └Spec──────────────-┘                    ○ 동작점                │
│ 1.00 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░                           │
│      1.6   1.8   2.0   2.2   2.4  …  3.2         주파수 (GHz) →       │
│ 출처: shmoo_lot7.csv · 판정 = RESULT · vanilaChatbot 2026-10-30         │  14
└──────────────────────────────────────────────────────────────────────┘
```

- **축**: x = 주파수(또는 주기), y = VDD. 위로 갈수록 전압이 높다. 업계에서 흔한 배치이고 지금 코드와도 같다 [20][21]. 축 이름은 컬럼 이름에서 단위를 뽑아 `VDD (V)`, `주파수 (GHz)`로 쓴다. 규칙: 끝이 `_V|_MV|_MHZ|_GHZ|_NS|_PS|_C`인 이름은 이름과 단위로 나눈다. MHz 값이 모두 ≥ 1000이면 GHz로 바꿔 소수 1–2자리로 쓴다. y 축 이름은 회전하지 않고 플롯 좌상단에 가로로 쓴다.
- **칸**: 플롯 영역을 칸 수로 나눠 채운다(고정 30×22 금지). 칸이 직사각형이 되어도 괜찮다. 칸 안에 글자는 쓰지 않는다.
- **표식**(데이터가 있을 때만):
  - 실패 구멍: `findings`의 `kind=="hole"` 범위를 빨강 3 px 테두리로 표시한다.
  - 천장(ceiling): 해당 y 행 위에 `--accent` 1.5 px 가로선과 라벨 "천장 1.30 V"를 그린다.
  - 벽(wall): 해당 x에 세로 점선과 라벨 "벽 2.4 GHz (보통 2.8)"을 그린다.
  - **Spec 박스**: 대시 사각형과 좌하단 라벨 `Spec`.
  - **동작점**: 원 하나.
  - Spec과 동작점은 지금 입력이 없다. `show_shmoo`에 선택 인자 `spec: {x:[lo,hi], y:[lo,hi]}`, `op: {x, y}`를 추가해 모델이나 사용자가 채운다. 없으면 그리지 않는다. spec 박스 안의 fail 칸 수를 세어 제목 후보에 쓴다.
- **범례**: 플롯 오른쪽 150 px 여백에 세로로 직접 라벨을 둔다: Pass / Fail / 실패 구멍 / Spec 영역 / 동작점. 실제로 그려진 항목만 넣는다. 미측정 칸이 있으면 "미측정(빈 칸)"을 추가한다.
- **제목 자동 생성**(결정적 규칙, 모델 호출 없음). 우선순위:
  1. 동작점이 fail이면 `"{장치}: 동작점({x}, {y})에서 Fail"`.
  2. 구멍이 있으면 `"{장치}: {y0}–{y1} V × {x0}–{x1} GHz에 실패 구멍 {n}칸"`.
  3. spec 안에 fail이 있으면 `"{장치}: Spec 영역 안 Fail {n}칸"`.
  4. 벽이 있으면 `"{장치}: 최고 전압에서도 {x} GHz까지 (다른 장치 중앙값 {m})"`.
  5. 그 밖에는 `"{장치}: {ymin} V 이상에서 {xmax} GHz까지 통과"`(최고 y 행의 경계).
  - 짧은 형태(M/S용)는 장치명과 핵심 수치만 남긴다.
  - 뷰 상단에 **제목 입력칸**을 두고 자동 제목으로 미리 채운다. 사용자가 고친 값이 캡처에 들어간다.
- **부제**: `VDD × 주파수 shmoo · 장치 {장치명(구성 컬럼)} · {조건 k=v}`. **출처**: `출처: {파일명} · 판정 = {result 컬럼}{(pass_values)} · vanilaChatbot {날짜}`.

### 5.2 Shmoo: 전체 장치 통과율 (기본 M)

목업: `shmoo-all-M.png`, `slide-halves.png` 왼쪽

```
┌────────────────────────────────┐
│ 24개 중 23개가 2.2 GHz까지      │
│ 1.10 V 이상에서 통과            │
│ 통과율 · 장치 24개 · TEMP 85°C  │
│ VDD (V)                         │
│ 1.28 ████████████▓▓▓▒·░░        │
│  …   ██████▓▓▓▒·░░░░░░░         │
│ 1.00 ▒░░░░░░░░░░░░░░░░░         │
│      1.6  2.0  2.4  2.8  3.2    │
│                  주파수 (GHz) → │
│ ■100% ■95–99% ■80–94% ■50–79% … │  구간 범례(아래)
│ 출처: … · 칸 색 = 그 조건 통과 장치 비율 │
└────────────────────────────────┘
```

- 색은 4.1의 구간 척도를 쓴다. 범례는 출처 줄 위에 한 줄(M에서 넘치면 두 줄)로 둔다.
- 칸 안 숫자(실패 장치 수)는 칸이 28×20 px 이상이고 1 ≤ 실패 수 < 전체일 때만 16 px로 쓴다. 그 밖에는 생략한다.
- 지금 보고 있는 조건에서 `low` 또는 `wall` finding이 있는 장치는 제목 대신 출처 줄 위 한 줄 주석으로 쓴다: "낮은 통과율: 1-5-2 (62%, 중앙값 88%)".
- 자동 제목: `"{N}개 중 {k}개가 {x} GHz까지 {y} V 이상에서 통과"`. 기준은 100% 칸으로 이루어진 직사각형의 오른쪽 위 모서리다. `k`는 그 직사각형을 모두 통과한 장치 수다.
- 권장 조합: 슬라이드 하나에 **M 전체(왼쪽) + M 문제 장치(오른쪽)**.

### 5.3 로그 뷰: 레벨별 시간 추이 (기본 L)

목업: `log-L.png`

```
┌─────────────────────────────────────────────────────────────────┐
│ 03:10–03:24, payment-api 타임아웃으로 ERROR 급증 (5분당 최대 92건)  │
│ order-svc 로그 · 2026-10-29 00:00–06:00 · 5,971건 · 5분 단위       │
│ 250 ■ERROR ■WARN ■INFO                     ░░░        5분당 건수  │
│ 200                                         ░██ ─ 03:10–03:24 장애 구간 │
│ 100 ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒░██▒▒  ERROR 265건 · …   │
│   0 ─────────────────────────────────────────────────────────── │
│     00:00    01:00    02:00    03:00    04:00    05:00    06:00   │
│ 출처: order-svc.log · 레벨 = 로그 레벨 필드 · vanilaChatbot …       │
└─────────────────────────────────────────────────────────────────┘
```

- **x 눈금**은 정시나 반올림한 시각에만 둔다(1/2/3/6/12시간, 10/15/30분 중 라벨 간격 ≥ 72 px를 만족하는 가장 작은 값). 첫 눈금에만 날짜를 붙인다.
- 쌓는 순서와 색은 4.1을 따른다(ERROR가 아래, INFO 회색이 위). 레벨 필터를 꺼서 INFO를 숨기는 것도 그대로 반영한다.
- **장애 구간 자동 검출**: ERROR+FATAL 버킷 값이 `중앙값 + 3×MAD` 이상이고, 그 버킷 중 최댓값의 20% 이상인 연속 구간을 찾는다. 사이에 0이 하나 끼어도 같은 구간으로 묶는다. 최대 2개 구간을 `--band`로 음영 처리하고, 구간 오른쪽에 2줄 주석을 단다:
  - 1줄(18/600): `{시작}–{끝} 장애 구간`
  - 2줄(18/400): `ERROR {n}건 · {구간 내 최다 패턴 요약 40자}`
  - 패턴 요약은 마스킹 토큰(`<n>`, `<ip>`)을 지우고 공백을 정리한 것이다. 주석이 오른쪽 끝을 넘으면 왼쪽에 둔다. 사건 타임라인은 시작·감지·완화·종료 시각을 시간축에 표시하는 것이 포스트모템 관례다 [22][23]. 여기서는 데이터로 알 수 있는 시작·끝만 그린다.
- **범례**: 플롯 안 좌상단 한 줄. y 단위 "5분당 건수"는 우상단에 둔다(축 이름을 회전하지 않는다).
- **패턴 선택 시**: 단색 `--accent`(ERROR) 또는 해당 레벨 색 막대만 그린다. 범례는 없애고, 부제를 `패턴: {요약}`으로 바꾼다.
- **자동 제목**:
  - 구간이 있으면 `"{시작}–{끝}, {패턴 핵심어}로 ERROR 급증 (5분당 최대 {m}건)"`. 핵심어는 패턴에서 마스킹 토큰과 서비스 접두사 `[...]`를 지운 앞 3–4 단어다.
  - 없으면 `"{기간} ERROR {n}건, 고르게 분포"`.

### 5.4 리포트 섹션

공통:
- **카드 테두리, 둥근 모서리, `#f5f5f4` 페이지 배경을 없앤다.** 섹션 = 흰 캔버스 + 안쪽 여백이다.
- 섹션 제목에서 `{리포트 제목} ·` 반복을 없앤다. 제목은 섹션별 결론, 부제는 `{기간} {카드} · {섹션 이름}`이다.

**요약 (S, 높이는 내용에 맞춤, 최대 540)**. 목업: `report-summary-S.png`

```
┌──────────────────────┐
│ 9월 지출 7% 증가      │
│ 2026년 9월 카드A      │
│ 총 지출               │
│ 160.5만 원            │  56/600
│ ▲ 10.5만 원 (+7.0%) 전월 대비 │  18/600 --over-text
│ ───────────────       │
│ 거래 수               │
│ 48건                  │  36/600
│ 출처: 카드A 9월 내역   │
└──────────────────────┘
```
- 금액은 슬라이드에서 `만 원` 단위로 반올림한다(소수 1자리). 원 단위 정확값이 필요하면 표 섹션을 쓴다.
- 증감은 ▲/▼ 기호와 부호를 함께 쓴다. 지출 증가는 `--over-text`, 감소는 `--ink-2`다(지출 감소를 초록 "좋음"으로 칠하지 않는다).

**카테고리별 지출 (M, L 가능)**. 목업: `report-categories-M.png`

- 가로 막대를 큰 순서로 그린다. **예산 초과 = `--over`, 나머지 = `--neutral-bar`**(강조 1색). 예산은 진한 세로 눈금이다.
- 값 라벨은 막대 끝(또는 예산 눈금 중 더 오른쪽) 뒤 10 px에 둔다. 막대가 230 px보다 길면 막대 안 흰 글자로 쓴다. 초과 항목은 `"{값}  +{초과액} 초과"`(600)로 쓴다.
- 범례 문장 대신 출처 줄 앞에 `| = 예산`을 쓴다.
- 자동 제목: 초과가 있으면 `"{카테고리들}이 예산 초과, 합계 {초과합} 넘음"`, 없으면 `"모든 카테고리 예산 이내, 최대는 {1위} {금액}"`. 카테고리는 최대 7개를 그리고 나머지는 "기타"로 접는다.

**큰 거래 (M/L)**
- 표 행 높이는 40 px, 글자는 18 px다. 금액은 오른쪽 정렬 + `tabular-nums`다. 헤더는 16 px `--ink-2`이고 아래에 1 px `--axis` 선 하나만 긋는다. 행 구분선은 `--grid`다.
- 슬라이드에는 **최대 7행**만 그리고, 넘으면 "외 n건"을 쓴다. 날짜는 `09-14` 형식이다.
- 제목: `"가장 큰 거래는 {가맹점} {금액}"`.

**메모**: 이미지 캡처 대상에서 뺀다. 섹션 바의 버튼을 `텍스트 복사`로 바꾼다(불릿 텍스트를 클립보드에 넣는다). 텍스트는 슬라이드에서 직접 편집할 수 있어야 한다.

## 6. 구현 메모 (우선순위 순, 이 문서에서는 구현하지 않음)

1. **`agent/views.py`**: 공통 "발표용 렌더" 모듈.
   - `CAPTURE_JS`가 화면의 `#plot svg`를 찍는 대신, 메시지 `{type:"capture", nonce, preset:"L|M|S", title:bool}`를 받아 페이지의 `renderSlide(preset, opts)`를 호출한다. 이 함수는 4장 토큰으로 **새 SVG**를 만들고(선택 테두리 등 UI 상태 제외) 캔버스 2x로 PNG를 만든다.
   - 공통 JS 헬퍼(`frame(title, sub, source)`, 눈금 솎기, 직접 라벨, 단위 파서)와 토큰 상수를 여기에 둔다. shmoo와 로그가 공유한다.
   - PNG에 `pHYs` 192 dpi 청크를 삽입한다(캔버스 `toBlob` 결과에 바이트 패치).
   - 직렬화한 SVG에 서브셋 글꼴 `@font-face`(data: URI)를 넣는다.
   - `captureName()`은 3.1의 파일명 규칙을 따른다.
2. **글꼴 번들**: `assets/fonts/`(Pretendard OTF + OFL)를 추가하고, `views.font_css(text)` 서브셋 함수(fontTools)를 만든다. `shmoo.render`, `logview.render`, `report.render`에서 호출한다. 의존성 추가는 별도로 승인받는다.
3. **`agent/shmoo.py`**:
   - `plot()`(화면용)과 `slidePlot(preset)`(캡처용)을 분리한다.
   - 색을 4.1 토큰으로 바꾸고(`rateColor` 연속 보간 → 6구간), 단위 파서, finding 표식(구멍/천장/벽), spec/op 입력, 자동 제목과 제목 입력칸을 추가한다.
   - `build()`에 `spec`, `op` 인자를 넣는다(+ `agent/tools.py`의 `show_shmoo` 스키마).
   - 화면용 `plot()`도 같은 토큰을 쓰게 해서 화면과 캡처의 색이 같게 한다.
4. **`agent/logview.py`**: `COLORS`를 4.1 레벨 표로 바꾸고, 쌓는 순서를 반대로 한다(심각한 레벨이 아래). 시각 눈금을 반올림하고, 장애 구간 검출과 주석, 자동 제목, `slidePlot(preset)`를 추가한다.
5. **`server/capture.py` + `agent/report.py`**:
   - `png(data, section, scale=2, preset=None)`에 프리셋별 viewport와 섹션 고정 폭을 적용한다(L 1180 / M 576 / S 376).
   - 리포트 CSS에서 카드 테두리, 반경, 회색 배경을 없애고, 섹션 제목을 결론형으로 바꾼다. `_bars()`의 색, 라벨 위치, "기타" 접기, 표 7행 제한, 만 원 반올림을 적용한다. 요약의 ▲/▼도 넣는다.
   - 리포트 스키마에 선택 필드 `headlines: {section: str}`를 추가한다(없으면 자동 제목).
   - `pHYs`는 Playwright 스크린샷 후 같은 패치 함수로 넣는다.
   - 메모 섹션은 `sections()`의 이미지 목록에서 빼고 텍스트 복사 API로 제공한다.
6. **`server/static/app.js` / `app.css`**: "현재 화면 복사"를 분할 버튼(`복사 ▾`: L/M/S, 제목 포함)으로 바꾸고 `preset`을 `frameCapture`와 섹션 PNG URL(`?preset=M`)에 넘긴다. 마지막 선택은 `localStorage`에 둔다(try/catch). 앱 CSS의 `IBM Plex Sans KR`(미설치)을 4.2 스택으로 바꾼다.
7. **검증**:
   - 프리셋마다 고정 데이터(지금 `design-input`과 같은 로그와 shmoo)로 PNG를 만들고 크기를 확인한다(L = 2360×1080 등).
   - 실제 PowerPoint(Windows 365, Mac)에 붙여넣기와 삽입을 해서 배치 크기와 글꼴을 확인한다.
   - 회색조 변환과 CVD 시뮬레이션(Chrome DevTools "Emulate vision deficiencies")으로 pass/fail, ERROR/WARN 구분을 확인한다.

## 7. 출처

1. Microsoft, "Change the size of your slides". 16:9 기본 13.333 × 7.5 in. https://support.microsoft.com/en-us/office/change-the-size-of-your-slides-040a811c-be43-40b9-8d04-0de5ed79987e
2. Microsoft, "Make your PowerPoint presentations accessible". 18 pt 이상, 산세리프, 대비 충분히, 대문자·이탤릭 남용 금지. https://support.microsoft.com/en-us/office/make-your-powerpoint-presentations-accessible-to-people-with-disabilities-6f7772b2-2f33-4bd2-8ca7-dae3b2b3ef25 · 96 dpi 기준 16:9 = 1280×720 px(내보내기 해상도 표): https://learn.microsoft.com/en-us/office/troubleshoot/powerpoint/change-export-slide-resolution
3. Edward Tufte, *The Visual Display of Quantitative Information* (2nd ed., Graphics Press, 2001). data-ink, chartjunk. https://www.edwardtufte.com/book/the-visual-display-of-quantitative-information/
4. Financial Times, Visual Vocabulary / Chart Doctor. https://github.com/Financial-Times/chart-doctor/tree/main/visual-vocabulary
5. The Economist, Chart style guide (v1.2, 2017). https://d30jyndnpz0ndz.cloudfront.net/documents/CHARTstyleguide_20170505.pdf
6. Datawrapper Academy / Blog. 축 라벨 생략과 직접 라벨: https://www.datawrapper.de/academy/why-datawrapper-does-not-include-axis-labels-for-many-charts · https://www.datawrapper.de/blog/improved-line-chart-editing · 색 사용: https://www.datawrapper.de/blog/colors
7. IBM Carbon Design System, Data visualization color palettes (alert 색, 순차·발산 팔레트). https://carbondesignsystem.com/data-visualization/color-palettes/
8. Publications Office of the EU, Data visualisation guide: labels and marks / legends. https://data.europa.eu/apps/data-visualisation-guide/labels-and-marks
9. Okabe & Ito, "Color Universal Design". https://jfly.uni-koeln.de/color/
10. ColorBrewer 2.0 (색맹 안전 순차·발산 척도). https://colorbrewer2.org
11. W3C, WCAG 2.1 Understanding 1.4.11 Non-text Contrast (3:1) / 1.4.3 Contrast (4.5:1). https://www.w3.org/WAI/WCAG21/Understanding/non-text-contrast.html · https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum.html
12. Microsoft, "Edit SVG images in Microsoft 365" (삽입·도형으로 변환). https://support.microsoft.com/en-us/topic/69f29d39-194a-4072-8c35-dbe5e7ea528c
13. Microsoft Q&A, "Convert SVG image to shape PowerPoint 2025/2026" (버전별 차이 보고). https://learn.microsoft.com/en-us/answers/questions/5693861/convert-svg-image-to-shape-powerpoint-2025-2026
14. Viridis/perceptual scales 소개 (단조 명도의 순차 척도). https://cran.r-project.org/web/packages/viridis/vignettes/intro-to-viridis.html
15. Pretendard (OFL 1.1, Inter + Source Han Sans 기반, 9 weight, 동적 서브셋). https://github.com/orioncactus/pretendard
16. MDN, "SVG as an image" (이미지로 로드한 SVG는 외부 리소스를 불러오지 않음). https://developer.mozilla.org/en-US/docs/Web/SVG/Guides/SVG_as_an_image
17. Noto CJK (OFL). https://github.com/notofonts/noto-cjk
18. IBM Plex (Sans KR 포함, OFL). https://github.com/IBM/plex
19. Spoqa Han Sans Neo (OFL). https://github.com/spoqa/spoqa-han-sans
20. Wikipedia, "Shmoo plot" (축 = 조절 변수, 사양을 만족하는 영역과 직사각형 동작 영역, 중심 동작점). https://en.wikipedia.org/wiki/Shmoo_plot
21. Design & Reuse, "Understanding shmoo plots and various terminology of testers". https://www.design-reuse.com/articles/47330/understanding-shmoo-plots-and-various-terminology-of-testers.html · Marvin Test Solutions, ICEasy Shmoo Plot Tool (pass/fail 색 표시 예). https://www.marvintest.com/KnowledgeBase/KBArticle.aspx?ID=244
22. Google SRE Book, "Postmortem Culture" / 예시 포스트모템의 타임라인. https://sre.google/sre-book/postmortem-culture/ · https://sre.google/sre-book/example-postmortem/
23. Atlassian, Incident postmortem template (타임라인: 감지·대응·복구). https://www.atlassian.com/incident-management/postmortem/templates
