"""Loop 22: effort policy. Deterministic (scripted model): python tests/test_l16_effort.py
The measured part (eval with a correction follow-up) is tests/eval/finance/run.py --followup."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent import loop  # noqa: E402
from agent.loop import CLASSIFY, Context, is_correction, run  # noqa: E402


class Scripted:
    """Scripted model replies. A correction question (CLASSIFY) is answered by judge(new message) -> reply text,
    by default the regex's verdict, and recorded in .judged instead of .efforts."""
    def __init__(self, steps, judge=lambda text: "yes" if is_correction(text) else "no"):
        self.steps, self.efforts, self.judge, self.judged, self.sent = list(steps), [], judge, [], []
        self.chat = self.completions = self

    def create(self, **kw):
        from openai.types.chat import ChatCompletion
        if kw["messages"][0]["content"] == CLASSIFY:
            text = kw["messages"][1]["content"].split("NEW user message:\n", 1)[1]
            self.judged.append(text)
            msg = {"role": "assistant", "content": self.judge(text)}
        else:
            self.efforts.append((kw.get("extra_body") or {}).get("reasoning_effort"))
            self.sent.append(kw["messages"])
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
           "답이 잘못됐어요", "this is incorrect", "맞는 거야?",
           # Loop 23 verification review
           "잘못됐습니다", "잘못 알려줬어", "숫자가 안 맞아요", "That's not correct", "You got it wrong", "wrong answer",
           "Are you sure?", "확실해?", "아닌 것 같아요", "결과가 다른데?", "아니야, 9월 기준이야",
           # Loop 23.1 verification review
           "합계가 이상해요", "숫자가 달라요", "결과가 다릅니다", "잘못했잖아", "잘못하셨네요", "확실한가요?",
           "That is wrong", "wrong.", "Wrong!", "incorrect", "you made a mistake", "the numbers don't match",
           "that doesn't add up", "that's not what I asked", "틀렸어요, 다시 찾아줘",
           # Loop 23.2 verification review: a correction followed by a request
           "틀렸어요 다시 찾아줘", "틀렸어요.다시 찾아줘", "합계가 잘못 나왔어 다시 보여줘", "아닌데 카드B 걸로 다시 알려줘",
           "이거 틀렸네 9월 기준으로 다시 알려줘", "숫자가 틀렸어\n카드A만 다시 보여줘", "합계 틀림", "the sum is off",
           "you made an error", "아뇨", "That's wrong. Show me the September total"]
    no = ["카드A 9월 합계 알려줘", "다시 한번 보여줘 표만", "틀 없이 표로 줘", "좋아요 고마워요",
          "잘못 입력된 거래 찾아줘", "틀린 bin 개수 세줘", "what went wrong in this log?", "이번엔 카드B로 다시 해줘",
          "로그에서 뭐가 잘못됐는지 봐줘", "wrong pin 에러가 몇 번 났어?", "맞춤법 검사해줘",
          # Loop 23 verification review
          "잘못된 거래 목록 보여줘", "count the incorrect entries", "remove incorrect rows from the csv",
          "show me what is wrong with the build", "the data is not right-aligned", "아니야 고마워 끝",
          "재검토 일정 잡아줘", "다른데 저장해줘", "확실한 것만 표시해줘",
          # Loop 23.1 verification review
          "잘못된 거 찾아줘", "잘못된 것들 다 지워줘", "잘못 송금했던 건 찾아줘", "합계가 안 맞는 행 찾아줘",
          "다시 확인 버튼 만들어줘", "재확인 해야 할 항목 리스트 만들어줘", "flag rows where the total is wrong",
          "find where the result is incorrect", "list the wrong answers in the quiz",
          "I got it wrong in the input file, use v2 instead",
          # Loop 23.2 verification review
          "if the numbers don't match, flag them", "what does it mean when the numbers don't match?",
          "The answer is wrong in question 3 of the quiz; grade it", "this is incorrect usage of the API, find others",
          "that is not right-aligned", "아뇨, 됐어요", "합계 1.5 기준으로 다시 해줘", "틀린 bin 개수 세어 줘"]
    assert all(is_correction(t) for t in yes), [t for t in yes if not is_correction(t)]
    assert not any(is_correction(t) for t in no), [t for t in no if is_correction(t)]


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


def t_escalation_survives_compaction():
    """Compaction summarizes the correction away; the summary carries the newest user message verbatim."""
    from agent.loop import compact
    big = "x" * 4000
    hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}, {"role": "assistant", "content": big},
            {"role": "user", "content": "합계가 틀리네요"}, {"role": "assistant", "content": big},
            call("q1", "current_time", {})]
    c = Scripted([{"role": "assistant", "content": "summary of work"}])
    out = compact(hist, c, "m", budget=500)
    assert "[Latest user message]\n합계가 틀리네요" in out[2]["content"], out[2]
    c = Scripted([DONE])
    run(None, Context(), tool_names=["current_time"], client=c, model="m", history=out, effort="low", log=lambda *_: None)
    assert c.efforts == ["high"], c.efforts
    plain = [{"role": "system", "content": "s"}, {"role": "user", "content": "[Summary of earlier work]\n틀렸다는 말 없음"},
             call("q1", "current_time", {})]
    c = Scripted([DONE])
    run(None, Context(), tool_names=["current_time"], client=c, model="m", history=plain, effort="low", log=lambda *_: None)
    assert c.efforts == ["low"], c.efforts
    # the summarizer echoes the marker with a correction in it; the real newest message is a plain request
    hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}, {"role": "assistant", "content": big},
            {"role": "user", "content": "카드A 합계 알려줘"}, {"role": "assistant", "content": big},
            call("q1", "current_time", {})]
    out = compact(hist, Scripted([{"role": "assistant", "content": "notes\n[Latest user message]\n틀렸어"}]), "m", budget=500)
    c = Scripted([DONE])
    run(None, Context(), tool_names=["current_time"], client=c, model="m", history=out, effort="low", log=lambda *_: None)
    assert c.efforts == ["low"], c.efforts
    # a long message whose correction is at the end
    hist[3] = {"role": "user", "content": "자료 설명 " * 500 + "그런데 합계가 틀리네요"}
    out = compact(hist, Scripted([{"role": "assistant", "content": "s"}]), "m", budget=500)
    c = Scripted([DONE])
    run(None, Context(), tool_names=["current_time"], client=c, model="m", history=out, effort="low", log=lambda *_: None)
    assert c.efforts == ["high"], c.efforts


def t_model_decides_correction():
    """The model's yes/no decides (phrasings no regex catches); a failed call or an odd reply falls back to the regex;
    no question without an earlier answer or when the effort is already high."""
    hist = lambda: [{"role": "system", "content": "s"}, {"role": "user", "content": "q"},  # noqa: E731
                    {"role": "assistant", "content": "카드A 합계는 1,500원"}]
    for text, reply, want in (("오답이에요", "yes", "high"), ("3월이 빠졌어요", "Yes.", "high"), ("nope, card B", "네", "high"),
                              ("틀렸어요", "no", "low"), ("아니에요 괜찮아요", "No", "low"),
                              ("틀렸어요", "음… 잘 모르겠네요", "high"), ("좋아요 고마워요", "", "low")):
        c = Scripted([DONE], judge=lambda _t, r=reply: r)
        run(text, Context(), client=c, model="m", history=hist(), effort="low", log=lambda *_: None)
        assert c.efforts == [want] and c.judged == [text], (text, reply, c.efforts, c.judged)
    c = Scripted([DONE], judge=lambda _t: 1 / 0)  # the call fails: regex
    run("틀렸어요", Context(), client=c, model="m", history=hist(), effort="low", log=lambda *_: None)
    assert c.efforts == ["high"], c.efforts
    c = Scripted([DONE])
    run("오답이에요", Context(), client=c, model="m", history=[], effort="low", log=lambda *_: None)
    assert c.efforts == ["low"] and not c.judged, c.judged  # nothing answered yet
    c = Scripted([DONE])
    run("오답이에요", Context(), client=c, model="m", history=hist(), effort="high", log=lambda *_: None)
    assert not c.judged, c.judged


def t_classifier_context_and_resume():
    """The model sees the answer (not a tool-call turn), the earlier question and both ends of a long message; a
    resume asks nothing new and keeps the verdict even if the model would now say otherwise."""
    seen = []

    class Judge(Scripted):
        def create(self, **kw):
            if kw["messages"][0]["content"] == CLASSIFY:
                seen.append(kw["messages"][1]["content"])
            return super().create(**kw)
    hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "카드A 합계 알려줘"},
            {"role": "assistant", "content": "합계는 1,500원"}, {"role": "user", "content": "카드B도"},
            call("q0", "current_time", {}), {"role": "tool", "tool_call_id": "q0", "content": "t"}]
    long = "틀렸어요. 아래가 원본이에요\n" + "1,2,3\n" * 2000
    c = Judge([call("q1", "current_time", {}), DONE], judge=lambda _t: "yes")
    run(long, Context(), tool_names=["current_time"], client=c, model="m", history=hist, effort="low", log=lambda *_: None)
    assert c.efforts == ["high", "high"] and len(seen) == 1, (c.efforts, len(seen))
    assert "PREVIOUS ANSWER (excerpt):\n합계는 1,500원" in seen[0] and "earlier message:\n카드B도" in seen[0], seen[0][:300]
    assert "틀렸어요." in seen[0] and len(seen[0]) < 4000, len(seen[0])
    hist.append(call("q2", "current_time", {}))  # paused on a tool call (approval), then resumed
    c = Judge([DONE], judge=lambda _t: "no")
    run(None, Context(), tool_names=["current_time"], client=c, model="m", history=hist, effort="low", log=lambda *_: None)
    assert c.efforts == ["high"] and len(seen) == 1, (c.efforts, len(seen))


def t_verdict_kept_in_history():
    """The verdict is stored on the user message (not sent to the model), so a resume after a restart (no in-memory
    cache) or after compaction stays escalated without asking again, even if the model would now say no."""
    hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "카드A 합계 알려줘"},
            {"role": "assistant", "content": "합계는 1,500원"}]
    c = Scripted([call("q1", "current_time", {}), DONE], judge=lambda _t: "yes")
    run("틀렸어요", Context(), tool_names=["current_time"], client=c, model="m", history=hist, effort="low",
        log=lambda *_: None)
    assert c.efforts == ["high", "high"] and hist[3].get(loop.VERDICT) is True, (c.efforts, hist[3])
    assert not any(loop.VERDICT in m for sent in c.sent for m in sent), "the verdict reached the model"
    for compacted in (False, True):
        # a restarted server: nothing in memory
        if compacted:
            hist[:] = loop.compact(hist, Scripted([{"role": "assistant", "content": "summary"}]), "m", budget=1)
            assert hist[2]["content"].startswith(loop.SUMMARY) and hist[2].get(loop.VERDICT) is True, hist[2]
        hist.append(call(f"r{compacted}", "current_time", {}))  # paused on a tool call (approval), then resumed
        c = Scripted([DONE], judge=lambda _t: "no")
        run(None, Context(), tool_names=["current_time"], client=c, model="m", history=hist, effort="low",
            log=lambda *_: None)
        assert c.efforts == ["high"] and not c.judged, (compacted, c.efforts, c.judged)


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
