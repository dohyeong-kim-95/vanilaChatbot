# R3 — 리서치 노트 (Loop 7–10)

조사일 2026-10-26. 각 항목 끝에 이 레포에서 검증한 내용을 적었다.

## 1. Durable run: 재접속과 중단/재개 (Loop 7)

| 출처 | 패턴 |
|---|---|
| [Agno background execution](https://docs.agno.com/background-execution/overview), [SSE reconnect 예제](https://docs.agno.com/examples/agent-os/run-lifecycle/sse-reconnect) | `background=True, stream=True`면 run을 분리된 task로 실행하고 이벤트에 순번을 매겨 버퍼링. `/resume`에 마지막 index를 주면 놓친 이벤트를 보내고 live로 이어감. **DB 필수**, 멀티 레플리카는 Redis Streams. 확인 tool에서 멈춘 뒤 백그라운드에서 이어 실행하는 HITL 예제도 있음 |
| [Letta long-running](https://docs.letta.com/guides/agents/long-running) | 서버가 chunk를 저장하고 `starting_after`(seq)로 재개. 활성 run 목록 API로 재시작한 앱이 다시 붙음 |
| [TanStack AI resumable streams](https://tanstack.com/ai/latest/docs/resumable-streams/custom-adapter) | durability adapter 인터페이스: append / read-after-offset / close / snapshot. offset은 불투명 문자열 → Redis·Postgres 무엇이든 |
| [SSE lifecycle 가이드](https://fast.io/resources/fastio-sse-connection-lifecycle-management.md) | `Last-Event-ID` 헤더로 재전송. 같은 이벤트가 두 번 올 수 있으니 handler는 멱등이어야 함 |

정리: **run 상태와 이벤트 로그는 저장소에 있고, HTTP 연결은 그것을 tail할 뿐**이라는 점에서 업계 구현이 모두 같다. 차이는 저장소(메모리/Redis/Postgres) 선택뿐이다.
→ 검증: SQLite events(seq) + `Last-Event-ID`, 승인을 interrupt로 처리, 서버 재시작 복구 (LOOP_LOG Loop 7).

## 2. Sandbox egress allowlist (Loop 8)

- 구조: sandbox에서는 네트워크를 제거하거나 내부망만 두고, **sandbox 밖의 프록시가 유일한 출구**가 되게 한다. Linux에서는 sandbox의 네트워크 namespace를 없앤다. ([정리 글](https://jiangren.com.au/en/learn/ai-engineer/claude-code-sandboxing), [allowlist 가이드](https://startdebugging.net/2026/07/how-to-lock-down-a-coding-agents-network-egress-with-a-strict-host-allowlist/))
- 기본값: 미리 허용한 도메인 없이 처음 접근할 때 사용자에게 묻는 방식.
- 알려진 약점
  - SOCKS5 hostname에 NUL 바이트를 넣어 allowlist를 우회한 취약점이 있었다(v2.0.24–2.1.89, sandbox-runtime에서 수정). → **hostname은 엄격한 정규식으로 검증한다.**
  - TLS를 복호화하지 않으면 client가 보낸 hostname(CONNECT/SNI)을 믿을 수밖에 없다 → 큰 CDN 도메인을 허용하면 domain fronting에 취약. → allowlist를 좁게 유지하고, 필요하면 TLS를 종단하는 프록시를 둔다.
  - ([PromptArmor: 어떤 도메인을 허용해야 하나](https://www.promptarmor.com/resources/what-domains-should-i-add-to-my-allowlist): 파일을 업로드할 수 있는 도메인(예: 패키지 레지스트리 업로드, paste 사이트)은 exfiltration 경로가 된다)
- → 검증: `--internal` Docker 네트워크 + CONNECT 전용 allowlist 프록시, 우회 시도 7종 차단, 도메인 추가는 승인 tool로 (LOOP_LOG Loop 8).

## 3. MCP (Loop 9)

- 2026-07-28 스펙: **stateless**. initialize handshake와 프로토콜 세션(`Mcp-Session-Id`)이 없어지고 `server/discover`가 그 자리를 대신함. sticky routing 없이 round-robin LB 가능. 서버가 시작하는 요청(elicitation/sampling)은 MRTR(multi-round-trip request)로 바뀜. `Mcp-Method` 헤더로 라우팅. OAuth/OIDC 정렬, MCP Apps·Tasks extension, 공식 deprecation 정책. ([공식 RC 글](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/), [Appwrite 요약](https://appwrite.io/blog/post/mcp-goes-stateless-in-the-2026-07-28-specification), [C# SDK v2](https://devblogs.microsoft.com/dotnet/announcing-v20-of-the-official-mcp-csharp-sdk/))
  - Python SDK 2.3(`mcp`)의 `Client(mode="auto")`는 discover를 먼저 시도하고, 안 되면 initialize로 fallback → 구/신 서버 모두 연결 가능.
- tools: `tools/list`(페이지네이션, `listChanged`), 결과는 `content`(비구조) + `structuredContent`(outputSchema가 있으면 필수, text로도 미러링 권장) + `isError`. ([structured content 서베이](https://olaservo-sandyland.static.hf.space/mcp/structured-content/server-survey.md))
- annotations: `readOnlyHint`(기본 false), `destructiveHint`(기본 true), `idempotentHint`(기본 false), `openWorldHint`(기본 true). **힌트일 뿐이므로 신뢰할 수 없는 서버의 annotation을 근거로 승인을 생략하면 안 된다**(스펙 문구). ([annotation 표](https://docs.praison.ai/docs/mcp/mcp-tool-annotations))
- tool poisoning / rug pull: tool description은 모델 프롬프트로 들어간다 → 검토한 description의 hash를 고정(pin)하고 바뀌면 등록하지 않는다.
- → 검증: stdio MCP 서버 연결, annotation 기반 승인(신뢰 서버만), pin으로 rug pull 차단, 오염된 description에 대한 모델 반응 측정 (LOOP_LOG Loop 9).

## 4. 평가 지표 (Loop 10)
- pass@k(k번 중 한 번이라도 성공)와 pass^k(k번 모두 성공). agent 신뢰성은 **pass^k**로 본다(τ-bench). 실제로 쓸 때 중요한 것은 "가끔 성공"이 아니라 "매번 성공"이기 때문. (R2 §7 참조)
- → 검증: 4 task × k=3 미니 suite (LOOP_LOG Loop 10).
