# R2. 웹 리서치 노트 (2차) — Agentic Chatbot 기술 스펙용

- 작성일: 2026-10-26
- 범위: R1에서 다루지 않은 7개 주제. 이벤트 프로토콜, HITL 승인, context 관리, 세션/런 영속화, 실용적 prompt injection 방어, prompt caching, 평가
- 표기: 원문(공식 문서·소스)을 직접 확인한 항목은 따로 표시하지 않음. **[2차]** = 검색 요약·서드파티 블로그로만 확인한 항목(교차검증 필요)
- 조사 환경 egress 제한으로 docs.ag-ui.com, ai-sdk.dev, platform.openai.com, arxiv.org 등은 직접 열지 못함 → GitHub raw 소스의 문서 원본(mdx)으로 대체 확인, 나머지는 [2차]
- R1 중복 회피: tool-calling wire format, sandbox 격리, GitHub App token, MCP, 기본 RAG, permission mode 목록, lethal trifecta/Rule of Two는 R1 참조

---

## TL;DR — 설계를 바꿀 만한 발견

1. **이벤트 스키마는 AG-UI 모양을 따르는 자체 스키마 + SSE + 모든 이벤트에 단조증가 `seq`**. 승인 요청은 "스트림 안의 질문"이 아니라 **run 종료(outcome=interrupt) → 새 run으로 resume** 모델이 AG-UI·OpenAI Agents SDK·LangGraph 공통 패턴.
2. **Run은 HTTP 요청과 분리**: worker가 이벤트를 durable log(DB/Redis Stream)에 쓰고, SSE 엔드포인트는 그 log를 `Last-Event-ID`부터 tail. Vercel AI SDK도 resumable stream에서 "client abort = disconnect, 중단은 별도 stop endpoint"로 정리함.
3. **승인 대기 = 상태 직렬화 후 프로세스 종료**. resume 시 tool call을 다시 만들지 말고 원래 `tool_call_id`로 결과만 이어붙임. LangGraph는 interrupt 노드를 **처음부터 재실행**하므로 interrupt 앞 side effect는 idempotent해야 함.
4. **Compaction은 2단계(오래된 tool result clearing → LLM handoff 요약) + 요약 뒤 "재주입"**: Claude Code는 compaction 후 system prompt·CLAUDE.md·plan·최근 수정 파일 5개를 디스크에서 다시 넣음. 요약에 모든 걸 맡기지 말고 **task/plan/파일목록은 구조화 상태로 별도 보관**.
5. **Prompt caching이 비용의 1순위 변수**: OpenAI(≥1024 tok 자동), DeepSeek(디스크 자동), vLLM APC 모두 **정확한 prefix 일치**만 적중. system prompt에 timestamp 금지, tool 목록 고정·정렬, history는 append-only, JSON key 순서 결정적. 동적 tool 제거는 cache를 깨므로 **"정의는 유지, 실행 단계에서 거부"**.
6. **멀티테넌트 vLLM은 `cache_salt`로 tenant별 prefix cache 격리**(timing side-channel 방지). OpenAI는 `prompt_cache_key`, OpenRouter는 `session_id`로 sticky routing.
7. **Token counting**: 모델별 tokenizer를 모르면 직전 응답의 `usage.prompt_tokens`를 기준값으로 쓰고, 증분만 휴리스틱으로 추정. vLLM은 `/tokenize`(chat template 적용)로 정확히 셀 수 있음.
8. **Sandbox는 일회용, 진실의 원천은 대화 로그 + git branch**: Claude Code on the web도 idle 후 VM 회수, 재오픈 시 **대화만 복원**하고 실행 중이던 명령은 복원하지 않음. pause/snapshot(E2B·Modal·Daytona)은 최적화 수단일 뿐.
9. **Prompt injection MVP 방어 = 구조적 통제**: spotlighting(datamarking/encoding)은 저렴한 완화책, CaMeL/plan-then-execute는 강력하지만 MVP엔 과함. **task별 tool allowlist + egress allowlist + 외부 이미지 CSP 차단 + 승인 게이트**가 실질적 방어선.
10. **평가: SWE-bench Verified는 OpenAI가 2026-02에 "오염"을 이유로 보고 중단**. Harbor(Terminal-Bench 2.0 공식 harness)로 TB2·SWE-bench류를 우리 agent에 직접 돌리고, **자체 task suite(검증 스크립트 기반)를 CI에** 넣는 것을 권장. τ²-bench는 한국어 pack이 있어 대화형 tool-use 평가에 쓸 만함.

---

## 1. Frontend ↔ agent backend 이벤트 프로토콜

### 1.1 AG-UI (CopilotKit 주도)
- 모든 이벤트 공통: `type`, `timestamp?`, `metadata?`(token usage·trace id를 마지막 이벤트에 실어 보내는 용도), `subagentRunId?`. https://github.com/ag-ui-protocol/ag-ui/blob/main/docs/concepts/events.mdx
- Lifecycle: `RUN_STARTED{threadId, runId, parentRunId?, input?}` → (`STEP_STARTED/FINISHED`)* → `RUN_FINISHED{outcome?, result?}` | `RUN_ERROR{message, code?}`. 시작·종료 이벤트는 필수.
- Text: `TEXT_MESSAGE_START{messageId, role}` / `_CONTENT{delta}` / `_END` (+ 합본 `_CHUNK`). Tool: `TOOL_CALL_START{toolCallId, toolCallName, parentMessageId?}` / `_ARGS{delta}` / `_END` / `TOOL_CALL_RESULT{messageId, toolCallId, content}`. Reasoning, `STATE_SNAPSHOT/DELTA`(JSON Patch), `MESSAGES_SNAPSHOT`, `ACTIVITY_*`, `CUSTOM`, `RAW`도 있음.
- 패턴 3종: Start-Content-End(스트리밍), Snapshot-Delta(상태 동기화), Lifecycle. 같은 id 이벤트는 하나의 논리 스트림, 순서 뒤바뀜에 견고해야 함.
- **Interrupt(초안 단계)**: `RUN_FINISHED.outcome = {type:"interrupt", interrupts:[{id, reason, message?, toolCallId?, responseSchema?, expiresAt?}]}`. 클라이언트는 **새 run**을 시작하며 `RunAgentInput.resume=[{interruptId, status, payload?}]`로 열린 interrupt를 **모두** 답해야 함(부분 resume 불가). `reason` 핵심값: `tool_call`, `input_required`, `confirmation`. 재개된 run은 `TOOL_CALL_START/ARGS`를 다시 보내지 않고 원래 `toolCallId`로 `TOOL_CALL_RESULT`만 보냄. Approve-with-edits는 `{approved, editedArgs}`(전체 교체, merge 아님). 만료/스키마 불일치/누락은 `RUN_ERROR`. https://github.com/ag-ui-protocol/ag-ui/blob/main/docs/concepts/interrupts.mdx
- Transport 무관(SSE/WS/HTTP 모두 가능)한 이벤트 규약.

### 1.2 Vercel AI SDK UI message stream (data stream protocol v1)
- SSE(`data: {json}\n\n`) + 헤더 `x-vercel-ai-ui-message-stream: v1`, 종료는 `data: [DONE]`. 근거: "SSE for standardization, keep-alive through ping, reconnect capabilities". https://github.com/vercel/ai/blob/main/content/docs/04-ai-sdk-ui/50-stream-protocol.mdx
- Part 종류: `start`, `text-start/delta/end`, `reasoning-*`, `source-url/document`, `file`, `data-<name>`(사용자 정의), `error`, `tool-input-start/delta/available`, **`tool-approval-request{toolCallId, approvalId, reason?}`**, `tool-approval-response{approvalId, approved, reason?}`, `tool-output-available`, **`tool-output-denied`**, `start-step`/`finish-step`, **`reset-step`**(재시도 시 해당 step 부분출력 무효화), `finish`, `abort{reason}`.
- Resumable stream: POST로 생성, `GET /api/chat/[id]/stream`으로 재접속, `resumable-stream` 패키지 + Redis에 스트림 저장. 기본은 **처음부터 replay**(`resumeStreamIsReplay`). **client abort는 disconnect로 취급 → 사용자 중단은 별도 stop endpoint 필요**. https://github.com/vercel/ai/blob/main/content/docs/04-ai-sdk-ui/03-chatbot-resume-streams.mdx

### 1.3 OpenAI Agents SDK streaming
- `Runner.run_streamed()` → `stream_events()`: `raw_response_event`(Responses API 토큰 델타), `run_item_stream_event`(name: `message_output_created`, `tool_called`, `tool_output`, `handoff_requested`, `mcp_approval_requested` 등), `agent_updated_stream_event`. **iterator가 끝나야 run 완료**(마지막 토큰 뒤에도 세션 저장·승인 bookkeeping·compaction이 돌 수 있음). 승인이 필요하면 stream이 끝나고 `result.interruptions`에 남음. https://github.com/openai/openai-agents-python/blob/main/docs/streaming.md
- 시사점: "토큰 레벨"과 "item 레벨" 두 층을 모두 노출하는 것이 UI 구현에 편함.

### 1.4 SSE vs WebSocket, 재접속
- SSE: 단방향, 평범한 HTTP라 LB/프록시와 호환, `id:` + `Last-Event-ID`로 재개 내장(단, 서버가 id를 붙이고 존중해야 함). WS: 양방향이지만 재접속·누락 보정을 앱이 직접 구현. 권장: **SSE로 이벤트 하향 + 별도 POST로 cancel/approve/메시지 상향**. [2차] https://tianpan.co/blog/2026-04-10-streaming-real-time-agent-uis-sse-backpressure-reconnection , https://websocket.org/comparisons/sse/
- 운영 함정: 프록시 버퍼링(nginx `X-Accel-Buffering: no`), LB idle timeout(30~60s) → 15s 간격 `: ping` 코멘트 heartbeat, HTTP/1.1에서 도메인당 ~6 연결 제한(HTTP/2면 완화). [2차] 동 출처
- LLM 특유 비용: 재접속 시 LLM을 다시 호출하면 이미 낸 토큰을 또 냄 → **생성은 worker에서 계속, 클라이언트는 log만 재구독**.

### 1.5 권장 이벤트 스키마 (AG-UI 정렬 + seq)
```jsonc
// 공통 envelope — SSE: "id: <seq>\nevent: <type>\ndata: <json>\n\n"
{ "seq": 1042, "runId": "run_..", "threadId": "th_..", "ts": "2026-10-26T..Z", "type": "...", ...payload }
```
| type | payload | 비고 |
|---|---|---|
| `run.started` | `{parentRunId?, model}` | 재개 run이면 parentRunId |
| `message.start/delta/end` | `{messageId, role, delta?}` | assistant 텍스트 |
| `reasoning.delta` | `{messageId, delta}` | 모델이 제공할 때만 |
| `tool.call.start/args/end` | `{toolCallId, name, delta?}` | args delta는 표시용, 실행은 end 후 검증된 JSON |
| `tool.result` | `{toolCallId, status: ok\|error\|denied, preview, truncated, artifactRef?}` | 전체 출력은 별도 저장, preview만 |
| `approval.requested` | `{interruptId, toolCallId, reason, display: {diff?, target?, command?}, responseSchema, expiresAt}` | **실제 실행될 내용**을 서버가 렌더 |
| `state.snapshot/delta` | `{todo?, filesTouched?, usage?}` | 사이드패널(plan, 파일, 비용) |
| `run.finished` | `{outcome: success\|interrupt\|cancelled, interrupts?, usage}` | interrupt면 resume 대기 |
| `run.error` | `{code, message, retryable}` | |
- 클라이언트→서버: `POST /threads/{id}/runs`(메시지 또는 `resume:[{interruptId, approved, editedArgs?}]`), `POST /runs/{id}/cancel`, `GET /threads/{id}/events?after=<seq>`(SSE, `Last-Event-ID` 지원).
- 이벤트는 DB에 append(진실의 원천), UI 상태는 이벤트 fold로 재구성 → 새로고침·멀티탭·모바일 동시 시청이 공짜로 됨. 나중에 AG-UI/Vercel 포맷 어댑터를 붙이기도 쉬움.

---

## 2. Human-in-the-loop 승인 패턴

### 2.1 OpenAI Agents SDK
- tool에 `needs_approval=True` 또는 `async (ctx, params, call_id) -> bool`. **arguments가 malformed JSON/비객체면 callable을 부르지 않고 무조건 수동 승인(fail closed)**. https://github.com/openai/openai-agents-python/blob/main/docs/human_in_the_loop.md
- 흐름: 승인 필요 → run 중단, `result.interruptions`(ToolApprovalItem: agent, tool_name, arguments) → `state = result.to_state()` → `state.to_string()`/`to_json()`으로 **DB 저장, 다른 프로세스에서 `RunState.from_json` 로드** → `state.approve(i)` / `state.reject(i, rejection_message=...)` → `Runner.run(agent, state)`.
- `always_approve/always_reject` sticky 결정은 RunState에 저장됨. 주의: shell/apply_patch의 sticky 결정은 **agent 범위가 아닌 tool 이름 범위** → 권한이 다른 executor 간 공유 위험. 부분 승인 가능(미해결 건은 다시 interrupt).
- 거절 시 모델에게 보이는 메시지를 커스터마이즈 가능(`tool_error_formatter`) → "사용자가 거절함. 대안을 제시하라" 같은 안내를 role:tool로.

### 2.2 LangGraph `interrupt()`
- checkpointer(운영은 DB 기반)가 graph state 저장, `thread_id`가 재개 커서. `Command(resume=value)`가 `interrupt()`의 반환값이 됨. **재개 시 해당 노드를 처음부터 다시 실행** → interrupt 이전 코드는 재실행되므로 side effect는 idempotent하거나 interrupt 이후로. https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/interrupts.mdx

### 2.3 Claude Code
- permission mode·sandbox 조합은 R1 B.2/G.3 참조. 추가: cloud session에서 질문/승인 대기 중이면 **environment expiry까지** 답할 수 있고, 대기 시간도 inactivity로 계산되어 VM이 회수될 수 있음(대화는 복원). https://code.claude.com/docs/en/claude-code-on-the-web.md

### 2.4 우리 설계에 적용
- 직접 구현 시 필요한 최소 상태: `{messages(마지막 assistant tool_calls 포함), pending: [{interruptId, toolCallId, name, args, policyReason}], approvals, budget/turn counters}`. LLM 루프는 순수 함수처럼 이 상태에서 재시작 가능해야 함.
- 승인된 tool 실행 결과만 role:tool로 붙이고 LLM을 재호출 → **LLM 호출을 반복하지 않으므로 비용·비결정성 없음**. 같은 assistant 메시지 안의 다른(승인 불필요) tool call은 interrupt 전에 실행해 두거나 함께 대기(일관성 위해 "같은 배치는 함께 대기" 권장).
- `expiresAt`(예: 24h) 이후 resume은 거절 → role:tool에 "승인 만료" 기록. idempotency key = `(threadId, interruptId)`.

---

## 3. Context window 관리

### 3.1 Tool result clearing (가장 가벼운 단계)
- Anthropic context editing `clear_tool_uses`: trigger(예: input 30k tok) 넘으면 **오래된 tool result부터** placeholder로 교체, `keep`(최근 N개 tool use 유지), `clear_at_least`(한 번에 최소 N tok 지움 — **cache 무효화 비용을 상쇄할 만큼 몰아서**), `exclude_tools`, `clear_tool_inputs`(기본 false: 호출 인자는 남김). https://platform.claude.com/docs/en/build-with-claude/context-editing
- OpenAI-compatible 모델용으로는 이 로직을 클라이언트에서 그대로 재현하면 됨: `[tool result cleared — re-run read_file(path) if needed]` 같은 **재획득 방법이 담긴 placeholder**.

### 3.2 LLM 요약 compaction
- Claude Code: 요약 후 **디스크에서 재주입**: system prompt, project CLAUDE.md, auto memory, plan mode의 plan, git status, 최근 수정 파일 최대 5개(5k tok 초과 파일은 경로만), 호출된 skill 본문(개당 5k, 총 25k), 실행 중 background 작업 목록. 경로 범위 규칙은 요약에 녹아 사라짐. https://code.claude.com/docs/en/context-window.md
- 요약 요청은 같은 system/tools/history prefix 위에 요약 지시를 덧붙여 보냄 → warm cache면 저렴. 자연스러운 작업 경계에서 compact 권장, 사용자 지정 compact 지시("test 출력과 코드 변경에 집중") 지원. https://code.claude.com/docs/en/prompt-caching.md , https://code.claude.com/docs/en/costs.md
- Codex CLI: "다른 LLM이 이어받을 handoff 요약"(진행상황·핵심 결정·제약/사용자 선호·남은 일) + 최근 사용자 메시지(~20k tok)만 남기고 history 재구성. OpenAI 모델은 서버측 compact endpoint(암호화 blob). [2차] https://gist.github.com/badlogic/cd2ef65b0697c4dbe2d13fbecb0a0a5f , https://codex.danielvaughan.com/2026/04/10/context-compaction-showdown-coding-agents/
- Anthropic API도 서버측 compaction(on-demand / threshold, beta)과 "최근 turn은 원문 유지" 옵션을 제공 — 우리 경우 provider 독립이므로 **클라이언트측 요약기**를 구현해야 함. https://platform.claude.com/docs/en/build-with-claude/compaction
- OpenHands condenser는 R1 B.4 참조(keep_first + 중간 요약).

### 3.3 무엇을 남길까 (권장 요약 템플릿)
1) 원래 사용자 요청 원문(+ 이후 요구사항 변경) 2) 현재 plan/todo와 상태 3) 수정·생성한 파일 목록과 변경 요지 4) 내린 결정과 이유, 실패한 시도(반복 방지) 5) 사용자 선호/제약 6) 다음 단계. 1·2·3은 요약에 의존하지 말고 **구조화 상태(DB)에서 매번 재주입**.

### 3.4 Token counting (모르는 모델)
- 1순위: 직전 응답 `usage.prompt_tokens` + 이후 추가분 추정. 추가분은 chars/3~4 보수적 휴리스틱(한국어는 토큰 밀도가 높으므로 더 보수적으로) [2차, 업계 관례].
- vLLM: `POST /tokenize`(chat messages 받으면 chat template 적용, `count`·`max_model_len` 반환), `/detokenize`. https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/serve/tokenize/api_router.py , [2차] 응답 필드: https://discuss.vllm.ai/t/tell-me-about-the-current-status-of-the-tokenize-endpoint-in-vllm/2089
- context window 크기는 `/v1/models`로 알 수 없음(R1) → 모델 설정에 `context_window`, `compact_at`(예: 75~80%) 명시, `finish_reason=length`/400 context 오류 시 강제 compaction 후 1회 재시도.

---

## 4. 세션/런 영속화 & 장시간 작업

### 4.1 실행 모델
- API 서버(무상태) → **job queue** → agent worker(run 1개 = job 1개, lease/heartbeat) → 이벤트를 DB/Redis Stream에 append → SSE는 tail. worker 죽으면 lease 만료 후 마지막 체크포인트(턴 경계)에서 재시작.
- 체크포인트 단위 = **LLM 응답 1회 + 그 tool 결과들**(턴 경계). LLM 출력은 비결정적이므로 "기록된 결과를 재생"해야 함 — Temporal도 LLM 호출을 activity로 기록하는 방식. Temporal은 OpenAI Agents SDK 통합·HITL Signal 레시피 제공. [2차] https://docs.temporal.io/ai , https://temporal.io/blog/introducing-temporal-and-agentic-sandboxes-openai-agents-sdk
- MVP 판단: Temporal은 강력하지만 운영 부담 큼 → **Postgres(runs, events, checkpoints) + Redis/pg 큐 + 턴 경계 체크포인트**로 충분, 규모 커지면 Temporal 검토.

### 4.2 Sandbox lifecycle
- Claude Code on the web: idle 시 VM 회수 → 재오픈 시 **새 VM + 대화 복원**, 실행 중이던 shell/subagent는 복원 안 됨(모델에게 "중단된 명령 목록"을 알려줌). 승인 대기도 idle로 계산. https://code.claude.com/docs/en/claude-code-on-the-web.md
- E2B: `pause()`(파일시스템+메모리, ~4s/GiB), resume ~1s, `lifecycle:{onTimeout:'pause', autoResume:true}`, 보관기간은 문서상 무기한(서드파티는 30일이라 함 — 상충). 반복 resume 시 파일 유실 이슈 보고 있음. [2차] https://e2b.dev/docs/sandbox/persistence , https://github.com/e2b-dev/E2B/issues/884
- Daytona: auto-stop 기본 15분 idle, auto-archive 기본 stop 후 7일(object storage로 이동), auto-delete 기본 off. [2차] https://www.daytona.io/docs/en/sandboxes
- Modal: `timeout` 기본 300s, `idle_timeout`, `snapshot_filesystem()`→Image(TTL 문서 간 상충), memory snapshot은 실험적(7일 만료). [2차] https://modal.com/docs/guide/sandbox-snapshots , https://modal.com/docs/guide/sandbox-memory-snapshots

### 4.3 권장
- **Workspace의 진실 = git**: 턴 종료마다(또는 승인 대기 진입 시) workspace를 `agent/<session>` WIP branch/commit 또는 tarball(object storage)로 저장 → sandbox는 언제든 버리고 재생성. pause/snapshot은 "warm resume 최적화"로만 사용.
- 타이머: 명령 timeout(R1) / sandbox idle 10~15분 / 승인 대기 TTL 24h / 세션 보관 30일 등 **계층별로 분리 정의**.
- 장기 실행 명령(dev server, 빌드)은 background job으로 분리하고 상태를 `state.snapshot`으로 노출; sandbox 회수 시 모델에게 중단 사실 주입.

---

## 5. 실용적 Prompt injection 방어 (MVP)

- **Spotlighting**(Microsoft, Hines et al. 2024): delimiting(구분자) / datamarking(비신뢰 텍스트의 공백을 특수 문자로 치환) / encoding(base64 등). GPT 계열 실험에서 공격 성공률 >50% → <2%; delimiter만으로는 약 절반 감소, 방어 지시문만으로는 효과 미미; encoding은 고성능 모델에서만 품질 유지. [2차] https://arxiv.org/abs/2403.14720 — 적응형 공격에는 약함(R1 G.4 결론 유지).
- **Dual LLM / CaMeL**: privileged LLM이 신뢰된 사용자 질의로부터 제어흐름(코드)을 만들고, quarantined LLM은 tool 없이 비신뢰 데이터만 처리, 값마다 capability 메타데이터로 정책 검사. AgentDojo에서 증명 가능한 보안 하에 77% 과제 해결(무방어 84%; v1은 67%). [2차] https://arxiv.org/abs/2503.18813 , https://github.com/google-research/camel-prompt-injection — 범용 coding agent엔 적용 난이도 높음.
- **Design patterns 논문**(Beurer-Kellner et al. 2025): action-selector, plan-then-execute, LLM map-reduce, dual LLM, code-then-execute, context-minimization. 원칙: "비신뢰 입력을 읽은 agent는 그 입력이 중대한 행동을 유발할 수 없도록 제약". [2차] https://arxiv.org/abs/2506.08837
- **MVP 체크리스트**
  1. Task별 tool allowlist: "질문 답변" 모드는 read-only tool만, "코드 수정" 모드에서만 write/bash, push/PR은 항상 승인. 단 cache 보존을 위해 tool **정의는 고정**하고 정책 계층에서 거부(§6).
  2. Egress allowlist proxy(R1 C.5) + 쓰기 가능한 범용 서비스 제외.
  3. 비신뢰 출력 spotlighting: `<untrusted_tool_output tool="web_fetch" id="..">…</untrusted_tool_output>` + 내부에 등장하는 동일 태그 escape(닫는 태그 위조 방지).
  4. UI: assistant markdown의 **외부 이미지 차단**(CSP `img-src 'self' data:` + 서버 프록시), 링크는 클릭 시 실제 URL 표시, 자동 fetch 금지.
  5. 승인 다이얼로그는 모델 설명이 아니라 실제 diff/명령/대상을 표시(R1 G.3).
  6. 비신뢰 출력을 읽은 이후의 outward action은 위험도 상향(예: web_fetch 이후 첫 push는 강제 승인) — "taint 플래그"를 run 상태에 유지.

---

## 6. Latency/cost — Prompt caching

### 6.1 Provider별
- **OpenAI**: ≥1024 tok prompt 자동 캐시, 128 tok 단위 적중, messages·이미지·tool 정의·structured output schema 모두 prefix에 포함. `usage.prompt_tokens_details.cached_tokens`로 확인. `prompt_cache_key`는 같은 머신으로의 routing 힌트, `prompt_cache_retention`(`in_memory` 기본 / `24h`). [2차] https://developers.openai.com/docs/guides/prompt-caching , https://developers.openai.com/cookbook/examples/prompt_caching_201
- **vLLM APC**: `enable_prefix_caching`, KV block 해시(부모 해시+블록 토큰+extra hash), **full block만 캐시**, prefill만 절약(decode 무관). 멀티테넌트는 요청별 `cache_salt`로 격리(timing 공격 방지). https://github.com/vllm-project/vllm/blob/main/docs/features/automatic_prefix_caching.md , https://github.com/vllm-project/vllm/blob/main/docs/design/prefix_caching.md
- **DeepSeek**: 디스크 캐시 기본 on, `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens` 보고, best-effort. [2차] https://api-docs.deepseek.com/guides/kv_cache
- **OpenRouter**: 대부분 자동, Anthropic 등은 `cache_control` 필요. sticky routing은 `session_id` 없으면 첫 system + 첫 user 메시지 해시로 키 생성. [2차] https://openrouter.ai/docs/features/prompt-caching
- 사용량 필드 이름이 provider마다 다름 → adapter에서 `cached_input_tokens`로 정규화.

### 6.2 왜 agent loop에서 결정적인가
- Agent는 매 턴 전체 history를 재전송, 입력:출력 토큰 비가 Manus 기준 약 100:1; cached input은 보통 정가의 1/10 수준. [2차] https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus
- 캐시는 **정확한 prefix 일치** — 중간 한 토큰만 바뀌어도 이후 전부 재계산. Claude Code는 레이어를 "system+tools → project context → conversation" 순으로 배치하고, 모델 변경·tool 집합 변경·compaction을 cache 무효화 이벤트로 명시. https://code.claude.com/docs/en/prompt-caching.md
- 규칙: system prompt에 timestamp/랜덤 id 금지(날짜가 필요하면 user 메시지 쪽에), tool 정의 순서·내용 고정, history append-only(과거 메시지 수정 금지 — clearing은 몰아서), JSON 직렬화 key 정렬, run 중 모델·파라미터 변경 금지, 같은 세션은 같은 endpoint로(sticky).

---

## 7. 평가

- **SWE-bench Verified**: OpenAI가 2026-02-23 "오염 증가·부적절한 테스트"를 이유로 보고 중단, SWE-bench Pro(1,865 task, 41 repo, Python/Go/TS/JS, 비공개 commercial split) 권고. Verified 70%대 모델이 Pro 공개셋에선 ~23%. [2차] https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/ — Lite(300)/Verified(500)는 여전히 **회귀 테스트용 저비용 smoke**로는 유용.
- **Terminal-Bench 2.0**: 컨테이너 환경의 터미널 작업(디버깅, 보안 취약점 수정 등). 공식 harness **Harbor**: `harbor run --dataset terminal-bench@2.0 --agent <agent> --model <model>`, Claude Code/OpenHands/Codex CLI 등 임의 agent 평가, SWE-bench·Aider Polyglot 등 타 데이터셋도 지원, Docker 또는 Daytona/Modal 등에서 병렬 실행. https://github.com/harbor-framework/terminal-bench-2 , https://github.com/laude-institute/harbor
- **τ²-bench**: tool-agent-user 대화형(airline/retail/telecom), 시뮬레이션 사용자, 6개 언어 pack(**한국어 포함**), 도메인당 50개 frozen task. (원 τ-bench의 pass^k 신뢰성 지표 [2차]) https://github.com/sierra-research/tau2-bench
- **자체 task suite(가장 중요)**: 각 task = `{repo snapshot, user prompt, 허용 tool, 검증 스크립트(pytest/grep/diff), 예산}`. 지표: 성공률, pass^k(동일 task k회 반복 일관성), 턴 수·토큰·비용·wall time, 승인 요청 횟수, 금지 행동 발생(egress 위반, 범위 밖 파일 수정). Prompt injection 회귀셋(README/issue에 심은 지시) 포함. 모델(OpenAI-compatible 백엔드)별 매트릭스로 nightly 실행.
- Harbor task 포맷을 맞춰 두면 자체 suite와 TB2를 같은 harness로 돌릴 수 있음 → **우리 agent를 Harbor agent adapter로 감싸는 것**을 Phase 2 과제로.

---

## 부록: 스펙 반영 체크리스트 (R1에 추가)
- [ ] 이벤트: `seq` 포함 envelope, SSE + `Last-Event-ID`, heartbeat 15s, 별도 POST(cancel/resume), events 테이블이 진실의 원천
- [ ] 승인: run 종료형 interrupt, RunState 직렬화, `(threadId, interruptId)` idempotency, `expiresAt`, approve-with-edits(전체 교체), fail-closed
- [ ] Context: clearing(`keep`, `clear_at_least`) → handoff 요약 → plan/파일목록/원 요청 재주입, `compact_at` 설정, length 오류 시 강제 compact
- [ ] Token: `usage` 기반 누적 + 휴리스틱, vLLM `/tokenize` 옵션
- [ ] 런타임: queue + worker lease, 턴 경계 체크포인트, workspace는 git/tarball로 저장, sandbox 일회용, 계층별 timeout
- [ ] 캐시: 정적 prefix 규칙, tool 정의 고정, `cached_input_tokens` 정규화, `prompt_cache_key`/`cache_salt`/sticky 세션
- [ ] 보안: mode별 tool 정책(정의 고정), spotlighting + 태그 escape, taint 플래그, CSP img-src 차단
- [ ] 평가: 자체 suite(검증 스크립트) CI, Harbor adapter, injection 회귀셋, 모델 매트릭스
