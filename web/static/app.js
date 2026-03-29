const state = {
  uploads: [],
  activeUploadId: null,
  configs: null,
  tools: null,
  sessionId: null,
  lastPrompt: null,
  supportedFormats: {
    video: [],
    image: [],
    audio: []
  }
};

const statusPill = document.getElementById("statusPill");
const refreshBtn = document.getElementById("refreshBtn");
const timelineBtn = document.getElementById("timelineBtn");
const positionHelperBtn = document.getElementById("positionHelperBtn");
const toggleDrawerBtn = document.getElementById("toggleDrawerBtn");
const themeToggleBtn = document.getElementById("themeToggleBtn");
const fileInput = document.getElementById("fileInput");
const browseBtn = document.getElementById("browseBtn");
const dropZone = document.getElementById("dropZone");
const uploadQueue = document.getElementById("uploadQueue");
const activeFileSelect = document.getElementById("activeFileSelect");
const clearUploadsBtn = document.getElementById("clearUploadsBtn");
const configGrid = document.getElementById("configGrid");
const toolList = document.getElementById("toolList");
const exampleList = document.getElementById("exampleList");
const chipRow = document.getElementById("chipRow");
const chatWindow = document.getElementById("chatWindow");
const composerForm = document.getElementById("composerForm");
const promptInput = document.getElementById("promptInput");
const sendBtn = document.querySelector(".send-btn");
const chatPanel = document.querySelector(".chat-panel");
const scrollToBottomBtn = document.getElementById("scrollToBottomBtn");

const layoutRoot = document.getElementById("layoutRoot");
const drawer = document.getElementById("drawer");
const drawerTitle = document.getElementById("drawerTitle");
const drawerSub = document.getElementById("drawerSub");
const drawerClose = document.getElementById("drawerClose");
const sectionTabs = Array.from(document.querySelectorAll(".section-tab"));
const navLinks = Array.from(document.querySelectorAll(".nav-link"));
const drawerSections = Array.from(document.querySelectorAll(".drawer-section"));

const processTracker = document.getElementById("processTracker");
const trackerSteps = document.getElementById("trackerSteps");
const trackerSub = document.getElementById("trackerSub");
const trackerBadge = document.getElementById("trackerBadge");
const trackerBar = document.getElementById("trackerBar");

const SESSION_KEY = "editbot_session_id";
const THEME_KEY = "editbot_theme";
const CHAT_SCROLL_THRESHOLD = 2;

function applyTheme(theme) {
  const useDark = theme === "dark";
  document.body.classList.toggle("dark", useDark);
  if (themeToggleBtn) {
    themeToggleBtn.setAttribute("aria-pressed", useDark ? "true" : "false");
    themeToggleBtn.setAttribute(
      "aria-label",
      useDark ? "Switch to light mode" : "Switch to dark mode"
    );
  }
}

function initTheme() {
  let savedTheme = localStorage.getItem(THEME_KEY);
  if (!savedTheme) {
    savedTheme = (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches)
      ? "dark"
      : "light";
  }
  applyTheme(savedTheme);
}

function getSessionId() {
  let id = sessionStorage.getItem(SESSION_KEY);
  if (!id) {
    id = (crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    sessionStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

state.sessionId = getSessionId();

const drawerMeta = {
  configs: {
    title: "Configurations",
    sub: "Available fonts, sizes, transitions, and style modes detected from your system configs."
  },
  tools: {
    title: "Tools",
    sub: "Automation modules available to the system right now."
  },
  examples: {
    title: "Example Prompts",
    sub: "Copy a template prompt to get the edit right the first time."
  }
};

const processState = {
  timer: null,
  steps: [],
  current: 0,
  active: false,
  messageEl: null
};

const uiState = {
  busy: false,
  thinkingEl: null,
  autoScroll: true
};
let lastChatScrollTop = 0;

function autoResizeTextarea() {
  if (!promptInput) return;
  promptInput.style.height = "auto";
  const lineHeight = parseFloat(getComputedStyle(promptInput).lineHeight) || 20;
  const maxHeight = lineHeight * 5 + 12;
  const nextHeight = Math.min(promptInput.scrollHeight, maxHeight);
  promptInput.style.height = `${nextHeight}px`;
  promptInput.style.overflowY = promptInput.scrollHeight > maxHeight ? "auto" : "hidden";
}

function distanceFromChatBottom() {
  if (!chatWindow) return 0;
  return chatWindow.scrollHeight - chatWindow.scrollTop - chatWindow.clientHeight;
}

function isChatNearBottom() {
  return distanceFromChatBottom() <= CHAT_SCROLL_THRESHOLD;
}

function updateScrollToBottomButton() {
  if (!scrollToBottomBtn) return;
  const canScroll = chatWindow && chatWindow.scrollHeight > chatWindow.clientHeight + 8;
  const show = Boolean(canScroll && !isChatNearBottom());
  scrollToBottomBtn.classList.toggle("visible", show);
}

function scrollChatToBottom(force = false) {
  if (!chatWindow) return;
  if (force || uiState.autoScroll) {
    chatWindow.scrollTop = chatWindow.scrollHeight;
  }
  lastChatScrollTop = chatWindow.scrollTop;
  updateScrollToBottomButton();
}

let currentPanel = null;

function escapeHtml(value) {
  const div = document.createElement("div");
  div.innerText = value;
  return div.innerHTML;
}

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let idx = 0;
  let size = bytes;
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024;
    idx += 1;
  }
  return `${size.toFixed(size >= 10 || idx === 0 ? 0 : 1)} ${units[idx]}`;
}

function setStatus(message, ok = true) {
  statusPill.textContent = message;
  statusPill.style.background = ok ? "rgba(46, 196, 182, 0.18)" : "rgba(255, 107, 53, 0.15)";
  statusPill.style.color = ok ? "#0b6b63" : "#b34116";
}

function addMessage(role, text, options = {}) {
  const message = document.createElement("div");
  message.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "ME" : "EB";

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (options.title) {
    const title = document.createElement("div");
    title.className = "bubble-title";
    title.innerText = options.title;
    bubble.appendChild(title);
  }

  const body = document.createElement("div");
  body.className = "bubble-text";
  let renderDone = Promise.resolve();
  if (options.html) {
    body.innerHTML = text;
  } else if (options.typewriter && role === "assistant") {
    body.innerText = "";
    renderDone = typewriter(body, text, {
      speed: options.speed,
      markdown: options.markdown
    });
  } else if (options.markdown) {
    body.classList.add("markdown");
    body.innerHTML = renderMarkdown(text);
  } else {
    body.innerText = text;
  }

  bubble.appendChild(body);

  if (role === "user") {
    message.appendChild(bubble);
    message.appendChild(avatar);
  } else {
    message.appendChild(avatar);
    message.appendChild(bubble);
  }

  chatWindow.appendChild(message);
  scrollChatToBottom(role === "user" || options.forceScroll === true);

  if (role === "user" && chatPanel) {
    chatPanel.classList.add("has-messages");
    updateScrollToBottomButton();
  }

  return { message, done: renderDone };
}

function typewriter(element, text, options = {}) {
  const speed = options.speed ?? 18;
  const chars = Array.from(text || "");
  let idx = 0;
  const useMarkdown = Boolean(options.markdown);
  if (useMarkdown) {
    element.classList.add("markdown");
  }
  return new Promise((resolve) => {
    if (chars.length === 0) {
      resolve();
      return;
    }
    const tick = () => {
      if (idx >= chars.length) {
        resolve();
        return;
      }
      if (useMarkdown) {
        const slice = chars.slice(0, idx + 1).join("");
        element.innerHTML = renderMarkdown(slice);
      } else {
        element.textContent += chars[idx];
      }
      idx += 1;
      scrollChatToBottom();
      if (idx < chars.length) {
        setTimeout(tick, speed);
      } else {
        resolve();
      }
    };
    tick();
  });
}

function renderMarkdown(md) {
  if (!md) return "";
  const escaped = escapeHtml(md).replace(/\r/g, "");
  const lines = escaped.split("\n");
  let html = "";
  let inCode = false;
  let inUl = false;
  let inOl = false;

  const closeLists = () => {
    if (inUl) {
      html += "</ul>";
      inUl = false;
    }
    if (inOl) {
      html += "</ol>";
      inOl = false;
    }
  };

  const inlineFormat = (text) => {
    let formatted = text;
    formatted = formatted.replace(/`([^`]+)`/g, "<code>$1</code>");
    formatted = formatted.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    formatted = formatted.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
    formatted = formatted.replace(/(^|[^_])_([^_]+)_(?!_)/g, "$1<em>$2</em>");
    return formatted;
  };

  lines.forEach((raw) => {
    const line = raw.trimEnd();
    if (line.startsWith("```")) {
      if (inCode) {
        html += "</code></pre>";
        inCode = false;
      } else {
        closeLists();
        inCode = true;
        html += "<pre><code>";
      }
      return;
    }

    if (inCode) {
      html += `${line}\n`;
      return;
    }

    if (!line.trim()) {
      closeLists();
      html += "<br>";
      return;
    }

    if (line.startsWith("### ")) {
      closeLists();
      html += `<h3>${inlineFormat(line.slice(4))}</h3>`;
      return;
    }
    if (line.startsWith("## ")) {
      closeLists();
      html += `<h2>${inlineFormat(line.slice(3))}</h2>`;
      return;
    }
    if (line.startsWith("# ")) {
      closeLists();
      html += `<h1>${inlineFormat(line.slice(2))}</h1>`;
      return;
    }

    const olMatch = line.match(/^(\d+)\.\s+(.*)/);
    if (olMatch) {
      if (!inOl) {
        closeLists();
        html += "<ol>";
        inOl = true;
      }
      html += `<li>${inlineFormat(olMatch[2])}</li>`;
      return;
    }

    if (line.startsWith("- ") || line.startsWith("* ")) {
      if (!inUl) {
        closeLists();
        html += "<ul>";
        inUl = true;
      }
      html += `<li>${inlineFormat(line.slice(2))}</li>`;
      return;
    }

    closeLists();
    html += `<p>${inlineFormat(line)}</p>`;
  });

  if (inCode) {
    html += "</code></pre>";
  }
  if (inUl) {
    html += "</ul>";
  }
  if (inOl) {
    html += "</ol>";
  }

  return html;
}

function setComposerDisabled(disabled) {
  uiState.busy = disabled;
  if (sendBtn) {
    sendBtn.disabled = disabled;
  }
  if (promptInput) {
    promptInput.disabled = disabled;
  }
  if (composerForm) {
    composerForm.classList.toggle("is-busy", disabled);
  }
}

function showThinking() {
  if (uiState.thinkingEl) return;
  const message = document.createElement("div");
  message.className = "message assistant thinking-message";

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = "EB";

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  const body = document.createElement("div");
  body.className = "bubble-text";
  body.innerHTML = "Thinking <span class=\"thinking-dots\"><span></span><span></span><span></span></span>";

  bubble.appendChild(body);
  message.appendChild(avatar);
  message.appendChild(bubble);
  chatWindow.appendChild(message);
  scrollChatToBottom();

  uiState.thinkingEl = message;
}

function hideThinking() {
  if (!uiState.thinkingEl) return;
  uiState.thinkingEl.remove();
  uiState.thinkingEl = null;
  updateScrollToBottomButton();
}

function getExtension(filename) {
  const parts = filename.split(".");
  return parts.length > 1 ? `.${parts.pop().toLowerCase()}` : "";
}

function classifyFile(filename) {
  const ext = getExtension(filename);
  if (state.supportedFormats.video.includes(ext)) return "video";
  if (state.supportedFormats.image.includes(ext)) return "image";
  if (state.supportedFormats.audio.includes(ext)) return "audio";
  return "asset";
}

function renderUploads() {
  uploadQueue.innerHTML = "";
  activeFileSelect.innerHTML = "";

  if (!state.uploads.length) {
    state.activeUploadId = null;
    uploadQueue.innerHTML = "<div class=\"file-sub\">No uploads yet.</div>";
    const option = document.createElement("option");
    option.textContent = "Upload a video to select";
    option.value = "";
    activeFileSelect.appendChild(option);
    return;
  }

  const mediaUploads = state.uploads.filter((file) => file.kind === "video" || file.kind === "image");
  if (!state.activeUploadId && mediaUploads.length) {
    state.activeUploadId = mediaUploads[0].id;
  }

  if (mediaUploads.length === 0) {
    state.activeUploadId = null;
    const option = document.createElement("option");
    option.textContent = "No video/image files uploaded";
    option.value = "";
    activeFileSelect.appendChild(option);
  } else {
    mediaUploads.forEach((file) => {
      const option = document.createElement("option");
      option.value = file.id;
      option.textContent = file.name;
      if (file.id === state.activeUploadId) {
        option.selected = true;
      }
      activeFileSelect.appendChild(option);
    });
  }

  state.uploads.forEach((file) => {
    const item = document.createElement("div");
    item.className = "file-item";

    const meta = document.createElement("div");
    meta.className = "file-meta";

    const title = document.createElement("div");
    title.className = "file-title";
    title.innerText = file.name;

    const sub = document.createElement("div");
    sub.className = "file-sub";
    sub.innerText = `${file.kind.toUpperCase()} · ${formatBytes(file.size)}`;

    meta.appendChild(title);
    meta.appendChild(sub);

    const actions = document.createElement("div");
    actions.className = "file-actions";

    const tag = document.createElement("div");
    tag.className = "file-tag";
    tag.innerText = file.kind;

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "file-remove";
    removeBtn.setAttribute("aria-label", `Remove ${file.name}`);
    removeBtn.textContent = "x";
    removeBtn.addEventListener("click", () => {
      removeUpload(file.id);
    });

    actions.appendChild(tag);
    actions.appendChild(removeBtn);

    item.appendChild(meta);
    item.appendChild(actions);
    uploadQueue.appendChild(item);
  });
}

async function removeUpload(fileId) {
  if (!fileId) return;
  try {
    const response = await fetch(
      `/api/upload/${encodeURIComponent(fileId)}?session_id=${encodeURIComponent(state.sessionId)}`,
      { method: "DELETE" }
    );
    if (!response.ok) {
      throw new Error(`Delete failed: ${response.status}`);
    }

    state.uploads = state.uploads.filter((file) => file.id !== fileId);
    if (state.activeUploadId === fileId) {
      const nextActiveVideo = state.uploads.find((file) => file.kind === "video" || file.kind === "image");
      state.activeUploadId = nextActiveVideo ? nextActiveVideo.id : null;
    }

    renderUploads();
    setStatus("File removed", true);
  } catch (error) {
    console.error(error);
    setStatus("Failed to remove file", false);
  }
}

function renderConfigSection(title, body) {
  const section = document.createElement("div");
  section.className = "config-section";

  const heading = document.createElement("h3");
  heading.innerText = title;

  section.appendChild(heading);
  section.appendChild(body);
  configGrid.appendChild(section);
}

function renderConfigs(configs) {
  configGrid.innerHTML = "";
  if (!configs) return;

  const fonts = configs.fonts?.fonts || [];
  const fontSizes = configs.text_styles?.font_sizes || {};
  const positions = configs.positions?.positions || {};
  const highlights = configs.highlight_styles?.styles || {};
  const transitions = configs.transitions?.transitions || [];
  const colors = configs.colors?.colors || {};

  const fontList = document.createElement("div");
  fontList.className = "chip-list";
  fonts.slice(0, 12).forEach((font) => {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.innerText = font.name;
    fontList.appendChild(chip);
  });
  renderConfigSection("Fonts", fontList);

  const sizeList = document.createElement("div");
  sizeList.className = "chip-list";
  Object.entries(fontSizes).forEach(([name, value]) => {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.innerText = `${name}: ${value}`;
    sizeList.appendChild(chip);
  });
  renderConfigSection("Font Sizes", sizeList);

  const positionList = document.createElement("div");
  positionList.className = "chip-list";
  Object.keys(positions).forEach((name) => {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.innerText = name.replace("_", " ");
    positionList.appendChild(chip);
  });
  renderConfigSection("Positions", positionList);

  const highlightList = document.createElement("div");
  highlightList.className = "chip-list";
  Object.keys(highlights).forEach((name) => {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.innerText = name.replace(/_/g, " ");
    highlightList.appendChild(chip);
  });
  renderConfigSection("Highlight Styles", highlightList);

  const colorList = document.createElement("div");
  colorList.className = "chip-list";
  Object.entries(colors).slice(0, 10).forEach(([name, value]) => {
    const chip = document.createElement("div");
    chip.className = "color-chip";
    const dot = document.createElement("span");
    dot.className = "color-dot";
    if (value?.rgb) {
      dot.style.background = `rgb(${value.rgb.join(",")})`;
    }
    const label = document.createElement("span");
    label.innerText = name;
    chip.appendChild(dot);
    chip.appendChild(label);
    colorList.appendChild(chip);
  });
  renderConfigSection("Color Palette", colorList);

  const transitionList = document.createElement("div");
  transitionList.className = "scroll-list";
  transitions.slice(0, 12).forEach((transition) => {
    const item = document.createElement("div");
    item.className = "scroll-item";
    item.innerText = `${transition.name} (${transition.code})`;
    transitionList.appendChild(item);
  });
  renderConfigSection("Transitions", transitionList);

  const stockList = document.createElement("div");
  stockList.className = "chip-list";
  ["overlay", "insert"].forEach((mode) => {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.innerText = mode;
    stockList.appendChild(chip);
  });
  renderConfigSection("Stock Footage Modes", stockList);
}

function renderTools(data) {
  toolList.innerHTML = "";
  exampleList.innerHTML = "";

  if (!data) return;

  data.tools.forEach((tool) => {
    const item = document.createElement("div");
    item.className = "tool-item";

    const title = document.createElement("h4");
    title.innerText = tool.name || tool.id;
    const desc = document.createElement("p");
    desc.innerText = tool.description || "";

    const meta = document.createElement("div");
    meta.className = "tool-meta";

    const chip = document.createElement("div");
    chip.className = "tool-chip";
    chip.innerText = tool.category || "general";

    meta.appendChild(chip);
    item.appendChild(title);
    item.appendChild(desc);
    item.appendChild(meta);
    toolList.appendChild(item);
  });

  data.examples.forEach((example) => {
    const card = document.createElement("div");
    card.className = "example-item";

    const text = document.createElement("p");
    text.innerText = example;

    const btn = document.createElement("button");
    btn.className = "ghost";
    btn.type = "button";
    btn.innerText = "Use prompt";
    btn.addEventListener("click", () => {
      promptInput.value = example;
      promptInput.focus();
      autoResizeTextarea();
    });

    card.appendChild(text);
    card.appendChild(btn);
    exampleList.appendChild(card);
  });
}

function setQuickChips() {
  const chips = [
    "Add captions with bold yellow text",
    "Remove silences and filler words",
    "Apply cross dissolve transitions",
    "Insert stock footage at 00:42",
    "Generate TikTok style captions"
  ];

  chipRow.innerHTML = "";
  chips.forEach((chipText) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.innerText = chipText;
    chip.addEventListener("click", () => {
      promptInput.value = chipText;
      promptInput.focus();
      autoResizeTextarea();
    });
    chipRow.appendChild(chip);
  });
}

function openPanel(panelId) {
  if (!panelId) return;

  if (!layoutRoot.classList.contains("drawer-closed") && currentPanel === panelId) {
    closeDrawer();
    return;
  }

  const meta = drawerMeta[panelId] || drawerMeta.configs;
  drawerTitle.textContent = meta.title;
  drawerSub.textContent = meta.sub;

  drawerSections.forEach((section) => {
    section.classList.toggle("active", section.dataset.panel === panelId);
  });

  sectionTabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.panel === panelId);
  });
  navLinks.forEach((link) => {
    link.classList.toggle("active", link.dataset.panel === panelId);
  });

  layoutRoot.classList.remove("drawer-closed");
  currentPanel = panelId;
}

function closeDrawer() {
  layoutRoot.classList.add("drawer-closed");
  sectionTabs.forEach((tab) => tab.classList.remove("active"));
  navLinks.forEach((link) => link.classList.remove("active"));
  currentPanel = null;
}

function openTimeline() {
  const params = new URLSearchParams();
  params.set("session_id", state.sessionId);
  if (state.activeUploadId) {
    params.set("video_id", state.activeUploadId);
  }
  const target = `/timeline?${params.toString()}`;
  window.open(target, "_blank", "noopener");
}

function openPositionHelper() {
  const params = new URLSearchParams();
  params.set("session_id", state.sessionId);
  if (state.activeUploadId) {
    params.set("video_id", state.activeUploadId);
  }
  const target = `/position-helper?${params.toString()}`;
  window.open(target, "_blank", "noopener");
}

function toggleDrawer() {
  if (layoutRoot.classList.contains("drawer-closed")) {
    openPanel("configs");
  } else {
    closeDrawer();
  }
}

function buildProcessSteps(prompt) {
  const lower = (prompt || "").toLowerCase();
  const steps = [];

  const stockKeywords = ["stock", "b-roll", "broll", "cutaway"];
  const silenceKeywords = ["silence", "filler", "remove silence", "cut silence", "trim silence", "remove pauses"];
  const captionKeywords = ["caption", "subtitle", "transcribe", "karaoke", "highlight"];
  const transitionKeywords = ["transition", "crossfade", "dissolve", "wipe", "dip to black", "xfade"];
  const rotateKeywords = ["rotate", "rotation", "clockwise", "counterclockwise", "anticlockwise"];
  const audioKeywords = ["extract audio", "audio only", "export audio"];
  const transcriptionKeywords = ["transcribe", "transcription", "speech to text"];

  if (stockKeywords.some((k) => lower.includes(k))) {
    steps.push("Stock footage pass");
  }
  if (silenceKeywords.some((k) => lower.includes(k))) {
    steps.push("Silence + filler cut");
  }
  if (audioKeywords.some((k) => lower.includes(k))) {
    steps.push("Extracting audio");
  }
  if (transcriptionKeywords.some((k) => lower.includes(k))) {
    steps.push("Transcribing audio");
  }
  if (transitionKeywords.some((k) => lower.includes(k))) {
    steps.push("Transitions" );
  }
  if (rotateKeywords.some((k) => lower.includes(k))) {
    steps.push("Rotate media");
  }
  if (captionKeywords.some((k) => lower.includes(k))) {
    steps.push("Captions + styling");
  }

  if (steps.length === 0) {
    steps.push("Preparing edit");
  }
  steps.push("Render output");
  return steps;
}

function isLikelyAction(prompt) {
  const lower = (prompt || "").toLowerCase();
  const keywords = [
    "caption",
    "subtitle",
    "cut",
    "trim",
    "silence",
    "stock",
    "b-roll",
    "broll",
    "transition",
    "rotate",
    "rotation",
    "clockwise",
    "counterclockwise",
    "anticlockwise",
    "cross dissolve",
    "crossfade",
    "extract audio",
    "audio only",
    "transcribe",
    "transcription",
    "caption file",
    "video info",
    "metadata",
    "resolution",
    "fps"
  ];
  return keywords.some((k) => lower.includes(k));
}

function looksLikeQuestion(prompt) {
  const lower = (prompt || "").toLowerCase().trim();
  if (!lower) return false;
  if (lower.includes("?")) return true;
  const starters = ["what ", "what's", "which ", "how ", "can you", "do you", "does it", "is it", "are you"];
  if (starters.some((s) => lower.startsWith(s))) return true;
  if (lower.includes("extension") || lower.includes("format") || lower.includes("file type") || lower.includes("capabilities") || lower.includes("features")) {
    return true;
  }
  return false;
}

function renderTracker(steps) {
  trackerSteps.innerHTML = "";
  steps.forEach((label, index) => {
    const row = document.createElement("div");
    row.className = "tracker-step";
    row.dataset.index = index;

    const name = document.createElement("div");
    name.textContent = label;

    const state = document.createElement("div");
    state.className = "state";

    const dot = document.createElement("span");
    dot.className = "dot";

    const text = document.createElement("span");
    text.textContent = "Queued";

    state.appendChild(dot);
    state.appendChild(text);

    row.appendChild(name);
    row.appendChild(state);
    trackerSteps.appendChild(row);
  });
}

function updateTrackerStep(index, status, label) {
  const row = trackerSteps.querySelector(`[data-index="${index}"]`);
  if (!row) return;
  row.classList.remove("queued", "running", "done", "error");
  row.classList.add(status);
  const text = row.querySelector(".state span:last-child");
  if (text) {
    text.textContent = label;
  }
}

function startProcessTracker(prompt) {
  const steps = buildProcessSteps(prompt);
  processState.steps = steps;
  processState.current = 0;
  processState.active = true;

  if (processState.messageEl && processState.messageEl.parentElement) {
    processState.messageEl.remove();
  }

  const message = document.createElement("div");
  message.className = "message assistant process-message";

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = "EB";

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  bubble.appendChild(processTracker);
  message.appendChild(avatar);
  message.appendChild(bubble);
  chatWindow.appendChild(message);
  scrollChatToBottom();

  processState.messageEl = message;

  renderTracker(steps);
  processTracker.hidden = false;
  trackerBadge.textContent = "Running";
  trackerSub.textContent = `Starting: ${steps[0]}`;
  trackerBar.style.width = "0%";

  steps.forEach((_, idx) => updateTrackerStep(idx, "queued", "Queued"));
  updateTrackerStep(0, "running", "Running");

  if (processState.timer) {
    clearInterval(processState.timer);
  }

  processState.timer = setInterval(() => {
    if (!processState.active) return;
    if (processState.current < steps.length - 1) {
      updateTrackerStep(processState.current, "done", "Done");
      processState.current += 1;
      updateTrackerStep(processState.current, "running", "Running");
      trackerSub.textContent = `Now: ${steps[processState.current]}`;
      const progress = Math.round((processState.current / steps.length) * 100);
      trackerBar.style.width = `${Math.min(progress, 95)}%`;
    }
  }, 2600);
}

function finishProcessTracker(success) {
  processState.active = false;
  if (processState.timer) {
    clearInterval(processState.timer);
    processState.timer = null;
  }

  if (success) {
    processState.steps.forEach((_, idx) => updateTrackerStep(idx, "done", "Done"));
    trackerBadge.textContent = "Complete";
    trackerSub.textContent = "Outputs ready. Review the results below.";
    trackerBar.style.width = "100%";
  } else {
    updateTrackerStep(processState.current, "error", "Failed");
    trackerBadge.textContent = "Failed";
    trackerSub.textContent = "Check the error details in the chat.";
    trackerBar.style.width = "45%";
  }
}

function holdProcessTracker(message) {
  processState.active = false;
  if (processState.timer) {
    clearInterval(processState.timer);
    processState.timer = null;
  }
  trackerBadge.textContent = "Needs Info";
  trackerSub.textContent = message || "Waiting for clarification.";
  trackerBar.style.width = "35%";
  updateTrackerStep(processState.current, "queued", "Waiting");
}

function addClarification(question, options = []) {
  const message = document.createElement("div");
  message.className = "message assistant";

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = "EB";

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  const title = document.createElement("div");
  title.className = "bubble-title";
  title.innerText = "Clarification needed";

  const body = document.createElement("div");
  body.className = "bubble-text";
  body.innerText = question || "Please clarify how you want to resolve the timeline conflict.";

  bubble.appendChild(title);
  bubble.appendChild(body);

  if (options.length) {
    const optionRow = document.createElement("div");
    optionRow.className = "clarification-options";

    options.forEach((opt) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "clarification-btn";
      btn.innerText = opt;
      btn.addEventListener("click", () => {
        const mergedPrompt = `${state.lastPrompt || ""}\nClarification: ${opt}`;
        submitPrompt(mergedPrompt, true);
      });
      optionRow.appendChild(btn);
    });

    bubble.appendChild(optionRow);
  }

  message.appendChild(avatar);
  message.appendChild(bubble);
  chatWindow.appendChild(message);
  scrollChatToBottom();
}

async function apiGet(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

async function refreshData() {
  try {
    const configs = await apiGet("/api/configs");
    state.configs = configs;
    state.supportedFormats.video = configs.supported_formats?.video_extensions?.input || [];
    state.supportedFormats.image = configs.supported_formats?.image_extensions?.input || [];
    state.supportedFormats.audio = configs.supported_formats?.audio_extensions?.input || [];
    renderConfigs(configs);

    const tools = await apiGet("/api/tools");
    state.tools = tools;
    renderTools(tools);

    const uploads = await apiGet(`/api/uploads?session_id=${encodeURIComponent(state.sessionId)}`);
    state.uploads = uploads.files.map((file) => ({
      ...file,
      kind: classifyFile(file.name)
    }));
    renderUploads();

    setStatus("Server ready", true);
  } catch (error) {
    console.error(error);
    setStatus("Server offline", false);
  }
}

async function uploadFiles(files) {
  if (!files.length) return;
  const formData = new FormData();
  Array.from(files).forEach((file) => {
    formData.append("files", file);
  });
  formData.append("session_id", state.sessionId);

  setStatus("Uploading files...", true);

  const response = await fetch("/api/upload", {
    method: "POST",
    body: formData
  });

  if (!response.ok) {
    setStatus("Upload failed", false);
    return;
  }

  const data = await response.json();
  const updated = data.files.map((file) => ({
    ...file,
    kind: classifyFile(file.name)
  }));
  state.uploads = [...state.uploads, ...updated];
  renderUploads();
  setStatus("Upload complete", true);
}

browseBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (event) => {
  uploadFiles(event.target.files);
  fileInput.value = "";
});

clearUploadsBtn.addEventListener("click", () => {
  fetch(`/api/session/cleanup?session_id=${encodeURIComponent(state.sessionId)}`, {
    method: "POST",
    keepalive: true
  }).finally(() => {
    state.uploads = [];
    state.activeUploadId = null;
    renderUploads();
  });
});

activeFileSelect.addEventListener("change", (event) => {
  state.activeUploadId = event.target.value;
});

["dragenter", "dragover"].forEach((evt) => {
  dropZone.addEventListener(evt, (event) => {
    event.preventDefault();
    dropZone.classList.add("dragover");
  });
});

["dragleave", "drop"].forEach((evt) => {
  dropZone.addEventListener(evt, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragover");
  });
});

dropZone.addEventListener("drop", (event) => {
  const files = event.dataTransfer.files;
  uploadFiles(files);
});

sectionTabs.forEach((tab) => {
  tab.addEventListener("click", () => openPanel(tab.dataset.panel));
});

navLinks.forEach((link) => {
  link.addEventListener("click", () => openPanel(link.dataset.panel));
});

if (toggleDrawerBtn) {
  toggleDrawerBtn.addEventListener("click", toggleDrawer);
}

if (drawerClose) {
  drawerClose.addEventListener("click", closeDrawer);
}

if (timelineBtn) {
  timelineBtn.addEventListener("click", openTimeline);
}
if (positionHelperBtn) {
  positionHelperBtn.addEventListener("click", openPositionHelper);
}

async function submitPrompt(prompt, fromClarification = false) {
  const trimmed = (prompt || "").trim();
  if (!trimmed) return;
  if (uiState.busy) return;
  setComposerDisabled(true);
  uiState.autoScroll = true;

  addMessage("user", trimmed);
  promptInput.value = "";
  autoResizeTextarea();

  state.lastPrompt = trimmed;
  const shouldTrack = isLikelyAction(trimmed) && !looksLikeQuestion(trimmed);

  showThinking();

  try {
    // Use SSE streaming endpoint for chat
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: trimmed,
        video_id: state.activeUploadId || null,
        session_id: state.sessionId
      })
    });

    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let streamBubble = null;
    let streamBody = null;
    let fullResponse = "";
    let gotAction = false;
    let actionIntent = null;
    let actionMessage = null;
    let revealedLength = 0;
    let revealTimer = null;
    const REVEAL_SPEED = 12; // ms per character

    function revealTick() {
      if (revealedLength >= fullResponse.length) {
        revealTimer = null;
        return;
      }
      // Reveal multiple chars per tick for fast bursts, single char for typing feel
      const charsToReveal = Math.min(3, fullResponse.length - revealedLength);
      revealedLength += charsToReveal;
      if (streamBody) {
        const visible = fullResponse.slice(0, revealedLength);
        streamBody.innerHTML = renderMarkdown(visible);
        scrollChatToBottom();
      }
      if (revealedLength < fullResponse.length) {
        revealTimer = setTimeout(revealTick, REVEAL_SPEED);
      } else {
        revealTimer = null;
      }
    }

    function scheduleReveal() {
      if (!revealTimer && revealedLength < fullResponse.length) {
        revealTimer = setTimeout(revealTick, REVEAL_SPEED);
      }
    }

    hideThinking();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const jsonStr = line.slice(6).trim();
        if (!jsonStr) continue;

        let event;
        try {
          event = JSON.parse(jsonStr);
        } catch {
          continue;
        }

        if (event.type === "token") {
          // Create bubble on first token
          if (!streamBubble) {
            const msg = document.createElement("div");
            msg.className = "message assistant";

            const avatar = document.createElement("div");
            avatar.className = "avatar";
            avatar.textContent = "EB";

            const bubble = document.createElement("div");
            bubble.className = "bubble";

            const body = document.createElement("div");
            body.className = "bubble-text markdown streaming";

            bubble.appendChild(body);
            msg.appendChild(avatar);
            msg.appendChild(bubble);
            chatWindow.appendChild(msg);

            streamBubble = msg;
            streamBody = body;
          }

          fullResponse += event.content;
          scheduleReveal();
        }

        if (event.type === "done") {
          // Flush any remaining buffered characters
          if (revealTimer) {
            clearTimeout(revealTimer);
            revealTimer = null;
          }
          if (streamBody) {
            revealedLength = fullResponse.length;
            streamBody.classList.remove("streaming");
            if (fullResponse) {
              streamBody.innerHTML = renderMarkdown(fullResponse);
            }
          }
          if (chatPanel) {
            chatPanel.classList.add("has-messages");
          }
        }

        if (event.type === "clarification") {
          addClarification(event.question, event.options || []);
          if (shouldTrack) {
            holdProcessTracker(event.question || "Waiting for clarification.");
          }
        }

        if (event.type === "action") {
          // This is an action intent - fall back to non-streaming /api/chat
          gotAction = true;
          actionIntent = event.intent;
          actionMessage = event.message || trimmed;
        }

        if (event.type === "error") {
          addMessage("assistant", event.content || "Something went wrong.", { title: "Error" });
        }
      }
    }

    // If we got an action intent, process it through the regular chat endpoint
    if (gotAction) {
      if (shouldTrack) {
        startProcessTracker(trimmed);
        addMessage("assistant", "Processing your request. This can take a few minutes depending on model size and video length.", { title: "Queued" });
      }
      showThinking();

      const actionResponse = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: actionMessage,
          video_id: state.activeUploadId || null,
          session_id: state.sessionId
        })
      });

      if (!actionResponse.ok) {
        throw new Error(`Process failed: ${actionResponse.status}`);
      }

      const result = await actionResponse.json();
      hideThinking();

      if (result.needs_clarification) {
        if (shouldTrack) {
          holdProcessTracker(result.clarification?.question || "Waiting for clarification.");
        }
        addClarification(result.clarification?.question, result.clarification?.options || []);
        setComposerDisabled(false);
        return;
      }

      if (result.reply) {
        addMessage("assistant", result.reply, { markdown: true });
      }

      if (result.success && result.outputs) {
        const outputs = result.outputs || {};
        if (Object.keys(outputs).length) {
          if (shouldTrack) finishProcessTracker(true);
          const lines = Object.entries(outputs).map(([key, value]) => `<strong>${escapeHtml(key)}</strong>: ${escapeHtml(value)}`);
          addMessage("assistant", `Done. Outputs:<br>${lines.join("<br>")}`, { html: true, title: "Complete" });
          setComposerDisabled(false);
          return;
        }
      }

      if (result.success) {
        if (shouldTrack) finishProcessTracker(true);
      } else {
        if (shouldTrack) finishProcessTracker(false);
        const errors = (result.errors || []).map((err) => `• ${escapeHtml(err)}`).join("<br>");
        addMessage("assistant", `Processing failed.<br>${errors}`, { html: true, title: "Error" });
      }
    }

    setComposerDisabled(false);
  } catch (error) {
    console.error(error);
    hideThinking();
    if (shouldTrack) {
      finishProcessTracker(false);
    }
    addMessage("assistant", "Something went wrong while processing. Check the server logs for details.", { title: "Error" });
    setComposerDisabled(false);
  }
}

composerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const prompt = promptInput.value.trim();
  submitPrompt(prompt);
});

if (promptInput) {
  promptInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      const prompt = promptInput.value.trim();
      if (prompt && !uiState.busy) {
        submitPrompt(prompt);
      }
    }
  });
}

if (chatWindow) {
  chatWindow.addEventListener("scroll", () => {
    const currentTop = chatWindow.scrollTop;
    const userScrolledUp = currentTop < lastChatScrollTop;
    if (userScrolledUp) {
      uiState.autoScroll = false;
    } else if (isChatNearBottom()) {
      uiState.autoScroll = true;
    }
    lastChatScrollTop = currentTop;
    updateScrollToBottomButton();
  });

  chatWindow.addEventListener("wheel", (event) => {
    if (event.deltaY < 0) {
      uiState.autoScroll = false;
      updateScrollToBottomButton();
    }
  }, { passive: true });

  chatWindow.addEventListener("pointerdown", () => {
    // User interaction inside chat should always allow manual scroll control.
    if (!isChatNearBottom()) {
      uiState.autoScroll = false;
      updateScrollToBottomButton();
    }
  });
}

if (scrollToBottomBtn) {
  scrollToBottomBtn.addEventListener("click", () => {
    uiState.autoScroll = true;
    scrollChatToBottom(true);
  });
}

refreshBtn.addEventListener("click", refreshData);

if (themeToggleBtn) {
  themeToggleBtn.addEventListener("click", () => {
    const isDark = document.body.classList.contains("dark");
    const nextTheme = isDark ? "light" : "dark";
    localStorage.setItem(THEME_KEY, nextTheme);
    applyTheme(nextTheme);
  });
}

setQuickChips();
refreshData();
initTheme();

if (promptInput) {
  promptInput.addEventListener("input", autoResizeTextarea);
  window.addEventListener("load", autoResizeTextarea);
}

window.addEventListener("resize", updateScrollToBottomButton);
updateScrollToBottomButton();

window.addEventListener("beforeunload", () => {
  const url = `/api/session/cleanup?session_id=${encodeURIComponent(state.sessionId)}`;
  if (navigator.sendBeacon) {
    navigator.sendBeacon(url);
  } else {
    fetch(url, { method: "POST", keepalive: true });
  }
});
