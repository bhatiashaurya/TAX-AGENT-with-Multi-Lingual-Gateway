/* Tax Agent — chat frontend (vanilla JS, no build step). */
"use strict";

const API = location.protocol.startsWith("http") ? location.origin : "http://127.0.0.1:8080";
const $ = (id) => document.getElementById(id);

/* ---------- markdown rendering ---------- */
marked.setOptions({ gfm: true, breaks: true });
function renderMarkdown(text) {
  const html = marked.parse(text || "");
  return DOMPurify.sanitize(html);
}
function highlightIn(el) {
  el.querySelectorAll("pre code").forEach((block) => {
    try { hljs.highlightElement(block); } catch (_) {}
  });
}

/* ---------- state ---------- */
let state = {
  conversationId: null,
  streaming: false,
  abort: null,
  attachments: [],   // {file, name}
  recognizing: false,
};

/* ---------- helpers ---------- */
function toast(msg) {
  const t = $("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 3200);
}
function scrollDown() {
  const thread = $("thread");
  thread.scrollTop = thread.scrollHeight;
}
function icon(paths) {
  return `<svg viewBox="0 0 24 24" class="ic">${paths}</svg>`;
}
const IC = {
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 012-2h10"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/>',
  regen: '<path d="M4 4v6h6M20 20v-6h-6"/><path d="M20 8a8 8 0 00-14-3M4 16a8 8 0 0014 3"/>',
  del: '<path d="M4 7h16M9 7V5a1 1 0 011-1h4a1 1 0 011 1v2M7 7l1 13h8l1-13"/>',
  check: '<path d="M5 13l4 4L19 7"/>',
};

/* ---------- conversations sidebar ---------- */
async function loadConversations() {
  try {
    const r = await fetch(`${API}/api/chat/conversations`);
    const { conversations } = await r.json();
    const list = $("convList");
    if (!conversations.length) {
      list.innerHTML = `<div class="conv-empty">No conversations yet</div>`;
      return;
    }
    list.innerHTML = conversations.map((c) => `
      <div class="conv ${c.id === state.conversationId ? "active" : ""}" data-id="${c.id}">
        <span class="conv-title">${escapeHtml(c.title)}</span>
        <button class="conv-del" data-del="${c.id}" title="Delete">${icon(IC.del)}</button>
      </div>`).join("");
    list.querySelectorAll(".conv").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-del]")) return;
        openConversation(el.dataset.id);
      });
    });
    list.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", (e) => { e.stopPropagation(); deleteConversation(b.dataset.del); }));
  } catch (_) { /* backend may still be booting */ }
}

async function deleteConversation(id) {
  await fetch(`${API}/api/chat/conversations/${id}`, { method: "DELETE" });
  if (id === state.conversationId) newChat();
  loadConversations();
}

async function openConversation(id) {
  const r = await fetch(`${API}/api/chat/conversations/${id}`);
  if (!r.ok) return;
  const conv = await r.json();
  state.conversationId = conv.id;
  $("welcome").hidden = true;
  const box = $("messages");
  box.innerHTML = "";
  for (const m of conv.messages) {
    const el = addMessage(m.role, m.content, { persistActions: true });
    if (m.role === "assistant") {
      finalizeAssistant(el, { citations: m.citations, confidence: m.confidence, refused: m.meta && m.meta.refused });
    }
  }
  loadConversations();
  scrollDown();
}

function newChat() {
  state.conversationId = null;
  $("messages").innerHTML = "";
  $("welcome").hidden = false;
  loadConversations();
  $("input").focus();
}

/* ---------- message rendering ---------- */
function addMessage(role, content, opts = {}) {
  const box = $("messages");
  const msg = document.createElement("div");
  msg.className = `msg ${role}`;
  const label = role === "user" ? "You" : "Tax Agent";
  const avatar = role === "user" ? "U" : "₹";
  if (role === "user") {
    msg.innerHTML = `
      <div class="avatar">${avatar}</div>
      <div class="msg-body">
        <div class="msg-role">${label}</div>
        <div class="bubble">${escapeHtml(content)}</div>
        <div class="msg-actions"></div>
      </div>`;
  } else {
    msg.innerHTML = `
      <div class="avatar">${avatar}</div>
      <div class="msg-body">
        <div class="msg-role">${label}</div>
        <div class="prose"></div>
        <div class="tool-slot"></div>
        <div class="notice-slot"></div>
        <div class="sources-slot"></div>
        <div class="msg-actions"></div>
      </div>`;
    if (content) {
      const prose = msg.querySelector(".prose");
      prose.innerHTML = renderMarkdown(content);
      highlightIn(prose);
    }
  }
  box.appendChild(msg);
  if (role === "user") attachUserActions(msg, content);
  return msg;
}

function attachUserActions(msg, content) {
  const actions = msg.querySelector(".msg-actions");
  actions.innerHTML = `<button class="act-btn" data-act="edit">${icon(IC.edit)} Edit</button>`;
  actions.querySelector("[data-act=edit]").addEventListener("click", () => startEdit(msg, content));
}

function startEdit(msg, content) {
  const body = msg.querySelector(".msg-body");
  const bubble = msg.querySelector(".bubble");
  const actions = msg.querySelector(".msg-actions");
  bubble.hidden = true; actions.hidden = true;
  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <textarea class="edit-area">${escapeHtml(content)}</textarea>
    <div class="edit-actions">
      <button class="send-btn" style="width:auto;padding:0 14px;border-radius:8px;" data-save>Save &amp; submit</button>
      <button class="act-btn" data-cancel>Cancel</button>
    </div>`;
  body.insertBefore(wrap, actions);
  const ta = wrap.querySelector("textarea");
  ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
  wrap.querySelector("[data-cancel]").addEventListener("click", () => {
    wrap.remove(); bubble.hidden = false; actions.hidden = false;
  });
  wrap.querySelector("[data-save]").addEventListener("click", async () => {
    const newText = ta.value.trim();
    if (!newText) return;
    const messageId = findMessageId(msg);
    // remove this and all following messages from the DOM
    let n = msg;
    while (n.nextSibling) n.nextSibling.remove();
    wrap.remove(); bubble.textContent = newText; bubble.hidden = false; actions.hidden = false;
    await streamRequest("/api/chat/edit", { conversation_id: state.conversationId, message_id: messageId, message: newText }, { newUserText: null });
  });
}

function finalizeAssistant(msg, { citations, confidence, refused }) {
  if (refused) msg.classList.add("refused");
  if (citations && citations.length) renderSources(msg, citations, confidence);
  const actions = msg.querySelector(".msg-actions");
  actions.innerHTML = `
    <button class="act-btn" data-act="copy">${icon(IC.copy)} Copy</button>
    <button class="act-btn" data-act="regen">${icon(IC.regen)} Regenerate</button>`;
  actions.querySelector("[data-act=copy]").addEventListener("click", (e) => {
    const text = msg.querySelector(".prose").innerText;
    navigator.clipboard.writeText(text).then(() => {
      e.currentTarget.innerHTML = `${icon(IC.check)} Copied`;
      setTimeout(() => (e.currentTarget.innerHTML = `${icon(IC.copy)} Copy`), 1500);
    });
  });
  actions.querySelector("[data-act=regen]").addEventListener("click", () => {
    if (state.streaming) return;
    const messageId = findMessageId(msg);
    msg.remove();
    streamRequest("/api/chat/regenerate", { conversation_id: state.conversationId, message_id: messageId }, { newUserText: null });
  });
}

function renderSources(msg, citations, confidence) {
  const slot = msg.querySelector(".sources-slot");
  const confHtml = (confidence != null)
    ? `<span class="conf-pill ${confidence < 0.6 ? "low" : ""}">${Math.round(confidence * 100)}% confidence</span>`
    : "";
  slot.innerHTML = `
    <details class="sources">
      <summary>${citations.length} source${citations.length > 1 ? "s" : ""} ${confHtml}</summary>
      ${citations.map((c) => `
        <div class="source-item">
          <div class="src-title">[${c.index}] ${escapeHtml(c.source)}${c.section ? " · " + escapeHtml(c.section) : ""}</div>
          <div class="src-snip">${escapeHtml(c.snippet || "")}</div>
        </div>`).join("")}
    </details>`;
}

/* map a DOM message element to its stored message id (position-based lookup) */
function findMessageId(msgEl) { return msgEl.dataset.mid || null; }

/* ---------- send + streaming ---------- */
async function send() {
  if (state.streaming) return;
  const input = $("input");
  const text = input.value.trim();
  if (!text && !state.attachments.length) return;

  $("welcome").hidden = true;
  addMessage("user", text || "(document)");
  input.value = ""; autoGrow();

  const form = new FormData();
  form.append("message", text || "Please analyse the attached document.");
  if (state.conversationId) form.append("conversation_id", state.conversationId);
  for (const a of state.attachments) form.append("files", a.file, a.name);
  clearAttachments();

  await streamRequest("/api/chat/stream", form, { isForm: true });
}

async function streamRequest(path, payload, opts = {}) {
  setStreaming(true);
  const assistant = addMessage("assistant", "");
  const prose = assistant.querySelector(".prose");
  const cursor = document.createElement("span");
  cursor.className = "cursor";
  prose.appendChild(cursor);
  scrollDown();

  let acc = "";
  let citations = null, confidence = null, refused = false, doneMeta = {};
  const controller = new AbortController();
  state.abort = controller;

  let body, headers = {};
  if (opts.isForm) { body = payload; }
  else { body = new URLSearchParams(payload); headers = { "Content-Type": "application/x-www-form-urlencoded" }; }

  let render = () => {
    prose.innerHTML = renderMarkdown(acc);
    prose.appendChild(cursor);
    highlightIn(prose);
    scrollDown();
  };
  let scheduled = false;
  const scheduleRender = () => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; render(); });
  };

  try {
    const resp = await fetch(API + path, { method: "POST", body, headers, signal: controller.signal });
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buffer.indexOf("\n\n")) >= 0) {
        const rawEvent = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        for (const line of rawEvent.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          let ev;
          try { ev = JSON.parse(line.slice(6)); } catch { continue; }
          handleEvent(ev);
        }
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") {
      acc += `\n\n_Connection error: ${err.message}. Please try again._`;
    }
  }

  function handleEvent(ev) {
    switch (ev.type) {
      case "conversation":
        state.conversationId = ev.id; break;
      case "start":
        assistant.dataset.mid = ""; break;
      case "notice":
        addNotice(assistant, ev.text); break;
      case "retrieval":
        break; // sources shown on citations event
      case "tool_use":
        renderToolCard(assistant, ev); break;
      case "text":
        acc += ev.text; scheduleRender(); break;
      case "citations":
        citations = ev.citations; confidence = ev.confidence; break;
      case "done":
        assistant.dataset.mid = ev.message_id || "";
        refused = ev.stop_reason === "refusal";
        doneMeta = ev;
        break;
    }
  }

  cursor.remove();
  prose.innerHTML = renderMarkdown(acc || "_(no response)_");
  highlightIn(prose);
  finalizeAssistant(assistant, { citations, confidence, refused });
  assistant.querySelector(".msg-actions").classList.add("pinned");
  setTimeout(() => assistant.querySelector(".msg-actions").classList.remove("pinned"), 1200);
  setStreaming(false);
  state.abort = null;
  loadConversations();
  scrollDown();
}

function addNotice(assistant, text) {
  const slot = assistant.querySelector(".notice-slot");
  const n = document.createElement("div");
  n.className = "notice"; n.textContent = text;
  slot.appendChild(n);
}
function renderToolCard(assistant, ev) {
  const slot = assistant.querySelector(".tool-slot");
  const card = document.createElement("div");
  card.className = "tool-card";
  card.innerHTML = `
    <div class="tool-head"><span class="dot"></span>Tool · ${escapeHtml(ev.name)}</div>
    <div class="tool-body">${escapeHtml(JSON.stringify(ev.result ?? ev.input, null, 2))}</div>`;
  slot.appendChild(card);
  scrollDown();
}

function setStreaming(on) {
  state.streaming = on;
  const btn = $("sendBtn");
  btn.classList.toggle("streaming", on);
  btn.querySelector(".send").hidden = on;
  btn.querySelector(".stop").hidden = !on;
  btn.disabled = false;
  btn.title = on ? "Stop" : "Send";
}

/* ---------- composer behaviour ---------- */
function autoGrow() {
  const input = $("input");
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 220) + "px";
  const hasContent = input.value.trim() || state.attachments.length;
  if (!state.streaming) $("sendBtn").disabled = !hasContent;
}

/* ---------- attachments ---------- */
function addAttachments(files) {
  for (const f of files) state.attachments.push({ file: f, name: f.name });
  renderAttachRow(); autoGrow();
}
function clearAttachments() { state.attachments = []; renderAttachRow(); }
function renderAttachRow() {
  const row = $("attachRow");
  if (!state.attachments.length) { row.hidden = true; row.innerHTML = ""; return; }
  row.hidden = false;
  row.innerHTML = state.attachments.map((a, i) => `
    <span class="chip">${icon('<path d="M14 2v6h6"/><path d="M4 2h10l6 6v14H4z"/>')} ${escapeHtml(a.name)}
      <button data-rm="${i}" title="Remove">${icon('<path d="M6 6l12 12M18 6L6 18"/>')}</button></span>`).join("");
  row.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => {
    state.attachments.splice(+b.dataset.rm, 1); renderAttachRow(); autoGrow();
  }));
}

/* ---------- voice dictation (Web Speech API) ---------- */
let recognition = null, heard = "";
const TAX_FIXES = [
  [/\bgst\b/gi, "GST"], [/\btds\b/gi, "TDS"], [/\btcs\b/gi, "TCS"],
  [/\bitr\b/gi, "ITR"], [/\bpan\b/gi, "PAN"], [/\bgstin\b/gi, "GSTIN"],
  [/\bhra\b/gi, "HRA"], [/\bltcg\b/gi, "LTCG"], [/\binput tax credit\b/gi, "input tax credit"],
];
function applyTaxFixes(s) { TAX_FIXES.forEach(([re, to]) => (s = s.replace(re, to))); return s; }

function toggleDictation() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { toast("Voice input isn't supported in this browser. Try Chrome, or type your question."); return; }
  if (state.recognizing) { stopDictation(); return; }
  // Browsers block mic access outside a secure context (HTTPS or localhost).
  // Over plain http:// the Web Speech API fails with "not-allowed" no matter
  // what the user clicks, so surface the real reason instead of a vague error.
  if (!window.isSecureContext) {
    toast("Voice needs a secure (HTTPS) connection — browsers block the mic on http://. Type your question, or serve the app over HTTPS.");
    return;
  }
  recognition = new SR();
  recognition.lang = $("langSelect").value;
  recognition.continuous = true;
  recognition.interimResults = true;
  heard = $("input").value ? $("input").value + " " : "";
  const baseLen = heard.length;
  recognition.onresult = (e) => {
    let finalTxt = heard.slice(0, baseLen), interim = "";
    // rebuild from all results for stability
    let acc = heard.slice(0, baseLen);
    for (let i = 0; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) acc += applyTaxFixes(r[0].transcript) + " ";
      else interim += r[0].transcript;
    }
    heard = acc;
    $("input").value = (acc + interim).trim();
    autoGrow();
    const ld = $("liveDictation");
    ld.hidden = false;
    ld.innerHTML = `${escapeHtml(acc)}<span class="interim">${escapeHtml(interim)}</span>`;
  };
  recognition.onerror = (e) => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed")
      toast("Microphone permission denied. Enable it, or type your question.");
    else if (e.error !== "aborted" && e.error !== "no-speech")
      toast(`Voice error: ${e.error}`);
  };
  recognition.onend = () => {
    if (state.recognizing && recognition) { try { recognition.start(); } catch (_) {} }
  };
  try { recognition.start(); } catch (_) {}
  state.recognizing = true;
  $("micBtn").classList.add("recording");
  $("micBtn").title = "Stop dictation";
}
function stopDictation() {
  state.recognizing = false;
  const r = recognition; recognition = null;
  if (r) { try { r.stop(); } catch (_) {} }
  $("micBtn").classList.remove("recording");
  $("micBtn").title = "Dictate (speak your question)";
  $("liveDictation").hidden = true;
  $("input").focus();
}

/* ---------- theme ---------- */
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  $("hljs-light").disabled = theme === "dark";
  $("hljs-dark").disabled = theme !== "dark";
  localStorage.setItem("ta-theme", theme);
}
function initTheme() {
  // Default to light (white) UI; users can switch to dark via the toggle and
  // that choice is remembered. We intentionally do NOT follow the OS
  // prefers-color-scheme, so a dark-mode OS doesn't force a black page.
  const saved = localStorage.getItem("ta-theme");
  applyTheme(saved === "dark" ? "dark" : "light");
}

/* ---------- misc ---------- */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
async function loadHealth() {
  try {
    const h = await (await fetch(`${API}/health`)).json();
    $("modelBadge").textContent = `${h.llm.provider} · ${h.rag.indexed_chunks} chunks`;
  } catch (_) { $("modelBadge").textContent = "offline"; }
}

/* ---------- wiring ---------- */
function init() {
  initTheme();
  loadConversations();
  loadHealth();

  $("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    if (state.streaming) { if (state.abort) state.abort.abort(); return; }
    send();
  });
  const input = $("input");
  input.addEventListener("input", autoGrow);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!state.streaming) send(); }
  });

  $("newChat").addEventListener("click", newChat);
  $("themeToggle").addEventListener("click", () =>
    applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark"));
  $("sidebarToggle").addEventListener("click", () => $("app").classList.toggle("sidebar-open"));

  $("attachBtn").addEventListener("click", () => $("fileInput").click());
  $("fileInput").addEventListener("change", (e) => { addAttachments(e.target.files); e.target.value = ""; });
  $("micBtn").addEventListener("click", toggleDictation);

  document.querySelectorAll(".suggestion").forEach((s) =>
    s.addEventListener("click", () => { input.value = s.dataset.q; autoGrow(); send(); }));

  // drag & drop attachments
  const thread = $("thread");
  ["dragover", "drop"].forEach((ev) => thread.addEventListener(ev, (e) => e.preventDefault()));
  thread.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) addAttachments(e.dataTransfer.files); });

  // keyboard: Ctrl/Cmd+K new chat
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); newChat(); }
  });

  input.focus();
}
document.addEventListener("DOMContentLoaded", init);
