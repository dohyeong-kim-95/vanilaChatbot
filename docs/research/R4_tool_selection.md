# R4 — tool이 많을 때(MCP 다수 연결) tool 선택 방식 리서치

조사일 2026-10-26. 실험 기록은 [LOOP_LOG Loop 11](../LOOP_LOG.md#loop-11--tool이-많을-때-tool-선택), 코드는 `tests/experiments/tool_selection.py`.

## 1. 문제가 생기는 이유
- **컨텍스트 비용**: tool 정의는 매 요청 prefix에 들어간다. Anthropic 사례: MCP 서버 5개·tool 58개 = 약 55k 토큰, 최대 134k까지 관측. ([Anthropic, Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use))
- **잘못된 선택**: 가장 흔한 실패는 이름이 비슷한 tool 사이의 오선택과 잘못된 인자. (같은 글, 예: `notification-send-user` vs `notification-send-channel`)
- **tool 수가 늘면 정확도가 떨어진다**: RAG-MCP 스트레스 테스트(정답 1개 + 방해 N−1개)에서 전부 넣었을 때 13.6% → 검색으로 1개만 넣었을 때 43.1%. ([RAG-MCP, arXiv 2505.03275](https://arxiv.org/html/2505.03275v1)) 작은 모델일수록 심하다. ([Less is More, arXiv 2411.15399](https://arxiv.org/pdf/2411.15399))

## 2. 접근법 분류

| 방식 | 핵심 | 대표 | 장점 | 약점 |
|---|---|---|---|---|
| **전부 노출** | 모든 tool을 `tools`에 | 기본 | 1 round-trip, 검색 실패 없음 | 토큰 비용·지연, 큰 카탈로그에서 정확도 저하 |
| **사전 검색(retrieval)** | 사용자 메시지로 top-k를 고른 뒤 그것만 노출 | RAG-MCP, Less-is-More | 토큰 대폭 감소, 1 round-trip | **검색이 놓치면 모델은 정답 tool을 볼 수조차 없다**(recall 의존) |
| **모델 주도 검색(tool search)** | `search_tools` meta-tool로 모델이 직접 쿼리 → 찾은 tool을 로드 | Anthropic Tool Search(`defer_loading`, regex/BM25/custom), OpenAI `tool_search`(hosted/client-executed, gpt-5.4+) | 모델이 쿼리를 다시 쓴다(언어·동의어), 토큰 85%↓, 정확도 ↑(Opus 4: 49→74%) | 추가 round-trip, 자주 쓰는 tool까지 검색하면 손해 → **상위 3–5개는 항상 로드** 권장 |
| **계층형(server → tool)** | 서버 목록을 보고 서버를 고른 뒤 그 서버의 tool 로드 | MCP-Zero(308 서버/2,797 tool, 토큰 98%↓), Tool-to-Agent Retrieval | 서버 단위 요약이 짧다 | 서버 선택을 먼저 확정해 버려 세밀한 매칭을 놓칠 수 있다(arXiv 2511.01854) |
| **집합 단위 검색** | tool을 하나씩이 아니라 조합으로 평가 | Tools Are Not Islands(arXiv 2607.25718) | 여러 tool이 필요한 작업 | 연구 단계 |
| **기술 문서 품질** | 명확한 이름·설명, `input_examples` | Anthropic, OpenAI 가이드 | 복잡한 인자 정확도 72→90% | 수작업 |
| **Programmatic tool calling** | 모델이 코드로 tool을 조합해 중간 결과를 컨텍스트 밖에 둔다 | Anthropic PTC | 토큰 37%↓ | 코드 실행 환경 필요 |

## 3. OpenAI 호환 API에서의 제약 (이 프로젝트의 전제)
- Anthropic `defer_loading`/`tool_reference`와 OpenAI hosted `tool_search`는 **provider 전용 기능**이라 Chat Completions 호환 계층에서는 쓸 수 없다. → client 측(우리 서버)에서 구현해야 한다.
- client 측 구현 방법은 두 가지:
  1. **search_load**: 찾은 tool을 다음 요청의 `tools`에 추가. 모델이 바로 네이티브 호출. 단점: `tools`는 prompt prefix의 맨 앞이라 **바뀔 때마다 그 뒤 캐시가 전부 깨진다**.
  2. **search_call**: `tools`는 `[search_tools, call_tool]`로 고정. 검색 결과로 스키마를 **메시지 본문**에 돌려주고, 모델은 `call_tool(name, arguments)`로 호출. 서버가 이름과 인자를 검증한다. prefix가 안정적이고 history는 append-only. 단점: 인자 스키마를 provider가 강제하지 못함(→ 서버 검증 필수).
- 운영 시: 검색 대상은 **사용자가 권한을 가진 tool로만**(OpenAI 문서), 검색 결과도 tool description이므로 pinning(§SPEC 4.3)한 것만.

## 4. 검색 품질 이슈
- BM25는 사용자 언어와 tool 설명 언어가 다르면 무너진다(한국어 질의 ↔ 영어 description). 해결책: (a) 모델이 영어로 검색 쿼리를 쓰게 한다(tool search), (b) 다국어 임베딩, (c) description에 다국어 별칭 추가.
- 검색 실패는 조용히 일어난다: 정답이 후보에 없으면 모델은 "그럴듯한 다른 tool"을 고르거나 포기한다. 그래서 **fallback 경로(검색 tool)를 항상 남겨 둔다**.
