# R6. 아티팩트 (Claude 벤치마킹)

조사일 2026-10-27. 공개 자료(아래 출처)와 측정(Loop 14)을 함께 정리했다. 출처에 없는 Claude 내부 구현은 추측하지 않는다.

## 1. Claude의 아티팩트

| 요소 | 공개된 동작 |
|---|---|
| 생성 | 모델이 전용 tool로 만든다. 새로 만들기, 일부 교체(old→new), 전체 다시 쓰기 |
| 종류 | 코드, Markdown, HTML, SVG, Mermaid, React 컴포넌트 |
| 표시 | 채팅 옆 패널. 버전 이력과 이전 버전 보기 |
| 격리 | 엄격하게 제한된 브라우저 sandbox(iframe)에서 실행. 외부 요청과 외부 스크립트는 CSP로 제한 |
| 공유 | 발행하면 공개 링크가 생김. 다른 사람이 리믹스하면 새 대화가 시작됨. 버전별로 발행 취소 가능 |
| 저장 | 발행된 아티팩트에서만 사용 가능. 아티팩트당 20MB, 텍스트만, 개인용/공유용. 발행을 취소하면 삭제 |
| AI 호출 | 아티팩트 안에서 `window.claude.complete()` 호출. 비용은 실행한 사용자에게 청구 |

## 2. 우리 구현과의 대응

| Claude | 이 레포 (Loop 14) | 상태 |
|---|---|---|
| 아티팩트 tool (create/update/rewrite) | `artifact` tool + 내용을 workspace 파일에서 가져오는 `file=` | 구현 |
| 버전 이력 | `artifacts/{id}/{n}.json` 불변 버전, 출처 기록 | 구현 |
| 별도 도메인에서 렌더링 | 별도 출처 렌더러 + HMAC 서명 URL(10분) | 구현 (로컬은 포트·호스트명으로 분리) |
| sandbox iframe + CSP | `sandbox="allow-scripts"`, `connect-src 'none'`, cdnjs만 허용, 앱 CSP `frame-src` | 구현, 측정 |
| React 컴포넌트 | 빌드 단계가 필요 | 보류 |
| 발행·리믹스, 저장 API, AI 호출 | postMessage 중계 + 승인·비용 한도로 설계 가능 | 보류 |
| (없음) | **고정 리포트 템플릿 + section 이미지 캡처** | 구현: 이 제품(지출 리포트)용 |

## 3. 왜 고정 템플릿도 두는가
일반 아티팩트는 모델이 HTML·JS를 매번 새로 쓴다. 우리 문제(월간 지출 리포트)에서는 이게 단점이다.
- 숫자를 다시 옮겨 적다가 틀릴 수 있다. 출력 토큰이 많아 느리다(Loop 13 기준선의 병목).
- 매달 형식이 달라진다(PROBLEM.md의 귀찮은 지점 6).
- 모델이 쓴 JS라서 격리를 전부 갖춰야 한다.

고정 템플릿에서 모델은 숫자(JSON)만 보낸다. 레이아웃·차트·escape는 코드가 맡는다. 페이지에 스크립트가 없으니 서버 캡처도 JS를 끈 채 할 수 있다.

## 4. 격리에서 배운 것 (측정, Loop 14)
- 모든 요청이 HTTP일 거라고 가정하면 안 된다. **WebRTC(STUN)는 CSP에 해당 지시어가 없어서** 패킷이 그대로 나갔다.
  - 렌더러가 페이지 맨 앞에서 생성자를 지워(설정 불가로 고정) 막았다.
  - 자식 iframe(about:blank, srcdoc)에서 새 생성자를 꺼내는 우회는 `frame-src 'none'`과 opaque origin 때문에 실패했다.
- iframe의 **자기 자신 이동**(`location = 외부`)은 sandbox 속성으로 막을 수 없다. 이걸 막은 것은 앱 쪽 CSP의 `frame-src`다.
- JS 반환값은 증거가 아니다. `sendBeacon`은 true를 반환하고, sandbox 안의 `form.submit()`은 조용히 무시된다. 실제로 나갔는지는 외부 수신기(sink)로만 판단한다.
- 남은 위험(미측정): DNS prefetch, CDN을 통한 신호(cdnjs는 큐레이션되어 있어 jsDelivr/npm보다 낫다), 브라우저 버그.

## 출처
- Simon Willison, CSP in sandboxed iframes (2026-04): https://simonwillison.net/2026/Apr/3/test-csp-iframe-escape/
- Simon Willison, AI-powered apps with Claude (2025-06): https://simonwillison.net/2025/Jun/25/ai-powered-apps-with-claude/
- Claude Help Center, What are artifacts: https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them
- CO/AI, Artifact remix: https://getcoai.com/news/anthropics-artifact-remix-feature-now-enables-users-to-collaborate-and-share-content
- Codecademy, Claude artifacts: https://www.codecademy.com/article/how-to-use-claude-artifacts-create-share-and-remix-ai-content
