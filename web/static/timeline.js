/* ─── EditBot Timeline Viewer – Logic ─── */
(() => {
  "use strict";

  // ─── Detect dark mode from system preference ───
  if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    document.body.classList.add("dark");
  }

  // ─── DOM refs ───
  const videoSelect = document.getElementById("videoSelect");
  const videoPlayer = document.getElementById("videoPlayer");
  const playBtn = document.getElementById("playBtn");
  const skipBackBtn = document.getElementById("skipBackBtn");
  const skipFwdBtn = document.getElementById("skipFwdBtn");
  const speedSelect = document.getElementById("speedSelect");
  const currentTimeDisplay = document.getElementById("currentTimeDisplay");
  const totalTimeDisplay = document.getElementById("totalTimeDisplay");
  const markInBtn = document.getElementById("markInBtn");
  const markOutBtn = document.getElementById("markOutBtn");
  const selectionDisplay = document.getElementById("selectionDisplay");
  const copyRangeBtn = document.getElementById("copyRangeBtn");
  const clearMarksBtn = document.getElementById("clearMarksBtn");
  const rulerWrap = document.getElementById("rulerWrap");
  const rulerCanvas = document.getElementById("rulerCanvas");
  const trackWrap = document.getElementById("trackWrap");
  const trackCanvas = document.getElementById("trackCanvas");
  const playhead = document.getElementById("playhead");
  const selectionOverlay = document.getElementById("selectionOverlay");
  const addMarkerBtn = document.getElementById("addMarkerBtn");
  const copyAllBtn = document.getElementById("copyAllBtn");
  const markersList = document.getElementById("markersList");
  const openMainBtn = document.getElementById("openMainBtn");
  const rotateBtn = document.getElementById("rotateBtn");
  const toastEl = document.getElementById("toast");

  // ─── State ───
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get("session_id") || "";
  const preselectedVideoId = params.get("video_id") || "";

  let duration = 0;
  let inPoint = null;
  let outPoint = null;
  let markers = [];
  let isSeeking = false;
  let animFrame = null;
  let thumbnailData = null; // will hold offscreen waveform
  let rotationDeg = 0;

  // ─── Helpers ───
  const fmt = (t) => {
    if (t == null || isNaN(t)) return "0.000s";
    return t.toFixed(3) + "s";
  };

  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    setTimeout(() => toastEl.classList.remove("show"), 2000);
  }

  // ─── Load uploads ───
  async function loadUploads() {
    try {
      const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
      const res = await fetch(`/api/uploads${qs}`);
      const data = await res.json();
      const files = data.files || [];
      videoSelect.innerHTML = "";

      const videoExts = [".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv"];
      const videos = files.filter((f) => {
        const ext = (f.name || "").toLowerCase().match(/\.[^.]+$/);
        return ext && videoExts.includes(ext[0]);
      });

      if (videos.length === 0) {
        const opt = document.createElement("option");
        opt.textContent = "No videos uploaded";
        opt.disabled = true;
        videoSelect.appendChild(opt);
        return;
      }

      videos.forEach((f) => {
        const opt = document.createElement("option");
        opt.value = f.id;
        opt.textContent = f.name;
        videoSelect.appendChild(opt);
      });

      // Auto-select
      if (preselectedVideoId && videos.some((v) => v.id === preselectedVideoId)) {
        videoSelect.value = preselectedVideoId;
      }
      loadVideo(videoSelect.value);
    } catch (e) {
      console.error("Failed to load uploads", e);
    }
  }

  function loadVideo(videoId) {
    if (!videoId) return;
    const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    videoPlayer.src = `/api/video/${videoId}${qs}`;
    videoPlayer.load();

    // Reset state
    inPoint = null;
    outPoint = null;
    markers = [];
    updateSelectionUI();
    renderMarkers();
  }

  videoSelect.addEventListener("change", () => loadVideo(videoSelect.value));

  // ─── Video events ───
  videoPlayer.addEventListener("loadedmetadata", () => {
    duration = videoPlayer.duration || 0;
    totalTimeDisplay.textContent = fmt(duration);
    resizeCanvases();
    drawRuler();
    drawTrack();
    updatePlayhead();
  });

  videoPlayer.addEventListener("play", () => { playBtn.innerHTML = "&#9646;&#9646;"; startAnimLoop(); });
  videoPlayer.addEventListener("pause", () => { playBtn.innerHTML = "&#9654;"; stopAnimLoop(); updatePlayhead(); });
  videoPlayer.addEventListener("ended", () => { playBtn.innerHTML = "&#9654;"; stopAnimLoop(); updatePlayhead(); });
  videoPlayer.addEventListener("timeupdate", () => {
    if (!isSeeking) {
      currentTimeDisplay.textContent = fmt(videoPlayer.currentTime);
    }
  });

  // ─── Transport controls ───
  playBtn.addEventListener("click", () => {
    if (videoPlayer.paused || videoPlayer.ended) videoPlayer.play();
    else videoPlayer.pause();
  });

  skipBackBtn.addEventListener("click", () => {
    videoPlayer.currentTime = Math.max(0, videoPlayer.currentTime - frameDuration());
    currentTimeDisplay.textContent = fmt(videoPlayer.currentTime);
    updatePlayhead();
  });

  skipFwdBtn.addEventListener("click", () => {
    videoPlayer.currentTime = Math.min(duration, videoPlayer.currentTime + frameDuration());
    currentTimeDisplay.textContent = fmt(videoPlayer.currentTime);
    updatePlayhead();
  });

  speedSelect.addEventListener("change", () => {
    videoPlayer.playbackRate = parseFloat(speedSelect.value) || 1;
  });

  // ─── Rotate ───
  rotateBtn.addEventListener("click", () => {
    rotationDeg = (rotationDeg + 90) % 360;
    applyRotation();
    toast(`Rotated ${rotationDeg}°`);
  });

  function applyRotation() {
    const isVertical = rotationDeg === 90 || rotationDeg === 270;
    videoPlayer.style.transform = `rotate(${rotationDeg}deg)`;
    if (isVertical) {
      // Scale down so the rotated video fits within its container
      const wrap = videoPlayer.parentElement;
      const wrapW = wrap.clientWidth;
      const wrapH = wrap.clientHeight || wrapW * 0.5625;
      const scale = Math.min(wrapH / wrapW, wrapW / wrapH, 1);
      videoPlayer.style.transform = `rotate(${rotationDeg}deg) scale(${scale.toFixed(4)})`;
    }
  }

  function frameDuration() {
    // Approximate single frame (~30fps → ~0.033s)
    return 1 / 30;
  }

  // ─── In / Out marks ───
  markInBtn.addEventListener("click", () => setIn(videoPlayer.currentTime));
  markOutBtn.addEventListener("click", () => setOut(videoPlayer.currentTime));
  clearMarksBtn.addEventListener("click", clearMarks);
  copyRangeBtn.addEventListener("click", copyRange);

  function setIn(t) {
    inPoint = t;
    if (outPoint !== null && outPoint < inPoint) outPoint = null;
    updateSelectionUI();
    toast(`In: ${fmt(t)}`);
  }

  function setOut(t) {
    outPoint = t;
    if (inPoint !== null && inPoint > outPoint) inPoint = null;
    updateSelectionUI();
    toast(`Out: ${fmt(t)}`);
  }

  function clearMarks() {
    inPoint = null;
    outPoint = null;
    updateSelectionUI();
  }

  function copyRange() {
    if (inPoint === null || outPoint === null) {
      toast("Set both In and Out points first");
      return;
    }
    const dur = outPoint - inPoint;
    const payload = `{ "start": ${inPoint.toFixed(3)}, "end": ${outPoint.toFixed(3)}, "duration": ${dur.toFixed(3)} }`;
    navigator.clipboard.writeText(payload).then(() => toast("Range copied!"));
  }

  function updateSelectionUI() {
    if (inPoint !== null && outPoint !== null) {
      const dur = outPoint - inPoint;
      selectionDisplay.textContent = `In: ${fmt(inPoint)}  Out: ${fmt(outPoint)}  Dur: ${fmt(dur)}`;
      selectionDisplay.style.color = "var(--accent)";
    } else if (inPoint !== null) {
      selectionDisplay.textContent = `In: ${fmt(inPoint)}`;
      selectionDisplay.style.color = "";
    } else if (outPoint !== null) {
      selectionDisplay.textContent = `Out: ${fmt(outPoint)}`;
      selectionDisplay.style.color = "";
    } else {
      selectionDisplay.textContent = "No selection";
      selectionDisplay.style.color = "";
    }
    drawSelectionOverlay();
  }

  function drawSelectionOverlay() {
    if (inPoint === null || outPoint === null || duration <= 0) {
      selectionOverlay.style.display = "none";
      return;
    }
    const w = trackWrap.clientWidth;
    const l = (inPoint / duration) * w;
    const r = (outPoint / duration) * w;
    selectionOverlay.style.display = "block";
    selectionOverlay.style.left = l + "px";
    selectionOverlay.style.width = (r - l) + "px";
  }

  // ─── Markers ───
  addMarkerBtn.addEventListener("click", () => addMarker(videoPlayer.currentTime));
  copyAllBtn.addEventListener("click", copyAllMarkers);

  function addMarker(t) {
    markers.push({ time: t, label: "" });
    markers.sort((a, b) => a.time - b.time);
    renderMarkers();
    drawTrack();
    toast(`Marker at ${fmt(t)}`);
  }

  function removeMarker(idx) {
    markers.splice(idx, 1);
    renderMarkers();
    drawTrack();
  }

  function renderMarkers() {
    if (markers.length === 0) {
      markersList.innerHTML = '<div class="tl-markers-empty">No markers yet. Press <kbd>M</kbd> or click "Add Marker" to mark positions.</div>';
      return;
    }
    markersList.innerHTML = "";
    markers.forEach((m, i) => {
      const row = document.createElement("div");
      row.className = "tl-marker-row";
      row.innerHTML = `
        <div class="tl-marker-color"></div>
        <span class="tl-marker-time">${fmt(m.time)}</span>
        <span class="tl-marker-label"><input type="text" placeholder="Label..." value="${escHtml(m.label)}" data-idx="${i}"></span>
        <button class="tl-marker-del" data-idx="${i}" title="Delete marker">&times;</button>
      `;
      row.addEventListener("click", (e) => {
        if (e.target.tagName === "INPUT" || e.target.tagName === "BUTTON") return;
        videoPlayer.currentTime = m.time;
        currentTimeDisplay.textContent = fmt(m.time);
        updatePlayhead();
      });
      markersList.appendChild(row);
    });

    // Bind label inputs
    markersList.querySelectorAll("input[data-idx]").forEach((inp) => {
      inp.addEventListener("input", (e) => {
        const idx = parseInt(e.target.dataset.idx, 10);
        if (markers[idx]) markers[idx].label = e.target.value;
      });
    });

    // Bind delete buttons
    markersList.querySelectorAll(".tl-marker-del[data-idx]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        removeMarker(parseInt(e.target.dataset.idx, 10));
      });
    });
  }

  function copyAllMarkers() {
    if (markers.length === 0) {
      toast("No markers to copy");
      return;
    }
    const data = markers.map((m) => ({
      time: parseFloat(m.time.toFixed(3)),
      label: m.label || undefined,
    }));
    navigator.clipboard.writeText(JSON.stringify(data, null, 2)).then(() => toast("Markers copied!"));
  }

  function escHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // ─── Canvas drawing ───
  function resizeCanvases() {
    const dpr = window.devicePixelRatio || 1;

    rulerCanvas.width = rulerWrap.clientWidth * dpr;
    rulerCanvas.height = rulerWrap.clientHeight * dpr;
    rulerCanvas.style.width = rulerWrap.clientWidth + "px";
    rulerCanvas.style.height = rulerWrap.clientHeight + "px";

    trackCanvas.width = trackWrap.clientWidth * dpr;
    trackCanvas.height = trackWrap.clientHeight * dpr;
    trackCanvas.style.width = trackWrap.clientWidth + "px";
    trackCanvas.style.height = trackWrap.clientHeight + "px";
  }

  function drawRuler() {
    const ctx = rulerCanvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const w = rulerCanvas.width;
    const h = rulerCanvas.height;
    ctx.clearRect(0, 0, w, h);

    if (duration <= 0) return;

    ctx.save();
    ctx.scale(dpr, dpr);
    const cw = w / dpr;
    const ch = h / dpr;

    // Determine tick interval based on duration and width
    const pixelsPerSecond = cw / duration;
    let majorInterval = 1; // seconds
    const minPixelGap = 80;
    const intervals = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
    for (const iv of intervals) {
      if (iv * pixelsPerSecond >= minPixelGap) {
        majorInterval = iv;
        break;
      }
    }

    const minorInterval = majorInterval / 4;

    // Minor ticks
    ctx.strokeStyle = "rgba(255,255,255,0.12)";
    ctx.lineWidth = 1;
    for (let t = 0; t <= duration; t += minorInterval) {
      const x = (t / duration) * cw;
      ctx.beginPath();
      ctx.moveTo(x, ch - 6);
      ctx.lineTo(x, ch);
      ctx.stroke();
    }

    // Major ticks + labels
    ctx.strokeStyle = "rgba(255,255,255,0.35)";
    ctx.fillStyle = "rgba(255,255,255,0.65)";
    ctx.font = "600 10px 'Space Grotesk', sans-serif";
    ctx.textAlign = "center";
    for (let t = 0; t <= duration; t += majorInterval) {
      const x = (t / duration) * cw;
      ctx.beginPath();
      ctx.moveTo(x, ch - 14);
      ctx.lineTo(x, ch);
      ctx.stroke();
      ctx.fillText(fmt(t), x, ch - 16);
    }

    ctx.restore();
  }

  function drawTrack() {
    const ctx = trackCanvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const w = trackCanvas.width;
    const h = trackCanvas.height;
    ctx.clearRect(0, 0, w, h);

    if (duration <= 0) return;

    ctx.save();
    ctx.scale(dpr, dpr);
    const cw = w / dpr;
    const ch = h / dpr;

    // Draw clip bar
    const barTop = 12;
    const barH = ch - 24;
    const grad = ctx.createLinearGradient(0, barTop, 0, barTop + barH);
    grad.addColorStop(0, "rgba(255,107,53,0.32)");
    grad.addColorStop(1, "rgba(255,107,53,0.12)");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.roundRect(2, barTop, cw - 4, barH, 6);
    ctx.fill();

    // Clip outline
    ctx.strokeStyle = "rgba(255,107,53,0.4)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(2, barTop, cw - 4, barH, 6);
    ctx.stroke();

    // Simulated waveform-ish visualization
    ctx.fillStyle = "rgba(255,107,53,0.35)";
    const bars = Math.min(Math.floor(cw / 3), 500);
    const barW = cw / bars;
    for (let i = 0; i < bars; i++) {
      // Pseudorandom heights based on position
      const seed = Math.sin(i * 0.3 + 1.7) * 0.5 + Math.sin(i * 0.07 + 0.3) * 0.3 + 0.4;
      const bh = seed * barH * 0.7;
      const y = barTop + (barH - bh) / 2;
      ctx.fillRect(i * barW + 1, y, barW - 1, bh);
    }

    // Draw markers on track
    ctx.fillStyle = "rgba(46, 196, 182, 0.9)";
    markers.forEach((m) => {
      const x = (m.time / duration) * cw;
      ctx.beginPath();
      ctx.moveTo(x - 4, barTop);
      ctx.lineTo(x + 4, barTop);
      ctx.lineTo(x, barTop + 10);
      ctx.closePath();
      ctx.fill();
      ctx.fillRect(x - 0.5, barTop, 1, barH);
    });

    ctx.restore();
  }

  // ─── Playhead ───
  function updatePlayhead() {
    if (duration <= 0) return;
    const ratio = videoPlayer.currentTime / duration;
    const x = ratio * trackWrap.clientWidth;
    playhead.style.left = x + "px";

    // Also update ruler playhead by reusing ruler offset
    currentTimeDisplay.textContent = fmt(videoPlayer.currentTime);
  }

  function startAnimLoop() {
    if (animFrame) return;
    const loop = () => {
      updatePlayhead();
      animFrame = requestAnimationFrame(loop);
    };
    animFrame = requestAnimationFrame(loop);
  }

  function stopAnimLoop() {
    if (animFrame) {
      cancelAnimationFrame(animFrame);
      animFrame = null;
    }
  }

  // ─── Timeline seeking ───
  function seekFromEvent(e, container) {
    const rect = container.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const ratio = Math.max(0, Math.min(1, x / rect.width));
    videoPlayer.currentTime = ratio * duration;
    currentTimeDisplay.textContent = fmt(videoPlayer.currentTime);
    updatePlayhead();
  }

  // Ruler click
  rulerWrap.addEventListener("mousedown", (e) => {
    isSeeking = true;
    seekFromEvent(e, rulerWrap);
  });

  // Track click & drag
  trackWrap.addEventListener("mousedown", (e) => {
    if (e.target.closest(".tl-playhead")) return;
    isSeeking = true;
    seekFromEvent(e, trackWrap);
  });

  document.addEventListener("mousemove", (e) => {
    if (!isSeeking) return;
    seekFromEvent(e, trackWrap);
  });

  document.addEventListener("mouseup", () => {
    isSeeking = false;
  });

  // ─── Keyboard shortcuts ───
  document.addEventListener("keydown", (e) => {
    // Ignore if typing in an input
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

    switch (e.key.toLowerCase()) {
      case " ":
        e.preventDefault();
        if (videoPlayer.paused || videoPlayer.ended) videoPlayer.play();
        else videoPlayer.pause();
        break;
      case "arrowleft":
        e.preventDefault();
        videoPlayer.currentTime = Math.max(0, videoPlayer.currentTime - (e.shiftKey ? 1 : frameDuration()));
        currentTimeDisplay.textContent = fmt(videoPlayer.currentTime);
        updatePlayhead();
        break;
      case "arrowright":
        e.preventDefault();
        videoPlayer.currentTime = Math.min(duration, videoPlayer.currentTime + (e.shiftKey ? 1 : frameDuration()));
        currentTimeDisplay.textContent = fmt(videoPlayer.currentTime);
        updatePlayhead();
        break;
      case "i":
        setIn(videoPlayer.currentTime);
        break;
      case "o":
        setOut(videoPlayer.currentTime);
        break;
      case "m":
        addMarker(videoPlayer.currentTime);
        break;
      case "j":
        speedSelect.value = "0.5";
        videoPlayer.playbackRate = 0.5;
        break;
      case "k":
        videoPlayer.pause();
        break;
      case "l":
        speedSelect.value = "2";
        videoPlayer.playbackRate = 2;
        break;
    }
  });

  // ─── Resize handling ───
  let resizeTimeout;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(() => {
      resizeCanvases();
      drawRuler();
      drawTrack();
      drawSelectionOverlay();
      updatePlayhead();
    }, 100);
  });

  // ─── Nav ───
  openMainBtn.addEventListener("click", () => {
    window.open("/", "_self");
  });

  // ─── Init ───
  loadUploads();
})();
