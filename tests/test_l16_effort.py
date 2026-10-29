"""Loop 22: effort policy. Deterministic (scripted model): python tests/test_l16_effort.py
The measured part (eval with a correction follow-up) is tests/eval/finance/run.py --followup."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.loop import CORRECTION_RE, Context, run  # noqa: E402


class Scripted:
    def __init__(self, steps):
        self.steps, self.efforts = list(steps), []
        self.chat = self.completions = self

    def create(self, **kw):
        from openai.types.chat import ChatCompletion
        self.efforts.append((kw.get("extra_body") or {}).get("reasoning_effort"))
        msg = self.steps.pop(0)
        return ChatCompletion.model_validate({"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                                              "choices": [{"index": 0, "finish_reason": "stop", "message": msg}]})


def call(cid, name, args):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


DONE = {"role": "assistant", "content": "done"}


def t_correction_phrases():
    yes = ["틀렸어요", "그거 아닌데", "다시 확인해줘", "다시 계산해 봐", "잘못 계산한 것 같아", "이거 맞아?", "that's wrong",
           "다시 한번 확인해 주세요", "합계가 틀리네요", "이거 맞나요?", "그게 아니에요", "재계산해줘", "틀린 것 같아요",
           "답이 잘못됐어요", "this is incorrect", "맞는 거야?"]
    no = ["카드A 9월 합계 알려줘", "다시 한번 보여줘 표만", "틀 없이 표로 줘", "좋아요 고마워요",
          "잘못 입력된 거래 찾아줘", "틀린 bin 개수 세줘", "what went wrong in this log?", "이번엔 카드B로 다시 해줘",
          "로그에서 뭐가 잘못됐는지 봐줘", "wrong pin 에러가 몇 번 났어?", "맞춤법 검사해줘"]
    assert all(CORRECTION_RE.search(t) for t in yes), [t for t in yes if not CORRECTION_RE.search(t)]
    assert not any(CORRECTION_RE.search(t) for t in no), [t for t in no if CORRECTION_RE.search(t)]


def t_escalates_on_correction_only_after_an_answer():
    ev = []
    c = Scripted([DONE])
    run("다시 확인해줘", Context(), client=c, model="m", history=[], effort="low", emit=ev.append, log=lambda *_: None)
    effort_ev = lambda: [e for e in ev if e["type"] == "effort"]  # noqa: E731
    assert c.efforts == ["low"] and not effort_ev(), (c.efforts, ev)  # nothing to correct yet
    hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    c = Scripted([DONE])
    run("틀렸어요, 다시 확인해 주세요", Context(), client=c, model="m", history=hist, effort="low", emit=ev.append,
        log=lambda *_: None)
    assert c.efforts == ["high"] and effort_ev() == [{"type": "effort", "effort": "high", "reason": "correction"}], (c.efforts, ev)
    c = Scripted([DONE])
    run("틀렸어요", Context(), client=c, model="m", history=list(hist), effort="max", log=lambda *_: None)
    assert c.efforts == ["max"], c.efforts  # never lowered


def t_escalation_survives_resume():
    """ask_user/approval/restart resume with task=None: the correction that started the run still counts."""
    hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}, {"role": "assistant", "content": "a"},
            {"role": "user", "content": "합계가 틀리네요"}, call("q1", "current_time", {})]
    c = Scripted([DONE])
    run(None, Context(), tool_names=["current_time"], client=c, model="m", history=hist, effort="low", log=lambda *_: None)
    assert c.efforts == ["high"], c.efforts
    hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}, call("q1", "current_time", {})]
    c = Scripted([DONE])
    run(None, Context(), tool_names=["current_time"], client=c, model="m", history=hist, effort="low", log=lambda *_: None)
    assert c.efforts == ["low"], c.efforts


def t_escalates_on_tool_error_streak():
    ev = []
    bad = lambda cid: call(cid, "read_file", {"path": 1})  # noqa: E731  (argument error -> "ERROR: ...")
    c = Scripted([bad("a"), bad("b"), DONE])
    run("x", Context(), tool_names=["read_file"], client=c, model="m", history=[], effort="low", emit=ev.append,
        log=lambda *_: None)
    assert c.efforts == ["low", "low", "high"], c.efforts
    assert [e for e in ev if e["type"] == "effort"] == [{"type": "effort", "effort": "high", "reason": "tool errors"}], ev
    c = Scripted([bad("a"), call("t", "current_time", {}), bad("b"), DONE])  # not consecutive: stays low
    run("x", Context(), tool_names=["read_file", "current_time"], client=c, model="m", history=[], effort="low",
        log=lambda *_: None)
    assert c.efforts == ["low"] * 4, c.efforts


if __name__ == "__main__":
    import time
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("t_"):
            t = time.time()
            try:
                fn()
                print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed += 1
                print(f"FAIL {name} ({time.time() - t:.0f}s): {type(e).__name__}: {str(e)[:400]}")
    sys.exit(1 if failed else 0)
