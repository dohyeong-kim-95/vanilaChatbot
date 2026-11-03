# R7. GB 단위 로그 → 시각화 → Spotfire .dxp 벤치마크

조사일 2026-11-03. 공개 자료(아래 각 행의 URL)와 로컬 측정을 정리했습니다. 표기: **[검증]** 공식 문서·논문·패키지 메타데이터로 확인, **[로컬]** 이 머신(Linux, Python 3.13, `spotfire` 2.4.2)에서 직접 실행, **[2차]** 비공식 출처, **[미확인]** 추정이거나 이번에 다시 확인하지 못한 출처입니다. 실데이터는 기밀이므로 공개 데이터와 mock 생성기만 씁니다. 연결 시스템(Spotfire 등)은 mock입니다. 배포 대상은 Linux 서버입니다.

## 1. 요약

| 항목 | 결론 |
|---|---|
| 무엇을 봐야 하나 | 반도체 테스트·장비 로그 분석의 핵심은 bin 수율, 웨이퍼 맵, 저수율 웨이퍼의 fail test, 파라메트릭 drift, site 간 차이, 테스터 freeze, 알람-센서 시간 정렬입니다 (§2). |
| 공개 데이터 | 반도체 쪽 공개 데이터는 모두 작거나(SECOM, PHM, Eigenvector) 맵 위주(WM-811K)입니다. **공개 STDF 코퍼스와 SECS/GEM 로그는 찾지 못했습니다.** GB 규모는 Loghub(HDFS, Thunderbird)와 Bosch(14.3 GB)뿐이고 둘 다 반도체 테스트 로그가 아닙니다. 그래서 **seed 기반 mock 생성기가 필수**입니다 (§3). |
| .dxp | Spotfire 없이 .dxp를 만드는 지원 경로와 공개 스펙은 없습니다. **SBDF 생성은 오픈소스(BSD-3)로 Linux에서 가능하며 로컬에서 확인했습니다.** 따라서 ".dxp export"는 "SBDF + 사람이 만든 템플릿 .dxp + mock 배포 단계(Automation Services/REST)"로 정의하고 채점합니다 (§4). |
| 기존 벤치마크 | GB 로그 → 시각화 → BI 파일 export 전체를 다루는 공개 벤치마크는 찾지 못했습니다. OpenRCA·AIOpsLab(대용량 텔레메트리, 숨긴 정답), DA-Code·VisEval(차트를 데이터로 채점), Terminal-Bench(숨긴 pytest)를 조합합니다 (§5). |
| 현재 sandbox의 벽 | 쓰기 가능한 경로가 모두 tmpfs이고 1 GB 메모리 한도에 포함됩니다. bash 타임아웃은 300 s로 고정되어 있습니다. `logview.build()`는 전체를 메모리에 올립니다. **디스크 기반 job 볼륨과 백그라운드 ingest + 진행 표시가 선행 조건**입니다 (§7). |

## 2. 문제 유형과 분석가가 봐야 하는 것

| # | 문제 유형 | 봐야 하는 화면 | 출처 |
|---|---|---|---|
| P1 | lot/wafer별 hard/soft bin 수율, 웨이퍼 위치 패턴 | bin Pareto, 수율 추이, **웨이퍼 맵**(die X/Y를 bin으로 색칠) | [AWS](https://aws.amazon.com/blogs/industries/faster-post-silicon-analysis-with-an-aws-data-lake) [검증: hard-bin 웨이퍼 맵], [electronicdesign big data](https://www.electronicdesign.com/technologies/test-measurement/article/21205086/big-data-in-semiconductorshow-to-collect-detect-and-act) [미확인] |
| P2 | 저수율 웨이퍼의 fail test. 예: "yield below 80 percent" 웨이퍼에서 fail률 10% 초과 test | 웨이퍼 수율로 거른 test-fail Pareto, X/Y 매핑 | [AWS](https://aws.amazon.com/blogs/industries/faster-post-silicon-analysis-with-an-aws-data-lake) [검증] |
| P3 | 파라메트릭 drift/shift (테스터 정렬 문제) | lot/시간별 test 히스토그램·box plot, 추이, SPC 한계 | [semiengineering](https://semiengineering.com/using-smart-data-to-boost-semiconductor-reliability) [미확인] |
| P4 | 멀티 site 차이 (probe card·소켓 site별 마모) | HEAD/SITE별 수율·파라메트릭, 시간에 따른 delta | [semiengineering S2S](https://semiengineering.com/finally-realizing-the-full-benefits-of-parallel-site-to-site-s2s-testing), [SWTW 2009](https://www.swtest.org/swtw_library/2009proc/PDF/S03_02_Marcelis_SWTW2009.pdf), [SWTW 2007](https://www.swtest.org/swtw_library/2007proc/PDF/S09_02_Wegleitner_SWTW2007.pdf) [미확인] |
| P5 | 테스터(ATE) freeze: "the same or similar test results across several parts", ATE가 reset될 때까지 지속. 2,200개가 미검사로 나간 사례 | 동일값 run-length 탐지, 타임라인 강조 | [electronicdesign test escapes](https://www.electronicdesign.com/technologies/industrial/article/21205537/avoiding-test-escapes-when-is-good-really-good) [검증] |
| P6 | final test fail bin ↔ wafer sort 상관 | 공정 단계 간 join, bin-to-bin 행렬 | [electronicdesign ATE](https://www.electronicdesign.com/technologies/test-measurement/article/21204192/big-data-and-ate-drive-component-quality) [미확인] |
| P7 | golden wafer 상관, retest 회복률 | 기준 대비 die별 비교, retest bin 변화 표 | [SWTW 2009](https://www.swtest.org/swtw_library/2009proc/PDF/S03_02_Marcelis_SWTW2009.pdf) [미확인] |
| P8 | FDC 센서 trace: 고장 탐지, 고장까지 시간 | recipe step별 다중 센서 trace, fault 이벤트 overlay, run 요약 | [PHM 2018 spec](https://phmsociety.org/wp-content/uploads/2018/05/PHM-Data-Challenge-2018-vFinal-v2_0.pdf), [Eigenvector](https://eigenvector.com/resources/data-sets/metal-etch-data-for-fault-detection-evaluation/) [검증] |
| P9 | 알람·장비 상태 변화의 시간 상관 | 센서/수율과 정렬한 알람 타임라인, 상태 Gantt | [arXiv 1904.07686](https://arxiv.org/pdf/1904.07686) [미확인]. S5F1 = Alarm Report(ALCD bit 8 = set/clear), S6F11 = Event Report, 알람 set/clear 시 보통 둘 다 발생 [검증: [secsgem S5F1](https://secsgem.readthedocs.io/en/latest/_modules/secsgem/secs/functions/s05f01.html), [S6F11](https://secsgem.readthedocs.io/en/latest/_modules/secsgem/secs/functions/s06f11.html)] |
| P10 | 웨이퍼 맵 패턴 분류 (WM-811K 8종 + none) | 패턴별 맵 갤러리와 개수 | [arXiv 2505.03848](https://arxiv.org/pdf/2505.03848): Edge-Ring, Edge-Loc, Loc, Random, Near-full [검증], Center/Donut/Scratch [미확인] |

파서가 감당해야 할 STDF 사실:
- STDF는 1985년에 시작했습니다 ([AWS](https://aws.amazon.com/blogs/industries/faster-post-silicon-analysis-with-an-aws-data-lake)). 현행 V4(2007판) 스펙은 100쪽이 넘습니다 ([Wikipedia](https://en.wikipedia.org/wiki/Standard_Test_Data_Format)). **V4-2007은 V4의 확장**으로 scan/memory fail 레코드를 추가합니다 ([spec PDF](https://ftp.roos.com/roos/Documentation.nsf/ed56cc07c1101bdb85256a9d000203c4/92102f712ce51df48825783800832332/$FILE/STDF%20Spec%20V4%202007.pdf)). 원래 V4의 공개일은 [미확인]입니다. 파서는 두 판을 모두 받아야 합니다.
- 레코드는 계층으로 중첩되고 일부는 선택 사항이라 병렬 처리에 맞지 않습니다. wafer/part/test 표(CSV 등)로 평탄화합니다 ([AWS](https://aws.amazon.com/blogs/industries/faster-post-silicon-analysis-with-an-aws-data-lake)) [검증].
- shmoo plot은 이번 출처에 나오지 않았습니다 [미확인]. 기존 `agent/shmoo.py` 템플릿으로 대응은 가능합니다.

## 3. 공개 데이터 후보

| 데이터 | 크기 | 형식 | 라이선스 | URL | 쓸 수 있는 질문 | GB 규모 / mock 필요 |
|---|---|---|---|---|---|---|
| UCI SECOM | 1567행 × 591특성, 104 fail, 5.1 MB (zip 1.9 MB) | 공백 구분 텍스트, NaN, 라벨에 timestamp | CC BY 4.0 | [UCI](https://archive.ics.uci.edu/dataset/179/secom) | 수율에 영향을 준 센서, 시간에 따른 pass/fail (P3, P8) | 아님. 생성기 seed (행 복제 + 시간·lot 열 추가) |
| Bosch Production Line | 14.3 GB, 논문은 1,183,748 샘플(test set으로 표기). train 행 수 [미확인] | 넓고 희소한 CSV (숫자·범주·날짜) | **[미확인]** Kaggle 대회 규칙 미열람 | [Kaggle](https://www.kaggle.com/c/bosch-production-line-performance), [arXiv 1701.00705](https://arxiv.org/pdf/1701.00705) | fail 예측(MCC로 채점 [검증]), station/시간 경로, 진짜 GB ingest | **예.** 반도체 아님. 재배포 불명확하므로 사용자가 직접 다운로드, 번들 금지 |
| PHM 2018 ion mill etch | [미확인] | 장비별 CSV: time, Tool, stage, Lot("wafer id"), runnum, recipe, recipe_step, 센서·카운터 S8–S24(17개). fault 파일(time, fault_name, Tool). fault 3종: FlowCool Pressure Dropped Below Limit / Too High / leak | [미확인] | [spec PDF](https://phmsociety.org/wp-content/uploads/2018/05/PHM-Data-Challenge-2018-vFinal-v2_0.pdf), [Drive](https://drive.google.com/open?id=15Jx9Scq9FqpIGn8jbAQB_lcHSXvIoPzb), [NASA DASHlink](https://c3.ndc.nasa.gov/dashlink/resources/1009/) (생존 [미확인]) | lot/recipe/step + 시간 정렬 fault가 있는 가장 가까운 공개 FDC trace (P8, P9) | 중간. 장비 수·기간을 늘려 텍스트 로그로 내보내는 생성기 seed |
| PHM 2016 CMP | [미확인] | run별 CSV 25변수 + removal rate | [미확인] | [PHM 2016](https://www.phmsociety.org/events/conference/phm/16/data-challenge) [미확인] | trace 요약 vs 결과 (가상 계측) | 작음. seed |
| Eigenvector LAM 9600 Metal Etch | 129 웨이퍼(정상 108, fault 21: 실험 29/31/33). MACHINE 2.1 MB, OES 4.7 MB, RFM 1.9 MB | MATLAB .mat | 명시 없음, 인용 요청(Wise et al., J. Chemometrics 13, 1999) | [Eigenvector](https://eigenvector.com/resources/data-sets/metal-etch-data-for-fault-detection-evaluation/) | 다중 센서 fault 탐지 (P8) | 작음. seed |
| WM-811K (LSWMD.pkl) | 맵 811,457장, lot 약 46k(46,393 vs 46,293로 출처 불일치), 라벨 172,950(패턴 있음 25,519). 파일 크기 [미확인] | pandas pickle (열 이름 [미확인]) | **[미확인]** | [Kaggle](https://www.kaggle.com/datasets/qingyi/wm811k-wafer-map), [arXiv 2505.03848](https://arxiv.org/pdf/2505.03848), [CSDN](https://damodev.csdn.net/6a435cc210ee7a33f284940f.html) | 웨이퍼 맵, lot별 패턴 개수 (P1, P10). 합성 STDF의 die bin seed | 확장 가능: die → PRR(+PTR). 811k × 수백 die × N test면 GB 도달 |
| MixedWM38 | 38,015장, 52×52 (0 없음 / 1 pass / 2 fail), 38 조합을 8차원 multi-hot로 표기, 일부 시뮬레이션 | NumPy archive | 저장소에 라이선스 표시 없음 | [GitHub](https://github.com/Junliangwangdhu/WaferMap) | 혼합 패턴 맵 | seed |
| Loghub HDFS_v1 | 1.47 GiB, 11,175,629줄, 38.7 h, 라벨 있음 | 원시 텍스트 로그 | 라이선스 명칭 없음. "freely available for research or academic work" | [Loghub](https://github.com/logpai/loghub) | 스트리밍 파서·템플릿 마이닝·이상 타임라인 스트레스 테스트 | **예** |
| Loghub Thunderbird / BGL | 211,212,192줄 29.60 GiB / 4,747,963줄 708.76 MiB, 둘 다 라벨 있음 | 텍스트 | 위와 같음 | [Loghub](https://github.com/logpai/loghub) | GB 티어(Thunderbird), 소형 티어(BGL) | 예 / 아님 |
| STDF 샘플 | 공개 코퍼스 없음 (stdf4j는 테스트 파일을 proprietary로 삭제 [미확인]) | — | — | [stdf4j](https://gitee.com/qkmango/stdf4j) | — | **생성 필수** |

생성 도구:

| 도구 | 라이선스 | 용도 | 출처 |
|---|---|---|---|
| Semi-ATE-STDF | MIT [검증] | V4·V4-2007 읽기/쓰기, `to_atdf()`, gzip/lzma/bz2, 양 endian, NaN/Inf. **합성 STDF writer로 사용** | [GitHub](https://github.com/Semi-ATE/STDF) |
| pystdf | GPL-2.0 [검증] | 이벤트 기반 V4 전용 파서. 번들 시 GPL 주의 | [GitHub](https://github.com/cmars/pystdf) |
| rust-stdf | [미확인] | Python이 느릴 때의 대안 | [docs.rs](https://docs.rs/crate/rust-stdf/latest/source) |

mock이 필수인 영역: SECS/GEM 로그(공개 코퍼스·표준 줄 형식 없음, SEMI E5/E30 대비 현실성 [미확인]; S5F1+S6F11 쌍과 S5F2/S6F12 ack를 흉내냄, [secsgem](https://secsgem.readthedocs.io/en/latest/_modules/secsgem/secs/functions/s05f01.html)), burn-in(공개 데이터 없음), 반도체 장비 알람·상태 로그(공개 데이터 없음).

GB 도달 계획:

| 입력 | 출처 | GB 도달 방법 |
|---|---|---|
| STDF V4/V4-2007 + ATDF | mock (Semi-ATE-STDF, die bin은 WM-811K 맵에서 seed) | lot × wafer × die × 200–2000 PTR, 값 분포는 SECOM 유사 |
| 장비 텍스트 로그 (SECS/GEM 유사 + FDC trace) | mock (PHM 2018 스키마·fault 시각에서 seed) | 장비 수 × 수개월 × 1 Hz, S5F1/S6F11 줄 섞기 |
| 일반 GB 텍스트 로그 | Loghub (실데이터) | 이미 1.47–29.6 GiB |
| 넓은 GB 표 | Bosch (사용자 다운로드) | 이미 14.3 GB, 라이선스 확인 전 재배포 금지 |

주입할 지저분함(†는 실제 현상 근거 있음, 나머지는 제안): site/tester 섞인 스트림과 site별 열화 †, 테스터 freeze †(검증), 선택 레코드 누락·순서 변화 †(검증), NaN/Inf †, endian 혼합 †, V4/V4-2007 혼합 †, drift와 교정 후 step shift †, retest(같은 X/Y 다른 bin) †, 장비 재시작(runnum 0 리셋, 헤더 중복), 시계 점프(NTP 역행, KST/UTC 혼합, epoch/ISO 혼합), 마지막 레코드 잘림·REC_LEN 손상, 인코딩 혼합(UTF-8/CP949, BOM, CRLF/LF), 알람 폭주, gzip/lzma 조각 혼합.

## 4. Spotfire .dxp / SBDF: 가능한 경로와 한계

| 주장 | 상태 | 출처 |
|---|---|---|
| .dxp는 7-zip으로 열리는 ZIP 계열 archive, 대화는 `/Bookmarks` 아래 XML | [2차] 검색 스니펫만 (직접 접근 403) | [Revvity KB](https://support.revvitysignals.com/hc/en-us/articles/10158704840468) |
| 내부 항목 `AnalysisDocument.xml`, `EmbeddedResources.xml`, `Metadata.xml`, `Preview.png` 등 | **[미확인]** 저신뢰 출처 하나뿐 | [filext.com](https://filext.com/fr/extension-de-fichier/DXP) |
| 필수 항목이 없으면 "The file is not a valid DXP file" (`DxpDocumentFileReader.RequireEntry`). 필수 항목 이름은 미공개 | [검증] | [KB0080778](https://support.tibco.com/external/article/KB0080778/tibco-spotfire-analysis-dxp-fails-to-ope.html) |
| `AnalysisDocument.xml` 스키마·내장 표 저장 방식의 공개 스펙 | 찾지 못함 | — |
| 오픈소스 `spotfire-community` 0.5.2, `dxp-analyzer-doc` 0.2.0 (둘 다 MIT): Library API·DXP 분석용. 처음부터 .dxp를 만든다는 설명 없음 | [검증] PyPI 메타데이터, 전체 문서 미열람 | [spotfire-community](https://pypi.org/project/spotfire-community/), [dxp-analyzer-doc](https://pypi.org/project/dxp-analyzer-doc/) |
| Library export는 GUID 이름 파일로 감싸며 12.0+에서 이중 unzip 필요 | [미확인] | [Revvity KB](https://support.revvitysignals.com/hc/en-us/articles/23600188449172-How-to-extract-a-dxp-file-from-the-TIBCO-Spotfire-Library) |

공식 경로 (모두 어딘가에 라이선스 있는 Spotfire가 필요합니다):

| 경로 | 내용 | 상태 |
|---|---|---|
| Automation Services (Job Builder) | Open Analysis from Library → **Replace Data Table**(library의 SBDF 또는 prompted information link) → **Save Analysis to Library**. 로컬 디스크에 .dxp를 저장하는 task는 없음 | [검증] [Job Builder tasks](https://docs.tibco.com/pub/sfire-analyst/latest/doc/html/TIB_sfire_autsvcs_UserGuide/autoservices/topics/job_builder_tasks.html) |
| AS / Web Player on Linux | node manager + .NET, 14.5부터 prerequisites 스크립트. Kerberos, SAP BW/Essbase/SSAS, credential task 2종, WMF/EMF, disk cache 미지원. linked file은 `AllowedFilePath` alias 필요. 11.4에서는 Windows 전용, Linux 지원 첫 버전 [미확인] | [검증] [Linux 문서](https://docs.tibco.com/pub/spotfire_server/latest/doc/html/TIB_sfire_server_tsas_admin_help/server/topics/running_web_player_or_automation_services_on_linux.html), [11.4](https://docs.tibco.com/pub/spotfire_server/11.4.0/doc/html/TIB_sfire_server_tsas_admin_help/server/topics/node_manager_installation.html) |
| Library REST API v2 | upload → chunk → finish, scope `api.library.write`, 업로드 .dxp는 12.4 이상에서 저장된 것이어야 함. 기본 최대 2 GB(`public-api.library.upload.limit.max-upload-size`), 초과 시 413 | [검증] [library-v2](https://docs.tibco.com/pub/spotfire_server/latest/doc/api/TIB_sfire_server_REST_API_Reference/library-v2.html), [REST 설정](https://docs.tibco.com/pub/spotfire_server/latest/doc/html/TIB_sfire_server_tsas_admin_help/server/topics/configuring_spotfire_server_web_services_apis.html) |
| Analyst 안의 IronPython | 공식 예제에 `SbdfFileDataSource` + `AddColumns`는 있으나 SaveAs 예제는 없음. IronPython3는 14.1–14.8, 15.0 | [검증] [예제](https://docs.tibco.com/pub/sfire-analyst/12.0.5/doc/html/en-US/TIB_sfire-analyst_UsersGuide/text/text_ironpython_example_scripts.htm), [API](https://docs.tibco.com/pub/doc_remote/sfire_dev/area/doc/api/tib_sfire-analyst_api/html/P_Spotfire_Dxp_Application_Scripting_ScriptLanguage_IronPython3.htm) |
| Analyst 명령줄 | `/server`, `/file`, `/offline`, `/serverless`. Windows 전용, 변환 스위치 없음 | [2차] [Revvity](https://support.revvitysignals.com/hc/en-us/articles/45862854742036) |
| 서버 CLI `import-library-content` | `*.part0.zip`, KEEP_NEW/OLD/BOTH, Library Administrator 필요 | [검증] [10.10.1 문서](https://docs.tibco.com/pub/spotfire_server/10.10.1/doc/html/TIB_sfire_server_tsas_admin_help/server/topics/import-library-content.html) |

SBDF와 `spotfire` 패키지:

| 항목 | 값 | 상태 |
|---|---|---|
| 버전·라이선스 | 2.4.2 (2026-05-12), "BSD-type 3-Clause" | [검증] [PyPI JSON](https://pypi.org/pypi/spotfire/2.4.2/json), [GitHub](https://github.com/spotfiresoftware/spotfire-python) |
| 플랫폼 | manylinux2014 x86_64, win_amd64 (cp39–cp314). **aarch64 wheel 없음** | [검증] |
| Spotfire 설치 불필요 | 문서에는 없음, Linux에서 pip 설치·실행 성공 | [로컬] |
| API | `spotfire.sbdf.export_data(obj, file, ...)`, `import_data(file)` | [로컬] |
| 빈 열 | 전부 null인 열은 `SBDFError("cannot determine type ...")`. `spotfire.set_spotfire_types`로 해결 | [검증] 패키지 소스 `sbdf.pyx` |
| 1M행 × 6열 왕복 | export 2.2 s, import 0.48 s, 39.3 MB. 타입 유지(DateTime/String/Real/LongInteger/Boolean), NaN 유지. import 후 dtype은 바뀜(Int64, object) → 값으로 비교 | [로컬] |
| 잘린 SBDF | import 시 **인터프리터가 "double free or corruption"으로 죽음** (예외 아님) → 채점기는 subprocess + timeout으로 읽어야 함 | [로컬] |
| 청크 쓰기, polars 입력, 다 GB SBDF | 미지원으로 가정. 집계·창 단위로 export | [미확인] |

결론: Spotfire 없이 .dxp를 손으로 만드는 것은 비공개·버전 의존 형식의 역공학이라 권하지 않습니다. 현실적 경로는 (1) 스트리밍 집계 후 2 GB 미만 SBDF 여러 개, (2) 사람이 실제 Spotfire 12.4+에서 한 번 만든 템플릿 .dxp, (3) REST 업로드 + Automation Services job(Replace Data Table → Save Analysis to Library)을 **mock으로 기록만** 하는 것입니다. 템플릿 일부 수정 방식도 어떤 수정이 안전한지 문서가 없어 [미확인]입니다.

## 5. 참고 벤치마크와 채점 방식

| 벤치마크 | 채점 | 규모·성적 | 가져올 것 |
|---|---|---|---|
| [OpenRCA](https://github.com/microsoft/OpenRCA) | 장애마다 (시각, 컴포넌트, 원인) 3요소를 정답과 비교. 매칭 규칙 [미확인] | 335 장애, 68 GB+, 80 GB 디스크/32 GB RAM. 논문 최고 11.34%, 리더보드는 6.27%/15.82%로 보도됨([2차]) | GB 텔레메트리에서 답의 구조화, 코드로 데이터를 뽑는 agent |
| [AIOpsLab](https://arxiv.org/html/2501.06706v1) | 결정적: 탐지 yes/no, 위치 top-1/top-3 정확 일치, 시스템 상태로 완화 판정. LLM judge는 선택·정성 | 48 문제 | 주입한 fault = 숨긴 정답. "cat이 context window를 넘친다"는 지적이 우리 핵심 난제 |
| [DA-Code](https://arxiv.org/html/2410.07331) | 전부 스크립트, LLM judge 없음. 차트는 데이터와 지정 메타데이터를 추출해 모두 맞아야 1점. VLM은 "poor performance and high costs"로 기각 | 500 과제(시각화 70), 최고 30.5% | **차트는 픽셀이 아니라 추출한 데이터로 채점** |
| [VisEval](https://arxiv.org/html/2407.00981v2) | SVG를 분해해 차트 종류·데이터·정렬 검사, 시뮬레이션 브라우저로 overflow/overlap 검사, GPT-4V 1–5점. 파싱 불가 차트는 illegal | 2,524 질의, 146 DB | DOM/SVG 구조 검사 + headless 레이아웃 검사, 시각 모델 점수는 별도 |
| [InfiAgent-DABench](https://arxiv.org/html/2401.05507v3) | `@answer_name[value]` 슬롯 + 정확 일치. 단, 그 전에 GPT-3.5가 응답을 재포맷 | 257 질문, 52 CSV | 이름 있는 답 슬롯. 우리는 `answers.json`을 직접 쓰게 해 재포맷 LLM을 없앰 |
| [Terminal-Bench 2.0](https://arxiv.org/html/2601.11868v1) | Docker 최종 상태에 대한 결정적 pytest, 테스트는 실행 후 복사, CI가 테스트·정답 포함 이미지 거부, 적대적 exploit agent로 감사. 2.1에서 28개(tbench) 또는 26개([Harbor](https://hub.harborframework.com/datasets/terminal-bench/terminal-bench-2-1)) 과제 수정 | 89 과제, 최고 62.9% | 과제 레이아웃(task.yaml, Dockerfile, tests/, 타임아웃), 보상 해킹 대책 |
| [Spider 2.0](https://arxiv.org/abs/2411.07763) | 실행 결과 비교 | 632 과제, o1-preview 21.3% | DuckDB에 올린 뒤 쿼리 결과 비교 |
| [MLE-bench](https://arxiv.org/abs/2410.07095) | 과제별 grader가 제출 파일 채점, 사람 리더보드 기준 메달 | 75 대회 | 과제별 grader + 고정 출력 파일 |
| [Loghub](https://github.com/logpai/loghub) / [LogPub](https://github.com/logpai/LogPub) | 데이터셋. 템플릿 주석으로 파싱 채점 | Loghub 19개(README 합계 약 83 GiB, 공식 총량 없음). LogPub는 16 GB RAM·100 GB 디스크·48 h+ 명시 | 라벨 있는 GB 로그, 자원 요구를 미리 밝히는 방식 |
| [DSBench](https://arxiv.org/html/2409.07703v1) | 분석 답은 GPT-4o judge | 466 + 74, 분석 최고 34.12% | 반면교사: 우리는 데이터를 생성하므로 닫힌 답으로 만들 수 있음 |

공통 결론: 이 체인 전체를 채점하는 공개 벤치마크는 없고, **.dxp export를 채점하는 벤치마크는 찾지 못했습니다.**

## 6. 제안: 벤치마크 과제 설계

공통 규칙
- 데이터: seed 기반 mock 생성기가 로그와 `truth.json`을 만듭니다. `truth.json`과 checker는 **sandbox 밖**에 두고 실행 후에만 씁니다(Terminal-Bench 방식). 실행마다 seed를 바꿔 암기를 막습니다.
- 답: agent는 `answers.json`(이름 있는 슬롯), 차트 spec(Plotly/Vega-Lite JSON 또는 id 붙은 SVG), SBDF, 배포 메타데이터(job XML / REST 호출 기록)를 냅니다.
- 채점: A 사실(결정적, 주 게이트) + B 시각화(구조 검사, 결정적) + C export(SBDF 내용 + mock 배포). 시각 모델 가독성 점수는 별도 보고하고 통과 여부에 쓰지 않습니다.
- 자원: 컨테이너 메모리를 파일 크기보다 작게 둬서 통째 로드가 실패하게 합니다(우리 설계, 출처 없음). 토큰·step·시간·peak RSS를 점수와 함께 기록합니다(AIOpsLab 방식).
- 해킹 방지: 값은 agent 중간 파일이 아니라 원본 로그에서 다시 계산합니다. agent 로그에서 truth/test 파일 접근을 검사합니다.

| # | 과제 | 입력 (크기) | 숨긴 정답 | checker | 예산 |
|---|---|---|---|---|---|
| T1 | 수율 요약 + 웨이퍼 맵 (P1) | 합성 STDF V4, 깨끗함 (~100 MB) | lot별 hard/soft bin 수율, 최악 웨이퍼 id | 수율 ±0.1%p, 웨이퍼 id 정확 일치, 맵 spec의 die 수·bin 색 매핑 검사 | 메모리 1 GB, 10 min, 40 step |
| T2 | drift 시작점 + 나쁜 site (P3, P4) | STDF V4/V4-2007 혼합, endian 혼합, NaN/Inf, retest (~1 GB) | drift test 번호, change point(part index/시각), 나쁜 site | test·site top-1(부분점 top-3), change point 허용오차 ±N part, 차트에 change point 표시 여부 | 1 GB, 30 min, 60 step, 재개 필수 |
| T3 | 테스터 freeze 창 찾기 (P5) | 여러 테스터가 섞인 STDF + 잘린 마지막 레코드 (~2 GB) | freeze 구간(시작·끝 part), 영향 part 수 | 구간 IoU ≥ 0.5, 영향 수 ±5%, 손상 레코드 수 정확 보고 | 1 GB, 60 min, 중간 kill 1회 주입 후 재개 |
| T4 | 알람 → fault 선행 관계 (P8, P9) | PHM 2018 스키마 seed의 장비 텍스트 로그 + SECS/GEM 유사 알람, 시계 점프·KST/UTC 혼합·인코딩 혼합·알람 폭주 (~5 GB) | fault 시각, 선행 알람 id와 lead time, 이상 창 K개 | 창 F1(IoU ≥ 0.5), 선행 알람 top-3, lead time ±10%, 차트 점 수 ≤ 10k/trace, 모든 창 강조 | 1 GB, 120 min, 100 step |
| T5 | 종합 + Spotfire export (P1–P9) | T2–T4 혼합 + Loghub Thunderbird 일부(일반 로그 변형) | 위 정답 전부 + 기대 SBDF 스키마·행 수 | A·B 통과 + SBDF를 subprocess로 import(타임아웃), 열 이름·`spotfire_type`·행 수·집계값 비교, 각 SBDF < 2 GB, mock job XML의 task 이름·순서(Open → Replace Data Table → Save to Library), REST 순서 upload→chunk→finish·scope·413 처리. 템플릿 .dxp는 바이트 불변 확인만 | 1 GB, 180 min |

"Spotfire에서 열리는지"는 자동 검증이 불가능합니다. 실제 Spotfire에서 mock 데이터로 만든 기준 .dxp를 한 번 확보할 때까지 수동 확인 항목으로 남깁니다.

## 7. 제안: 추가할 tool / agent

현재 제약(레포 코드 기준): 컨테이너 `--read-only --memory 1g --cpus 1`, `/tmp` 256 MB tmpfs (`agent/sandbox.py:77-79`), agent에게 `/workspace`는 읽기 전용 (`agent/sandbox.py:82`), `/workspace/outputs`는 128 MB tmpfs이며 메모리 한도에 포함 (`agent/sandbox.py:17,83`), bash 타임아웃 기본 60 s·**최대 300 s 고정** (`agent/tools.py:47-49`), `logview.build()`는 전부 메모리에 올림 (`agent/logview.py:21-27`).

| # | 이름 | 입력 → 출력 | 실행 위치 | 이유 |
|---|---|---|---|---|
| 0 | (선행) job 볼륨 | — → 디스크 기반 쓰기 가능 `/workspace/jobs/{id}` bind mount | `sandbox.py` 수정 | tmpfs는 1 GB 메모리를 먹음. Parquet·DuckDB spill이 디스크로 가야 함 |
| 1 | `log_profile` | path → 크기, head/tail, 임의 offset 샘플 20개, 형식(CSV/kv/텍스트/STDF 바이너리) 추정, timestamp regex, 줄 수 추정, parse spec JSON | sandbox, 10 s 이내 | 전체 읽기 없이 계획을 세움 |
| 2 | `ingest_start` | path, spec → `job_id`. ~256 MB 바이트 범위(줄/레코드 경계)로 쪼개 파싱, Drain3 템플릿 id, `parts/*.parquet`(zstd), `manifest.json` 원자적 갱신, 분 단위 rollup·지표 envelope 생성 | sandbox 백그라운드(`docker exec` + `nohup`) | 300 s 상한과 1 GB 메모리를 넘기 위함. DuckDB `memory_limit` 명시(기본값이 cgroup을 따르는지 [미확인]) |
| 3 | `job_status` | job_id → 처리 바이트/전체, 행 수, ETA, 현재 청크, 마지막 오류 | 백엔드가 manifest 읽음, UI가 polling | **사용자가 요청한 진행 표시** |
| 4 | `ingest_resume` | job_id → 끝난 청크 건너뛰고 Drain3 snapshot 다시 로드 | sandbox | 타임아웃·재시작 시 최대 1청크 손실 |
| 5 | `stdf_flatten` | STDF 경로 → wafer/part/test Parquet (V4·V4-2007, 양 endian, 선택 레코드 누락 허용) | sandbox, ingest 안에서 호출 | AWS가 권한 평탄화. Semi-ATE-STDF(MIT) 기반, 속도 부족 시 rust-stdf [미확인] |
| 6 | `log_query` | SQL → 제한된 결과 (LIMIT 강제, 결과 바이트 상한) | sandbox, DuckDB over parquet + rollup | 원시 줄을 LLM에 넣지 않음. `string_agg`·정렬 기반 집계는 spill 불가라 원시 parts에서 피함 |
| 7 | 템플릿 `logview_big` (`agent/logview.py` 확장) | rollup + drill-down 쿼리 결과 → 개요(스택 막대, 템플릿 표, min/max envelope), drill-down은 `tsdownsample` MinMaxLTTB(~2,000점, 인덱스 반환), 밀집 산점은 datashader PNG | 백엔드 렌더 → sandbox iframe | 고정 템플릿이라 숫자만 전달. 웨이퍼 맵·shmoo는 기존 템플릿 재사용 |
| 8 | `export_sbdf` | 쿼리 또는 "rollups" → `.sbdf` (빈 열은 `set_spotfire_types`), 행 상한 초과 시 거부하고 집계 제안 | sandbox (x86_64) | SBDF는 전체 df를 메모리에 둠. 2 GB 미만 유지 |
| 9 | `export_dxp` (mock) | SBDF 목록 + 템플릿 id → 번들(SBDF + 템플릿 .dxp + Automation Services job XML + REST 호출 기록) | 백엔드, 외부 호출 없음 | 실제 .dxp 생성은 Spotfire 필요. 나중에 Linux node manager(14.5+)로 교체 가능 |
| 10 | agent `log_analyst` (skill/서브 agent) | 사용자 질문 + 파일 → profile → spec 확인 → ingest → 진행 보고 → 개요 → 순위 매긴 발견(이상 창, 상위 템플릿, 지표 이탈) → drill-down → export | 기존 agent loop | 큰 계획을 단계로 나누고 각 단계를 300 s 안에 끝냄 |

## 8. 미확인 / 위험

| 항목 | 영향 | 대응 |
|---|---|---|
| .dxp 내부 스키마, 외부 생성 .dxp가 열리는지 | export 채점 범위 | SBDF + mock 배포로 채점, 기준 .dxp 1개 확보 전까지 수동 |
| Bosch·WM-811K·PHM·Eigenvector·MixedWM38 라이선스, Loghub의 연구 외 사용 조건 | 번들·재배포 불가 가능성 | 사용자 직접 다운로드, 레포에는 생성기만 |
| PHM 2016/2018·WM-811K 파일 크기, Bosch train 행 수, WM-811K lot 수(46,393 vs 46,293) | 수치 표기 | 대략값으로 표기 |
| NASA DASHlink, MIR lab 링크 생존 | 다운로드 경로 | 대체 경로 확인 필요 |
| STDF V4 최초 공개일, WM-811K Center/Donut/Scratch 클래스명(해당 논문 기준) | 문서 정확성 | 다른 출처로 재확인 |
| semiengineering·SWTW·electronicdesign(big data/ATE)·arXiv 1904.07686 (P1, P3, P4, P6, P7, P9) | 문제 유형 근거 | 이번 패스에서 다시 열지 않음 |
| SECS/GEM mock의 SEMI E5/E30 대비 현실성, burn-in 형식 | 벤치마크 현실성 | 도메인 담당자 검토 |
| DuckDB 기본 메모리가 cgroup을 따르는지, Polars `.csv.gz` 스트리밍 여부, Drain3 1 CPU 처리량, SBDF 청크 쓰기 | OOM·시간 초과 | `memory_limit` 명시, 1 GB 컨테이너에서 실측 |
| `spotfire` aarch64 wheel 없음 | ARM 서버면 빌드 필요 | x86_64 Linux 서버 전제 |
| 잘린 SBDF 읽기 시 프로세스 crash | checker 자체가 죽음 | subprocess + timeout |
| OpenRCA 매칭 규칙·점수 차이(11.34% vs 6.27%), ITBench F1 0.35, OpsEval FAE-Score, MLE-bench 24 h·로그 검사 | 참고 수치 | 인용 시 [미확인] 유지 |
| Automation Services Linux 지원 첫 버전과 .NET 버전 | 실제 연동 시점 | mock 유지 |
