const SESSION_KEY = "editbot_session_id";

const videoSelect = document.getElementById("videoSelect");
const videoPlayer = document.getElementById("videoPlayer");
const videoInfo = document.getElementById("videoInfo");

const crosshairX = document.getElementById("crosshairX");
const crosshairY = document.getElementById("crosshairY");
const crosshairDot = document.getElementById("crosshairDot");

const pixelXEl = document.getElementById("pixelX");
const pixelYEl = document.getElementById("pixelY");
const percentXEl = document.getElementById("percentX");
const percentYEl = document.getElementById("percentY");
const coordPayload = document.getElementById("coordPayload");
const copyCoordsBtn = document.getElementById("copyCoordsBtn");
const openMainBtn = document.getElementById("openMainBtn");

const query = new URLSearchParams(window.location.search);
let sessionId = (query.get("session_id") || "").trim();
let requestedVideoId = (query.get("video_id") || "").trim();
let uploads = [];

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function isVideoFile(file) {
  const type = (file.content_type || "").toLowerCase();
  if (type.startsWith("video/")) return true;
  const name = (file.name || "").toLowerCase();
  return [
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv",
    ".webm", ".m4v", ".mpeg", ".mpg", ".3gp", ".ts", ".mts"
  ].some((ext) => name.endsWith(ext));
}

function getSessionId() {
  if (sessionId) {
    sessionStorage.setItem(SESSION_KEY, sessionId);
    return sessionId;
  }

  const existing = sessionStorage.getItem(SESSION_KEY);
  if (existing) {
    sessionId = existing;
    return sessionId;
  }

  sessionId = (crypto && crypto.randomUUID)
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  sessionStorage.setItem(SESSION_KEY, sessionId);
  return sessionId;
}

function setCrosshairVisible(visible) {
  const opacity = visible ? "1" : "0";
  crosshairX.style.opacity = opacity;
  crosshairY.style.opacity = opacity;
  crosshairDot.style.opacity = opacity;
}

function setCoordinateValues(xPx, yPx, xPct, yPct) {
  pixelXEl.textContent = String(xPx);
  pixelYEl.textContent = String(yPx);
  percentXEl.textContent = `${xPct.toFixed(2)}%`;
  percentYEl.textContent = `${yPct.toFixed(2)}%`;
  coordPayload.value = JSON.stringify(
    {
      x: xPx,
      y: yPx,
      percent_x: Number(xPct.toFixed(4)),
      percent_y: Number(yPct.toFixed(4)),
      mode: "overlay",
    },
    null,
    2
  );
}

function resetCoordinateValues() {
  pixelXEl.textContent = "-";
  pixelYEl.textContent = "-";
  percentXEl.textContent = "-";
  percentYEl.textContent = "-";
  coordPayload.value = JSON.stringify(
    {
      x: null,
      y: null,
      percent_x: null,
      percent_y: null,
      mode: "overlay",
    },
    null,
    2
  );
}

function updateVideoInfo() {
  const w = videoPlayer.videoWidth || 0;
  const h = videoPlayer.videoHeight || 0;
  const duration = Number.isFinite(videoPlayer.duration) ? videoPlayer.duration : 0;
  if (!w || !h) {
    videoInfo.textContent = "No video loaded";
    return;
  }
  videoInfo.textContent = `${w}x${h} • ${duration.toFixed(2)}s`;
}

function setVideoSource(videoId) {
  const sid = getSessionId();
  if (!videoId) {
    videoPlayer.removeAttribute("src");
    videoPlayer.load();
    videoInfo.textContent = "No uploaded videos in this session";
    return;
  }
  const src = `/api/video/${encodeURIComponent(videoId)}?session_id=${encodeURIComponent(sid)}&_=${Date.now()}`;
  videoPlayer.src = src;
  videoPlayer.load();
}

function renderVideoOptions() {
  videoSelect.innerHTML = "";
  if (!uploads.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No uploaded videos found";
    videoSelect.appendChild(opt);
    videoSelect.disabled = true;
    setVideoSource("");
    return;
  }

  videoSelect.disabled = false;
  uploads.forEach((file) => {
    const opt = document.createElement("option");
    opt.value = file.id;
    opt.textContent = file.name;
    if (file.id === requestedVideoId) {
      opt.selected = true;
    }
    videoSelect.appendChild(opt);
  });

  if (!requestedVideoId || !uploads.some((f) => f.id === requestedVideoId)) {
    requestedVideoId = uploads[0].id;
    videoSelect.value = requestedVideoId;
  }

  setVideoSource(requestedVideoId);
}

async function loadUploads() {
  const sid = getSessionId();
  try {
    const response = await fetch(`/api/uploads?session_id=${encodeURIComponent(sid)}`);
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`);
    }
    const data = await response.json();
    uploads = (data.files || []).filter(isVideoFile);
    renderVideoOptions();
  } catch (error) {
    console.error(error);
    uploads = [];
    renderVideoOptions();
    videoInfo.textContent = "Failed to load uploads";
  }
}

function updateCursorFromEvent(event) {
  if (!videoPlayer.videoWidth || !videoPlayer.videoHeight) return;
  const rect = videoPlayer.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return;

  const x = clamp(event.clientX - rect.left, 0, rect.width);
  const y = clamp(event.clientY - rect.top, 0, rect.height);

  const xRatio = x / rect.width;
  const yRatio = y / rect.height;

  const xPx = Math.round(xRatio * videoPlayer.videoWidth);
  const yPx = Math.round(yRatio * videoPlayer.videoHeight);
  const xPct = xRatio * 100;
  const yPct = yRatio * 100;

  crosshairX.style.top = `${y}px`;
  crosshairY.style.left = `${x}px`;
  crosshairDot.style.left = `${x}px`;
  crosshairDot.style.top = `${y}px`;
  setCrosshairVisible(true);

  setCoordinateValues(xPx, yPx, xPct, yPct);
}

async function copyPayload() {
  const text = coordPayload.value || "";
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    const old = copyCoordsBtn.textContent;
    copyCoordsBtn.textContent = "Copied";
    setTimeout(() => {
      copyCoordsBtn.textContent = old;
    }, 1000);
  } catch (_) {
    coordPayload.select();
    document.execCommand("copy");
  }
}

videoSelect.addEventListener("change", () => {
  requestedVideoId = videoSelect.value;
  setVideoSource(requestedVideoId);
});

videoPlayer.addEventListener("loadedmetadata", updateVideoInfo);
videoPlayer.addEventListener("mousemove", updateCursorFromEvent);
videoPlayer.addEventListener("click", updateCursorFromEvent);
videoPlayer.addEventListener("mouseleave", () => setCrosshairVisible(false));

copyCoordsBtn.addEventListener("click", copyPayload);

openMainBtn.addEventListener("click", () => {
  window.location.href = "/";
});

resetCoordinateValues();
setCrosshairVisible(false);
loadUploads();
