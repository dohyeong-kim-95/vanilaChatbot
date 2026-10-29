"""Loop 17: "로그 분석해줘" -> look at the file, ask what to focus on (ask_user), show the matching view.
Deterministic: python tests/test_l12_triage.py          (sniff, ask/resume, real app + Chrome with a fake LLM)
LLM:           python tests/test_l12_triage.py t_llm_triage   (gateway on :8100 or LLM_BASE_URL; K=1)
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "eval" / "shmoo"))
sys.path.insert(0, str(ROOT / "tests" / "eval" / "finance"))
FAKE_PORT = 8398
if "t_llm_triage" not in sys.argv:  # the app's runs talk to the fake model below
    os.environ["LLM_BASE_URL"] = f"http://127.0.0.1:{FAKE_PORT}/v1"
import test_l9_artifacts as l9  # noqa: E402
import gen  # noqa: E402
from agent import skills  # noqa: E402
from agent.artifacts import ArtifactStore  # noqa: E402
from agent.loop import SYSTEM, Context, Interrupt, run  # noqa: E402
from agent.sandbox import Sandbox  # noqa: E402
from agent.userstore import UserStore  # noqa: E402
from cases import build  # noqa: E402

K = int(os.environ.get("K", 1))
TMP = l9.TMP / "triage"
FILES = TMP / "files"
FILES.mkdir(parents=True)
gen.write(FILES / "shmoo_L2610A.csv")
build(TMP / "fx")
(FILES / "card_b_2026-09.csv").write_bytes((TMP / "fx" / "card_b_2026-09.csv").read_bytes())
APP_LOG = "".join(
    f"2026-10-29 21:{m:02d}:{s:02d} {lvl} {msg}\n" for m in range(0, 60) for s, lvl, msg in (
        (1, "INFO", f"job {m} started"), (7, "ERROR" if m % 7 == 3 else "INFO",
                                          f"timeout after {30 + m % 5}s on host 10.0.0.{m % 4}" if m % 7 == 3 else "ok"),
        (40, "WARN" if m % 10 == 0 else "INFO", "disk 91% full on /var" if m % 10 == 0 else "heartbeat")))
(FILES / "app.log").write_text(APP_LOG)
QUESTION = {"question": "shmoo 로그예요. 무엇을 볼까요?", "options": [
    {"label": "이상한 칩 찾기", "description": "shmoo 뷰 + 이상 패턴"}, {"label": "전체 요약"}]}


def t_sniff_kinds():
    out = subprocess.run([sys.executable, str(skills.DIR / "log-triage/scripts/sniff.py"), *sorted(FILES.iterdir())],
                         capture_output=True, text=True, check=True).stdout
    blocks = {b.split()[0]: b for b in out.split("== ")[1:]}
    assert "fits: shmoo" in blocks["shmoo_L2610A.csv"] and "card" not in blocks["shmoo_L2610A.csv"].split("fits:")[1]
    assert "fits: card-transactions" in blocks["card_b_2026-09.csv"]
    assert "kind: text log" in blocks["app.log"] and "timeout after <n>s on host" in blocks["app.log"]
    print("   ", {k: v.split("fits: ")[1].split(" (")[0].strip() for k, v in blocks.items()})


class Scripted:
    """OpenAI-shaped client that replays tool calls: enough to drive run() without a model."""
    def __init__(self, steps):
        self.steps, self.seen = list(steps), []
        self.chat = self.completions = self
        self.models = type("M", (), {"list": lambda s: type("L", (), {"data": [type("D", (), {"id": "fake"})]})})()

    def create(self, **kw):
        from openai.types.chat import ChatCompletion
        self.seen.append(kw["messages"][-1])
        msg = self.steps.pop(0)
        return ChatCompletion.model_validate({"id": "x", "object": "chat.completion", "created": 0, "model": "fake",
                                              "choices": [{"index": 0, "finish_reason": "stop", "message": msg}]})


def call(name, args, cid="c1"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}


def t_ask_interrupt_resume():
    ctx = Context()
    cl = Scripted([call("ask_user", QUESTION), {"role": "assistant", "content": "done"}])
    try:
        run("분석해줘", ctx, tool_names=["ask_user"], client=cl, log=lambda *_: None)
        raise AssertionError("ask_user must pause the run")
    except Interrupt as i:
        assert i.name == "ask_user" and i.arguments["options"][0]["label"] == "이상한 칩 찾기"
    # resume like the server does: same history, decision = the answer text
    ctx2 = Context(decisions={"c1": "이상한 칩 찾기"})
    cl2 = Scripted([call("ask_user", QUESTION), {"role": "assistant", "content": "done"}])
    hist = []
    try:
        run("분석해줘", ctx2, tool_names=["ask_user"], client=cl2, history=hist, log=lambda *_: None)
    except Interrupt:
        raise AssertionError("a decided call must not pause again")
    assert hist[-2]["role"] == "tool" and hist[-2]["content"] == "The user answered: 이상한 칩 찾기", hist[-2:]
    # malformed questions go back to the model as errors, never to the user
    bad = Scripted([call("ask_user", {"question": "?", "options": [{"label": "only one"}]}),
                    {"role": "assistant", "content": "ok"}])
    h = []
    run("x", Context(), tool_names=["ask_user"], client=bad, history=h, log=lambda *_: None)
    assert h[-2]["content"].startswith("ERROR: ValueError: options must be 2-5"), h[-2]
    # an approval-style answer cannot be smuggled in by the model as an argument
    sm = Scripted([call("ask_user", {**QUESTION, "answer": "yes"}), {"role": "assistant", "content": "ok"}])
    h = []
    run("x", Context(), tool_names=["ask_user"], client=sm, history=h, log=lambda *_: None)
    assert "unknown arguments: ['answer']" in h[-2]["content"], h[-2]


def fake_llm():
    """OpenAI-compatible server for the app: ask once, then answer with what the user picked."""
    import uvicorn
    from fastapi import FastAPI
    app = FastAPI()

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": "fake", "object": "model"}]}

    @app.post("/v1/chat/completions")
    def chat(body: dict):
        last = body["messages"][-1]
        if last["role"] == "user":
            msg = call("ask_user", QUESTION, "q1")
        else:
            msg = {"role": "assistant", "content": f"선택: {last['content']}"}
        return {"id": "x", "object": "chat.completion", "created": 0, "model": "fake",
                "choices": [{"index": 0, "finish_reason": "stop", "message": msg}]}
    srv = uvicorn.Server(uvicorn.Config(app, port=FAKE_PORT, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{FAKE_PORT}/v1/models"); break
        except httpx.TransportError:
            time.sleep(0.1)
    return srv


def t_ui_question_flow():
    """Real app + Chrome: the question shows as a card with option buttons, a click resumes the run, a reload while
    waiting shows the card again, and the API refuses an approval-shaped answer."""
    UserStore(l9.DATA).create_project("alice", "tri")
    srv = fake_llm()
    ps = l9.servers()
    pw, b = l9.browser()
    try:
        page = b.new_page(viewport={"width": 1300, "height": 800})
        page.goto(l9.APP + "/")
        page.evaluate("() => localStorage.setItem('user', 'alice')")
        page.goto(l9.APP + "/")
        page.wait_for_function("() => document.querySelector('#project option')")
        page.select_option("#project", "tri")
        page.fill("#input", "로그 분석해줘")
        page.click("#send")
        page.wait_for_selector(".question button.option")
        labels = page.locator(".question .label").all_inner_texts()
        assert labels == ["이상한 칩 찾기", "전체 요약"], labels
        page.screenshot(path=str(TMP / "ui-question.png"))
        rid = l9.client("alice").get(f"/sessions/{page.evaluate('() => state.session')}").json()["active_run"]["run_id"]
        r = l9.client("alice").post(f"/runs/{rid}/approval", json={"call_id": "q1", "approved": True})
        assert r.status_code == 422, (r.status_code, r.text)
        assert l9.client("bob").post(f"/runs/{rid}/approval", json={"call_id": "q1", "answer": "x"}).status_code == 404
        page.reload()  # the pending question comes back from the event log
        page.wait_for_selector(".question button.option")
        page.locator(".question button.option").first.click()
        page.wait_for_selector("text=선택: The user answered: 이상한 칩 찾기")
        assert page.locator(".question .answered").inner_text() == "이상한 칩 찾기"
        assert l9.client("alice").post(f"/runs/{rid}/approval", json={"call_id": "q1", "answer": "again"}).status_code == 409
        print(f"    ui -> {TMP / 'ui-question.png'}")
    finally:
        b.close(); pw.stop(); l9.kill(ps); srv.should_exit = True


# ---------------------------------------------------------------- LLM

CASES = [("shmoo_L2610A.csv", "show_shmoo", ["shmoo"]), ("card_b_2026-09.csv", "show_report", ["카드", "거래", "지출"]),
         ("app.log", "show_logview", ["로그", "log"])]


def t_llm_triage():
    """Only '로그 분석해줘' + the file. Expected: one question whose first sentence says what the file is, options;
    we pick the first option; then the matching view is shown."""
    rows = []
    for fname, view, words in CASES:
        for i in range(K):
            ws = TMP / f"llm-{fname}-{i}"
            (ws / "uploads").mkdir(parents=True)
            (ws / "outputs").mkdir()
            (ws / "uploads" / fname).write_bytes((FILES / fname).read_bytes())
            subprocess.run(["chown", "-R", "1000:1000", str(ws)])
            sk = skills.discover()
            asked = []

            def ask(name, args):
                asked.append(args)
                return args["options"][0]["label"]
            sb = Sandbox(ws, readonly=("uploads",), mounts={skills.DIR: skills.MOUNT})
            ctx = Context(sandbox=sb, skills=sk, artifacts=ArtifactStore(ws.parent / f"arts-{fname}-{i}"), ask=ask)
            m, calls, t0 = l9.llm(), [], time.time()
            try:
                ans, _ = run("업로드한 파일은 /workspace/uploads 에 있어요.\n\n로그 분석해줘", ctx,
                             tool_names=["bash", "read_file", "write_file", "edit_file", "use_skill", "ask_user",
                                         "show_shmoo", "show_report", "show_logview", "artifact"],
                             client=m, max_steps=25, history=[{"role": "system", "content": "\n\n".join(
                                 [SYSTEM, skills.prompt(sk)])}],
                             log=lambda l: calls.append(l.split("(")[0].split("] ")[1]))
            finally:
                sb.close()
            q = asked[0] if asked else {}
            shown = [a["type"] for a in ctx.artifacts.list()]
            ok_view = {"show_shmoo": "shmoo", "show_report": "report", "show_logview": "logview"}[view]
            rows.append({"file": fname, "asked": len(asked), "says_what": any(w in q.get("question", "") for w in words),
                         "options": [o["label"] for o in q.get("options", [])], "views": shown,
                         "view_ok": ok_view in shown, "tools": calls,
                         "sec": round(time.time() - t0), "out_tokens": m.completion,
                         "question": q.get("question", "")[:200], "answer": (ans or "")[:200]})
            print("   ", json.dumps(rows[-1], ensure_ascii=False))
    ok = sum(r["asked"] == 1 and r["says_what"] and r["view_ok"] for r in rows)
    print(f"    asked once + said what the file is + matching view: {ok}/{len(rows)}")
    assert ok == len(rows)


if __name__ == "__main__":
    only = sys.argv[1:]
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (only and name in only or not only and not name.startswith("t_llm")):
            t = time.time()
            try:
                fn()
                print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed += 1
                print(f"FAIL {name} ({time.time() - t:.0f}s): {type(e).__name__}: {str(e)[:400]}")
    sys.exit(1 if failed else 0)
