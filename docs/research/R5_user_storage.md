# R5 — 사용자별 저장 공간 (CLAUDE.md, .claude, 업로드, 결과물)

조사일 2026-10-27. 실험 기록은 [LOOP_LOG Loop 12](../LOOP_LOG.md#loop-12--사용자별-저장-공간), 코드는 `agent/userstore.py`, 테스트는 `tests/test_l8_userstore.py`.

## 1. Claude Code의 지시사항(CLAUDE.md) 구조
- 범위별 위치: managed policy(`/etc/claude-code/CLAUDE.md`, 조직), user(`~/.claude/CLAUDE.md`), project(`./CLAUDE.md`), local(`CLAUDE.local.md`). 넓은 범위부터 좁은 범위 순서로 **이어 붙인다**. "All discovered files are concatenated into context rather than overriding each other." ([Claude Code docs, memory](https://code.claude.com/docs/en/memory))
- 충돌 처리는 정해져 있지 않다: "if two instructions contradict each other, Claude may pick one arbitrarily", user rule과 project rule이 충돌하면 "Claude may follow either one". → **우선순위를 원한다면 프롬프트에 명시하거나, 코드로 강제해야 한다.**
- 지시사항은 강제력이 없다: "Claude treats them as context, not enforced configuration. To block an action regardless of what Claude decides, use a PreToolUse hook." → 이 레포의 원칙(승인·네트워크 차단 같은 강제는 코드)과 같다.
- auto memory: Claude가 직접 쓰는 메모, 매 세션 앞부분(200줄/25KB)만 로드. CLAUDE.md(사람이 씀)와 분리되어 있다.
- 외부 import(`@path`)가 작업 디렉터리 밖을 가리키면 승인 대화상자를 띄운다 — "다른 사람이 커밋한 파일"을 신뢰하지 않기 위해서.

## 2. Claude Code의 설정(settings.json) 구조
- 우선순위: **managed > CLI > project local > shared project > user**. 상위가 같은 키를 덮는다. ([Claude Code docs, settings](https://code.claude.com/docs/en/settings))
- 배열(`permissions.allow` 등)은 덮지 않고 **합친다**. 단 `availableModels` 같은 일부 키는 managed 값이 그대로 적용(상한).
- 보안 관련 일부 키는 "더 엄격한 값"이 어느 계층에서 오든 이긴다(예: `disableClaudeAiConnectors: true`).
- 저장소에 커밋된 `.claude/settings.json`의 allow 규칙은 **workspace trust 승인 후에만** 적용, deny/ask는 즉시 적용.
- 클라우드 세션은 사용자 설정·local 설정을 읽지 않고 server-managed 설정만 받는다.

→ 우리 설계에 적용: 조직(managed) 설정은 사용자·프로젝트가 넓힐 수 없는 **상한**, 목록은 합치되 allowlist는 조직 목록과 **교집합**.

## 3. ChatGPT Projects
- 프로젝트 = 대화 + 업로드 파일 + 프로젝트 지시사항. 프로젝트 지시사항은 그 프로젝트 안에서 전역 custom instructions보다 **우선**. ([OpenAI Academy, Projects](https://academy.openai.com/public/clubs/work-users-ynjqu/resources/projects), [TechRadar](https://www.techradar.com/ai-platforms-assistants/chatgpt/chatgpt-project-only-memory-is-live-and-it-might-change-how-you-work-with-ai))
- project-only memory: 그 프로젝트의 대화·파일만 참고. 기존 프로젝트에는 켤 수 없다.
- 파일 수 제한이 요금제별로 있음(Free 5 / Plus 25 / Enterprise 40, 2차 출처).

## 4. 멀티테넌트 sandbox의 저장
- E2B: pause/resume 스냅샷(메모리+디스크 또는 디스크만), **Volumes**(sandbox 수명과 분리된 영구 저장, private beta)를 생성 시 mount. ([E2B persistence](https://e2b.dev/docs/sandbox/persistence), [E2B volumes](https://docs.e2b.dev/volumes))
- Dedalus: 사용자당 머신 1대 + 영구 home, 대화 사이에는 sleep. ([Dedalus cookbook](https://docs.dedaluslabs.ai/cookbook/agent-sandbox-per-user))
- Blaxel: standby 상태 유지만으로는 장기 보존을 보장할 수 없다 → Volume 권장. 공유 커널 컨테이너는 멀티테넌트에서 blast radius가 크다 → microVM. ([Blaxel](https://blaxel.ai/blog/best-platforms-persistent-sandbox-environments.html), [Upstash 비교](https://upstash.com/blog/ai-agent-sandbox-providers-compared-2026))
- 공통 패턴: **영구 저장(volume/object storage)과 실행(sandbox)을 분리**, sandbox는 일회용. 우리 SPEC §5.2와 같다.

## 5. 메모리 오염(memory poisoning)
- SpAIware(2024, Johann Rehberger): 문서/웹페이지의 간접 프롬프트 인젝션으로 ChatGPT 장기 메모리에 지시를 심어, **이후 모든 대화**의 내용을 외부로 보내게 함. OpenAI는 처음엔 model safety 문제로 분류, PoC 후 유출 경로를 패치. 메모리 조작 자체는 여전히 열린 문제. ([The Hacker News](https://thehackernews.com/2024/09/chatgpt-macos-flaw-couldve-enabled-long.html))
- ZombieAgent(2026-01, Radware): 이메일 첨부파일이 connector를 통해 메모리를 심고, 메일로 전파(worm) 가능성.
- OWASP Top 10 for Agentic Applications(2025-12): ASI06 Memory & Context Poisoning. 대응으로 **provenance(출처) 추적**, 쓰기 경로의 사람 검토, 신뢰 등급 상한(출처가 낮으면 내용이 그럴듯해도 신뢰를 올리지 않음)이 거론된다. (2차 출처: [zealynx ASI06](https://www.zealynx.io/blogs/owasp-asi06-memory-context-poisoning), [arXiv 2606.22030](https://arxiv.org/pdf/2606.22030))
- 시사점: 메모리 쓰기는 **쓰는 시점**에 막아야 한다(읽는 시점엔 이미 모든 대화에 들어가 있다). → `remember`는 승인 필수 + 출처 기록 + 프롬프트에서 낮은 신뢰로 표시.

## 6. sandbox가 쓴 git 메타데이터를 호스트가 다루는 위험
- git은 저장소의 `.git/config`를 따른다. `core.fsmonitor` 같은 설정은 임의 명령을 실행한다(CVE-2022-24765의 핵심). 이후 git은 **다른 사용자 소유** 저장소를 거부하는 `safe.directory`를 도입. ([GitHub blog](https://github.blog/2022-04-12-git-security-vulnerability-announced))
- 이 레포의 상황: sandbox uid(1000)와 호스트 사용자 uid가 같고, `github.SAFE`가 `safe.directory=*`까지 넣는다 → 이 보호가 전혀 작동하지 않는다. `core.hooksPath`/`core.fsmonitor`만 끄고 있었지만 **clean filter**(`[filter "x"] clean = <명령>` + `.gitattributes`)는 `git add`가 그대로 실행한다. Loop 12 대조 실험에서 실제로 호스트에서 실행됨을 확인.
- 대응: git 메타데이터(`--git-dir`)를 sandbox가 쓸 수 없는 곳에 두고 work tree만 sandbox와 공유한다. 필터 정의는 신뢰하는 config에만 있으므로 `.gitattributes`가 이름을 대도 실행할 것이 없다. `--git-dir`를 명시하면 safe.directory 검사도 건너뛰므로, 그 git dir은 반드시 호스트만 쓰는 곳이어야 한다.

## 7. 설계에 반영한 것 (요약)
| 항목 | 결정 | 근거 |
|---|---|---|
| 지시사항 계층 | 조직 → 사용자 → 프로젝트 → 메모리 순으로 붙이되 **헤더에 우선순위를 명시** | §1: 이어붙이기만 하면 충돌 시 임의 선택 |
| 강제할 것 | 경로·권한·네트워크는 코드로 강제, 지시사항은 "선호" | §1, §2 |
| 설정 병합 | 스칼라는 상위 우선, 목록은 합집합, 조직 allowlist는 상한(교집합) | §2 |
| `.claude/` | sandbox에 mount하지 않음, 서버만 읽음 | §2 workspace trust와 같은 이유 |
| 메모리 | 승인 필수 `remember`, 출처 기록, 낮은 신뢰로 표시, 행동 지시는 무시하라고 명시 | §5 |
| 업로드 | 내용 hash로 저장, 이름당 1회(불변), sandbox에는 read-only | §3, §5 |
| sandbox ↔ 저장소 | 실행 중에만 컨테이너, 끝나면 repo는 git push, outputs는 복사(심볼릭 링크 제외) | §4, §6 |
