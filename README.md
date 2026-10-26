# vanilaChatbot

"실제로 문제를 해결하는 agent를 가진 chatbot"(ChatGPT agent / Claude Code Web 류)의 **기술 명세**와, 그 명세를 검증한 **MVP 코드**.

- 📄 명세: [docs/SPEC.md](docs/SPEC.md)
- 🔁 리서치↔테스트 루프 기록: [docs/LOOP_LOG.md](docs/LOOP_LOG.md)
- 🔎 리서치 노트: [docs/research/](docs/research/)

LLM은 OpenAI 호환 Chat Completions만 가정합니다. 테스트에는 `claude -p`(Haiku 5.5)를 감싼 자체 gateway(`gateway/app.py`)를 사용했고, `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`만 바꾸면 다른 OpenAI 호환 모델로 그대로 동작합니다.

실행 방법은 [SPEC 부록 B](docs/SPEC.md#부록-b-실행) 참고.
