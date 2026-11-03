"""Loop 36: markdown in the model's answers (server/static/md.js). Rendered in the real page (Chrome): the block and
inline forms answers use, Korean right after emphasis, and that nothing in an answer becomes HTML or a script link.
No LLM; starts the app on :8306 and the renderer on :8307.
python tests/test_l28_markdown.py [test_name ...]"""
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_l22_layout import APP, PW, servers  # noqa: E402

CASES = [
    ("**굵게**는 *기울임*, ~~취소~~, `x<y`", "<p><strong>굵게</strong>는 <em>기울임</em>, <del>취소</del>, <code>x&lt;y</code></p>"),
    ('**"값"**은 1.0이고 **(단위: V)**를', '<p><strong>"값"</strong>은 1.0이고 <strong>(단위: V)</strong>를</p>'),
    ("첫 줄\n둘째 줄\n\n새 문단", "<p>첫 줄<br>둘째 줄</p><p>새 문단</p>"),
    ("설명:\n- a\n- b\n  - c\n1. x", "<p>설명:</p><ul><li>a</li><li>b<ul><li>c</li></ul></li></ul><ol><li>x</li></ol>"),
    ("3. 셋\n4. 넷", '<ol start="3"><li>셋</li><li>넷</li></ol>'),
    ("- [x] 끝\n- [ ] 남음", '<ul><li class="task"><input type="checkbox" disabled="">끝</li>'
                           '<li class="task"><input type="checkbox" disabled="">남음</li></ul>'),
    ("## 제목 ##\n---\n> 인용\n> 둘", "<h2>제목</h2><hr><blockquote><p>인용<br>둘</p></blockquote>"),
    ("| 항목 | 값 |\n|---|--:|\n| a | `x|y` |\n| b |", '<div class="table"><div class="md-bar"><span>표 · 2행</span><button type="button">표 복사</button></div><div class="table-wrap"><table><thead><tr><th>항목</th>'
     '<th style="text-align: right;">값</th></tr></thead><tbody><tr><td>a</td><td style="text-align: right;">'
     '<code>x|y</code></td></tr><tr><td>b</td><td style="text-align: right;"></td></tr></tbody></table></div></div>'),
    ("결과:\n| a | b |\n|---|---|\n| 1 | 2 |", '<p>결과:</p><div class="table"><div class="md-bar"><span>표 · 1행</span><button type="button">표 복사</button></div><div class="table-wrap"><table><thead><tr><th>a</th><th>b</th>'
     '</tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table></div></div>'),
    # left as written: snake_case, arithmetic, an unclosed mark, escapes
    ("file_name_here, 2 * 3 * 4, **열림, \\*별\\*", "<p>file_name_here, 2 * 3 * 4, **열림, *별*</p>"),
    ("*a **b** c*", "<p><em>a <strong>b</strong> c</em></p>"),
    # links: http(s) only, bare links without the sentence's last mark
    ("[문서](https://example.com/a_(b)) 그리고 https://example.com/x_y.", '<p><a href="https://example.com/a_(b)" '
     'target="_blank" rel="noopener noreferrer">문서</a> 그리고 <a href="https://example.com/x_y" target="_blank" '
     'rel="noopener noreferrer">https://example.com/x_y</a>.</p>'),
    # never HTML, never a script link
    ("<img src=x onerror=alert(1)> <script>alert(1)</script>",
     "<p>&lt;img src=x onerror=alert(1)&gt; &lt;script&gt;alert(1)&lt;/script&gt;</p>"),
    ("[누르기](javascript:alert(1)) [b](data:text/html,x) [c]( JAVASCRIPT:x)", "<p>누르기 b c</p>"),
    # from the review: headings with #, ***both***, dates, a particle after a link, a one-line fence, links in links,
    # a sentence with | after a table
    ("# C#\n## 제목 ##\n#해시태그", "<h1>C#</h1><h2>제목</h2><p>#해시태그</p>"),
    ("***굵고 기울임*** 텍스트", "<p><em><strong>굵고 기울임</strong></em> 텍스트</p>"),
    ("회의는 다음과 같다.\n2024. 11. 4. 기준으로 진행\n2. 둘째", "<p>회의는 다음과 같다.<br>2024. 11. 4. 기준으로 진행<br>2. 둘째</p>"),
    ("https://a.com/경로에서 확인", '<p><a href="https://a.com/" target="_blank" rel="noopener noreferrer">'
     'https://a.com/</a>경로에서 확인</p>'),
    ("```bash npm i```\n다음 문단", "<p><code>bash npm i</code><br>다음 문단</p>"),
    ("[https://a.com](https://b.com)", '<p><a href="https://b.com" target="_blank" rel="noopener noreferrer">https://a.com</a></p>'),
    ("| a | b |\n|---|---|\n| 1 | 2 |\n설명: a|b", '<div class="table"><div class="md-bar"><span>표 · 1행</span><button '
     'type="button">표 복사</button></div><div class="table-wrap"><table><thead><tr><th>a</th><th>b</th></tr></thead>'
     '<tbody><tr><td>1</td><td>2</td></tr></tbody></table></div></div><p>설명: a|b</p>'),
    ("**[굵은 링크](https://a.b)**", '<p><strong><a href="https://a.b" target="_blank" rel="noopener noreferrer">굵은 링크</a></strong></p>'),
]
CODE = "```python\nprint('<b>')\n\n  x = 1\n```\n뒤 문장"


def t_markdown():
    from playwright.sync_api import sync_playwright
    procs = servers()
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(channel="chrome", headless=True)
            ctx = br.new_context(viewport={"width": 1200, "height": 800})
            ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=APP)
            page = ctx.new_page()
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            console = []
            page.on("console", lambda m: console.append(m.text))
            page.on("dialog", lambda d: (errs.append("dialog " + d.message), d.dismiss()))
            page.goto(APP + "/")
            page.fill("#login-user", "alice")
            page.fill("#login-password", PW)
            page.click("#login button")
            page.wait_for_selector("#artifacts li")
            html = lambda src: page.evaluate("s => { const d = document.createElement('div'); d.append(MD.render(s)); return d.innerHTML }", src)
            bad = [(src, got, want) for src, want in CASES if (got := html(src)) != want]
            assert not bad, "\n".join(f"{s!r}\n  got  {g}\n  want {w}" for s, g, w in bad)

            # a code block keeps its text as is (indent, blank line, <b>) and copies it
            page.evaluate("s => addMsg('assistant', s)", CODE)
            box = page.locator("#messages .msg.assistant.md").last
            assert box.locator(".md-bar span").inner_text() == "python"
            code = box.locator("pre code").evaluate("e => e.textContent")
            assert code == "print('<b>')\n\n  x = 1", repr(code)
            assert box.locator("p").inner_text() == "뒤 문장"
            box.locator(".md-bar button").click()
            page.wait_for_selector("#messages .md-bar button:text('복사됨')")
            assert page.evaluate("navigator.clipboard.readText()") == code
            page.wait_for_selector("#messages .md-bar button:text('복사됨')", state="detached")
            # a table copies for Excel: an HTML table (codes with leading zeros kept as text) and tab-separated text
            page.evaluate("s => addMsg('assistant', s)", "| 코드 | 이름 | 값 |\n|---|---|--:|\n| 007 | **굵은** a\tb | 1.5 |\n| 12 | `x|y` | |")
            tb = page.locator("#messages .msg.assistant.md").last
            assert tb.locator(".md-bar span").inner_text() == "표 · 2행"
            tb.locator(".md-bar button").click()
            page.wait_for_selector("#messages .md-bar button:text('복사됨')")
            clip = page.evaluate("""async () => { const [it] = await navigator.clipboard.read();
                const [t, h] = await Promise.all([it.getType('text/plain'), it.getType('text/html')]);
                return { tsv: await t.text(), html: await h.text() }; }""")
            assert clip["tsv"] == "코드\t이름\t값\r\n007\t굵은 a b\t1.5\r\n12\tx|y\t", repr(clip["tsv"])
            assert "<th>코드</th>" in clip["html"] and "mso-number-format" in clip["html"].split("007")[0].rsplit("<td", 1)[1], clip["html"]
            assert clip["html"].count("mso-number-format") == 1 and "<b>" not in clip["html"] and "<strong>" not in clip["html"], clip["html"]
            # what the user typed and errors stay plain text
            page.evaluate("addMsg('user', '**그대로**'); addMsg('error', '<b>x</b>')")
            assert page.locator("#messages .msg.user").last.evaluate("e => e.innerHTML") == "**그대로**"
            assert page.locator("#messages .msg.error").last.evaluate("e => e.innerHTML") == "&lt;b&gt;x&lt;/b&gt;"
            # a wide table scrolls in its own box, not the chat
            page.evaluate("s => addMsg('assistant', s)", "| " + " | ".join(f"열{i}" for i in range(40)) + " |\n|"
                          + "---|" * 40 + "\n| " + " | ".join("값" * 6 for _ in range(40)) + " |")
            assert page.eval_on_selector("#messages", "e => e.scrollWidth <= e.clientWidth")
            assert page.eval_on_selector("#messages .table-wrap >> nth=-1", "e => e.scrollWidth > e.clientWidth")
            # degenerate answers (a model repeating itself): no exception, no frozen page
            for src in ["- " * 50000, "1. " * 30000, ">" * 100000 + " x", "*a " * 33000, "**a " * 25000, "~~a " * 25000,
                        "[" * 100000, "*a" + " *b" * 33000, "# a" + " " * 100000 + "b", "[a" * 50000, "`a " * 33000,
                        ("- x\n  " * 2000), ("  " * 3000 + "- x\n") * 30]:
                t = time.time()
                n = page.evaluate("s => MD.render(s).childNodes.length", src)
                assert n >= 1 and time.time() - t < 1.5, (src[:20], len(src), time.time() - t)
            # and if rendering ever throws, the message shows as text and the history after it still draws
            page.evaluate("""() => { const r = MD.render; MD.render = () => { throw new Error('x') };
                addMsg('assistant', '**plain**'); MD.render = r; addMsg('assistant', '**after**'); }""")
            assert page.locator("#messages .msg.assistant").nth(-2).inner_text() == "**plain**"
            assert page.locator("#messages .msg.assistant strong").last.inner_text() == "after"
            # narrow window: a long code line scrolls in its block, the chat does not scroll sideways
            page.set_viewport_size({"width": 700, "height": 800})
            page.evaluate("s => addMsg('assistant', s)", "```\n" + "x" * 400 + "\n```")
            assert page.eval_on_selector("#messages", "e => e.scrollWidth <= e.clientWidth"), \
                page.eval_on_selector("#messages", "e => [e.scrollWidth, e.clientWidth]")
            assert not errs, errs
            # one CSP line is expected: Chrome parses the copied HTML table on the page while writing it to the clipboard
            # and reports its style attribute (the mso text format), which still reaches the clipboard (checked above)
            assert len([c for c in console if "Content Security Policy" in c]) <= 1, console
            br.close()
    finally:
        for p in procs:
            p.send_signal(signal.SIGKILL); p.wait()


if __name__ == "__main__":
    only, failed = sys.argv[1:], []
    for name, fn in list(globals().items()):
        if name.startswith("t_") and (not only or name in only):
            try:
                t = time.time(); fn(); print(f"PASS {name} ({time.time() - t:.0f}s)")
            except Exception as e:
                failed.append(name)
                print("FAIL", name, repr(e)[:3000])
    sys.exit(1 if failed else 0)
