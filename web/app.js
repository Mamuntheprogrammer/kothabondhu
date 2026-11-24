/* Kotha Bondhu (কথাবন্ধু) — front-end
   No framework. One audio graph, one SSE stream, zero blocking work. */

(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const LS = {
    theme: "kotha-bondhu.theme",
    prefs: "kotha-bondhu.prefs",
    draft: "kotha-bondhu.draft",
    history: "kotha-bondhu.history",
  };

  const SAMPLES = [
    "আমি বাংলায় কথা বলি — and I can switch to English in the same breath.\nআজ তাপমাত্রা ৩৬.৫°C, খরচ ৳৫০০, আর সময় সন্ধ্যা ৭টা।",
    "স্বাগতম! কথাবন্ধু আপনার লেখা বাংলা ও ইংরেজি গল্প, খবর কিংবা স্ক্রিপ্ট — যেকোনো কিছু স্বাভাবিকভাবে পড়ে শোনাবে।\nTry a different voice and speed to find your favourite.",
    "Hello! This is Kotha Bondhu running completely offline on your machine.\nEvery sentence is synthesised locally, streamed as it is ready, and never leaves this computer.",
  ];

  const state = {
    status: null,
    models: [],
    voices: [],
    lang: "auto",
    voice: null,
    voiceBn: null,
    speed: 1,
    loudness: true,
    steadyBn: true,
    autoplay: true,
    theme: "dark",
    job: null,
    history: [],
    downloads: {},
    clone: null,
    cloneVoice: null,
    cloneTokenSaved: false,
    cloneBusy: "",
    cloneAudio: null,
    cloneRecording: null,
    serverOk: false,
    previewing: null,
    samples: 0,
    lastMetaUpdate: 0,
  };

  const player = {
    ctx: null,
    gain: null,
    analyser: null,
    sources: [],
    buffers: [],
    metas: [],
    loading: {},
    nextIndex: 0,
    nextTime: 0,
    startAt: 0,
    total: 0,
    playing: false,
    paused: false,
    finished: false,
    generationDone: false,
    expectedChunks: 0,
    autoplayAllowed: true,
    muted: false,
    jobId: null,
    lastDisplaySecond: -1,
  };

  const waveState = { phase: 0 };

  let els = {};
  let voiceSearchTimer = 0;
  let draftTimer = 0;

  /* ---------------------------------------------------------------- helpers */

  const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));

  function fmtTime(seconds) {
    const s = Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds) : 0;
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${String(r).padStart(2, "0")}`;
  }

  function fmtBytes(bytes) {
    if (!bytes) return "—";
    const mb = bytes / 1e6;
    if (mb >= 1000) return `${(mb / 1000).toFixed(1)} GB`;
    return `${Math.round(mb)} MB`;
  }

  function initials(name) {
    const parts = String(name || "Kotha Bondhu").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "KB";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }

  function langName(code) {
    if (code === "bn") return "বাংলা";
    if (code === "en") return "English";
    if (code === "mixed") return "Mixed";
    return "Auto";
  }

  function snippet(text, max = 110) {
    const clean = String(text || "").replace(/\s+/g, " ").trim();
    return clean.length > max ? `${clean.slice(0, max)}…` : clean;
  }

  function timeAgo(ts) {
    const diff = Math.max(0, Date.now() - Number(ts || 0)) / 1000;
    if (diff < 45) return "just now";
    if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
    return `${Math.round(diff / 86400)} d ago`;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function svgIcon(id, className = "icon") {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", className);
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#${id}`);
    svg.appendChild(use);
    return svg;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    const type = response.headers.get("content-type") || "";
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      if (type.includes("json")) {
        try {
          const body = await response.json();
          if (body && body.error) message = body.error;
        } catch {
          /* ignore */
        }
      }
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    if (type.includes("json")) return response.json();
    return response;
  }

  /* ----------------------------------------------------------------- toasts */

  function toast(title, message = "", kind = "info", options = {}) {
    const node = el("div", `toast is-${kind}`);
    const dot = el("span", "toast-dot");
    const copy = el("div", "toast-copy");
    const heading = el("strong", "", title);
    const body = el("p", "", message);
    copy.append(heading, body);
    const close = el("button", "toast-close");
    close.setAttribute("aria-label", "Dismiss");
    close.appendChild(svgIcon("i-x"));
    const bar = el("div", "toast-bar");
    node.append(dot, copy, close, bar);
    els.toasts.appendChild(node);

    let timer = 0;
    let closed = false;

    const control = {
      el: node,
      update(nextTitle, nextMessage, nextKind) {
        if (nextTitle !== undefined) heading.textContent = nextTitle;
        if (nextMessage !== undefined) body.textContent = nextMessage;
        if (nextKind) {
          node.classList.remove("is-info", "is-success", "is-error");
          node.classList.add(`is-${nextKind}`);
        }
      },
      progress(percent) {
        bar.style.width = `${clamp(Number(percent) || 0, 0, 100)}%`;
      },
      close() {
        if (closed) return;
        closed = true;
        clearTimeout(timer);
        node.classList.add("is-leaving");
        setTimeout(() => node.remove(), 260);
      },
      keep() {
        clearTimeout(timer);
      },
    };

    close.addEventListener("click", control.close);
    const timeout = options.timeout === undefined ? (kind === "error" ? 7000 : 4200) : options.timeout;
    if (timeout > 0) timer = setTimeout(control.close, timeout);
    if (options.progress) bar.style.width = "0%";
    else bar.remove();
    return control;
  }

  /* ------------------------------------------------------------------ theme */

  function setTheme(theme) {
    state.theme = theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = state.theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", state.theme === "dark" ? "#08090d" : "#f4f1ea");
    try {
      localStorage.setItem(LS.theme, state.theme);
    } catch {
      /* ignore */
    }
  }

  function toggleTheme() {
    setTheme(state.theme === "dark" ? "light" : "dark");
  }

  /* ------------------------------------------------------------------ prefs */

  function loadPrefs() {
    try {
      const prefs = JSON.parse(localStorage.getItem(LS.prefs) || "{}");
      if (["auto", "bn", "en"].includes(prefs.lang)) state.lang = prefs.lang;
      if (typeof prefs.voice === "string") state.voice = prefs.voice;
      if (typeof prefs.voiceBn === "string") state.voiceBn = prefs.voiceBn || null;
      if (Number.isFinite(prefs.speed)) state.speed = clamp(prefs.speed, 0.5, 2);
      if (typeof prefs.loudness === "boolean") state.loudness = prefs.loudness;
      if (typeof prefs.steadyBn === "boolean") state.steadyBn = prefs.steadyBn;
      if (typeof prefs.autoplay === "boolean") state.autoplay = prefs.autoplay;
      if (typeof prefs.cloneVoice === "string") state.cloneVoice = prefs.cloneVoice;
    } catch {
      /* ignore */
    }
  }

  function savePrefs() {
    try {
      localStorage.setItem(
        LS.prefs,
        JSON.stringify({
          lang: state.lang,
          voice: state.voice,
          voiceBn: state.voiceBn,
          speed: state.speed,
          loudness: state.loudness,
          steadyBn: state.steadyBn,
          autoplay: state.autoplay,
          cloneVoice: state.cloneVoice,
        })
      );
    } catch {
      /* ignore */
    }
  }

  function saveDraft() {
    try {
      localStorage.setItem(LS.draft, els.text.value);
    } catch {
      /* ignore */
    }
  }

  function loadDraft() {
    try {
      const draft = localStorage.getItem(LS.draft);
      return draft === null ? SAMPLES[0] : draft;
    } catch {
      return SAMPLES[0];
    }
  }

  /* ---------------------------------------------------------------- history */

  function loadHistory() {
    try {
      const data = JSON.parse(localStorage.getItem(LS.history) || "[]");
      state.history = Array.isArray(data) ? data.filter((item) => item && item.id) : [];
    } catch {
      state.history = [];
    }
  }

  function saveHistory() {
    try {
      localStorage.setItem(LS.history, JSON.stringify(state.history.slice(0, 15)));
    } catch {
      /* ignore */
    }
    renderHistory();
  }

  function addHistory(entry) {
    state.history = state.history.filter((item) => item.id !== entry.id);
    state.history.unshift(entry);
    state.history = state.history.slice(0, 15);
    saveHistory();
  }

  function removeHistory(id) {
    state.history = state.history.filter((item) => item.id !== id);
    saveHistory();
  }

  function renderHistory() {
    els.historyList.replaceChildren();
    if (!state.history.length) {
      els.historyList.appendChild(el("li", "history-empty", "Your last generations will show up here."));
      return;
    }
    for (const entry of state.history) {
      const li = el("li", "history-item");
      li.dataset.id = entry.id;

      const play = el("button", "history-play");
      play.dataset.historyPlay = "1";
      play.setAttribute("aria-label", "Play again");
      play.appendChild(svgIcon("i-play"));

      const copy = el("div", "history-copy");
      const text = el("span", "history-text", snippet(entry.text, 120));
      const meta = el("span", "history-meta");
      meta.appendChild(el("span", `lang-dot ${entry.language === "bn" ? "bn" : "en"}`));
      meta.appendChild(el("span", "", langName(entry.language)));
      meta.appendChild(el("span", "", "·"));
      meta.appendChild(el("span", "", timeAgo(entry.at)));
      if (entry.duration) {
        meta.appendChild(el("span", "", "·"));
        meta.appendChild(el("span", "", fmtTime(entry.duration)));
      }
      copy.append(text, meta);

      const del = el("button", "history-del");
      del.dataset.historyDel = "1";
      del.setAttribute("aria-label", "Remove from history");
      del.appendChild(svgIcon("i-trash"));

      li.append(play, copy, del);
      els.historyList.appendChild(li);
    }
  }

  function restoreHistory(entry) {
    if (!entry) return;
    els.text.value = entry.text || "";
    state.lang = ["auto", "bn", "en"].includes(entry.language) ? entry.language : "auto";
    if (entry.voice) state.voice = entry.voice;
    state.voiceBn = typeof entry.voice_bn === "string" ? entry.voice_bn || null : null;
    state.cloneVoice = typeof entry.clone_voice === "string" ? entry.clone_voice : null;
    if (Number.isFinite(entry.speed)) state.speed = clamp(entry.speed, 0.5, 2);
    if (typeof entry.steady_bn === "boolean") state.steadyBn = entry.steady_bn;
    applyPrefsToUI();
    updateStats();
    savePrefs();
    saveDraft();
    setStatus("Loaded from history.", "");
    els.text.focus();
  }

  /* ----------------------------------------------------------------- models */

  function modelFor(language) {
    const key = language === "bn" ? "mms-ben-onnx" : "kokoro";
    return state.models.find((model) => model.key === key);
  }

  function modelCopy(model) {
    if (model.key === "kokoro") {
      return {
        title: "English",
        sub: "Kokoro-82M · 30 voices",
        desc: "Natural, expressive English in US and UK accents. About 350 MB, downloaded once.",
        action: "Download",
      };
    }
    if (model.key === "indic-tiny-bn") {
      return {
        title: "বাংলা · male & female",
        sub: "Indic Tiny TTS · 2 voices",
        desc: "Fast, steady Bangla voices for people who want a choice. About 93 MB, runs on CPU. CC-BY-4.0 (Vocos vocoder MIT).",
        action: "Download",
      };
    }
    return {
      title: "বাংলা",
      sub: "MMS-TTS · single speaker",
      desc: "Compact Bangla model exported to ONNX. Runs on CPU. About 150 MB. CC-BY-NC (non-commercial).",
      action: "Set up",
    };
  }

  function renderPill() {
    const pill = els.statusPill;
    const text = els.statusPillText;
    pill.classList.remove("is-ready", "is-warn", "is-error", "is-loading");
    if (!state.serverOk) {
      pill.classList.add("is-error");
      text.textContent = "Server offline";
      return;
    }
    if (!state.models.length) {
      pill.classList.add("is-loading");
      text.textContent = "Checking models";
      return;
    }
    const ready = state.models.filter((model) => model.ready).length;
    if (ready === state.models.length) {
      pill.classList.add("is-ready");
      text.textContent = "Offline ready";
    } else if (ready > 0) {
      pill.classList.add("is-warn");
      text.textContent = `${ready}/${state.models.length} models ready`;
    } else {
      pill.classList.add("is-warn");
      text.textContent = "Models needed";
    }
  }

  function updateModelCount() {
    const ready = state.models.filter((model) => model.ready).length;
    els.modelCount.textContent = `${ready}/${state.models.length || 3}`;
    els.modelCount.classList.toggle("is-ready", state.models.length > 0 && ready === state.models.length);
  }

  function renderMiniModels() {
    els.miniModelList.replaceChildren();
    if (!state.models.length) {
      els.miniModelList.append(el("div", "mini-row skeleton-row"), el("div", "mini-row skeleton-row"));
      return;
    }
    for (const model of state.models) {
      const info = modelCopy(model);
      const row = el("div", "mini-row");
      row.style.cursor = "pointer";
      row.addEventListener("click", () => openModal("models"));

      const dot = el("span", `model-dot${model.ready ? " is-ready" : ""}`);
      const copy = el("div", "mini-copy");
      copy.append(el("strong", "", info.title), el("small", "", info.sub));
      const stateLabel = el("span", `mini-state${model.ready ? " is-ready" : ""}`, model.ready ? "Ready" : "Needed");
      row.append(dot, copy, stateLabel);
      els.miniModelList.appendChild(row);
    }
  }

  function renderModalModels() {
    els.modalModels.replaceChildren();
    for (const model of state.models) {
      const info = modelCopy(model);
      const card = el("article", "model-card");
      card.dataset.modelCard = model.key;

      const header = el("header");
      const headCopy = el("div");
      headCopy.append(el("h3", "", info.title), el("p", "model-sub", info.sub));
      const badge = el("span", `badge ${model.ready ? "is-done" : ""}`, model.ready ? "Ready" : "Missing");
      header.append(headCopy, badge);

      const desc = el("p", "model-desc", info.desc);
      const bar = el("div", "model-bar");
      const fill = el("div", model.ready ? "is-ready" : "");
      fill.dataset.modelBar = model.key;
      fill.style.width = model.ready ? "100%" : "0%";
      bar.appendChild(fill);

      const footer = el("footer");
      const size = el("code", "", model.ready ? fmtBytes(model.cached_bytes) : fmtBytes(model.bytes));
      const right = el("div");
      right.style.display = "flex";
      right.style.alignItems = "center";
      right.style.gap = "12px";
      const pct = el("span", "model-progress");
      pct.dataset.modelPct = model.key;
      const button = el("button", `btn ${model.ready ? "btn-quiet" : "btn-primary"}`, model.ready ? "Re-check" : info.action);
      button.dataset.download = model.key;
      right.append(pct, button);
      footer.append(size, right);

      card.append(header, desc, bar, footer);
      els.modalModels.appendChild(card);

      if (state.downloads[model.key]) updateModelCardProgress(model.key);
    }
  }

  function updateModelCardProgress(key) {
    const card = els.modalModels.querySelector(`[data-model-card="${key}"]`);
    const download = state.downloads[key];
    if (!card || !download) return;
    const bar = card.querySelector("[data-model-bar]");
    const pct = card.querySelector("[data-model-pct]");
    const button = card.querySelector("[data-download]");
    const percent = clamp(download.percent || 0, 0, 100);
    if (bar) bar.style.width = `${percent}%`;
    if (pct) pct.textContent = download.label ? `${percent.toFixed(0)}% · ${download.label}` : "Starting…";
    if (button) {
      button.disabled = true;
      button.textContent = "Downloading…";
    }
  }

  function startDownload(key) {
    if (state.downloads[key]) return;
    const download = { es: null, percent: 0, label: "", closed: false };
    state.downloads[key] = download;
    updateModelCardProgress(key);

    const tracker = toast("Downloading model", "Connecting…", "info", { timeout: 0, progress: true });
    const source = new EventSource(`/api/models/download?key=${encodeURIComponent(key)}`);
    download.es = source;

    const finish = (kind, title, message, timeout = 0) => {
      download.closed = true;
      try {
        source.close();
      } catch {
        /* ignore */
      }
      delete state.downloads[key];
      tracker.update(title, message, kind);
      tracker.progress(kind === "error" ? 0 : 100);
      if (timeout) setTimeout(() => tracker.close(), timeout);
      if (kind === "success") {
        refreshStatus(false).then(() => {
          renderModalModels();
          renderMiniModels();
        });
      } else {
        renderModalModels();
      }
    };

    source.onmessage = (event) => {
      if (download.closed) return;
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      if (data.type === "start") {
        tracker.update("Downloading model", data.title || key, "info");
      } else if (data.type === "progress") {
        download.percent = Number(data.percent) || 0;
        download.label = data.label || "";
        updateModelCardProgress(key);
        tracker.progress(download.percent);
        const doneMb = (Number(data.done) || 0) / 1e6;
        const totalMb = (Number(data.total) || 0) / 1e6;
        tracker.update(
          "Downloading model",
          totalMb ? `${doneMb.toFixed(1)} / ${totalMb.toFixed(0)} MB · ${download.percent.toFixed(0)}%` : data.label
        );
      } else if (data.type === "done") {
        finish("success", "Model ready", data.message || "", 2600);
      } else if (data.type === "error") {
        finish("error", "Download failed", data.message || "Try again in a moment.", 9000);
      }
    };

    source.onerror = () => {
      if (download.closed) return;
      tracker.update("Download interrupted", "Reconnecting to the local server…", "info");
    };
  }

  /* ----------------------------------------------------------------- voices */

  function renderVoiceList(filter = "") {
    const query = filter.trim().toLowerCase();
    const matches = (value) => !query || String(value || "").toLowerCase().includes(query);
    const clones = ((state.clone && state.clone.voices) || []).filter(
      (voice) => matches(voice.name) || matches(voice.text) || matches(voice.id)
    );
    const regular = state.voices.filter(
      (voice) => matches(voice.name) || matches(voice.style) || matches(voice.id)
    );
    els.voiceList.replaceChildren();

    if (clones.length) {
      els.voiceList.appendChild(el("div", "menu-group", "Cloned voices · Bengali"));
      for (const voice of clones) {
        const selected = voice.id === state.cloneVoice;
        const option = el("div", `voice-option is-clone${selected ? " is-selected" : ""}`);
        option.dataset.cloneVoice = voice.id;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", selected ? "true" : "false");
        option.tabIndex = 0;
        const avatar = el("span", "voice-avatar", initials(voice.name));
        const copy = el("span", "voice-copy");
        copy.append(el("strong", "", voice.name), el("small", "", `Cloned · ${fmtTime(voice.seconds)} reference`));
        const check = svgIcon("i-check", "icon check");
        const preview = el("button", "preview-btn");
        preview.type = "button";
        preview.dataset.previewClone = voice.id;
        preview.setAttribute("aria-label", `Play reference for ${voice.name}`);
        preview.appendChild(svgIcon("i-volume"));
        option.append(avatar, copy, check, preview);
        els.voiceList.appendChild(option);
      }
    }

    let currentGroup = null;
    for (const voice of regular) {
      if (voice.group !== currentGroup) {
        currentGroup = voice.group;
        els.voiceList.appendChild(el("div", "menu-group", currentGroup));
      }
      const isBangla = voice.language === "bn";
      const activeId = isBangla ? state.voiceBn : state.voice;
      const selected = !state.cloneVoice && (voice.id === "mms_bn" ? !state.voiceBn : voice.id === activeId);
      const option = el("div", `voice-option${isBangla ? " is-bn" : ""}${selected ? " is-selected" : ""}`);
      option.dataset.voice = voice.id;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", selected ? "true" : "false");
      option.tabIndex = 0;
      const avatar = el("span", "voice-avatar", initials(voice.name));
      const copy = el("span", "voice-copy");
      copy.append(el("strong", "", voice.name), el("small", "", voice.style));
      const check = svgIcon("i-check", "icon check");
      option.append(avatar, copy, check);
      if (voice.id !== "mms_bn") {
        const preview = el("button", "preview-btn");
        preview.type = "button";
        preview.dataset.preview = voice.id;
        preview.setAttribute("aria-label", `Preview ${voice.label}`);
        preview.appendChild(svgIcon("i-volume"));
        option.appendChild(preview);
      }
      els.voiceList.appendChild(option);
    }

    if (!clones.length && !regular.length) {
      els.voiceList.appendChild(
        el("div", "menu-group", query ? "No voice matches that search." : "No voices available yet.")
      );
      return;
    }
    const bnModel = state.models.find((model) => model.key === "indic-tiny-bn");
    if (bnModel && !bnModel.ready) {
      const note = el("div", "menu-group", "Male/female Bangla voices need a 93 MB download in Models.");
      note.style.textTransform = "none";
      note.style.letterSpacing = "0";
      note.style.fontSize = "11px";
      els.voiceList.appendChild(note);
    }
  }

  function updateVoiceTrigger() {
    const voice = state.voices.find((item) => item.id === state.voice) || state.voices.find((item) => item.language === "en") || null;
    const bangla = state.voices.find((item) => item.id === state.voiceBn) ||
      state.voices.find((item) => item.id === "mms_bn") || null;
    const bnModel = state.models.find((model) => model.key === "indic-tiny-bn");
    const banglaReady = !state.voiceBn || (bnModel && bnModel.ready);
    const cloneVoice = ((state.clone && state.clone.voices) || []).find(
      (item) => item.id === state.cloneVoice
    );

    if (cloneVoice) {
      els.voiceAvatar.textContent = initials(cloneVoice.name);
      els.voiceName.textContent = cloneVoice.name;
      els.voiceStyle.textContent = "Cloned voice · Bengali";
      els.voiceTrigger.disabled = false;
      els.voiceNote.textContent = "Cloned voice reads every segment; English may sound accented.";
      els.voiceNote.classList.add("is-warn");
      return;
    }

    if (state.lang === "bn") {
      els.voiceAvatar.textContent = bangla && bangla.id !== "mms_bn" ? initials(bangla.name) : "বাং";
      els.voiceName.textContent = bangla ? bangla.name : "MMS · single speaker";
      els.voiceStyle.textContent = bangla ? bangla.style : "Bangla · default";
      els.voiceTrigger.disabled = false;
      if (!banglaReady) {
        els.voiceNote.textContent = "This Bangla voice needs a 93 MB download in Models.";
        els.voiceNote.classList.add("is-warn");
      } else {
        els.voiceNote.textContent = "Male and female Bangla voices are in the menu. Steady tone is on by default.";
        els.voiceNote.classList.remove("is-warn");
      }
      return;
    }

    if (!voice) {
      els.voiceAvatar.textContent = "—";
      els.voiceName.textContent = "No English voice";
      els.voiceStyle.textContent = "Download the English model";
      els.voiceTrigger.disabled = false;
      els.voiceNote.textContent = "Open Models to download Kokoro-82M.";
      els.voiceNote.classList.add("is-warn");
      return;
    }

    els.voiceAvatar.textContent = initials(voice.name);
    els.voiceName.textContent = voice.name;
    els.voiceStyle.textContent = voice.style;
    els.voiceTrigger.disabled = false;
    const english = modelFor("en");
    if (english && !english.ready) {
      els.voiceNote.textContent = "English model not downloaded yet.";
      els.voiceNote.classList.add("is-warn");
    } else if (!banglaReady) {
      els.voiceNote.textContent = `Bangla uses MMS until ${bangla ? bangla.name : "the voice"} is downloaded.`;
      els.voiceNote.classList.add("is-warn");
    } else {
      els.voiceNote.textContent = `Bangla uses ${bangla && bangla.id !== "mms_bn" ? bangla.name : "MMS · single speaker"}.`;
      els.voiceNote.classList.remove("is-warn");
    }
    if (voice.id !== state.voice) {
      state.voice = voice.id;
      savePrefs();
    }
  }

  function setVoice(id) {
    const picked = state.voices.find((item) => item.id === id);
    state.cloneVoice = null;
    if (picked && picked.language === "bn") {
      state.voiceBn = id === "mms_bn" ? null : id;
      toast(
        "Bangla voice selected",
        id === "mms_bn" ? "Back to the MMS single speaker." : `${picked.name} will read Bangla segments.`,
        "success",
        { timeout: 2600 }
      );
    } else {
      state.voice = id;
    }
    savePrefs();
    updateVoiceTrigger();
    renderVoiceList($("#voice-search")?.value || "");
    closeVoiceMenu();
  }

  function setCloneVoice(id) {
    const meta = ((state.clone && state.clone.voices) || []).find((item) => item.id === id);
    if (!meta) return;
    state.cloneVoice = id;
    savePrefs();
    updateVoiceTrigger();
    renderVoiceList($("#voice-search")?.value || "");
    renderCloneVoiceList();
    closeVoiceMenu();
    toast("Cloned voice selected", `${meta.name} will read every segment.`, "success", { timeout: 3000 });
  }

  async function previewCloneVoice(id, button) {
    button.classList.add("is-loading");
    try {
      const response = await fetch(`/api/clone/voices/${encodeURIComponent(id)}/audio`);
      if (!response.ok) {
        let message = "Reference clip unavailable.";
        try {
          const data = await response.json();
          if (data.error) message = data.error;
        } catch {
          /* ignore */
        }
        throw new Error(message);
      }
      const buffer = await response.arrayBuffer();
      const ctx = await ensureCtx();
      const decoded = await ctx.decodeAudioData(buffer);
      const source = ctx.createBufferSource();
      source.buffer = decoded;
      source.connect(player.gain);
      source.start();
      state.previewing = source;
      source.onended = () => {
        if (state.previewing === source) state.previewing = null;
      };
    } catch (error) {
      toast("Reference unavailable", error.message || String(error), "error", { timeout: 6000 });
    } finally {
      button.classList.remove("is-loading");
    }
  }

  async function previewVoice(id, button) {
    if (state.previewing) {
      try {
        state.previewing.stop();
      } catch {
        /* ignore */
      }
      state.previewing = null;
    }
    button.classList.add("is-loading");
    try {
      const response = await fetch("/api/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice: id }),
      });
      if (!response.ok) {
        let message = "Preview unavailable.";
        try {
          const data = await response.json();
          if (data.error) message = data.error;
        } catch {
          /* ignore */
        }
        throw new Error(message);
      }
      const buffer = await response.arrayBuffer();
      const ctx = await ensureCtx();
      const decoded = await ctx.decodeAudioData(buffer);
      const source = ctx.createBufferSource();
      source.buffer = decoded;
      source.connect(player.gain);
      source.start();
      state.previewing = source;
      source.onended = () => {
        if (state.previewing === source) state.previewing = null;
      };
    } catch (error) {
      toast("Preview unavailable", error.message || String(error), "error", { timeout: 6000 });
    } finally {
      button.classList.remove("is-loading");
    }
  }

  function openVoiceMenu() {
    els.voiceMenu.hidden = false;
    els.voiceTrigger.setAttribute("aria-expanded", "true");
    renderVoiceList(els.voiceSearch.value);
    setTimeout(() => els.voiceSearch.focus(), 30);
  }

  function closeVoiceMenu() {
    els.voiceMenu.hidden = true;
    els.voiceTrigger.setAttribute("aria-expanded", "false");
  }

  /* ------------------------------------------------------------ clone panel */

  function cloneChip(label, ok, detail) {
    const chip = el("span", `clone-chip${ok ? " is-ready" : ""}`, `${label} · ${detail}`);
    chip.title = detail;
    return chip;
  }

  function renderClonePanel() {
    if (!els.cloneChips || !state.clone) return;
    const clone = state.clone;
    const env = clone.environment || {};
    const model = clone.model || {};
    const device = clone.device || {};
    const voices = clone.voices || [];

    els.cloneWarning.textContent = clone.mock
      ? "Test mode: the standard-library mock worker is active. No real cloning is performed."
      : device.warning || "Runtime ready. Downloads happen only when you ask for them.";
    els.cloneWarning.classList.toggle("is-ok", Boolean(clone.ready && !device.warning));

    els.cloneChips.replaceChildren(
      cloneChip("Runtime", Boolean(env.ready), env.ready ? (env.mock ? "mock" : env.profile || "ready") : "not installed"),
      cloneChip("Model", Boolean(model.ready), model.ready ? (model.mock ? "mock" : fmtBytes(model.cached_bytes)) : "not downloaded"),
      cloneChip("Device", Boolean(device.cuda || clone.mock), device.name || "unknown"),
      cloneChip("Voices", voices.length > 0, `${voices.length} saved`)
    );

    if (!els.cloneProfile.dataset.touched) {
      els.cloneProfile.value = device.recommended === "cuda" ? "cuda" : "cpu";
    }
    els.cloneToken.placeholder = state.cloneTokenSaved ? "Token saved (memory only)" : "hf_…";
    els.cloneSetup.textContent = env.ready ? "Environment ready" : "Install environment";
    els.cloneSetup.disabled = Boolean(env.ready || state.cloneBusy);
    els.cloneDownload.textContent = model.ready ? "Model ready" : "Download model";
    els.cloneDownload.disabled = Boolean(model.ready || state.cloneBusy);
    const missing = (model.missing || []).length;
    els.cloneModelCopy.textContent = model.mock
      ? "Mock mode — no files."
      : `${fmtBytes(model.cached_bytes)} / ${fmtBytes(model.bytes)}${missing ? ` · ${missing} files missing` : " · ready"}`;
    renderCloneVoiceList();
  }

  function renderCloneVoiceList() {
    if (!els.cloneVoiceList) return;
    const voices = (state.clone && state.clone.voices) || [];
    els.cloneVoiceList.replaceChildren();
    if (!voices.length) {
      els.cloneVoiceList.appendChild(el("p", "clone-empty", "No cloned voices yet. Add one above, then choose it from the voice menu."));
      return;
    }
    for (const voice of voices) {
      const active = voice.id === state.cloneVoice;
      const row = el("div", `clone-voice-row${active ? " is-active" : ""}`);
      row.append(el("span", "voice-avatar", initials(voice.name)));
      const copy = el("div", "clone-voice-copy");
      copy.append(
        el("strong", "", voice.name),
        el("small", "", `${fmtTime(voice.seconds)} · ${snippet(voice.text, 56)}`)
      );
      const actions = el("div", "clone-voice-actions");
      const play = el("button");
      play.type = "button";
      play.title = "Play reference clip";
      play.dataset.clonePreview = voice.id;
      play.appendChild(svgIcon("i-volume"));
      const use = el("button", "is-use", active ? "Active" : "Use");
      use.type = "button";
      use.dataset.cloneUse = voice.id;
      use.disabled = active;
      const remove = el("button");
      remove.type = "button";
      remove.title = "Delete voice";
      remove.dataset.cloneDelete = voice.id;
      remove.appendChild(svgIcon("i-trash"));
      actions.append(play, use, remove);
      row.append(copy, actions);
      els.cloneVoiceList.appendChild(row);
    }
  }

  function startCloneStream(kind, url, title) {
    if (state.cloneBusy) {
      toast("Already working", "Let the current clone task finish first.", "info", { timeout: 3000 });
      return;
    }
    state.cloneBusy = kind;
    els.cloneSetup.disabled = true;
    els.cloneDownload.disabled = true;
    els.cloneProgress.hidden = false;
    els.cloneProgressFill.style.width = "0%";
    els.cloneProgressLabel.textContent = "Connecting…";
    els.cloneLog.hidden = false;
    els.cloneLog.textContent = "";
    const tracker = toast(title, "Connecting…", "info", { timeout: 0, progress: true });
    const source = new EventSource(url);
    let closed = false;

    const finish = (ok, message) => {
      if (closed) return;
      closed = true;
      try {
        source.close();
      } catch {
        /* ignore */
      }
      state.cloneBusy = "";
      tracker.update(ok ? "Clone setup ready" : "Clone setup failed", message || "", ok ? "success" : "error");
      tracker.progress(ok ? 100 : 0);
      setTimeout(() => tracker.close(), ok ? 3200 : 9000);
      refreshStatus(false).then(renderClonePanel);
      renderClonePanel();
    };

    source.onmessage = (event) => {
      if (closed) return;
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      if (data.type === "ping") return;
      if (data.type === "start") {
        els.cloneProgressLabel.textContent = data.message || data.title || "Starting…";
        if (data.line) els.cloneLog.textContent += `${data.line}\n`;
        tracker.update(title, data.title || data.message || "Starting…");
      } else if (data.type === "progress") {
        const percent = clamp(Number(data.percent) || 0, 0, 100);
        els.cloneProgressFill.style.width = `${percent}%`;
        const mb = (value) => `${((Number(value) || 0) / 1e6).toFixed(1)} MB`;
        els.cloneProgressLabel.textContent = data.total
          ? `${percent.toFixed(0)}% · ${mb(data.done)} / ${mb(data.total)} · ${data.label || ""}`
          : data.label || "Working…";
        tracker.progress(percent);
        tracker.update(title, els.cloneProgressLabel.textContent);
      } else if (data.type === "log") {
        if (data.line) {
          els.cloneLog.textContent += `${data.line}\n`;
          els.cloneLog.scrollTop = els.cloneLog.scrollHeight;
        }
      } else if (data.type === "done") {
        finish(true, data.message || "Done.");
      } else if (data.type === "error") {
        finish(false, data.message || "The local task failed.");
      }
    };
    source.onerror = () => {
      if (!closed) tracker.update("Connection interrupted", "Reconnecting to the local server…", "info");
    };
  }

  async function saveCloneToken() {
    const token = els.cloneToken.value.trim();
    try {
      const data = await api("/api/clone/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      state.cloneTokenSaved = Boolean(data.saved);
      els.cloneToken.value = "";
      renderClonePanel();
      toast(data.saved ? "Token saved" : "Token cleared", data.message || "", "success", { timeout: 3500 });
    } catch (error) {
      toast("Could not save token", error.message || String(error), "error");
    }
  }

  function readAsDataURL(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(new Error("Could not read that audio file."));
      reader.readAsDataURL(blob);
    });
  }

  function encodeWav(buffer) {
    const channels = Math.min(2, buffer.numberOfChannels || 1);
    const frames = buffer.length || 0;
    const bytes = 44 + frames * channels * 2;
    const view = new DataView(new ArrayBuffer(bytes));
    const text = (offset, value) => {
      for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i));
    };
    text(0, "RIFF");
    view.setUint32(4, bytes - 8, true);
    text(8, "WAVE");
    text(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, channels, true);
    view.setUint32(24, buffer.sampleRate || 24000, true);
    view.setUint32(28, (buffer.sampleRate || 24000) * channels * 2, true);
    view.setUint16(32, channels * 2, true);
    view.setUint16(34, 16, true);
    text(36, "data");
    view.setUint32(40, frames * channels * 2, true);
    const data = [];
    for (let channel = 0; channel < channels; channel += 1) data.push(buffer.getChannelData(channel));
    let offset = 44;
    for (let i = 0; i < frames; i += 1) {
      for (let channel = 0; channel < channels; channel += 1) {
        const value = clamp(data[channel][i] || 0, -1, 1);
        view.setInt16(offset, value < 0 ? value * 32768 : value * 32767, true);
        offset += 2;
      }
    }
    return new Blob([view.buffer], { type: "audio/wav" });
  }

  async function toggleMic() {
    if (state.cloneRecording) {
      try {
        state.cloneRecording.recorder.stop();
      } catch {
        /* ignore */
      }
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      toast("Microphone unavailable", "Use a WAV/FLAC file instead.", "error");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      const recorder = new MediaRecorder(stream);
      const chunks = [];
      const ctx = await ensureCtx();
      state.cloneRecording = { recorder, stream };
      els.cloneMic.textContent = "Stop recording";
      els.cloneMic.classList.add("is-busy");
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size) chunks.push(event.data);
      };
      recorder.onstop = async () => {
        state.cloneRecording = null;
        els.cloneMic.textContent = "Record microphone";
        els.cloneMic.classList.remove("is-busy");
        stream.getTracks().forEach((track) => track.stop());
        try {
          const recorded = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
          const decoded = await ctx.decodeAudioData(await recorded.arrayBuffer());
          state.cloneAudio = encodeWav(decoded);
          els.cloneFileName.textContent = `Microphone · ${decoded.duration.toFixed(1)}s`;
          toast("Recording ready", `${decoded.duration.toFixed(1)}s captured. Add a transcript to save it.`, "success");
        } catch (error) {
          toast("Could not use recording", error.message || String(error), "error");
        }
      };
      recorder.start();
      toast("Recording…", "Speak the transcript clearly, then press Stop recording.", "info", { timeout: 5000 });
    } catch (error) {
      state.cloneRecording = null;
      els.cloneMic.textContent = "Record microphone";
      els.cloneMic.classList.remove("is-busy");
      toast("Microphone blocked", error.message || String(error), "error");
    }
  }

  async function addCloneVoice() {
    if (!els.cloneConsent.checked) {
      toast("Permission needed", "Confirm you have the right to use this voice.", "info");
      return;
    }
    if (!state.cloneAudio) {
      toast("No reference clip", "Choose an audio file or record a short sample first.", "info");
      return;
    }
    const name = els.cloneVoiceName.value.trim();
    const text = els.cloneVoiceText.value.trim();
    if (!name || !text) {
      toast("Almost there", "Add a voice name and the exact transcript.", "info");
      return;
    }
    const button = els.cloneAdd;
    button.disabled = true;
    button.classList.add("is-busy");
    try {
      const audio = await readAsDataURL(state.cloneAudio);
      const data = await api("/api/clone/voices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, text, language: "bn", audio }),
      });
      els.cloneVoiceName.value = "";
      els.cloneVoiceText.value = "";
      els.cloneVoiceFile.value = "";
      els.cloneFileName.textContent = "No clip chosen";
      state.cloneAudio = null;
      await refreshStatus(false);
      renderClonePanel();
      toast("Voice added", `${data.voice.name} is saved. Use it from the voice menu.`, "success");
    } catch (error) {
      toast("Could not add voice", error.message || String(error), "error", { timeout: 8000 });
    } finally {
      button.disabled = false;
      button.classList.remove("is-busy");
    }
  }

  async function deleteCloneVoice(id) {
    try {
      await api(`/api/clone/voices/${encodeURIComponent(id)}/delete`, { method: "POST" });
      if (state.cloneVoice === id) {
        state.cloneVoice = null;
        savePrefs();
      }
      await refreshStatus(false);
      renderClonePanel();
      updateVoiceTrigger();
      toast("Voice removed", "The reference clip was deleted from this machine.", "success");
    } catch (error) {
      toast("Could not remove voice", error.message || String(error), "error");
    }
  }

  /* ------------------------------------------------------------------ status */

  let statusRetries = 0;

  async function refreshStatus(force = false) {
    try {
      // `refresh` asks the server to re-hash and verify every model file.
      // Ordinary loads use the fast presence/size cache.
      const data = await api(`/api/status${force ? "?refresh=1" : `?t=${Date.now()}`}`);
      state.status = data;
      state.serverOk = true;
      state.models = Array.isArray(data.models) ? data.models : [];
      state.voices = Array.isArray(data.voices) ? data.voices : [];
      state.clone = data.clone || state.clone;
      const savedClone = ((state.clone && state.clone.voices) || []).find(
        (voice) => voice.id === state.cloneVoice
      );
      if (state.cloneVoice && !savedClone) state.cloneVoice = null;
      if (!state.voice || !state.voices.some((voice) => voice.id === state.voice)) {
        const saved = state.voice;
        const fallback =
          state.voices.find((voice) => voice.id === data.default_voice) ||
          state.voices[0] ||
          null;
        state.voice = fallback ? fallback.id : saved || null;
      }
      const about = $("#about-version");
      if (about) about.textContent = `Kotha Bondhu 2.2 · core ${data.core_version || "2.0.0"}`;
      els.cachePath.textContent = data.cache_dir || "—";
      renderPill();
      renderMiniModels();
      renderModalModels();
      renderClonePanel();
      updateVoiceTrigger();
      updateModelCount();
      if (!els.voiceMenu.hidden) renderVoiceList(els.voiceSearch.value);
      statusRetries = 0;
    } catch (error) {
      state.serverOk = false;
      renderPill();
      statusRetries += 1;
      if (statusRetries <= 6) {
        setTimeout(() => refreshStatus(false), 1600 * statusRetries);
      } else {
        toast("Server offline", "Start Kotha Bondhu with `python ai_version/run.py`.", "error", { timeout: 0 });
      }
    }
  }

  /* ------------------------------------------------------------------ speak */

  function neededLanguages(text) {
    if (state.lang !== "auto") return [state.lang];
    const hasBn = /[\u0980-\u09FF]/.test(text);
    const hasEn = /[A-Za-z]/.test(text);
    if (hasBn && hasEn) return ["bn", "en"];
    if (hasBn) return ["bn"];
    return ["en"];
  }

  function resetPlayer(clearBuffers = true) {
    haltSources();
    if (clearBuffers) {
      player.buffers = [];
      player.metas = [];
      player.loading = {};
      player.nextIndex = 0;
      player.total = 0;
      player.jobId = null;
    }
    player.generationDone = false;
    player.expectedChunks = 0;
    player.lastDisplaySecond = -1;
    syncControls();
  }

  function resetJobUI() {
    setProgress(0);
    renderParts([]);
    updatePartsCurrent(-1);
    els.progress.classList.remove("is-running");
    els.playerTitle.textContent = "Your voice, ready when you are.";
    els.playerTitle.classList.add("is-empty");
    setPlayerBadge("idle", "");
    setStatus("Ready when you are.", "");
    syncControls();
  }

  function beginSpeakUI(text) {
    els.progress.classList.add("is-running");
    setProgress(0.02);
    setStatus("Splitting text…", "busy");
    els.statusMeta.textContent = "";
    els.playerTitle.textContent = snippet(text, 120);
    els.playerTitle.classList.remove("is-empty");
    setPlayerBadge("preparing", "is-live");
    syncControls();
  }

  async function speak() {
    const text = els.text.value.trim();
    if (!text) {
      toast("Nothing to speak yet", "Write or paste a little text first.", "info");
      els.text.focus();
      return;
    }
    if (text.length > 20000) {
      toast("Text is too long", "The limit is 20,000 characters per run.", "error");
      return;
    }

    if (state.cloneVoice) {
      if (!state.clone || !state.clone.can_synthesize) {
        toast("Clone not ready", "Open Models → Clone to finish the one-time setup.", "info");
        openModal("clone");
        return;
      }
    } else {
      const needed = neededLanguages(text);
      const missing = needed.filter((language) => {
        const model = modelFor(language);
        return model && !model.ready;
      });
      const bnVoiceModel = state.voiceBn ? state.models.find((model) => model.key === "indic-tiny-bn") : null;
      if (bnVoiceModel && !bnVoiceModel.ready && needed.includes("bn")) {
        missing.push("voice");
      }
      if (missing.length) {
        const names = missing
          .map((language) =>
            language === "voice"
              ? "Bangla male/female voices"
              : language === "bn"
                ? "Bangla (MMS-TTS)"
                : "English (Kokoro)"
          )
          .join(" and ");
        toast("Model needed", `${names} is not downloaded yet.`, "info");
        openModal("models");
        return;
      }
    }

    if (state.job?.status === "running") {
      stopAll(true);
    } else if (playerHasAudio()) {
      haltSources();
      resetPlayer(true);
    }

    saveDraft();
    resetPlayer(true);
    state.job = {
      id: null,
      status: "running",
      text,
      total: 0,
      received: 0,
      seen: new Set(),
      parts: [],
      duration: 0,
      es: null,
    };
    player.generationDone = false;
    player.autoplayAllowed = state.autoplay;
    beginSpeakUI(text);

    try {
      const response = await api("/api/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text,
          language: state.lang,
          voice: state.voice,
          voice_bn: state.voiceBn,
          clone_voice: state.cloneVoice || null,
          speed: state.speed,
          loudness: state.loudness,
          steady_bn: state.steadyBn,
        }),
      });
      state.job.id = response.job;
      player.jobId = response.job;
      connectJob(response.job);
      syncControls();
    } catch (error) {
      state.job.status = "error";
      els.progress.classList.remove("is-running");
      setStatus(error.message || "Could not start synthesis.", "error");
      setPlayerBadge("error", "is-error");
      toast("Could not start", error.message || String(error), "error");
      syncControls();
    }
  }

  function connectJob(jobId) {
    closeJobStream();
    const source = new EventSource(`/api/jobs/${jobId}/events`);
    state.job.es = source;
    let terminal = false;

    source.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      try {
        handleJobEvent(data);
      } catch (error) {
        console.error("event handler", error);
      }
      if (["done", "error", "cancelled"].includes(data.type)) {
        terminal = true;
        try {
          source.close();
        } catch {
          /* ignore */
        }
        if (state.job) state.job.es = null;
      }
    };

    source.onerror = () => {
      if (terminal) return;
      if (state.job?.status === "running") {
        setStatus("Connection interrupted — reconnecting…", "busy");
      }
    };
  }

  function closeJobStream() {
    if (state.job?.es) {
      try {
        state.job.es.close();
      } catch {
        /* ignore */
      }
      state.job.es = null;
    }
  }

  function handleJobEvent(event) {
    if (!state.job) return;
    switch (event.type) {
      case "plan": {
        state.job.total = event.total;
        state.job.languages = event.languages || [];
        state.job.parts = (event.segments || []).map((segment) => ({
          language: segment.language,
          text: segment.text,
          ready: false,
          seconds: 0,
        }));
        renderParts(state.job.parts);
        const languages = (event.languages || []).map(langName).join(" + ");
        setStatus(`Synthesizing ${event.total} segment${event.total === 1 ? "" : "s"} · ${languages}`, "busy");
        els.statusMeta.textContent = `${event.total} parts`;
        setProgress(0.03);
        break;
      }
      case "stage": {
        if (event.stage === "loading") {
          setPlayerBadge("loading", "is-live");
          if (event.fraction != null) {
            setProgress(0.03 + clamp(event.fraction, 0, 1) * 0.12);
          }
        }
        setStatus(event.message || "Working…", "busy");
        break;
      }
      case "chunk_start": {
        setStatus(`Synthesizing ${event.index + 1}/${event.total} · ${langName(event.language)}`, "busy");
        updatePartsCurrent(event.index);
        break;
      }
      case "chunk": {
        if (event.index != null && state.job.seen.has(event.index)) break;
        if (event.index != null) state.job.seen.add(event.index);
        state.job.received += 1;
        state.job.duration += Number(event.seconds) || 0;
        const ratio = state.job.total ? state.job.received / state.job.total : 0.1;
        setProgress(clamp(0.03 + ratio * 0.97, 0.03, 0.99));
        setStatus(
          `Generated ${state.job.received}/${state.job.total || "?"} · ${fmtTime(state.job.duration)} of audio`,
          "busy"
        );
        els.statusMeta.textContent = `${state.job.received}/${state.job.total || "?"} parts · ${fmtTime(state.job.duration)}`;
        markPartReady(event.language, event.text, Number(event.seconds) || 0);
        setPlayerBadge("generating", "is-live");
        loadChunk(event);
        syncControls();
        break;
      }
      case "warning": {
        toast("Skipped a fragment", event.message || "", "info", { timeout: 5000 });
        break;
      }
      case "done": {
        state.job.status = "done";
        state.job.duration = Number(event.duration) || state.job.duration;
        player.generationDone = true;
        player.expectedChunks = Number(event.chunks) || state.job.received;
        els.progress.classList.remove("is-running");
        setProgress(1);
        setStatus(`Generated in ${event.seconds}s · ${fmtTime(event.duration)} of audio`, "ok");
        els.statusMeta.textContent = `${event.chunks} parts · ${fmtTime(event.duration)}`;
        setPlayerBadge(player.playing && !player.paused ? "playing" : "ready", player.playing ? "is-live" : "is-done");
        updatePartsCurrent(-1);
        const used = state.job.languages || [];
        addHistory({
          id: state.job.id,
          text: state.job.text,
          language: used.length > 1 ? "mixed" : used[0] || state.lang,
          voice: state.cloneVoice || state.voice,
          voice_bn: state.cloneVoice ? null : state.voiceBn,
          clone_voice: state.cloneVoice,
          speed: state.speed,
          steady_bn: state.steadyBn,
          at: Date.now(),
          duration: Number(event.duration) || state.job.duration,
        });
        if (!state.autoplay) {
          setStatus("Generated — press play or download.", "ok");
        }
        syncControls();
        break;
      }
      case "error": {
        state.job.status = "error";
        els.progress.classList.remove("is-running");
        setProgress(0);
        setStatus(event.message || "Generation failed.", "error");
        setPlayerBadge("error", "is-error");
        toast("Generation failed", event.message || "Unknown error.", "error", { timeout: 9000 });
        updatePartsCurrent(-1);
        syncControls();
        break;
      }
      case "cancelled": {
        state.job.status = "cancelled";
        els.progress.classList.remove("is-running");
        updatePartsCurrent(-1);
        if (event.reason === "superseded") setStatus("Replaced by a new run.", "");
        else setStatus("Stopped.", "");
        setPlayerBadge("stopped", "");
        syncControls();
        break;
      }
      default:
        break;
    }
  }

  /* ------------------------------------------------------------------ parts */

  function renderParts(parts) {
    els.parts.replaceChildren();
    if (!parts || !parts.length) {
      els.parts.appendChild(el("span", "parts-empty", "Segments appear here as they are generated."));
      return;
    }
    parts.forEach((part, index) => {
      const node = el("span", `part ${part.language === "bn" ? "bn" : "en"}`);
      node.dataset.part = String(index);
      node.title = part.text || "";
      node.appendChild(el("span", "dot"));
      node.appendChild(el("span", "part-text", snippet(part.text, 34)));
      els.parts.appendChild(node);
    });
  }

  function markPartReady(language, text, seconds) {
    if (!state.job) return;
    const parts = state.job.parts || [];
    let index = parts.findIndex((part) => !part.ready && part.language === language && part.text === text);
    if (index < 0) {
      parts.push({ language, text, ready: true, seconds });
      renderParts(parts);
      index = parts.length - 1;
    }
    const part = parts[index];
    part.ready = true;
    part.seconds = seconds;
    const node = els.parts.querySelector(`[data-part="${index}"]`);
    if (node) {
      node.classList.add("is-ready");
      node.classList.remove("is-current");
      let time = node.querySelector(".part-time");
      if (!time) {
        time = el("span", "part-time");
        node.appendChild(time);
      }
      time.textContent = seconds ? fmtTime(seconds) : "";
    }
  }

  function updatePartsCurrent(planIndex) {
    $$(".part", els.parts).forEach((node) => node.classList.remove("is-current"));
    if (!state.job || planIndex < 0) return;
    const node = els.parts.querySelector(`[data-part="${planIndex}"]`);
    if (node) node.classList.add("is-current");
  }

  /* ----------------------------------------------------------------- player */

  function playerHasAudio() {
    return player.buffers.some(Boolean);
  }

  function totalBufferedDuration() {
    let total = 0;
    for (let i = 0; i < player.buffers.length; i += 1) {
      if (!player.buffers[i]) break;
      total += player.buffers[i].duration;
    }
    return total;
  }

  async function ensureCtx() {
    if (!player.ctx) {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) throw new Error("This browser does not support Web Audio.");
      const ctx = new Ctor();
      const gain = ctx.createGain();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0.82;
      gain.connect(analyser);
      analyser.connect(ctx.destination);
      gain.gain.value = player.muted ? 0 : 1;
      player.ctx = ctx;
      player.gain = gain;
      player.analyser = analyser;
    }
    if (player.ctx.state === "suspended" && !player.paused) {
      try {
        await player.ctx.resume();
      } catch {
        /* ignore */
      }
    }
    return player.ctx;
  }

  async function loadChunk(meta) {
    const index = Number(meta.index);
    if (!Number.isFinite(index) || index < 0) return;
    if (player.buffers[index] || player.loading[index]) return;
    player.loading[index] = true;
    try {
      const response = await fetch(meta.url);
      if (!response.ok) throw new Error(`Chunk ${index} unavailable`);
      const raw = await response.arrayBuffer();
      const ctx = await ensureCtx();
      const decoded = await ctx.decodeAudioData(raw);
      player.buffers[index] = decoded;
      player.metas[index] = meta;
      player.total = totalBufferedDuration();
      scheduleAvailable();
      if (player.playing && !player.paused) setPlayerBadge("playing", "is-live");
      syncControls();
    } catch (error) {
      console.warn("chunk failed", index, error);
    } finally {
      delete player.loading[index];
    }
  }

  function scheduleBuffer(index, offset = 0) {
    if (!player.ctx) return;
    const buffer = player.buffers[index];
    if (!buffer) return;
    const ctx = player.ctx;
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(player.gain);
    const now = ctx.currentTime;
    const when = player.nextTime > now ? player.nextTime : now + 0.045;
    if (!player.startAt) player.startAt = when - offset;
    source.start(when, offset);
    player.nextTime = when + Math.max(0, buffer.duration - offset);
    player.sources.push(source);
    player.playing = true;
    source.onended = () => {
      const at = player.sources.indexOf(source);
      if (at >= 0) player.sources.splice(at, 1);
    };
  }

  function scheduleAvailable() {
    if (!player.ctx || !player.autoplayAllowed) return;
    let guard = 0;
    while (player.buffers[player.nextIndex] && guard < 600) {
      scheduleBuffer(player.nextIndex, 0);
      player.nextIndex += 1;
      guard += 1;
    }
    if (player.playing) setPlayVisual();
  }

  function haltSources() {
    for (const source of player.sources) {
      try {
        source.stop();
      } catch {
        /* ignore */
      }
      try {
        source.disconnect();
      } catch {
        /* ignore */
      }
    }
    player.sources = [];
    player.playing = false;
    player.paused = false;
    player.nextTime = 0;
    player.startAt = 0;
    player.nextIndex = 0;
    if (player.ctx && player.ctx.state === "suspended") {
      player.ctx.resume().catch(() => {});
    }
    setPlayVisual();
  }

  function setPlayVisual() {
    els.playBtn.classList.toggle("is-playing", player.playing && !player.paused);
  }

  async function pausePlayback() {
    if (!player.ctx) return;
    player.paused = true;
    player.playing = true;
    setPlayVisual();
    setPlayerBadge("paused", "");
    try {
      await player.ctx.suspend();
    } catch {
      /* ignore */
    }
    syncControls();
  }

  async function resumePlayback() {
    if (!player.ctx) return;
    player.paused = false;
    player.playing = true;
    setPlayVisual();
    setPlayerBadge("playing", "is-live");
    try {
      await player.ctx.resume();
    } catch {
      /* ignore */
    }
    syncControls();
  }

  async function restartPlayback() {
    await ensureCtx();
    haltSources();
    player.autoplayAllowed = true;
    player.finished = false;
    player.nextIndex = 0;
    player.nextTime = 0;
    player.startAt = 0;
    scheduleAvailable();
    if (player.playing) {
      setPlayerBadge("playing", "is-live");
      setPlayVisual();
    }
    syncControls();
  }

  async function togglePlay() {
    try {
      if (player.playing && !player.paused) {
        await pausePlayback();
        return;
      }
      if (player.paused) {
        await resumePlayback();
        return;
      }
      if (playerHasAudio()) {
        await restartPlayback();
        return;
      }
      if (state.job?.status === "running") {
        player.autoplayAllowed = true;
        setStatus("Waiting for the first segment…", "busy");
        return;
      }
      if (state.job?.id) {
        await replayJob(state.job.id);
        return;
      }
      toast("Nothing to play yet", "Generate a voice first.", "info");
    } catch (error) {
      toast("Playback problem", error.message || String(error), "error");
    }
  }

  function seekTo(target) {
    if (!player.ctx || player.total <= 0) return;
    const ctx = player.ctx;
    haltSources();
    player.autoplayAllowed = true;
    const clamped = clamp(target, 0, Math.max(0, player.total - 0.02));
    let acc = 0;
    let index = 0;
    let offset = 0;
    for (; index < player.buffers.length; index += 1) {
      const buffer = player.buffers[index];
      if (!buffer) break;
      if (acc + buffer.duration > clamped) {
        offset = clamped - acc;
        break;
      }
      acc += buffer.duration;
    }
    const now = ctx.currentTime;
    player.startAt = now + 0.05 - clamped;
    player.nextTime = now + 0.05;
    player.nextIndex = index;
    if (player.buffers[index]) {
      scheduleBuffer(index, offset);
      player.nextIndex = index + 1;
    }
    scheduleAvailable();
    player.playing = true;
    player.paused = false;
    player.finished = false;
    setPlayVisual();
    setPlayerBadge("playing", "is-live");
    updatePlaybackUI();
    syncControls();
  }

  function finishPlayback() {
    if (!player.playing) return;
    player.playing = false;
    player.paused = false;
    player.finished = true;
    setPlayVisual();
    setPlayerBadge("done", "is-done");
    syncControls();
  }

  function stopAll(fromSpeak = false) {
    const generating = state.job?.status === "running";
    if (generating) {
      const id = state.job?.id;
      if (id) {
        fetch(`/api/jobs/${id}/cancel`, { method: "POST" }).catch(() => {});
      }
      closeJobStream();
      resetPlayer(true);
      state.job = null;
      resetJobUI();
      setStatus("Stopped.", "");
    } else {
      haltSources();
      player.finished = false;
      player.lastDisplaySecond = -1;
      setPlayVisual();
      setPlayerBadge("stopped", "");
      if (!fromSpeak) setStatus("Stopped.", "");
    }
    syncControls();
  }

  async function replayJob(jobId) {
    if (!jobId) return;
    try {
      const snapshot = await api(`/api/jobs/${jobId}`);
      if (!snapshot.chunks || !snapshot.chunks.length) {
        toast("Audio expired", "That generation is no longer on the server. Run it again.", "error");
        return;
      }
      closeJobStream();
      resetPlayer(true);
      state.job = {
        id: snapshot.id,
        status: snapshot.status,
        text: snapshot.text,
        total: snapshot.chunks.length,
        received: snapshot.chunks.length,
        seen: new Set(snapshot.chunks.map((chunk) => chunk.index)),
        parts: snapshot.chunks.map((chunk) => ({
          language: chunk.language,
          text: chunk.text,
          ready: true,
          seconds: chunk.seconds,
        })),
        duration: snapshot.duration,
        es: null,
      };
      player.jobId = snapshot.id;
      player.generationDone = true;
      player.expectedChunks = snapshot.chunks.length;
      player.autoplayAllowed = true;
      els.playerTitle.textContent = snippet(snapshot.text, 120);
      els.playerTitle.classList.remove("is-empty");
      renderParts(state.job.parts);
      setProgress(1);
      setStatus(`Playing ${fmtTime(snapshot.duration)} of audio`, "ok");
      els.statusMeta.textContent = `${snapshot.chunks.length} parts · ${fmtTime(snapshot.duration)}`;
      setPlayerBadge("loading", "is-live");
      await ensureCtx();
      for (const chunk of snapshot.chunks) loadChunk(chunk);
      syncControls();
    } catch (error) {
      toast("Could not load audio", error.message || String(error), "error");
    }
  }

  function updatePlaybackUI() {
    if (!player.ctx || player.total <= 0) {
      if (player.lastDisplaySecond !== 0) {
        els.timeCurrent.textContent = "0:00";
        els.timeTotal.textContent = "0:00";
        els.seek.classList.remove("has-progress");
        player.lastDisplaySecond = 0;
      }
      return;
    }
    const elapsed = clamp(player.startAt > 0 ? player.ctx.currentTime - player.startAt : 0, 0, player.total);
    const ratio = clamp(elapsed / player.total, 0, 1);
    const percent = `${(ratio * 100).toFixed(2)}%`;
    els.seekFill.style.width = percent;
    els.seekThumb.style.left = percent;
    els.seek.classList.toggle("has-progress", ratio > 0.001);
    els.seek.setAttribute("aria-valuenow", String(Math.round(elapsed)));
    els.seek.setAttribute("aria-valuemax", String(Math.round(player.total)));
    const second = Math.floor(elapsed);
    if (second !== player.lastDisplaySecond) {
      player.lastDisplaySecond = second;
      els.timeCurrent.textContent = fmtTime(elapsed);
      els.timeTotal.textContent = fmtTime(player.total);
    }
    if (
      player.generationDone &&
      player.playing &&
      !player.paused &&
      player.sources.length === 0 &&
      player.ctx.currentTime >= player.nextTime - 0.02
    ) {
      finishPlayback();
    }
  }

  /* ---------------------------------------------------------------- waveform */

  function drawWave() {
    const canvas = els.wave;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const width = Math.round(rect.width);
    const height = Math.round(rect.height);
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    const g = canvas.getContext("2d");
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, width, height);

    const mid = height / 2;
    const active = Boolean(player.analyser && player.playing && !player.paused && !player.finished);
    let samples = null;
    if (active) {
      samples = new Uint8Array(player.analyser.fftSize);
      player.analyser.getByteTimeDomainData(samples);
    }

    waveState.phase += active ? 0.05 : 0.014;
    const points = Math.max(56, Math.floor(width / 3));
    const amplitude = active ? mid * 0.62 : mid * 0.34;

    g.beginPath();
    for (let i = 0; i <= points; i += 1) {
      const p = i / points;
      const x = p * width;
      let value;
      if (samples) {
        value = (samples[Math.floor(p * (samples.length - 1))] - 128) / 128;
      } else {
        value =
          Math.sin(p * Math.PI * 4 + waveState.phase) * 0.55 +
          Math.sin(p * Math.PI * 9 - waveState.phase * 1.6) * 0.22 +
          Math.sin(p * Math.PI * 15 + waveState.phase * 0.7) * 0.08;
      }
      const envelope = Math.sin(Math.PI * p);
      const y = mid - value * envelope * amplitude;
      if (i === 0) g.moveTo(x, y);
      else g.lineTo(x, y);
    }

    const stroke = g.createLinearGradient(0, 0, width, 0);
    stroke.addColorStop(0, "rgba(255, 180, 104, 0.95)");
    stroke.addColorStop(0.5, "rgba(255, 111, 145, 0.95)");
    stroke.addColorStop(1, "rgba(94, 234, 212, 0.95)");
    g.strokeStyle = stroke;
    g.lineWidth = active ? 2.1 : 1.7;
    g.lineJoin = "round";
    g.lineCap = "round";
    g.shadowColor = "rgba(255, 138, 92, 0.55)";
    g.shadowBlur = active ? 16 : 10;
    g.stroke();

    g.lineTo(width, mid);
    g.lineTo(0, mid);
    g.closePath();
    const fill = g.createLinearGradient(0, 0, 0, height);
    fill.addColorStop(0, active ? "rgba(255, 138, 92, 0.22)" : "rgba(255, 138, 92, 0.1)");
    fill.addColorStop(1, "rgba(255, 138, 92, 0)");
    g.shadowBlur = 0;
    g.fillStyle = fill;
    g.fill();
  }

  function tick() {
    drawWave();
    updatePlaybackUI();
    requestAnimationFrame(tick);
  }

  /* -------------------------------------------------------------------- UI */

  function setStatus(message, kind = "") {
    els.statusText.textContent = message;
    els.statusLine.classList.toggle("is-ok", kind === "ok");
    els.statusLine.classList.toggle("is-error", kind === "error");
    els.statusLine.classList.toggle("is-busy", kind === "busy");
  }

  function setProgress(fraction) {
    const percent = `${clamp(fraction, 0, 1) * 100}%`;
    els.progressFill.style.width = percent;
  }

  function setPlayerBadge(text, className) {
    els.playerBadge.textContent = text;
    els.playerBadge.className = `badge ${className || ""}`.trim();
  }

  function updateSpeedVisual() {
    els.speedValue.textContent = `${state.speed.toFixed(2)}×`;
    const percent = ((state.speed - 0.5) / 1.5) * 100;
    els.speed.style.setProperty("--val", `${percent}%`);
  }

  function updateStats() {
    const text = els.text.value;
    const chars = text.length;
    const words = (text.trim().match(/\S+/g) || []).length;
    const bn = (text.match(/[\u0980-\u09FF]/g) || []).length;
    const en = (text.match(/[A-Za-z]/g) || []).length;
    const seconds = bn / 12.5 + en / 15 + Math.max(0, words - bn / 4 - en / 5) * 0.06;
    els.statChars.textContent = chars.toLocaleString();
    els.statWords.textContent = words.toLocaleString();
    els.statEst.textContent = `~${fmtTime(seconds)} audio`;
  }

  function applyPrefsToUI() {
    $$(".seg-btn").forEach((button) => {
      const active = button.dataset.lang === state.lang;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    els.speed.value = String(state.speed);
    els.loudness.checked = state.loudness;
    els.steadyBn.checked = state.steadyBn;
    els.autoplay.checked = state.autoplay;
    updateSpeedVisual();
    updateVoiceTrigger();
  }

  function syncControls() {
    const generating = state.job?.status === "running";
    const hasAudio = playerHasAudio();
    const terminalStatus = ["done", "cancelled", "error"].includes(state.job?.status);
    const canExport = Boolean(state.job?.id && terminalStatus && hasAudio);

    els.speakBtn.disabled = generating;
    els.speakBtn.classList.toggle("is-busy", generating);
    els.speakLabel.textContent = generating ? "Generating" : "Speak";

    els.stopBtn.disabled = !(generating || hasAudio);
    els.stopBtn2.disabled = !(generating || hasAudio);
    els.exportBtn.disabled = !canExport;
    els.exportBtn2.disabled = !canExport;
    els.playBtn.disabled = !(hasAudio || (state.job?.id && !generating));
  }

  function openModal(tab = "models") {
    els.modal.hidden = false;
    document.body.classList.add("modal-open");
    switchTab(tab);
    renderModalModels();
    renderClonePanel();
    const close = els.modal.querySelector(".modal-head [data-close]");
    if (close) setTimeout(() => close.focus(), 20);
  }

  function closeModal() {
    els.modal.hidden = true;
    document.body.classList.remove("modal-open");
    closeVoiceMenu();
    closeExportMenu();
    if (state.cloneRecording) {
      try {
        state.cloneRecording.recorder.stop();
      } catch {
        /* ignore */
      }
    }
  }

  function switchTab(tab) {
    $$(".tab", els.modal).forEach((button) => button.classList.toggle("is-active", button.dataset.tab === tab));
    $("#tab-models").hidden = tab !== "models";
    $("#tab-clone").hidden = tab !== "clone";
    $("#tab-about").hidden = tab !== "about";
    $("#modal-title").textContent =
      tab === "about" ? "About Kotha Bondhu" : tab === "clone" ? "Voice cloning" : "Models";
    if (tab === "clone") renderClonePanel();
  }

  function openExportMenu() {
    if (els.exportBtn.disabled) return;
    els.exportMenu.hidden = false;
  }

  function closeExportMenu() {
    els.exportMenu.hidden = true;
  }

  async function exportAudio(format) {
    closeExportMenu();
    if (!state.job?.id) return;
    const label = format.toUpperCase();
    const tracker = toast(`Preparing ${label}`, "Rendering the full file…", "info", { timeout: 0 });
    try {
      const response = await fetch(`/api/jobs/${state.job.id}/export?format=${format}`);
      if (!response.ok) {
        let message = `Export failed (${response.status})`;
        try {
          const data = await response.json();
          if (data.error) message = data.error;
        } catch {
          /* ignore */
        }
        throw new Error(message);
      }
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") || "";
      const match = /filename="?([^";]+)"?/i.exec(disposition);
      const filename = match ? match[1] : `kotha-bondhu.${format}`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      tracker.update("Download started", filename, "success");
      setTimeout(() => tracker.close(), 2600);
    } catch (error) {
      tracker.update("Export failed", error.message || String(error), "error");
      setTimeout(() => tracker.close(), 8000);
    }
  }

  /* ------------------------------------------------------------------ events */

  function bind() {
    els.text.addEventListener("input", () => {
      updateStats();
      clearTimeout(draftTimer);
      draftTimer = setTimeout(saveDraft, 280);
    });

    $$(".seg-btn").forEach((button) => {
      button.addEventListener("click", () => {
        state.lang = button.dataset.lang;
        savePrefs();
        applyPrefsToUI();
        closeVoiceMenu();
      });
    });

    els.speed.addEventListener("input", () => {
      state.speed = clamp(Number(els.speed.value) || 1, 0.5, 2);
      updateSpeedVisual();
      savePrefs();
    });

    els.loudness.addEventListener("change", () => {
      state.loudness = els.loudness.checked;
      savePrefs();
    });
    els.steadyBn.addEventListener("change", () => {
      state.steadyBn = els.steadyBn.checked;
      savePrefs();
    });
    els.autoplay.addEventListener("change", () => {
      state.autoplay = els.autoplay.checked;
      player.autoplayAllowed = state.autoplay;
      savePrefs();
      // Turning auto-play off should not cut the sentence that is already
      // speaking; it just stops new chunks from being queued.
      if (state.autoplay && playerHasAudio() && !player.playing) restartPlayback();
    });

    els.voiceTrigger.addEventListener("click", () => {
      if (els.voiceMenu.hidden) openVoiceMenu();
      else closeVoiceMenu();
    });

    els.voiceSearch.addEventListener("input", () => {
      clearTimeout(voiceSearchTimer);
      voiceSearchTimer = setTimeout(() => renderVoiceList(els.voiceSearch.value), 80);
    });

    els.voiceList.addEventListener("click", (event) => {
      const previewClone = event.target.closest("[data-preview-clone]");
      if (previewClone) {
        event.stopPropagation();
        previewCloneVoice(previewClone.dataset.previewClone, previewClone);
        return;
      }
      const preview = event.target.closest("[data-preview]");
      if (preview) {
        event.stopPropagation();
        previewVoice(preview.dataset.preview, preview);
        return;
      }
      const cloneOption = event.target.closest("[data-clone-voice]");
      if (cloneOption) {
        setCloneVoice(cloneOption.dataset.cloneVoice);
        return;
      }
      const option = event.target.closest("[data-voice]");
      if (option) setVoice(option.dataset.voice);
    });

    els.voiceList.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const cloneOption = event.target.closest("[data-clone-voice]");
      if (cloneOption) {
        event.preventDefault();
        setCloneVoice(cloneOption.dataset.cloneVoice);
        return;
      }
      const option = event.target.closest("[data-voice]");
      if (option) {
        event.preventDefault();
        setVoice(option.dataset.voice);
      }
    });

    document.addEventListener("pointerdown", (event) => {
      if (!els.voiceMenu.hidden && !els.voiceMenu.contains(event.target) && !els.voiceTrigger.contains(event.target)) {
        closeVoiceMenu();
      }
      if (!els.exportMenu.hidden && !els.exportMenu.contains(event.target) && !els.exportBtn.contains(event.target)) {
        closeExportMenu();
      }
    });

    els.speakBtn.addEventListener("click", speak);
    els.stopBtn.addEventListener("click", () => stopAll());
    els.stopBtn2.addEventListener("click", () => stopAll());
    els.playBtn.addEventListener("click", togglePlay);

    els.exportBtn.addEventListener("click", () => {
      if (els.exportMenu.hidden) openExportMenu();
      else closeExportMenu();
    });
    els.exportBtn2.addEventListener("click", () => exportAudio("wav"));
    els.exportMenu.addEventListener("click", (event) => {
      const button = event.target.closest("[data-format]");
      if (button) exportAudio(button.dataset.format);
    });

    els.muteBtn.addEventListener("click", () => {
      player.muted = !player.muted;
      if (player.gain) player.gain.gain.value = player.muted ? 0 : 1;
      els.muteBtn.classList.toggle("is-muted", player.muted);
    });

    els.seek.addEventListener("pointerdown", (event) => {
      if (!playerHasAudio() || player.total <= 0) return;
      els.seek.classList.add("is-dragging");
      try {
        els.seek.setPointerCapture(event.pointerId);
      } catch {
        /* ignore */
      }
      seekFromPointer(event);
    });
    els.seek.addEventListener("pointermove", (event) => {
      if (els.seek.classList.contains("is-dragging")) seekFromPointer(event);
    });
    const endDrag = () => els.seek.classList.remove("is-dragging");
    els.seek.addEventListener("pointerup", endDrag);
    els.seek.addEventListener("pointercancel", endDrag);

    els.sampleBtn.addEventListener("click", () => {
      state.samples = (state.samples + 1) % SAMPLES.length;
      els.text.value = SAMPLES[state.samples];
      updateStats();
      saveDraft();
      els.text.focus();
    });

    els.clearBtn.addEventListener("click", () => {
      els.text.value = "";
      updateStats();
      saveDraft();
      els.text.focus();
    });

    els.historyList.addEventListener("click", (event) => {
      const item = event.target.closest(".history-item");
      if (!item) return;
      const entry = state.history.find((candidate) => candidate.id === item.dataset.id);
      if (!entry) return;
      if (event.target.closest("[data-history-del]")) {
        removeHistory(entry.id);
        return;
      }
      if (event.target.closest("[data-history-play]")) {
        if (state.job?.status === "running") stopAll(true);
        restoreHistory(entry);
        replayJob(entry.id);
        return;
      }
      restoreHistory(entry);
    });

    els.clearHistory.addEventListener("click", () => {
      if (!state.history.length) return;
      state.history = [];
      saveHistory();
      toast("History cleared", "Your saved runs were removed from this browser.", "info");
    });

    els.themeBtn.addEventListener("click", toggleTheme);
    els.themeInline.addEventListener("click", toggleTheme);

    const openModels = () => openModal("models");
    els.railModels.addEventListener("click", openModels);
    els.railClone.addEventListener("click", () => openModal("clone"));
    els.voiceCloneManage.addEventListener("click", () => {
      closeVoiceMenu();
      openModal("clone");
    });
    els.railAbout.addEventListener("click", () => openModal("about"));
    els.modelsBtnInline.addEventListener("click", openModels);
    els.manageModels.addEventListener("click", openModels);

    els.modal.addEventListener("click", (event) => {
      if (event.target.closest("[data-close]")) {
        closeModal();
        return;
      }
      const tab = event.target.closest(".tab");
      if (tab) switchTab(tab.dataset.tab);
    });

    els.modalModels.addEventListener("click", (event) => {
      const button = event.target.closest("[data-download]");
      if (!button) return;
      const model = state.models.find((candidate) => candidate.key === button.dataset.download);
      if (model?.ready) {
        refreshStatus(true);
        toast("Re-checking models", "Verified from local cache.", "info", { timeout: 2500 });
        return;
      }
      startDownload(button.dataset.download);
    });

    els.openCache.addEventListener("click", async () => {
      try {
        await api("/api/open-cache", { method: "POST" });
        toast("Opened cache", "Model files live in this folder.", "success", { timeout: 2500 });
      } catch (error) {
        toast("Could not open folder", error.message || String(error), "error");
      }
    });

    els.cloneTokenSave.addEventListener("click", saveCloneToken);
    els.cloneToken.addEventListener("keydown", (event) => {
      if (event.key === "Enter") saveCloneToken();
    });
    els.cloneProfile.addEventListener("change", () => {
      els.cloneProfile.dataset.touched = "1";
    });
    els.cloneSetup.addEventListener("click", () =>
      startCloneStream("setup", `/api/clone/setup?profile=${encodeURIComponent(els.cloneProfile.value)}`, "Installing clone runtime")
    );
    els.cloneDownload.addEventListener("click", () =>
      startCloneStream("download", "/api/clone/download", "Downloading IndicF5")
    );
    els.cloneVoiceFile.addEventListener("change", () => {
      const file = els.cloneVoiceFile.files && els.cloneVoiceFile.files[0];
      state.cloneAudio = file || null;
      els.cloneFileName.textContent = file ? `${file.name} · ${fmtBytes(file.size)}` : "No clip chosen";
    });
    els.cloneMic.addEventListener("click", toggleMic);
    els.cloneAdd.addEventListener("click", addCloneVoice);
    els.cloneVoiceList.addEventListener("click", (event) => {
      const preview = event.target.closest("[data-clone-preview]");
      if (preview) {
        previewCloneVoice(preview.dataset.clonePreview, preview);
        return;
      }
      const use = event.target.closest("[data-clone-use]");
      if (use) {
        setCloneVoice(use.dataset.cloneUse);
        return;
      }
      const remove = event.target.closest("[data-clone-delete]");
      if (remove) deleteCloneVoice(remove.dataset.cloneDelete);
    });

    els.dropZone.addEventListener("dragover", (event) => {
      event.preventDefault();
      els.dropZone.classList.add("is-dragging");
    });
    els.dropZone.addEventListener("dragleave", (event) => {
      if (!els.dropZone.contains(event.relatedTarget)) els.dropZone.classList.remove("is-dragging");
    });
    els.dropZone.addEventListener("drop", async (event) => {
      event.preventDefault();
      els.dropZone.classList.remove("is-dragging");
      const file = event.dataTransfer?.files?.[0];
      if (!file) return;
      if (file.size > 2 * 1024 * 1024) {
        toast("File is too large", "Drop a text file under 2 MB.", "error");
        return;
      }
      try {
        els.text.value = await file.text();
        updateStats();
        saveDraft();
        toast("Loaded text", `${file.name} · ${els.text.value.length.toLocaleString()} characters`, "success", {
          timeout: 3000,
        });
      } catch (error) {
        toast("Could not read file", error.message || String(error), "error");
      }
    });

    document.addEventListener("keydown", (event) => {
      const active = document.activeElement;
      const tag = active?.tagName || "";
      const inField = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || active?.isContentEditable;
      const onButton = tag === "BUTTON";

      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        speak();
        return;
      }
      if (event.key === "Escape") {
        if (!els.modal.hidden) closeModal();
        else if (!els.voiceMenu.hidden) closeVoiceMenu();
        else if (!els.exportMenu.hidden) closeExportMenu();
        else if (state.job?.status === "running" || playerHasAudio()) stopAll();
        return;
      }
      if (event.key === "/" && !inField && !onButton && els.modal.hidden) {
        event.preventDefault();
        els.text.focus();
        return;
      }
      if (event.code === "Space" && !inField && !onButton && els.modal.hidden && (state.job || playerHasAudio())) {
        event.preventDefault();
        togglePlay();
      }
    });

    window.addEventListener("pagehide", () => {
      if (state.job?.status === "running" && state.job.id) {
        try {
          navigator.sendBeacon(`/api/jobs/${state.job.id}/cancel`);
        } catch {
          /* ignore */
        }
      }
    });
  }

  function seekFromPointer(event) {
    const rect = els.seek.getBoundingClientRect();
    const ratio = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1);
    if (player.total > 0) seekTo(ratio * player.total);
  }

  /* -------------------------------------------------------------------- init */

  function cacheEls() {
    els = {
      statusPill: $("#status-pill"),
      statusPillText: $("#status-pill-text"),
      themeBtn: $("#theme-btn"),
      themeInline: $("#theme-btn-inline"),
      railModels: $("#rail-models"),
      railClone: $("#rail-clone"),
      railAbout: $("#rail-about"),
      text: $("#text"),
      dropZone: $("#drop-zone"),
      statChars: $("#stat-chars"),
      statWords: $("#stat-words"),
      statEst: $("#stat-est"),
      sampleBtn: $("#sample-btn"),
      clearBtn: $("#clear-btn"),
      voiceTrigger: $("#voice-trigger"),
      voiceAvatar: $("#voice-avatar"),
      voiceName: $("#voice-name"),
      voiceStyle: $("#voice-style"),
      voiceNote: $("#voice-note"),
      voiceMenu: $("#voice-menu"),
      voiceSearch: $("#voice-search"),
      voiceList: $("#voice-list"),
      voiceCloneManage: $("#voice-clone-manage"),
      cloneWarning: $("#clone-warning"),
      cloneChips: $("#clone-chips"),
      cloneToken: $("#clone-token"),
      cloneTokenSave: $("#clone-token-save"),
      cloneProfile: $("#clone-profile"),
      cloneSetup: $("#clone-setup"),
      cloneDownload: $("#clone-download"),
      cloneModelCopy: $("#clone-model-copy"),
      cloneProgress: $("#clone-progress"),
      cloneProgressFill: $("#clone-progress-fill"),
      cloneProgressLabel: $("#clone-progress-label"),
      cloneLog: $("#clone-log"),
      cloneVoiceName: $("#clone-voice-name"),
      cloneVoiceText: $("#clone-voice-text"),
      cloneVoiceFile: $("#clone-voice-file"),
      cloneFileName: $("#clone-file-name"),
      cloneConsent: $("#clone-consent"),
      cloneMic: $("#clone-mic"),
      cloneAdd: $("#clone-add"),
      cloneVoiceList: $("#clone-voice-list"),
      speed: $("#speed"),
      speedValue: $("#speed-value"),
      loudness: $("#loudness"),
      steadyBn: $("#steady-bn"),
      autoplay: $("#autoplay"),
      speakBtn: $("#speak-btn"),
      speakLabel: $("#speak-btn .btn-label"),
      stopBtn: $("#stop-btn"),
      exportBtn: $("#export-btn"),
      exportMenu: $("#export-menu"),
      modelsBtnInline: $("#models-btn-inline"),
      modelCount: $("#model-count"),
      progressFill: $("#progress-fill"),
      progress: $(".progress"),
      statusLine: $(".status-line"),
      statusText: $("#status-text"),
      statusMeta: $("#status-meta"),
      playerBadge: $("#player-badge"),
      playerTitle: $("#player-title"),
      wave: $("#wave"),
      playBtn: $("#play-btn"),
      stopBtn2: $("#stop-btn-2"),
      exportBtn2: $("#export-btn-2"),
      muteBtn: $("#mute-btn"),
      seek: $("#seek"),
      seekFill: $("#seek-fill"),
      seekThumb: $("#seek-thumb"),
      timeCurrent: $("#time-current"),
      timeTotal: $("#time-total"),
      parts: $("#parts"),
      miniModelList: $("#mini-model-list"),
      manageModels: $("#manage-models"),
      historyList: $("#history-list"),
      clearHistory: $("#clear-history"),
      modal: $("#modal"),
      modalModels: $("#modal-model-list"),
      modalTitle: $("#modal-title"),
      cachePath: $("#cache-path"),
      openCache: $("#open-cache"),
      toasts: $("#toasts"),
    };
  }

  function init() {
    cacheEls();
    loadPrefs();
    loadHistory();

    let theme = "dark";
    try {
      theme = localStorage.getItem(LS.theme) || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    } catch {
      /* ignore */
    }
    setTheme(theme);

    els.text.value = loadDraft();
    updateStats();
    applyPrefsToUI();
    renderHistory();
    renderParts([]);
    syncControls();
    bind();

    setStatus("Ready when you are.", "");
    refreshStatus(false);
    requestAnimationFrame(tick);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
