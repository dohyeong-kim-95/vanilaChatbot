// Minimal chat UI (Loop 14). Model/tool text is only ever inserted as text (never innerHTML); artifact content
// runs in a sandboxed iframe on the renderer's origin (see server/render.py).
"use strict";

const $ = (id) => document.getElementById(id);
const state = { user: "", project: "", session: "", artifact: null, streaming: false };

function store(key, value) {
  try { value === undefined ? (value = localStorage.getItem(key)) : localStorage.setItem(key, value); } catch (e) { value = null; }
  return value;
}

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "X-User": state.user, "Content-Type": "application/json", ...(opts.headers || {}) } });
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
  return r.headers.get("content-type")?.includes("json") ? r.json() : r;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function scrollDown() { const m = $("messages"); m.scrollTop = m.scrollHeight; }

function addMsg(role, text) {
  $("messages").append(el("div", `msg ${role}`, text));
  scrollDown();
}

function addArtifactRef(id, version, title) {
  const b = el("button", "artifact-ref");
  b.append(el("span", null, title || id), el("span", "meta", version ? `v${version}` : "아티팩트"));
  b.onclick = () => openArtifact(id, version);
  $("messages").append(b);
  scrollDown();
}

// ---- sidebar ------------------------------------------------------------------------------------
async function loadProjects() {
  const sel = $("project");
  sel.replaceChildren();
  if (!state.user) return;
  const { projects } = await api("/projects");
  for (const p of projects) sel.append(new Option(p, p));
  state.project = projects.includes(store("project")) ? store("project") : (projects[0] || "");
  sel.value = state.project;
  await loadProject();
}

async function loadProject() {
  $("sessions").replaceChildren();
  $("artifacts").replaceChildren();
  $("messages").replaceChildren();
  state.session = "";
  if (!state.project) return;
  store("project", state.project);
  const { sessions } = await api(`/projects/${state.project}/sessions`);
  for (const s of sessions) {
    const li = el("li", null, s);
    li.onclick = () => openSession(s);
    li.dataset.id = s;
    $("sessions").append(li);
  }
  await loadArtifacts();
  if (sessions.length) await openSession(sessions[0]);
}

async function loadArtifacts() {
  const { artifacts } = await api(`/projects/${state.project}/artifacts`);
  const ul = $("artifacts");
  ul.replaceChildren();
  for (const a of artifacts) {
    const li = el("li", null, a.title);
    li.append(el("span", "meta", `v${a.version}`));
    li.title = `${a.id} (${a.type})`;
    li.dataset.id = a.id;
    li.onclick = () => openArtifact(a.id);
    ul.append(li);
  }
  markActive();
}

function markActive() {
  for (const li of $("sessions").children) li.classList.toggle("active", li.dataset.id === state.session);
  for (const li of $("artifacts").children) li.classList.toggle("active", li.dataset.id === state.artifact?.id);
}

async function openSession(sid) {
  state.session = sid;
  markActive();
  const s = await api(`/sessions/${sid}`);
  $("messages").replaceChildren();
  for (const m of s.messages) m.role === "artifact" ? addArtifactRef(m.id) : addMsg(m.role, m.content);
  if (s.active_run) follow(s.active_run.run_id);
}

// ---- chat -----------------------------------------------------------------------------------------
async function send(text) {
  if (!state.session) {
    const { session_id } = await api("/sessions", { method: "POST", body: JSON.stringify({ project_id: state.project }) });
    await loadProject();
    await openSession(session_id);
  }
  addMsg("user", text);
  const { run_id } = await api(`/sessions/${state.session}/messages`, { method: "POST", body: JSON.stringify({ content: text }) });
  await follow(run_id);
}

// SSE over fetch (EventSource cannot send the X-User header). Resumes with Last-Event-ID.
async function follow(rid, after = 0) {
  state.streaming = true;
  $("send").disabled = true;
  const status = el("div", "tool", "작업 중…");
  $("messages").append(status);
  try {
    const r = await api(`/runs/${rid}/events`, { headers: { "Last-Event-ID": String(after) } });
    const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += value;
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const id = block.match(/^id: (\d+)/m);
        const data = block.match(/^data: (.*)$/m);
        if (id) after = Number(id[1]);
        if (data) onEvent(rid, JSON.parse(data[1]), after, status);
      }
    }
  } catch (e) {
    addMsg("error", String(e));
  } finally {
    status.remove();
    state.streaming = false;
    $("send").disabled = false;
  }
}

function onEvent(rid, ev, seq, status) {
  switch (ev.type) {
    case "text": case "final":
      if (ev.content) addMsg("assistant", ev.content);
      break;
    case "tool_call": {
      let name = ev.name;
      try { if (ev.name === "bash") name += ": " + JSON.parse(ev.arguments).command.split("\n")[0].slice(0, 80); } catch (e) {}
      status.textContent = name;
      break;
    }
    case "artifact":
      addArtifactRef(ev.id, ev.version, ev.title);
      loadArtifacts();
      openArtifact(ev.id, ev.version);
      break;
    case "approval_request": showApproval(rid, ev, seq); break;
    case "warning": case "error": addMsg("error", ev.message); break;
  }
}

function showApproval(rid, ev, seq) {
  const box = el("div", "approval");
  box.append(el("div", null, `승인 필요: ${ev.tool}`), el("pre", null, JSON.stringify(ev.arguments, null, 2)));
  const row = el("div", "row");
  const yes = el("button", "primary", "승인");
  const no = el("button", null, "거절");
  const decide = async (approved) => {
    yes.disabled = no.disabled = true;
    await api(`/runs/${rid}/approval`, { method: "POST", body: JSON.stringify({ approved }) });
    box.replaceChildren(el("div", "tool", `${ev.tool}: ${approved ? "승인함" : "거절함"}`));
    follow(rid, seq);
  };
  yes.onclick = () => decide(true);
  no.onclick = () => decide(false);
  row.append(yes, no);
  box.append(row);
  $("messages").append(box);
  scrollDown();
}

// ---- artifact panel -------------------------------------------------------------------------------
// The iframe attributes are the isolation contract: scripts yes, same-origin/forms/popups/top-navigation no.
function showFrame(url) {
  const f = document.createElement("iframe");
  f.setAttribute("sandbox", "allow-scripts");
  f.setAttribute("referrerpolicy", "no-referrer");
  f.setAttribute("allow", "");
  f.src = url;
  $("frame-wrap").replaceChildren(f);
  return f;
}

async function openArtifact(id, version) {
  const a = await api(`/projects/${state.project}/artifacts/${id}` + (version ? `?version=${version}` : ""));
  state.artifact = a;
  $("app").classList.add("with-panel");
  $("panel").hidden = false;
  $("art-title").textContent = a.title;
  $("art-title").title = `${a.id} · ${a.type}`;
  const sel = $("art-version");
  sel.replaceChildren(...a.versions.map((v) => new Option(`v${v}`, v)));
  sel.value = a.version;
  $("art-code").textContent = a.content;
  $("art-code").hidden = true;
  $("frame-wrap").hidden = false;
  $("art-source").textContent = "코드";
  showFrame(a.view_url);
  showSections(a);
  markActive();
}

// Reports: each section as an image (copy to clipboard or save), for pasting into a document.
function showSections(a) {
  const bar = $("art-sections");
  bar.replaceChildren();
  bar.hidden = !a.sections;
  if (!a.sections) return;
  const base = `/projects/${state.project}/artifacts/${a.id}/sections`;
  const png = async (sec) => (await api(`${base}/${sec}.png?version=${a.version}`)).blob();
  for (const s of [...a.sections, { id: "all", title: "전체" }]) {
    const g = el("div", "sec");
    const copy = el("button", null, "복사");
    const save = el("button", null, "저장");
    copy.title = `${s.title} 이미지를 클립보드에 복사`;
    save.title = `${s.title} 이미지를 PNG로 저장`;
    copy.onclick = async () => {
      copy.disabled = true;
      try {
        // pass a promise: Safari needs the ClipboardItem created inside the click
        await navigator.clipboard.write([new ClipboardItem({ "image/png": png(s.id) })]);
        copy.textContent = "복사됨";
      } catch (e) {
        copy.textContent = "실패";
        addMsg("error", `이미지 복사 실패: ${e}`);
      }
      setTimeout(() => { copy.textContent = "복사"; copy.disabled = false; }, 1500);
    };
    save.onclick = async () => {
      const url = URL.createObjectURL(await png(s.id));
      const link = el("a");
      link.href = url;
      link.download = `${a.id}-v${a.version}-${s.id}.png`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    g.append(el("span", null, s.title), copy, save);
    bar.append(g);
  }
}

// ---- wiring ---------------------------------------------------------------------------------------
$("user").value = state.user = store("user") || "";
$("user").onchange = async () => {
  state.user = $("user").value.trim();
  store("user", state.user);
  await loadProjects().catch((e) => addMsg("error", String(e)));
};
$("project").onchange = () => { state.project = $("project").value; loadProject(); };
$("new-project").onclick = async () => {
  const id = prompt("프로젝트 id (소문자, 숫자, -, _)");
  if (!id) return;
  await api("/projects", { method: "POST", body: JSON.stringify({ project_id: id }) }).catch((e) => addMsg("error", String(e)));
  store("project", id);
  await loadProjects();
};
$("new-session").onclick = () => { state.session = ""; $("messages").replaceChildren(); markActive(); $("input").focus(); };
$("composer").onsubmit = (e) => {
  e.preventDefault();
  const text = $("input").value.trim();
  if (!text || state.streaming || !state.project) return;
  $("input").value = "";
  send(text).catch((err) => addMsg("error", String(err)));
};
$("input").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
};
$("art-version").onchange = () => openArtifact(state.artifact.id, $("art-version").value);
$("art-source").onclick = () => {
  const code = $("art-code");
  code.hidden = !code.hidden;
  $("frame-wrap").hidden = !code.hidden;
  $("art-source").textContent = code.hidden ? "코드" : "미리보기";
};
$("art-close").onclick = () => {
  $("panel").hidden = true;
  $("app").classList.remove("with-panel");
  state.artifact = null;
  markActive();
};
if (state.user) loadProjects().catch((e) => addMsg("error", String(e)));
