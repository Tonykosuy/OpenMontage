(() => {
  "use strict";

  const state = {
    status: null,
    templates: [],
    projects: [],
    projectId: null,
    project: null,
    assets: [],
    renders: [],
    voiceData: null,
    personas: [],
    fonts: [],
    fontCategories: [],
    fontPairings: [],
    fontCategory: "all",
    fontSearch: "",
    fontPreviewText: "Xưởng phim AI OpenMontage — Đậm đà sắc Việt 2026!",
    fontPreviewSize: 30,
    fontPreviewWeight: "700",
    catalogMeta: { total: 0, filtered_total: 0, page: 1, page_size: 48, total_pages: 1, categories: [] },
    catalogItems: [],
    catalogItemsById: {},
    catalogQuery: { category: "all", q: "", page: 1, page_size: 48 },
    modalCatalogItem: null,
    lastTtsResult: null,
    activePollTimer: null,
  };

  const $ = (id) => document.getElementById(id);

  function escapeHtml(val) {
    return String(val ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function toast(msg, isError = false) {
    const box = $("toast-container");
    const el = document.createElement("div");
    el.className = `toast${isError ? " error" : ""}`;
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => {
      el.remove();
    }, isError ? 5500 : 3500);
  }

  async function apiFetch(url, options = {}) {
    const resp = await fetch(url, options);
    let data = {};
    try {
      data = await resp.json();
    } catch (_) {
      data = {};
    }
    if (!resp.ok) {
      throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
    }
    return data;
  }

  function switchTab(tabName) {
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tabName);
    });
    document.querySelectorAll(".tab-pane").forEach((pane) => {
      pane.classList.toggle("active", pane.id === `tab-${tabName}`);
    });
  }

  function resolvePreviewUrl(pathOrUrl) {
    if (!pathOrUrl) return "";
    if (pathOrUrl.startsWith("/media/") || pathOrUrl.startsWith("http")) return pathOrUrl;
    const matched = state.assets.find((a) => a.path === pathOrUrl);
    if (matched) return matched.url;
    const marker = `${state.projectId}/`;
    const normalized = pathOrUrl.replace(/\\/g, "/");
    const idx = normalized.indexOf(`/projects/${marker}`);
    if (idx >= 0) {
      return "/media/" + normalized.slice(idx + "/projects/".length);
    }
    return "";
  }

  // -------------------------------------------------------------------------
  // Init & Metadata Rendering
  // -------------------------------------------------------------------------

  async function init() {
    bindStaticEvents();
    try {
      const [statusData, tplData, projData, voiceData, fontData] = await Promise.all([
        apiFetch("/api/studio/status"),
        apiFetch("/api/studio/templates"),
        apiFetch("/api/studio/projects"),
        apiFetch("/api/studio/voices"),
        apiFetch("/api/studio/fonts"),
      ]);

      state.status = statusData;
      state.templates = tplData.templates || [];
      state.projects = projData.projects || [];
      state.voiceData = voiceData;
      state.personas = voiceData.personas || [];
      state.fonts = fontData.fonts || [];
      state.fontCategories = fontData.categories || [];
      state.fontPairings = fontData.pairings || [];

      injectStudioFonts(state.fonts);
      renderStatusPills();
      populateDropdowns();
      renderPersonaGrid();
      renderFontStudio();
      renderProjectList();

      loadCatalog(1);

      const preferred =
        state.projects.find((p) => p.scene_count > 0 && p.project_id !== "demos") ||
        state.projects[0];

      if (preferred && preferred.scene_count > 0) {
        await loadProject(preferred.project_id);
      } else {
        const firstTpl = state.templates[0];
        if (firstTpl) {
          const created = await apiFetch("/api/studio/projects", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              project_id: "xuong-phim-dau-tien",
              title: firstTpl.project.title,
              template_id: firstTpl.template_id,
            }),
          });
          applyLoadedProject(created);
          await refreshProjectsList();
        }
      }
    } catch (err) {
      toast(`Lỗi khởi tạo Studio: ${err.message}`, true);
    }
  }

  function renderStatusPills() {
    const st = state.status;
    if (!st) return;
    const pills = [];
    if (st.gpu && st.gpu.cuda) {
      pills.push(`<span class="pill pill-ok">🎮 ${escapeHtml(st.gpu.device_name)} (${st.gpu.vram_total_gb} GB)</span>`);
    } else {
      pills.push(`<span class="pill pill-warn">CPU Mode</span>`);
    }
    const t = st.tools || {};
    const voiceCount = state.personas.length || (st.voices || []).length;
    pills.push(
      `<span class="pill ${t.piper_tts ? "pill-ok" : "pill-warn"}">🎙️ ${voiceCount} Giọng Đọc</span>`
    );
    const catTotal = state.catalogMeta?.total || 0;
    if (catTotal > 0) {
      pills.push(`<span class="pill pill-ok">📚 ${catTotal} Mẫu &amp; Style</span>`);
    }
    if (state.fonts.length > 0) {
      pills.push(`<span class="pill pill-ok">🔤 ${state.fonts.length} Font</span>`);
    }
    pills.push(
      `<span class="pill ${t.video_compose ? "pill-ok" : "pill-warn"}">🎬 Remotion</span>`
    );
    pills.push(
      `<span class="pill ${t.transcriber ? "pill-ok" : "pill-warn"}">📝 Whisper</span>`
    );
    if (st.disk) {
      pills.push(`<span class="pill pill-muted">💾 Trống ${st.disk.free_gb} GB</span>`);
    }
    $("status-pills").innerHTML = pills.join("");
  }

  function populateDropdowns() {
    const st = state.status || {};
    const vd = state.voiceData || {};
    const tplOptions = state.templates
      .map((t) => `<option value="${escapeHtml(t.template_id)}">${escapeHtml(t.title)}</option>`)
      .join("");
    $("template-select").innerHTML = tplOptions;
    $("new-project-template").innerHTML = tplOptions;

    $("proj-profile").innerHTML = (st.profiles || [])
      .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.label)}</option>`)
      .join("");

    $("proj-theme").innerHTML = (st.themes || [])
      .map((t) => `<option value="${escapeHtml(t.id)}">${escapeHtml(t.label)}</option>`)
      .join("");

    const personas = state.personas || [];
    const personaOpts = [`<option value="">(Mặc định — Dùng Piper Offline bên cạnh)</option>`]
      .concat(
        personas.map(
          (p) =>
            `<option value="${escapeHtml(p.id)}">${escapeHtml(p.avatar || "🎙️")} ${escapeHtml(p.name)} [${escapeHtml(p.gender_label)} · ${escapeHtml(p.tone_label)}]</option>`
        )
      )
      .join("");
    $("proj-voice-persona").innerHTML = personaOpts;
    $("tts-persona").innerHTML = personas
      .map(
        (p) =>
          `<option value="${escapeHtml(p.id)}">${escapeHtml(p.avatar || "🎙️")} ${escapeHtml(p.name)} (${escapeHtml(p.tone_label)})</option>`
      )
      .join("") + `<option value="">(Tùy chỉnh thủ công bên dưới)</option>`;

    const rawVoices = vd.raw_piper_voices || st.voices || [];
    const voiceOpts = rawVoices
      .map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.label)}</option>`)
      .join("");
    $("proj-voice").innerHTML = voiceOpts || `<option value="vi_VN-vais1000-medium">vi_VN-vais1000-medium</option>`;
    $("tts-voice").innerHTML = $("proj-voice").innerHTML;

    const musicOpts = [`<option value="">(Không dùng nhạc nền)</option>`]
      .concat(
        (st.music_tracks || []).map(
          (m) => `<option value="${escapeHtml(m.path)}">${escapeHtml(m.name)} (${m.duration_seconds}s)</option>`
        )
      )
      .join("");
    $("proj-music").innerHTML = musicOpts;

    const colorOpts = [`<option value="">(Mặc định - Không chỉnh màu)</option>`]
      .concat(
        (vd.color_grade_profiles || []).map(
          (c) => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.label)}</option>`
        )
      )
      .join("");
    $("proj-color-grade").innerHTML = colorOpts;

    const enhanceOpts = (vd.audio_enhance_presets || [])
      .map((a) => `<option value="${escapeHtml(a.id)}">${escapeHtml(a.label)}</option>`)
      .join("");
    $("proj-audio-enhance").innerHTML = enhanceOpts || `<option value="clean_speech">clean_speech</option>`;
    $("tts-enhance").innerHTML = $("proj-audio-enhance").innerHTML;

    $("tts-timbre").innerHTML = (vd.timbre_presets || [])
      .map((t) => `<option value="${escapeHtml(t.id)}">${escapeHtml(t.label)}</option>`)
      .join("");

    $("voice-filter-gender").innerHTML = (vd.genders || [])
      .map((g) => `<option value="${escapeHtml(g.id)}">${escapeHtml(g.label)}</option>`)
      .join("");
    $("voice-filter-age").innerHTML = (vd.age_groups || [])
      .map((a) => `<option value="${escapeHtml(a.id)}">${escapeHtml(a.label)}</option>`)
      .join("");
    $("voice-filter-tone").innerHTML = (vd.tones || [])
      .map((t) => `<option value="${escapeHtml(t.id)}">${escapeHtml(t.label)}</option>`)
      .join("");

    if (state.fonts.length) {
      const fontOpts = state.fonts
        .map((f) => `<option value="${escapeHtml(f.family)}">${escapeHtml(f.label)}</option>`)
        .join("");
      $("cfg-font").innerHTML = fontOpts;
      $("cfg-caption-font").innerHTML =
        `<option value="">(Theo Font tiêu đề dự án)</option>` + fontOpts;
    }
  }

  // -------------------------------------------------------------------------
  // Project Loading & Saving
  // -------------------------------------------------------------------------

  async function refreshProjectsList() {
    const data = await apiFetch("/api/studio/projects");
    state.projects = data.projects || [];
    renderProjectList();
  }

  function renderProjectList() {
    const box = $("project-list");
    if (!state.projects.length) {
      box.innerHTML = `<div class="muted">Chưa có dự án nào.</div>`;
      return;
    }
    box.innerHTML = state.projects
      .map((p) => {
        const active = p.project_id === state.projectId ? " active" : "";
        return `
          <button type="button" class="list-item${active}" data-open-project="${escapeHtml(p.project_id)}">
            <span class="list-item-title">${escapeHtml(p.title || p.project_id)}</span>
            <span class="list-item-sub">${escapeHtml(p.project_id)} • ${p.scene_count} cảnh • ${p.render_count} video</span>
          </button>`;
      })
      .join("");

    box.querySelectorAll("[data-open-project]").forEach((btn) => {
      btn.addEventListener("click", () => loadProject(btn.dataset.openProject));
    });
  }

  async function loadProject(projectId) {
    try {
      const data = await apiFetch(`/api/studio/projects/${encodeURIComponent(projectId)}`);
      applyLoadedProject(data);
      renderProjectList();
      toast(`Đã mở dự án: ${data.project.title || projectId}`);
    } catch (err) {
      toast(`Không mở được dự án: ${err.message}`, true);
    }
  }

  function applyLoadedProject(data) {
    state.project = JSON.parse(JSON.stringify(data.project));
    state.projectId = state.project.project_id;
    state.assets = data.assets || [];
    state.renders = data.renders || [];

    syncProjectFormFromState();
    renderTimeline();
    renderAssetSidebar();
    renderRenderSidebar();
    refreshAssetDropdowns();

    if (state.renders.length > 0) {
      playRenderedVideo(state.renders[0]);
    }
  }

  function syncProjectFormFromState() {
    const p = state.project;
    if (!p) return;
    const tc = p.theme_config || {};
    const cp = p.caption_props || {};
    $("current-project-id").textContent = p.project_id || "—";
    $("active-style-label").textContent = p.style_title || p.style_preset_id || "Mặc định";
    $("proj-title").value = p.title || "";
    $("proj-profile").value = p.profile || "youtube_landscape";
    $("proj-theme").value = p.theme || "flat-motion-graphics";
    $("proj-voice-persona").value = p.voice_persona || "";
    $("proj-voice").value = p.voice_model || "vi_VN-vais1000-medium";
    $("proj-voice-speed").value = String(p.voice_speed ?? 1.0);
    $("voice-speed-val").textContent = Number(p.voice_speed ?? 1.0).toFixed(2);
    $("proj-music").value = p.music_path || "";
    $("proj-music-vol").value = String(p.music_volume ?? 0.14);
    $("music-vol-val").textContent = String(Math.round((p.music_volume ?? 0.14) * 100));
    $("proj-color-grade").value = p.color_grade_profile || "";
    $("proj-color-intensity").value = String(p.color_grade_intensity ?? 0.7);
    $("color-int-val").textContent = Number(p.color_grade_intensity ?? 0.7).toFixed(2);
    $("proj-audio-enhance").value = p.audio_enhance_preset || "clean_speech";

    $("cfg-bg-color").value = tc.backgroundColor || "#0F172A";
    $("cfg-primary-color").value = tc.primaryColor || "#60A5FA";
    $("cfg-accent-color").value = tc.accentColor || "#22D3EE";
    $("cfg-text-color").value = tc.textColor || "#F8FAFC";
    const fontEl = $("cfg-font");
    const rawFont = String(tc.headingFont || "Inter");
    const matchedFont = Array.from(fontEl.options).find(
      (opt) => opt.value === rawFont || rawFont.toLowerCase().includes(opt.value.toLowerCase())
    );
    fontEl.value = matchedFont ? matchedFont.value : (fontEl.options[0]?.value || "Inter");

    const capFontEl = $("cfg-caption-font");
    if (capFontEl) {
      const rawCapFont = String(cp.fontFamily || "");
      const matchedCap = rawCapFont
        ? Array.from(capFontEl.options).find(
            (opt) => opt.value && (opt.value === rawCapFont || rawCapFont.toLowerCase().includes(opt.value.toLowerCase()))
          )
        : null;
      capFontEl.value = matchedCap ? matchedCap.value : "";
    }

    if ($("active-heading-font-pill")) {
      $("active-heading-font-pill").textContent = fontEl.value || "Inter";
    }
    if ($("active-caption-font-pill")) {
      $("active-caption-font-pill").textContent = capFontEl?.value || "Theo Font chính";
    }

    const paceMap = { moderate: "normal", slow: "cinematic" };
    const rawPace = paceMap[tc.pace] || tc.pace || "normal";
    const paceEl = $("cfg-pace");
    paceEl.value = Array.from(paceEl.options).some((o) => o.value === rawPace) ? rawPace : "normal";

    $("cfg-cap-size").value = String(cp.fontSize ?? 54);
    $("cfg-cap-highlight").value = cp.highlightColor || "#FACC15";
    $("cfg-cap-words").value = String(cp.wordsPerPage ?? 3);
    const boxEl = $("cfg-cap-box");
    const rawBox = typeof cp.boxStyle === "string" ? cp.boxStyle : "pill";
    boxEl.value = Array.from(boxEl.options).some((o) => o.value === rawBox) ? rawBox : "pill";

    $("proj-auto-tts").checked = p.auto_tts !== false;
    $("proj-auto-captions").checked = p.auto_captions !== false;
    $("proj-auto-fit").checked = p.auto_fit_timing !== false;

    if (typeof renderPersonaGrid === "function") {
      renderPersonaGrid();
    }
    if (typeof renderFontStudio === "function") {
      renderFontStudio();
    }
  }

  function syncStateFromProjectForm() {
    if (!state.project) return;
    state.project.title = $("proj-title").value.trim() || state.projectId;
    state.project.profile = $("proj-profile").value;
    state.project.theme = $("proj-theme").value;
    state.project.voice_persona = $("proj-voice-persona").value;
    state.project.voice_model = $("proj-voice").value;
    state.project.voice_speed = parseFloat($("proj-voice-speed").value) || 1.0;
    state.project.music_path = $("proj-music").value;
    state.project.music_volume = parseFloat($("proj-music-vol").value) || 0.14;
    state.project.color_grade_profile = $("proj-color-grade").value;
    state.project.color_grade_intensity = parseFloat($("proj-color-intensity").value) || 0.7;
    state.project.audio_enhance_preset = $("proj-audio-enhance").value || "clean_speech";

    const fontVal = $("cfg-font").value;
    const paceVal = $("cfg-pace").value;
    const paceSpring =
      paceVal === "fast"
        ? { damping: 24, stiffness: 180 }
        : paceVal === "cinematic"
        ? { damping: 32, stiffness: 80 }
        : paceVal === "snappy"
        ? { damping: 18, stiffness: 160 }
        : { damping: 28, stiffness: 120 };

    state.project.theme_config = {
      ...(state.project.theme_config || {}),
      backgroundColor: $("cfg-bg-color").value,
      primaryColor: $("cfg-primary-color").value,
      accentColor: $("cfg-accent-color").value,
      textColor: $("cfg-text-color").value,
      headingFont: fontVal,
      bodyFont: fontVal,
      pace: paceVal,
      springConfig: paceSpring,
      captionHighlightColor: $("cfg-cap-highlight").value,
    };
    const nextCapProps = {
      ...(state.project.caption_props || {}),
      fontSize: parseInt($("cfg-cap-size").value, 10) || 54,
      highlightColor: $("cfg-cap-highlight").value,
      wordsPerPage: parseInt($("cfg-cap-words").value, 10) || 3,
      boxStyle: $("cfg-cap-box").value || "pill",
    };
    const capFontVal = $("cfg-caption-font")?.value || "";
    if (capFontVal) {
      nextCapProps.fontFamily = capFontVal;
    } else {
      delete nextCapProps.fontFamily;
    }
    state.project.caption_props = nextCapProps;

    state.project.auto_tts = $("proj-auto-tts").checked;
    state.project.auto_captions = $("proj-auto-captions").checked;
    state.project.auto_fit_timing = $("proj-auto-fit").checked;
  }

  async function saveCurrentProject(quiet = false) {
    if (!state.project) return null;
    syncStateFromProjectForm();
    const res = await apiFetch("/api/studio/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.project),
    });
    state.project = res.project;
    state.assets = res.assets || state.assets;
    state.renders = res.renders || state.renders;
    await refreshProjectsList();
    if (!quiet) toast("💾 Đã lưu kịch bản dự án!");
    return res;
  }

  // -------------------------------------------------------------------------
  // Sidebar Assets & Renders
  // -------------------------------------------------------------------------

  function renderAssetSidebar() {
    const box = $("asset-list");
    if (!state.assets.length) {
      box.innerHTML = `<div class="muted">Chưa có tệp tài nguyên nào. Bạn có thể tải lên hoặc tạo ở tab Công Cụ AI.</div>`;
      return;
    }
    box.innerHTML = state.assets
      .map((a) => {
        const icon = a.kind === "video" ? "🎥" : a.kind === "audio" ? "🔊" : "🖼️";
        const canInsert = a.kind === "image" || a.kind === "video";
        return `
          <div class="asset-row">
            <span class="asset-name" title="${escapeHtml(a.filename)}">${icon} ${escapeHtml(a.filename)}</span>
            ${
              canInsert
                ? `<button type="button" class="btn btn-xs btn-secondary" data-insert-asset="${escapeHtml(a.path)}" data-insert-kind="${escapeHtml(a.kind)}" title="Chèn thành cảnh mới">+</button>`
                : ""
            }
          </div>`;
      })
      .join("");

    box.querySelectorAll("[data-insert-asset]").forEach((btn) => {
      btn.addEventListener("click", () => {
        insertMediaScene(btn.dataset.insertKind, btn.dataset.insertAsset, "Cảnh từ kho tài nguyên");
      });
    });
  }

  function renderRenderSidebar() {
    const box = $("sidebar-render-list");
    if (!state.renders.length) {
      box.innerHTML = `<div class="muted">Dự án này chưa render video nào.</div>`;
      return;
    }
    box.innerHTML = state.renders
      .map(
        (r, idx) => `
          <button type="button" class="list-item" data-play-render="${idx}">
            <span class="list-item-title">🎬 ${escapeHtml(r.filename)}</span>
            <span class="list-item-sub">${r.size_mb} MB</span>
          </button>`
      )
      .join("");

    box.querySelectorAll("[data-play-render]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const item = state.renders[Number(btn.dataset.playRender)];
        if (item) {
          playRenderedVideo(item);
          switchTab("render");
        }
      });
    });
  }

  function refreshAssetDropdowns() {
    const audioVideoAssets = state.assets.filter((a) => a.kind === "audio" || a.kind === "video");
    $("stt-asset-select").innerHTML = audioVideoAssets.length
      ? audioVideoAssets
          .map((a) => `<option value="${escapeHtml(a.path)}">${escapeHtml(a.folder)}/${escapeHtml(a.filename)}</option>`)
          .join("")
      : `<option value="">(Chưa có tệp audio/video trong dự án)</option>`;

    const imageAssets = state.assets.filter((a) => a.kind === "image");
    $("enh-asset-select").innerHTML = imageAssets.length
      ? imageAssets
          .map((a) => `<option value="${escapeHtml(a.path)}">${escapeHtml(a.folder)}/${escapeHtml(a.filename)}</option>`)
          .join("")
      : `<option value="">(Chưa có ảnh — hãy tải ảnh lên)</option>`;
  }

  // -------------------------------------------------------------------------
  // Timeline Builder & Dynamic Scene Cards
  // -------------------------------------------------------------------------

  function reflowSceneTimings() {
    const cuts = state.project?.cuts || [];
    let cursor = 0.0;
    for (const cut of cuts) {
      const dur = Math.max(1.0, Number(cut.out_seconds ?? 4.0) - Number(cut.in_seconds ?? 0.0));
      cut.in_seconds = Number(cursor.toFixed(2));
      cut.out_seconds = Number((cursor + dur).toFixed(2));
      cursor = cut.out_seconds;
    }
  }

  function makeDefaultCut(type, startSec) {
    const id = `sc-${Date.now().toString().slice(-5)}`;
    const base = {
      id,
      type,
      in_seconds: startSec,
      out_seconds: Number((startSec + 4.5).toFixed(2)),
      backgroundColor: "#0F172A",
      accentColor: "#22D3EE",
      narration: "",
      source: "",
    };
    switch (type) {
      case "hero_title":
        return { ...base, text: "Tiêu đề nổi bật", subtitle: "Mô tả ngắn gọn cho cảnh này" };
      case "stat_card":
        return { ...base, stat: "100%", subtitle: "Chỉ số ấn tượng cần làm nổi bật" };
      case "comparison":
        return {
          ...base,
          title: "So sánh hiệu quả",
          leftLabel: "Cách cũ",
          leftValue: "Chậm",
          rightLabel: "Cách mới",
          rightValue: "Nhanh x5",
          color: "#F8FAFC",
        };
      case "bar_chart":
        return {
          ...base,
          title: "Biểu đồ so sánh",
          chartData: [
            { label: "Giai đoạn 1", value: 40 },
            { label: "Giai đoạn 2", value: 75 },
            { label: "Giai đoạn 3", value: 98 },
          ],
          chartColors: ["#22D3EE", "#34D399", "#F59E0B"],
          showGrid: true,
          showValues: true,
        };
      case "pie_chart":
        return {
          ...base,
          title: "Tỉ lệ phân bổ",
          chartData: [
            { label: "AI tự động", value: 70 },
            { label: "Sáng tạo", value: 30 },
          ],
          chartColors: ["#34D399", "#60A5FA"],
          donut: true,
          centerLabel: "Hiệu suất",
          centerValue: "70%",
          showLegend: true,
        };
      case "kpi_grid":
        return {
          ...base,
          title: "Các chỉ số chính",
          columns: 3,
          chartData: [
            { label: "Tốc độ", value: 95, suffix: "%" },
            { label: "Chi phí", value: 0, suffix: " đ" },
            { label: "Độ nét", value: 1080, suffix: "p" },
          ],
        };
      case "progress_bar":
        return {
          ...base,
          title: "Tiến độ hoàn thành",
          progress: 85,
          progressLabel: "85% đã sẵn sàng",
          progressColor: "#34D399",
        };
      case "callout":
        return {
          ...base,
          title: "Ghi chú quan trọng",
          text: "Nội dung nhấn mạnh giúp người xem nhớ thông điệp chính.",
          callout_type: "tip",
          color: "#F8FAFC",
        };
      case "image":
      case "video":
        return { ...base, animation: "zoom-in" };
      default:
        return { ...base, type: "text_card", text: "Nội dung văn bản chính", subtitle: "Chú thích phụ" };
    }
  }

  function addScene(type, overrides = {}) {
    if (!state.project) return;
    const cuts = state.project.cuts || [];
    const lastEnd = cuts.length ? Number(cuts[cuts.length - 1].out_seconds || 0) : 0;
    const newCut = { ...makeDefaultCut(type, lastEnd), ...overrides };
    cuts.push(newCut);
    state.project.cuts = cuts;
    renderTimeline();
    switchTab("timeline");
  }

  function insertMediaScene(kind, filePath, narration = "") {
    addScene(kind === "video" ? "video" : "image", {
      source: filePath,
      narration,
      animation: "zoom-in",
    });
    toast(`✅ Đã chèn ${kind === "video" ? "video clip" : "hình ảnh"} vào cuối Timeline!`);
  }

  function renderTimeline() {
    const cuts = state.project?.cuts || [];
    const totalDur = cuts.length ? Math.max(...cuts.map((c) => Number(c.out_seconds || 0))) : 0;
    $("scene-count").textContent = String(cuts.length);
    $("timeline-duration").textContent = `${totalDur.toFixed(1)}s`;

    // Ruler
    const ruler = $("timeline-ruler");
    if (!cuts.length) {
      ruler.innerHTML = `<span class="muted">Timeline trống — bấm nút "+ Tiêu đề / Số liệu / Ảnh..." để thêm cảnh.</span>`;
    } else {
      const safeTotal = Math.max(1, totalDur);
      ruler.innerHTML = cuts
        .map((c, idx) => {
          const dur = Math.max(0.5, Number(c.out_seconds) - Number(c.in_seconds));
          const pct = Math.max(8, Math.round((dur / safeTotal) * 100));
          const label = c.text || c.title || c.stat || c.type;
          return `
            <div class="ruler-block" style="flex: ${pct} 1 0%" data-scroll-scene="${idx}" title="${escapeHtml(label)}">
              <div class="ruler-block-title">#${idx + 1} ${escapeHtml(label)}</div>
              <div class="ruler-block-time">${Number(c.in_seconds).toFixed(1)}s → ${Number(c.out_seconds).toFixed(1)}s</div>
            </div>`;
        })
        .join("");

      ruler.querySelectorAll("[data-scroll-scene]").forEach((el) => {
        el.addEventListener("click", () => {
          const target = $("scene-list").children[Number(el.dataset.scrollScene)];
          if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
        });
      });
    }

    // Scene Cards
    const sceneTypes = state.status?.scene_types || [];
    const container = $("scene-list");
    container.innerHTML = cuts
      .map((cut, idx) => renderSceneCardHtml(cut, idx, sceneTypes))
      .join("");

    bindSceneCardEvents(cuts);
  }

  function renderSceneCardHtml(cut, idx, sceneTypes) {
    const typeOptions = sceneTypes
      .map(
        (t) =>
          `<option value="${escapeHtml(t.id)}"${cut.type === t.id ? " selected" : ""}>${escapeHtml(t.label)}</option>`
      )
      .join("");

    let specificHtml = "";
    const t = cut.type;

    if (t === "hero_title" || t === "text_card") {
      specificHtml = `
        <div class="grid-2">
          <label class="field"><span>Tiêu đề chính</span><input class="input" data-idx="${idx}" data-field="text" value="${escapeHtml(cut.text || "")}" /></label>
          <label class="field"><span>Phụ đề mô tả</span><input class="input" data-idx="${idx}" data-field="subtitle" value="${escapeHtml(cut.subtitle || "")}" /></label>
        </div>`;
    } else if (t === "stat_card") {
      specificHtml = `
        <div class="grid-2">
          <label class="field"><span>Số liệu nổi bật (VD: 88%, 0 ĐỒNG)</span><input class="input" data-idx="${idx}" data-field="stat" value="${escapeHtml(cut.stat || "")}" /></label>
          <label class="field"><span>Giải thích bên dưới</span><input class="input" data-idx="${idx}" data-field="subtitle" value="${escapeHtml(cut.subtitle || "")}" /></label>
        </div>`;
    } else if (t === "callout") {
      specificHtml = `
        <div class="grid-3">
          <label class="field"><span>Tiêu đề hộp</span><input class="input" data-idx="${idx}" data-field="title" value="${escapeHtml(cut.title || "")}" /></label>
          <label class="field"><span>Kiểu hộp</span>
            <select class="input" data-idx="${idx}" data-field="callout_type">
              ${["tip", "info", "warning", "quote"].map((k) => `<option value="${k}"${cut.callout_type === k ? " selected" : ""}>${k}</option>`).join("")}
            </select>
          </label>
          <label class="field"><span>Màu nhấn</span><input class="input" type="color" data-idx="${idx}" data-field="accentColor" value="${escapeHtml(cut.accentColor || "#22D3EE")}" /></label>
        </div>
        <label class="field"><span>Nội dung chi tiết</span><input class="input" data-idx="${idx}" data-field="text" value="${escapeHtml(cut.text || "")}" /></label>`;
    } else if (t === "comparison") {
      specificHtml = `
        <label class="field"><span>Tiêu đề so sánh</span><input class="input" data-idx="${idx}" data-field="title" value="${escapeHtml(cut.title || "")}" /></label>
        <div class="grid-2">
          <div class="grid-2">
            <label class="field"><span>Nhãn cột trái</span><input class="input" data-idx="${idx}" data-field="leftLabel" value="${escapeHtml(cut.leftLabel || "")}" /></label>
            <label class="field"><span>Giá trị trái</span><input class="input" data-idx="${idx}" data-field="leftValue" value="${escapeHtml(cut.leftValue || "")}" /></label>
          </div>
          <div class="grid-2">
            <label class="field"><span>Nhãn cột phải</span><input class="input" data-idx="${idx}" data-field="rightLabel" value="${escapeHtml(cut.rightLabel || "")}" /></label>
            <label class="field"><span>Giá trị phải</span><input class="input" data-idx="${idx}" data-field="rightValue" value="${escapeHtml(cut.rightValue || "")}" /></label>
          </div>
        </div>`;
    } else if (t === "progress_bar") {
      specificHtml = `
        <div class="grid-3">
          <label class="field"><span>Tiêu đề</span><input class="input" data-idx="${idx}" data-field="title" value="${escapeHtml(cut.title || "")}" /></label>
          <label class="field"><span>Phần trăm (0-100)</span><input class="input" type="number" min="0" max="100" data-idx="${idx}" data-field="progress" data-num="1" value="${escapeHtml(cut.progress ?? 75)}" /></label>
          <label class="field"><span>Mô tả tiến độ</span><input class="input" data-idx="${idx}" data-field="progressLabel" value="${escapeHtml(cut.progressLabel || "")}" /></label>
        </div>`;
    } else if (t === "bar_chart" || t === "pie_chart" || t === "kpi_grid") {
      const rows = (cut.chartData || [])
        .map(
          (row, rIdx) => `
          <div class="chart-row">
            <input class="input" placeholder="Nhãn" data-idx="${idx}" data-chart-row="${rIdx}" data-chart-key="label" value="${escapeHtml(row.label || "")}" />
            <input class="input" type="number" step="any" placeholder="Giá trị" data-idx="${idx}" data-chart-row="${rIdx}" data-chart-key="value" value="${escapeHtml(row.value ?? 0)}" />
            <button type="button" class="btn btn-xs btn-danger" data-idx="${idx}" data-del-chart-row="${rIdx}">×</button>
          </div>`
        )
        .join("");
      specificHtml = `
        <label class="field"><span>Tiêu đề biểu đồ / KPI</span><input class="input" data-idx="${idx}" data-field="title" value="${escapeHtml(cut.title || "")}" /></label>
        <div class="chart-rows">${rows}</div>
        <div><button type="button" class="btn btn-xs btn-secondary" data-idx="${idx}" data-add-chart-row="1">+ Thêm dòng dữ liệu</button></div>`;
    } else if (t === "image" || t === "video") {
      const previewUrl = resolvePreviewUrl(cut.source);
      const assetSelectOpts = [`<option value="">-- Chọn từ Kho Tài Nguyên --</option>`]
        .concat(
          state.assets
            .filter((a) => (t === "video" ? a.kind === "video" : a.kind === "image"))
            .map(
              (a) =>
                `<option value="${escapeHtml(a.path)}"${a.path === cut.source ? " selected" : ""}>${escapeHtml(a.filename)}</option>`
            )
        )
        .join("");
      specificHtml = `
        <div class="grid-2">
          <label class="field">
            <span>Chọn tệp có sẵn trong dự án</span>
            <select class="input" data-idx="${idx}" data-field="source">${assetSelectOpts}</select>
          </label>
          <div class="field">
            <span>Hoặc tải tệp từ máy &amp; lớp phủ nền</span>
            <div class="inline-row">
              <label class="btn btn-xs btn-secondary upload-btn">
                📤 Tải lên
                <input type="file" hidden accept="${t === "video" ? "video/*" : "image/*"}" data-idx="${idx}" data-scene-upload="${t}" />
              </label>
              <input class="input" placeholder="Overlay (VD: rgba(0,0,0,0.4))" data-idx="${idx}" data-field="backgroundOverlay" value="${escapeHtml(cut.backgroundOverlay || "")}" />
            </div>
          </div>
        </div>
        ${
          previewUrl
            ? t === "video"
              ? `<video class="scene-media-preview" src="${escapeHtml(previewUrl)}" controls muted></video>`
              : `<img class="scene-media-preview" src="${escapeHtml(previewUrl)}" alt="Preview" />`
            : `<div class="muted">Chưa chọn tệp ${t}. Hãy tải tệp lên hoặc tạo ảnh ở tab Công Cụ AI.</div>`
        }`;
    }

    const scenePersonaOpts = [`<option value="">Theo giọng mặc định dự án</option>`]
      .concat(
        (state.personas || []).map(
          (p) =>
            `<option value="${escapeHtml(p.id)}"${cut.voice_persona === p.id ? " selected" : ""}>${escapeHtml(p.avatar || "🎙️")} ${escapeHtml(p.name)}</option>`
        )
      )
      .join("");
    const projThemeCfg = state.project?.theme_config || {};
    const cutBg = cut.backgroundColor || projThemeCfg.backgroundColor || "#0F172A";
    const cutAccent = cut.accentColor || projThemeCfg.accentColor || "#22D3EE";

    return `
      <article class="scene-card" data-scene-idx="${idx}">
        <div class="scene-card-head">
          <div class="scene-controls">
            <span class="scene-badge">Cảnh #${idx + 1}</span>
            <select class="input input-sm" data-idx="${idx}" data-field="type">${typeOptions}</select>
            <span class="muted">Từ</span>
            <input class="input time-input" type="number" step="0.1" min="0" data-idx="${idx}" data-field="in_seconds" data-num="1" value="${escapeHtml(cut.in_seconds)}" />
            <span class="muted">đến</span>
            <input class="input time-input" type="number" step="0.1" min="0.5" data-idx="${idx}" data-field="out_seconds" data-num="1" value="${escapeHtml(cut.out_seconds)}" />
            <span class="muted">s</span>
          </div>
          <div class="scene-controls">
            <button type="button" class="btn btn-xs btn-secondary" data-move="${idx}" data-dir="-1" title="Lên">↑</button>
            <button type="button" class="btn btn-xs btn-secondary" data-move="${idx}" data-dir="1" title="Xuống">↓</button>
            <button type="button" class="btn btn-xs btn-secondary" data-dup="${idx}" title="Nhân đôi">⧉</button>
            <button type="button" class="btn btn-xs btn-danger" data-del="${idx}" title="Xóa cảnh">🗑️</button>
          </div>
        </div>

        ${specificHtml}

        <div class="scene-style-bar">
          <label class="field">
            <span>🎭 Giọng riêng cảnh #${idx + 1}</span>
            <select class="input input-sm" data-idx="${idx}" data-field="voice_persona">${scenePersonaOpts}</select>
          </label>
          <label class="field">
            <span>🎞️ Hiệu ứng (Animation)</span>
            <select class="input input-sm" data-idx="${idx}" data-field="animation">
              ${["zoom-in", "zoom-out", "pan-left", "pan-right", "ken-burns", "parallax", "none"]
                .map((a) => `<option value="${a}"${(cut.animation || "zoom-in") === a ? " selected" : ""}>${a}</option>`)
                .join("")}
            </select>
          </label>
          <label class="field">
            <span>✨ Chuyển cảnh (Transition)</span>
            <select class="input input-sm" data-idx="${idx}" data-field="transition_in">
              ${["cut", "fade", "dissolve", "slide", "wipe"]
                .map((tr) => `<option value="${tr}"${(cut.transition_in || "fade") === tr ? " selected" : ""}>${tr}</option>`)
                .join("")}
            </select>
          </label>
          <div class="field">
            <span>🎨 Màu nền / Màu nhấn cảnh</span>
            <div class="inline-row">
              <input class="input input-color" type="color" title="Màu nền cảnh" data-idx="${idx}" data-field="backgroundColor" value="${escapeHtml(cutBg)}" />
              <input class="input input-color" type="color" title="Màu nhấn cảnh" data-idx="${idx}" data-field="accentColor" value="${escapeHtml(cutAccent)}" />
            </div>
          </div>
        </div>

        <div class="narration-box">
          <div class="narration-head">
            <span>🎙️ Lời thoại AI cho cảnh #${idx + 1} (tự đọc &amp; tạo phụ đề)</span>
            <button type="button" class="btn btn-xs btn-secondary" data-preview-tts="${idx}">🔊 Nghe thử cảnh này</button>
          </div>
          <textarea class="textarea" rows="2" placeholder="Nhập câu thuyết minh cho cảnh này (bỏ trống nếu chỉ hiện nhạc nền)..." data-idx="${idx}" data-field="narration">${escapeHtml(cut.narration || "")}</textarea>
          <audio class="audio-player hidden" controls data-scene-audio="${idx}"></audio>
        </div>
      </article>`;
  }

  function bindSceneCardEvents(cuts) {
    const root = $("scene-list");

    root.querySelectorAll("[data-field]").forEach((el) => {
      const eventName = el.tagName === "SELECT" || el.type === "color" ? "change" : "input";
      el.addEventListener(eventName, () => {
        const idx = Number(el.dataset.idx);
        const field = el.dataset.field;
        const cut = cuts[idx];
        if (!cut) return;
        let val = el.value;
        if (el.dataset.num) val = parseFloat(val) || 0;
        cut[field] = val;

        if (field === "type") {
          const tpl = makeDefaultCut(val, cut.in_seconds);
          cuts[idx] = {
            ...tpl,
            id: cut.id,
            in_seconds: cut.in_seconds,
            out_seconds: cut.out_seconds,
            narration: cut.narration || "",
          };
          renderTimeline();
        } else if (field === "source") {
          renderTimeline();
        } else if (field === "in_seconds" || field === "out_seconds") {
          const totalDur = cuts.length ? Math.max(...cuts.map((c) => Number(c.out_seconds || 0))) : 0;
          $("timeline-duration").textContent = `${totalDur.toFixed(1)}s`;
        }
      });
    });

    root.querySelectorAll("[data-chart-row]").forEach((el) => {
      el.addEventListener("input", () => {
        const idx = Number(el.dataset.idx);
        const rIdx = Number(el.dataset.chartRow);
        const key = el.dataset.chartKey;
        const row = cuts[idx]?.chartData?.[rIdx];
        if (row) {
          row[key] = key === "value" ? parseFloat(el.value) || 0 : el.value;
        }
      });
    });

    root.querySelectorAll("[data-add-chart-row]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.idx);
        cuts[idx].chartData = cuts[idx].chartData || [];
        cuts[idx].chartData.push({ label: "Mục mới", value: 50 });
        renderTimeline();
      });
    });

    root.querySelectorAll("[data-del-chart-row]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.idx);
        const rIdx = Number(btn.dataset.delChartRow);
        cuts[idx].chartData.splice(rIdx, 1);
        renderTimeline();
      });
    });

    root.querySelectorAll("[data-move]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.move);
        const target = idx + Number(btn.dataset.dir);
        if (target < 0 || target >= cuts.length) return;
        const [item] = cuts.splice(idx, 1);
        cuts.splice(target, 0, item);
        reflowSceneTimings();
        renderTimeline();
      });
    });

    root.querySelectorAll("[data-dup]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.dup);
        const copy = JSON.parse(JSON.stringify(cuts[idx]));
        copy.id = `sc-${Date.now().toString().slice(-5)}`;
        cuts.splice(idx + 1, 0, copy);
        reflowSceneTimings();
        renderTimeline();
      });
    });

    root.querySelectorAll("[data-del]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.del);
        cuts.splice(idx, 1);
        reflowSceneTimings();
        renderTimeline();
      });
    });

    root.querySelectorAll("[data-scene-upload]").forEach((input) => {
      input.addEventListener("change", async () => {
        const file = input.files?.[0];
        if (!file) return;
        const idx = Number(input.dataset.idx);
        try {
          const uploaded = await uploadFileToProject(file);
          cuts[idx].source = uploaded.path;
          renderTimeline();
          toast(`✅ Đã gắn ${uploaded.filename} vào cảnh #${idx + 1}`);
        } catch (err) {
          toast(`Lỗi upload: ${err.message}`, true);
        }
      });
    });

    root.querySelectorAll("[data-preview-tts]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const idx = Number(btn.dataset.previewTts);
        const text = (cuts[idx]?.narration || "").trim();
        if (!text) {
          toast("Hãy nhập lời thoại cho cảnh này trước!", true);
          return;
        }
        btn.disabled = true;
        try {
          syncStateFromProjectForm();
          const res = await apiFetch("/api/studio/tts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              project_id: state.projectId,
              text,
              voice_persona: cuts[idx].voice_persona || state.project.voice_persona || "",
              model: state.project.voice_model,
              speed: state.project.voice_speed,
              pitch_semitones: state.project.voice_pitch ?? 0,
              timbre: state.project.voice_timbre || "balanced",
              enhance_preset: state.project.audio_enhance_preset || "clean_speech",
              filename: `preview_scene_${idx + 1}.wav`,
            }),
          });
          const audioEl = root.querySelector(`[data-scene-audio="${idx}"]`);
          if (audioEl) {
            audioEl.src = `${res.url}?t=${Date.now()}`;
            audioEl.classList.remove("hidden");
            audioEl.play().catch(() => {});
          }
          toast(`🔊 Giọng đọc dài ${res.duration_seconds}s (${res.persona_used || "Piper"})`);
        } catch (err) {
          toast(`Lỗi TTS: ${err.message}`, true);
        } finally {
          btn.disabled = false;
        }
      });
    });
  }

  // -------------------------------------------------------------------------
  // Upload & Tool Actions
  // -------------------------------------------------------------------------

  async function uploadFileToProject(file) {
    const fd = new FormData();
    fd.append("project_id", state.projectId || "quick-studio");
    fd.append("file", file);
    const res = await apiFetch("/api/studio/upload", { method: "POST", body: fd });
    const projData = await apiFetch(`/api/studio/projects/${encodeURIComponent(state.projectId)}`);
    state.assets = projData.assets || [];
    renderAssetSidebar();
    refreshAssetDropdowns();
    return res;
  }

  function renderVisualResultBox(boxId, asset, label) {
    const box = $(boxId);
    box.classList.remove("hidden");
    const safeUrl = escapeHtml(`${asset.url}?t=${Date.now()}`);
    box.innerHTML = `
      <img src="${safeUrl}" alt="${escapeHtml(label)}" />
      <div class="inline-row">
        <a href="${escapeHtml(asset.url)}" download class="btn btn-xs btn-secondary">⬇️ Tải về</a>
        <button type="button" class="btn btn-xs btn-accent" data-use-visual="1">➕ Chèn vào Cảnh mới trong Timeline</button>
      </div>`;
    box.querySelector("[data-use-visual]").addEventListener("click", () => {
      insertMediaScene(asset.kind || "image", asset.path, "");
    });
  }

  // -------------------------------------------------------------------------
  // Background Job Polling & Video Player
  // -------------------------------------------------------------------------

  function pollJob(jobId, onUpdate, onDone) {
    if (state.activePollTimer) clearInterval(state.activePollTimer);
    const tick = async () => {
      try {
        const data = await apiFetch(`/api/studio/jobs/${encodeURIComponent(jobId)}`);
        const job = data.job;
        onUpdate(job);
        if (job.status === "completed" || job.status === "failed") {
          clearInterval(state.activePollTimer);
          state.activePollTimer = null;
          onDone(job);
        }
      } catch (err) {
        clearInterval(state.activePollTimer);
        state.activePollTimer = null;
        toast(`Lỗi theo dõi tiến trình: ${err.message}`, true);
      }
    };
    tick();
    state.activePollTimer = setInterval(tick, 1200);
  }

  function playRenderedVideo(renderObj) {
    const player = $("studio-video-player");
    const empty = $("player-empty");
    const actions = $("player-actions");
    const dl = $("player-download-btn");
    const meta = $("player-meta");

    player.src = `${renderObj.url}?t=${Date.now()}`;
    player.classList.remove("hidden");
    empty.classList.add("hidden");
    actions.classList.remove("hidden");
    dl.href = renderObj.url;

    const p = renderObj.probe || {};
    const parts = [renderObj.filename];
    if (p.duration_seconds) parts.push(`${p.duration_seconds}s`);
    if (p.width && p.height) parts.push(`${p.width}x${p.height}`);
    if (renderObj.size_mb || p.size_mb) parts.push(`${renderObj.size_mb || p.size_mb} MB`);
    meta.textContent = parts.join(" • ");
  }

  // -------------------------------------------------------------------------
  // Static Event Bindings
  // -------------------------------------------------------------------------

  // -------------------------------------------------------------------------
  // 750+ OpenMontage Catalog & Multi-Persona Voice Studio
  // -------------------------------------------------------------------------

  async function loadCatalog(page = 1) {
    state.catalogQuery.page = page;
    const params = new URLSearchParams({
      category: state.catalogQuery.category || "all",
      q: state.catalogQuery.q || "",
      page: String(page),
      page_size: String(state.catalogQuery.page_size || 48),
    });
    try {
      const data = await apiFetch(`/api/studio/catalog?${params.toString()}`);
      state.catalogMeta = {
        total: data.total || 0,
        filtered_total: data.filtered_total || 0,
        page: data.page || 1,
        page_size: data.page_size || 48,
        total_pages: data.total_pages || 1,
        categories: data.categories || [],
      };
      state.catalogItems = data.items || [];
      state.catalogItems.forEach((item) => {
        state.catalogItemsById[item.id] = item;
      });
      renderStatusPills();
      renderCatalogSection();
    } catch (err) {
      toast(`Lỗi tải thư viện mẫu: ${err.message}`, true);
    }
  }

  function renderCatalogSection() {
    const meta = state.catalogMeta;
    const badge = $("catalog-total-badge");
    if (badge) {
      badge.textContent = `${meta.total} mẫu & phong cách (đang hiện: ${meta.filtered_total})`;
    }

    const catBox = $("catalog-categories");
    if (catBox) {
      catBox.innerHTML = (meta.categories || [])
        .map((c) => {
          const active = c.id === state.catalogQuery.category ? " active" : "";
          return `<button type="button" class="cat-pill${active}" data-cat-id="${escapeHtml(c.id)}">${escapeHtml(c.label)} (${c.count})</button>`;
        })
        .join("");
      catBox.querySelectorAll("[data-cat-id]").forEach((btn) => {
        btn.addEventListener("click", () => {
          state.catalogQuery.category = btn.dataset.catId;
          loadCatalog(1);
        });
      });
    }

    const grid = $("catalog-grid");
    if (grid) {
      if (!state.catalogItems.length) {
        grid.innerHTML = `<div class="muted">Không tìm thấy mẫu nào phù hợp từ khóa.</div>`;
      } else {
        grid.innerHTML = state.catalogItems
          .map((item) => {
            const tc = item.style_config?.themeConfig || {};
            const swatches = [tc.backgroundColor, tc.primaryColor, tc.accentColor, tc.textColor]
              .filter(Boolean)
              .slice(0, 4)
              .map((hex) => `<span class="swatch-dot" style="background:${escapeHtml(hex)}" title="${escapeHtml(hex)}"></span>`)
              .join("");
            const thumbUrl = `/api/studio/catalog/${encodeURIComponent(item.id)}/thumb.svg`;
            return `
              <article class="catalog-card">
                <img class="catalog-thumb" src="${thumbUrl}" loading="lazy" alt="${escapeHtml(item.vi_title || item.title)}" />
                <div class="catalog-body">
                  <div class="catalog-meta-row">
                    <span class="badge-mini">${escapeHtml(item.category_label || item.category)}</span>
                    <div class="swatch-row">${swatches}</div>
                  </div>
                  <div class="catalog-title">${escapeHtml(item.vi_title || item.title)}</div>
                  <p class="catalog-desc">${escapeHtml(item.summary || "")}</p>
                  <div class="catalog-actions">
                    <button type="button" class="btn btn-xs btn-primary" data-apply-style="${escapeHtml(item.id)}">🎨 Áp dụng</button>
                    <button type="button" class="btn btn-xs btn-accent" data-insert-sample="${escapeHtml(item.id)}">➕ Chèn cảnh</button>
                    <button type="button" class="btn btn-xs btn-secondary" data-view-catalog="${escapeHtml(item.id)}">📖 Chi tiết</button>
                  </div>
                </div>
              </article>`;
          })
          .join("");

        grid.querySelectorAll("[data-apply-style]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const item = state.catalogItemsById[btn.dataset.applyStyle];
            if (item) applyCatalogStyle(item);
          });
        });
        grid.querySelectorAll("[data-insert-sample]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const item = state.catalogItemsById[btn.dataset.insertSample];
            if (item) insertCatalogSample(item);
          });
        });
        grid.querySelectorAll("[data-view-catalog]").forEach((btn) => {
          btn.addEventListener("click", () => openCatalogModal(btn.dataset.viewCatalog));
        });
      }
    }

    const pageInfo = $("catalog-page-info");
    if (pageInfo) {
      pageInfo.textContent = `Trang ${meta.page} / ${meta.total_pages} (${meta.filtered_total} mẫu)`;
    }
    if ($("catalog-prev")) $("catalog-prev").disabled = meta.page <= 1;
    if ($("catalog-next")) $("catalog-next").disabled = meta.page >= meta.total_pages;
  }

  function applyCatalogStyle(item) {
    if (!state.project || !item) return;
    const sc = item.style_config || {};
    const tc = sc.themeConfig || {};
    state.project.style_preset_id = item.id;
    state.project.style_title = item.vi_title || item.title;
    if (sc.theme) state.project.theme = sc.theme;
    if (sc.color_grade !== undefined) state.project.color_grade_profile = sc.color_grade || "";
    if (sc.color_intensity !== undefined) state.project.color_grade_intensity = sc.color_intensity;
    if (sc.audio_preset) state.project.audio_enhance_preset = sc.audio_preset;

    state.project.theme_config = {
      ...(state.project.theme_config || {}),
      ...tc,
      pace: sc.pace || state.project.theme_config?.pace || "normal",
    };

    if (sc.caption_style) {
      if (typeof sc.caption_style === "object") {
        state.project.caption_props = {
          ...(state.project.caption_props || {}),
          ...sc.caption_style,
          boxStyle: sc.caption_style.boxStyle || (typeof state.project.caption_props?.boxStyle === "string" ? state.project.caption_props.boxStyle : "pill"),
        };
      } else {
        state.project.caption_props = {
          ...(state.project.caption_props || {}),
          boxStyle: sc.caption_style,
          highlightColor: tc.captionHighlightColor || tc.accentColor || "#FACC15",
        };
      }
    }

    (state.project.cuts || []).forEach((cut) => {
      if (tc.backgroundColor) cut.backgroundColor = tc.backgroundColor;
      if (tc.accentColor) cut.accentColor = tc.accentColor;
    });

    syncProjectFormFromState();
    renderTimeline();
    toast(`🎨 Đã áp dụng phong cách "${item.vi_title || item.title}" cho toàn bộ video!`);
  }

  function insertCatalogSample(item) {
    if (!item) return;
    const sample = item.sample_cut || {};
    const tc = item.style_config?.themeConfig || {};
    const overrides = {
      ...sample,
      backgroundColor: sample.backgroundColor || tc.backgroundColor || "#0F172A",
      accentColor: sample.accentColor || tc.accentColor || "#22D3EE",
    };
    addScene(sample.type || "hero_title", overrides);
    toast(`➕ Đã chèn cảnh mẫu từ "${item.vi_title || item.title}" vào Timeline!`);
  }

  async function openCatalogModal(itemId) {
    try {
      const data = await apiFetch(`/api/studio/catalog/${encodeURIComponent(itemId)}`);
      const item = data.item;
      if (!item) return;
      state.modalCatalogItem = item;
      state.catalogItemsById[item.id] = item;

      $("catalog-modal-title").textContent = item.vi_title || item.title;
      $("catalog-modal-thumb").src = `/api/studio/catalog/${encodeURIComponent(item.id)}/thumb.svg`;
      $("catalog-modal-meta").innerHTML = `
        <div class="badge-mini">${escapeHtml(item.category_label || item.category)}</div>
        <p class="muted" style="margin-top:8px">${escapeHtml(item.summary || "")}</p>
        <div class="muted"><strong>Theme:</strong> <code>${escapeHtml(item.style_config?.theme || "default")}</code> • <strong>Color Grade:</strong> <code>${escapeHtml(item.style_config?.color_grade || "none")}</code></div>
        ${item.source_file ? `<div class="muted"><strong>Nguồn OpenMontage:</strong> <code>${escapeHtml(item.source_file)}</code></div>` : ""}`;
      $("catalog-modal-content").textContent = data.content || "(Preset thiết kế thị giác dựng sẵn trong OpenMontage Studio)";
      $("catalog-modal").classList.remove("hidden");
    } catch (err) {
      toast(`Không mở được chi tiết mẫu: ${err.message}`, true);
    }
  }

  function renderPersonaGrid() {
    const grid = $("voice-persona-grid");
    if (!grid) return;
    const g = $("voice-filter-gender")?.value || "all";
    const a = $("voice-filter-age")?.value || "all";
    const l = $("voice-filter-lang")?.value || "all";
    const t = $("voice-filter-tone")?.value || "all";
    const q = ($("voice-search")?.value || "").trim().toLowerCase();

    const filtered = (state.personas || []).filter((p) => {
      if (g !== "all" && p.gender !== g) return false;
      if (a !== "all" && p.age_group !== a) return false;
      if (l !== "all" && p.language !== l) return false;
      if (t !== "all" && p.tone !== t) return false;
      if (q) {
        const hay = `${p.name} ${p.description} ${p.tone_label} ${p.age_label} ${p.gender_label}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    const badge = $("voice-count-badge");
    if (badge) {
      badge.textContent = `${filtered.length} / ${(state.personas || []).length} giọng đọc`;
    }

    if (!filtered.length) {
      grid.innerHTML = `<div class="muted">Không có giọng đọc nào khớp bộ lọc. Hãy thử chọn "Tất cả".</div>`;
      return;
    }

    const activePersonaId = state.project?.voice_persona || "";
    grid.innerHTML = filtered
      .map((p) => {
        const sel = p.id === activePersonaId ? " selected" : "";
        return `
          <article class="persona-card${sel}">
            <div class="persona-head">
              <span class="persona-avatar">${escapeHtml(p.avatar || "🎙️")}</span>
              <div>
                <div class="persona-name">${escapeHtml(p.name)}</div>
                <div class="persona-badges">
                  <span class="badge-mini">${escapeHtml(p.gender_label)}</span>
                  <span class="badge-mini">${escapeHtml(p.age_label)}</span>
                  <span class="badge-mini">${escapeHtml(p.tone_label)}</span>
                  <span class="badge-mini">${p.language === "en" ? "🇺🇸 EN" : "🇻🇳 VI"}</span>
                </div>
              </div>
            </div>
            <p class="muted">${escapeHtml(p.description)}</p>
            <p class="persona-sample">“${escapeHtml(p.sample_text)}”</p>
            <div class="persona-actions">
              <button type="button" class="btn btn-xs btn-secondary" data-play-persona="${escapeHtml(p.id)}">🔊 Nghe thử</button>
              <button type="button" class="btn btn-xs btn-accent" data-pick-persona="${escapeHtml(p.id)}">✅ Chọn giọng này</button>
            </div>
          </article>`;
      })
      .join("");

    grid.querySelectorAll("[data-play-persona]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const pid = btn.dataset.playPersona;
        const p = (state.personas || []).find((x) => x.id === pid);
        if (!p) return;
        btn.disabled = true;
        try {
          const res = await apiFetch("/api/studio/tts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              project_id: state.projectId || "demos",
              voice_persona: p.id,
              text: p.sample_text,
              prefer_offline: $("tts-prefer-offline")?.checked || false,
              filename: `audition_${p.id}.wav`,
            }),
          });
          const audio = $("persona-audition-audio");
          if (audio) {
            audio.src = `${res.url}?t=${Date.now()}`;
            audio.classList.remove("hidden");
            audio.play().catch(() => {});
          }
          toast(`🔊 Đang phát thử: ${p.name} (${res.duration_seconds}s)`);
        } catch (err) {
          toast(`Lỗi nghe thử: ${err.message}`, true);
        } finally {
          btn.disabled = false;
        }
      });
    });

    grid.querySelectorAll("[data-pick-persona]").forEach((btn) => {
      btn.addEventListener("click", () => applyPersonaSelection(btn.dataset.pickPersona));
    });
  }

  function applyPersonaSelection(personaId) {
    const p = (state.personas || []).find((x) => x.id === personaId);
    if (!p) return;
    if (state.project) {
      state.project.voice_persona = p.id;
      state.project.voice_model = p.piper_model || state.project.voice_model;
      state.project.voice_speed = p.speed ?? 1.0;
      state.project.voice_pitch = p.pitch_semitones ?? 0;
      state.project.voice_timbre = p.timbre || "balanced";
      state.project.audio_enhance_preset = p.enhance_preset || "clean_speech";
      syncProjectFormFromState();
    }
    if ($("tts-persona")) $("tts-persona").value = p.id;
    syncTtsFormFromPersona(p);
    renderPersonaGrid();
    toast(`✅ Đã chọn giọng "${p.name}" cho dự án & Phòng thu!`);
  }

  function syncTtsFormFromPersona(p) {
    if (!p) return;
    if (p.piper_model && $("tts-voice")) $("tts-voice").value = p.piper_model;
    if ($("tts-speed")) {
      $("tts-speed").value = String(p.speed ?? 1.0);
      $("tts-speed-val").textContent = Number(p.speed ?? 1.0).toFixed(2);
    }
    if ($("tts-pitch")) {
      $("tts-pitch").value = String(p.pitch_semitones ?? 0);
      $("tts-pitch-val").textContent = Number(p.pitch_semitones ?? 0).toFixed(1);
    }
    if (p.timbre && $("tts-timbre")) $("tts-timbre").value = p.timbre;
    if (p.enhance_preset && $("tts-enhance")) $("tts-enhance").value = p.enhance_preset;
  }

  // -------------------------------------------------------------------------
  // Typography & 40+ Font Studio
  // -------------------------------------------------------------------------

  function injectStudioFonts(fonts) {
    document.querySelectorAll("link.dyn-font-link").forEach((el) => el.remove());
    let customStyle = document.getElementById("studio-custom-fonts-style");
    if (!customStyle) {
      customStyle = document.createElement("style");
      customStyle.id = "studio-custom-fonts-style";
      document.head.appendChild(customStyle);
    }

    const customRules = [];
    const googleSpecs = [];

    (fonts || []).forEach((f) => {
      if (f.is_custom && f.file_url) {
        customRules.push(
          `@font-face { font-family: '${f.family.replace(/'/g, "")}'; src: url('${f.file_url}'); font-display: swap; }`
        );
      } else if (f.family) {
        const famQ = encodeURIComponent(f.family).replace(/%20/g, "+");
        const hasBold = Array.isArray(f.weights) && f.weights.includes("700");
        googleSpecs.push(hasBold ? `family=${famQ}:wght@400;700` : `family=${famQ}`);
      }
    });

    customStyle.textContent = customRules.join("\n");

    const chunkSize = 15;
    for (let i = 0; i < googleSpecs.length; i += chunkSize) {
      const chunk = googleSpecs.slice(i, i + chunkSize);
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.className = "dyn-font-link";
      link.href = `https://fonts.googleapis.com/css2?${chunk.join("&")}&display=swap`;
      document.head.appendChild(link);
    }
  }

  function renderFontStudio() {
    const badge = $("font-total-badge");
    if (badge) {
      badge.textContent = `${state.fonts.length} Font chữ (${state.fontPairings.length} bộ phối)`;
    }

    const activeHead = state.project?.theme_config?.headingFont || $("cfg-font")?.value || "Inter";
    const activeCap = state.project?.caption_props?.fontFamily || $("cfg-caption-font")?.value || "";
    if ($("active-heading-font-pill")) {
      $("active-heading-font-pill").textContent = activeHead;
    }
    if ($("active-caption-font-pill")) {
      $("active-caption-font-pill").textContent = activeCap || "Theo Font chính";
    }

    const pairGrid = $("font-pairings-grid");
    if (pairGrid) {
      pairGrid.innerHTML = (state.fontPairings || [])
        .map((pair) => {
          return `
            <article class="font-pairing-card">
              <div class="font-pairing-head">
                <div class="font-pairing-title">${escapeHtml(pair.title)}</div>
                <span class="badge-mini">${escapeHtml(pair.badge)}</span>
              </div>
              <div class="font-pairing-preview">
                <div style="font-family:'${escapeHtml(pair.headingFont)}',sans-serif;font-weight:700;font-size:17px;color:#f8fafc;">
                  ${escapeHtml(pair.headingFont)} — Tiêu đề chính
                </div>
                <div style="font-family:'${escapeHtml(pair.bodyFont)}',sans-serif;font-size:12.5px;color:#cbd5e1;margin-top:2px;">
                  ${escapeHtml(pair.bodyFont)} (Nội dung) • 💬 ${escapeHtml(pair.captionFont)} (Phụ đề)
                </div>
              </div>
              <p class="muted">${escapeHtml(pair.description)}</p>
              <button type="button" class="btn btn-xs btn-primary" data-apply-pairing="${escapeHtml(pair.id)}">✨ Áp Bộ Phối Này</button>
            </article>`;
        })
        .join("");

      pairGrid.querySelectorAll("[data-apply-pairing]").forEach((btn) => {
        btn.addEventListener("click", () => applyFontPairing(btn.dataset.applyPairing));
      });
    }

    const catBox = $("font-filter-category");
    if (catBox) {
      catBox.innerHTML = (state.fontCategories || [])
        .map((cat) => {
          const cnt =
            cat.id === "all"
              ? state.fonts.length
              : state.fonts.filter((f) => f.category === cat.id).length;
          const active = cat.id === state.fontCategory ? " active" : "";
          return `<button type="button" class="cat-pill${active}" data-font-cat="${escapeHtml(cat.id)}">${escapeHtml(cat.label)} (${cnt})</button>`;
        })
        .join("");

      catBox.querySelectorAll("[data-font-cat]").forEach((btn) => {
        btn.addEventListener("click", () => {
          state.fontCategory = btn.dataset.fontCat;
          renderFontStudio();
        });
      });
    }

    renderFontCardsOnly();
  }

  function renderFontCardsOnly() {
    const grid = $("font-catalog-grid");
    if (!grid) return;
    const cat = state.fontCategory || "all";
    const q = (state.fontSearch || "").trim().toLowerCase();
    const previewTxt = (state.fontPreviewText || "").trim();
    const sizePx = Number(state.fontPreviewSize || 30);
    const weightVal = String(state.fontPreviewWeight || "700");

    const filtered = (state.fonts || []).filter((f) => {
      if (cat !== "all" && f.category !== cat) return false;
      if (q) {
        const hay = `${f.family} ${f.label} ${f.best_for} ${f.category_label}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    if (!filtered.length) {
      grid.innerHTML = `<div class="muted">Không tìm thấy font nào khớp bộ lọc. Bạn có thể tải thêm font .ttf/.otf từ máy tính!</div>`;
      return;
    }

    const activeHead = state.project?.theme_config?.headingFont || $("cfg-font")?.value || "";
    const activeCap = state.project?.caption_props?.fontFamily || $("cfg-caption-font")?.value || "";

    grid.innerHTML = filtered
      .map((f) => {
        const isHead = activeHead === f.family;
        const isCap = activeCap === f.family;
        const cls = isHead ? " active-heading" : isCap ? " active-caption" : "";
        const sample = previewTxt || f.sample_text;
        return `
          <article class="font-card${cls}">
            <div class="font-pairing-head">
              <strong>${escapeHtml(f.family)}</strong>
              <span class="badge-mini">${escapeHtml(f.category_label)}</span>
            </div>
            <div class="font-sample-heading" data-default-sample="${escapeHtml(f.sample_text)}" style="font-family:'${escapeHtml(f.family)}',system-ui,sans-serif;font-size:${sizePx}px;font-weight:${escapeHtml(weightVal)};">
              ${escapeHtml(sample)}
            </div>
            <div class="font-sample-sub">
              <span>🇻🇳 Tiếng Việt • Phù hợp: ${escapeHtml(f.best_for)}</span>
              ${isHead ? ` • <strong style="color:var(--primary)">Đang làm Tiêu đề</strong>` : ""}
              ${isCap ? ` • <strong style="color:var(--accent)">Đang làm Phụ đề</strong>` : ""}
            </div>
            <div class="font-card-actions">
              <button type="button" class="btn btn-xs btn-primary" data-apply-font-all="${escapeHtml(f.family)}">🎯 Áp Toàn Bộ</button>
              <button type="button" class="btn btn-xs btn-secondary" data-apply-font-heading="${escapeHtml(f.family)}">📌 Làm Tiêu Đề</button>
              <button type="button" class="btn btn-xs btn-accent" data-apply-font-caption="${escapeHtml(f.family)}">💬 Làm Phụ Đề</button>
            </div>
          </article>`;
      })
      .join("");

    grid.querySelectorAll("[data-apply-font-all]").forEach((btn) => {
      btn.addEventListener("click", () => applyFontChoice(btn.dataset.applyFontAll, "all"));
    });
    grid.querySelectorAll("[data-apply-font-heading]").forEach((btn) => {
      btn.addEventListener("click", () => applyFontChoice(btn.dataset.applyFontHeading, "heading"));
    });
    grid.querySelectorAll("[data-apply-font-caption]").forEach((btn) => {
      btn.addEventListener("click", () => applyFontChoice(btn.dataset.applyFontCaption, "caption"));
    });
  }

  function applyFontChoice(family, target = "all") {
    if (!family || !state.project) return;
    state.project.theme_config = state.project.theme_config || {};
    state.project.caption_props = state.project.caption_props || {};

    if (target === "all" || target === "heading") {
      state.project.theme_config.headingFont = family;
      state.project.theme_config.bodyFont = family;
    }
    if (target === "all" || target === "caption") {
      state.project.caption_props.fontFamily = family;
    }

    syncProjectFormFromState();
    const label =
      target === "all"
        ? "Tiêu đề, Nội dung & Phụ đề"
        : target === "heading"
        ? "Font Tiêu đề & Nội dung"
        : "Font Phụ đề Karaoke";
    toast(`🔤 Đã áp dụng font "${family}" cho ${label}!`);
  }

  function applyFontPairing(pairingId) {
    const pair = (state.fontPairings || []).find((p) => p.id === pairingId);
    if (!pair || !state.project) return;
    state.project.theme_config = {
      ...(state.project.theme_config || {}),
      headingFont: pair.headingFont,
      bodyFont: pair.bodyFont,
    };
    state.project.caption_props = {
      ...(state.project.caption_props || {}),
      fontFamily: pair.captionFont,
    };
    syncProjectFormFromState();
    toast(`✨ Đã áp dụng bộ phối "${pair.headingFont} + ${pair.bodyFont} + ${pair.captionFont}"!`);
  }

  function bindStaticEvents() {
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });

    $("proj-voice-speed").addEventListener("input", (e) => {
      $("voice-speed-val").textContent = Number(e.target.value).toFixed(2);
    });

    $("proj-color-intensity").addEventListener("input", (e) => {
      $("color-int-val").textContent = Number(e.target.value).toFixed(2);
    });

    $("btn-goto-catalog").addEventListener("click", () => switchTab("catalog"));
    $("btn-open-voice-tab").addEventListener("click", () => switchTab("voice"));
    $("btn-open-font-tab").addEventListener("click", () => switchTab("fonts"));

    $("cfg-font").addEventListener("change", () => {
      if ($("active-heading-font-pill")) {
        $("active-heading-font-pill").textContent = $("cfg-font").value || "Inter";
      }
      renderFontStudio();
    });
    $("cfg-caption-font").addEventListener("change", () => {
      if ($("active-caption-font-pill")) {
        $("active-caption-font-pill").textContent = $("cfg-caption-font").value || "Theo Font chính";
      }
      renderFontStudio();
    });

    // Tab 2B: Font Studio controls
    $("font-preview-text").addEventListener("input", (e) => {
      state.fontPreviewText = e.target.value;
      const text = state.fontPreviewText.trim();
      document.querySelectorAll("#font-catalog-grid .font-sample-heading").forEach((el, idx) => {
        el.textContent = text || el.dataset.defaultSample || state.fonts[idx]?.sample_text || el.textContent;
      });
    });
    $("font-preview-size").addEventListener("input", (e) => {
      state.fontPreviewSize = Number(e.target.value) || 30;
      $("font-size-val").textContent = String(state.fontPreviewSize);
      document.querySelectorAll("#font-catalog-grid .font-sample-heading").forEach((el) => {
        el.style.fontSize = `${state.fontPreviewSize}px`;
      });
    });
    $("font-preview-weight").addEventListener("change", (e) => {
      state.fontPreviewWeight = e.target.value;
      document.querySelectorAll("#font-catalog-grid .font-sample-heading").forEach((el) => {
        el.style.fontWeight = state.fontPreviewWeight;
      });
    });
    $("font-search").addEventListener("input", (e) => {
      state.fontSearch = e.target.value;
      renderFontCardsOnly();
    });
    $("font-upload-input").addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        const fd = new FormData();
        fd.append("file", file);
        const res = await apiFetch("/api/studio/fonts/upload", { method: "POST", body: fd });
        state.fonts = res.fonts || state.fonts;
        injectStudioFonts(state.fonts);
        populateDropdowns();
        renderStatusPills();
        if (res.font?.family) {
          state.fontCategory = "all";
          applyFontChoice(res.font.family, "all");
        } else {
          renderFontStudio();
        }
        toast(`✅ Đã cài font "${res.font?.family || file.name}" vào Kho Font!`);
      } catch (err) {
        toast(`Lỗi tải font: ${err.message}`, true);
      }
      e.target.value = "";
    });

    $("proj-voice-persona").addEventListener("change", (e) => {
      const pid = e.target.value;
      if (pid) {
        applyPersonaSelection(pid);
      } else if (state.project) {
        state.project.voice_persona = "";
        renderPersonaGrid();
      }
    });

    $("btn-apply-colors-all").addEventListener("click", () => {
      if (!state.project) return;
      const bg = $("cfg-bg-color").value;
      const acc = $("cfg-accent-color").value;
      (state.project.cuts || []).forEach((c) => {
        c.backgroundColor = bg;
        c.accentColor = acc;
      });
      syncStateFromProjectForm();
      renderTimeline();
      toast("🎨 Đã áp dụng bảng màu mới cho toàn bộ các cảnh!");
    });

    // Tab 2: 750+ Catalog
    let catalogTimer = null;
    $("catalog-search").addEventListener("input", (e) => {
      state.catalogQuery.q = e.target.value;
      clearTimeout(catalogTimer);
      catalogTimer = setTimeout(() => loadCatalog(1), 220);
    });
    $("catalog-clear-search").addEventListener("click", () => {
      $("catalog-search").value = "";
      state.catalogQuery.q = "";
      state.catalogQuery.category = "all";
      loadCatalog(1);
    });
    $("catalog-prev").addEventListener("click", () => {
      if (state.catalogMeta.page > 1) loadCatalog(state.catalogMeta.page - 1);
    });
    $("catalog-next").addEventListener("click", () => {
      if (state.catalogMeta.page < state.catalogMeta.total_pages) loadCatalog(state.catalogMeta.page + 1);
    });

    $("catalog-modal-close").addEventListener("click", () => $("catalog-modal").classList.add("hidden"));
    $("catalog-modal").addEventListener("click", (e) => {
      if (e.target === $("catalog-modal")) $("catalog-modal").classList.add("hidden");
    });
    $("catalog-modal-apply").addEventListener("click", () => {
      if (state.modalCatalogItem) {
        applyCatalogStyle(state.modalCatalogItem);
        $("catalog-modal").classList.add("hidden");
      }
    });
    $("catalog-modal-insert").addEventListener("click", () => {
      if (state.modalCatalogItem) {
        insertCatalogSample(state.modalCatalogItem);
        $("catalog-modal").classList.add("hidden");
      }
    });

    // Tab 3: Multi-Voice Persona filters
    ["voice-filter-gender", "voice-filter-age", "voice-filter-lang", "voice-filter-tone"].forEach((id) => {
      $(id).addEventListener("change", renderPersonaGrid);
    });
    $("voice-search").addEventListener("input", renderPersonaGrid);

    $("tts-persona").addEventListener("change", (e) => {
      const p = (state.personas || []).find((x) => x.id === e.target.value);
      if (p) syncTtsFormFromPersona(p);
    });

    $("tts-pitch").addEventListener("input", (e) => {
      $("tts-pitch-val").textContent = Number(e.target.value).toFixed(1);
    });

    $("proj-music-vol").addEventListener("input", (e) => {
      $("music-vol-val").textContent = String(Math.round(Number(e.target.value) * 100));
    });

    $("btn-preview-music").addEventListener("click", () => {
      const val = $("proj-music").value;
      const track = (state.status?.music_tracks || []).find((m) => m.path === val);
      const player = $("music-preview-audio");
      if (!track) {
        toast("Chưa chọn nhạc nền để nghe thử.", true);
        return;
      }
      if (player.src && !player.paused) {
        player.pause();
        $("btn-preview-music").textContent = "▶";
      } else {
        player.src = track.url;
        player.classList.remove("hidden");
        player.play().catch(() => {});
        $("btn-preview-music").textContent = "⏸";
      }
    });

    $("btn-refresh-projects").addEventListener("click", refreshProjectsList);

    $("btn-create-project").addEventListener("click", async () => {
      const title = $("new-project-title").value.trim();
      const templateId = $("new-project-template").value;
      if (!title) {
        toast("Vui lòng nhập tên dự án mới.", true);
        return;
      }
      try {
        const res = await apiFetch("/api/studio/projects", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title, template_id: templateId }),
        });
        $("new-project-title").value = "";
        applyLoadedProject(res);
        await refreshProjectsList();
        toast(`✨ Đã tạo dự án mới: ${title}`);
      } catch (err) {
        toast(`Lỗi tạo dự án: ${err.message}`, true);
      }
    });

    $("btn-load-template").addEventListener("click", () => {
      const tid = $("template-select").value;
      const tpl = state.templates.find((t) => t.template_id === tid);
      if (!tpl || !state.project) return;
      const currentId = state.project.project_id;
      state.project = {
        ...JSON.parse(JSON.stringify(tpl.project)),
        project_id: currentId,
      };
      syncProjectFormFromState();
      renderTimeline();
      toast(`📋 Đã nạp kịch bản mẫu: ${tpl.title}`);
    });

    $("btn-save-project").addEventListener("click", () => saveCurrentProject(false));

    document.querySelectorAll("[data-add-type]").forEach((btn) => {
      btn.addEventListener("click", () => addScene(btn.dataset.addType));
    });

    $("sidebar-upload-input").addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        const res = await uploadFileToProject(file);
        toast(`📤 Đã tải lên: ${res.filename}`);
      } catch (err) {
        toast(`Lỗi tải lên: ${err.message}`, true);
      }
      e.target.value = "";
    });

    // Tab 2: TTS Studio
    $("tts-speed").addEventListener("input", (e) => {
      $("tts-speed-val").textContent = Number(e.target.value).toFixed(2);
    });

    $("btn-run-tts").addEventListener("click", async () => {
      const text = $("tts-text").value.trim();
      if (!text) {
        toast("Hãy nhập văn bản cần đọc.", true);
        return;
      }
      const btn = $("btn-run-tts");
      btn.disabled = true;
      try {
        const res = await apiFetch("/api/studio/tts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            project_id: state.projectId,
            text,
            voice_persona: $("tts-persona").value || null,
            model: $("tts-voice").value,
            speed: parseFloat($("tts-speed").value) || 1.0,
            pitch_semitones: parseFloat($("tts-pitch").value) || 0.0,
            timbre: $("tts-timbre").value || "balanced",
            enhance_preset: $("tts-enhance").value || "clean_speech",
            prefer_offline: $("tts-prefer-offline").checked,
            sentence_silence: parseFloat($("tts-silence").value) || 0.25,
          }),
        });
        state.lastTtsResult = { ...res, text };
        $("tts-result-box").classList.remove("hidden");
        $("tts-dur-label").textContent = `${res.duration_seconds}s`;
        $("tts-download-link").href = res.url;
        $("tts-audio-player").src = `${res.url}?t=${Date.now()}`;
        $("tts-audio-player").play().catch(() => {});
        $("tts-caption-preview").innerHTML = (res.captions || [])
          .slice(0, 30)
          .map(
            (c) =>
              `<span class="word-chip">${escapeHtml(c.word)} <small class="muted">${(c.startMs / 1000).toFixed(1)}s</small></span>`
          )
          .join("");
        const projData = await apiFetch(`/api/studio/projects/${encodeURIComponent(state.projectId)}`);
        state.assets = projData.assets || [];
        renderAssetSidebar();
        refreshAssetDropdowns();
        toast(`🔊 Tạo giọng đọc thành công (${res.duration_seconds}s · ${res.persona_used || "Piper"})`);
      } catch (err) {
        toast(`Lỗi TTS: ${err.message}`, true);
      } finally {
        btn.disabled = false;
      }
    });

    $("btn-tts-add-scene").addEventListener("click", () => {
      const text = $("tts-text").value.trim();
      if (!text) return;
      const dur = Math.max(3.0, (state.lastTtsResult?.duration_seconds || 4.0) + 0.8);
      const lastEnd = (state.project?.cuts || []).slice(-1)[0]?.out_seconds || 0;
      addScene("text_card", {
        in_seconds: lastEnd,
        out_seconds: Number((lastEnd + dur).toFixed(2)),
        text: text.slice(0, 90),
        subtitle: "Tạo từ Piper TTS Studio",
        narration: text,
      });
      toast("✅ Đã thêm cảnh mới với lời thoại vừa tạo!");
    });

    // Tab 2: Whisper STT
    $("btn-run-stt").addEventListener("click", async () => {
      const inputPath = $("stt-asset-select").value;
      if (!inputPath) {
        toast("Chưa có tệp audio/video trong dự án.", true);
        return;
      }
      const btn = $("btn-run-stt");
      btn.disabled = true;
      try {
        const res = await apiFetch("/api/studio/enhance", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            project_id: state.projectId,
            operation: "transcribe",
            input_path: inputPath,
            model_size: $("stt-model").value,
            language: $("stt-lang").value || null,
          }),
        });
        $("stt-result-box").classList.remove("hidden");
        $("stt-transcript-text").value = res.data?.full_text || JSON.stringify(res.data, null, 2);
        toast("📝 Đã nhận diện xong giọng nói!");
      } catch (err) {
        toast(`Lỗi Whisper STT: ${err.message}`, true);
      } finally {
        btn.disabled = false;
      }
    });

    // Tab 3: Code Snippet
    $("btn-gen-code").addEventListener("click", async () => {
      const btn = $("btn-gen-code");
      btn.disabled = true;
      try {
        const res = await apiFetch("/api/studio/visual", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            project_id: state.projectId,
            mode: "code_snippet",
            language: $("code-lang").value,
            theme: $("code-theme").value,
            title: $("code-title").value.trim(),
            code: $("code-input").value,
          }),
        });
        renderVisualResultBox("code-result", res.asset, "Code Card");
        const projData = await apiFetch(`/api/studio/projects/${encodeURIComponent(state.projectId)}`);
        state.assets = projData.assets || [];
        renderAssetSidebar();
        refreshAssetDropdowns();
        toast("💻 Đã tạo ảnh Code Card!");
      } catch (err) {
        toast(`Lỗi Code Card: ${err.message}`, true);
      } finally {
        btn.disabled = false;
      }
    });

    // Tab 3: Mermaid Diagram
    $("btn-gen-diagram").addEventListener("click", async () => {
      const btn = $("btn-gen-diagram");
      btn.disabled = true;
      try {
        const res = await apiFetch("/api/studio/visual", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            project_id: state.projectId,
            mode: "diagram",
            diagram_type: "mermaid",
            title: $("diag-title").value.trim(),
            theme: $("diag-theme").value,
            definition: $("diag-input").value,
          }),
        });
        renderVisualResultBox("diag-result", res.asset, "Mermaid Diagram");
        const projData = await apiFetch(`/api/studio/projects/${encodeURIComponent(state.projectId)}`);
        state.assets = projData.assets || [];
        renderAssetSidebar();
        refreshAssetDropdowns();
        toast("🔀 Đã vẽ sơ đồ Mermaid!");
      } catch (err) {
        toast(`Lỗi Mermaid: ${err.message}`, true);
      } finally {
        btn.disabled = false;
      }
    });

    // Tab 3: Image Enhancement
    $("enh-upload-input").addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        const res = await uploadFileToProject(file);
        $("enh-asset-select").value = res.path;
        toast(`📤 Đã sẵn sàng xử lý ảnh: ${res.filename}`);
      } catch (err) {
        toast(`Lỗi tải ảnh: ${err.message}`, true);
      }
      e.target.value = "";
    });

    document.querySelectorAll("[data-enhance]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const inputPath = $("enh-asset-select").value;
        if (!inputPath) {
          toast("Hãy chọn hoặc tải lên 1 tấm ảnh trước!", true);
          return;
        }
        btn.disabled = true;
        try {
          const res = await apiFetch("/api/studio/enhance", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              project_id: state.projectId,
              operation: btn.dataset.enhance,
              input_path: inputPath,
            }),
          });
          renderVisualResultBox("enh-result", res.asset, btn.dataset.enhance);
          const projData = await apiFetch(`/api/studio/projects/${encodeURIComponent(state.projectId)}`);
          state.assets = projData.assets || [];
          renderAssetSidebar();
          refreshAssetDropdowns();
          toast("✨ Xử lý ảnh AI thành công!");
        } catch (err) {
          toast(`Lỗi xử lý ảnh: ${err.message}`, true);
        } finally {
          btn.disabled = false;
        }
      });
    });

    // Tab 3: Local Diffusion & Stock Search
    $("btn-gen-sd").addEventListener("click", async () => {
      const prompt = $("sd-prompt").value.trim();
      if (!prompt) {
        toast("Hãy nhập prompt mô tả hình ảnh cần tạo.", true);
        return;
      }
      const [w, h] = $("sd-size").value.split("x").map(Number);
      const btn = $("btn-gen-sd");
      btn.disabled = true;
      try {
        const res = await apiFetch("/api/studio/visual", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            project_id: state.projectId,
            mode: "local_diffusion",
            prompt,
            width: w || 768,
            height: h || 512,
          }),
        });
        toast("🎨 Đang vẽ ảnh AI trên GPU...");
        pollJob(
          res.job_id,
          () => {},
          async (job) => {
            btn.disabled = false;
            if (job.status === "completed" && job.result) {
              renderVisualResultBox("sd-result", job.result, "AI Art");
              const projData = await apiFetch(`/api/studio/projects/${encodeURIComponent(state.projectId)}`);
              state.assets = projData.assets || [];
              renderAssetSidebar();
              refreshAssetDropdowns();
              toast("✅ Tạo ảnh AI thành công!");
            } else {
              toast(`Tạo ảnh AI thất bại: ${job.error || "Unknown"}`, true);
            }
          }
        );
      } catch (err) {
        btn.disabled = false;
        toast(`Lỗi Local Diffusion: ${err.message}`, true);
      }
    });

    $("btn-search-stock").addEventListener("click", async () => {
      const query = $("stock-query").value.trim();
      if (!query) {
        toast("Hãy nhập từ khóa tìm kiếm stock.", true);
        return;
      }
      const btn = $("btn-search-stock");
      btn.disabled = true;
      try {
        const res = await apiFetch("/api/studio/visual", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            project_id: state.projectId,
            mode: "stock_video",
            query,
            kind: $("stock-kind").value,
            limit: 3,
          }),
        });
        const grid = $("stock-result");
        const clips = res.clips || [];
        if (!clips.length) {
          grid.innerHTML = `<div class="muted">Không tìm thấy clip miễn phí phù hợp cho "${escapeHtml(query)}". Thử từ khóa tiếng Anh ngắn hơn.</div>`;
        } else {
          grid.innerHTML = clips
            .map(
              (c, idx) => `
              <div class="stock-card">
                ${
                  c.kind === "video"
                    ? `<video src="${escapeHtml(c.url)}" controls muted></video>`
                    : `<img src="${escapeHtml(c.url)}" alt="Stock" />`
                }
                <div class="list-item-sub">${escapeHtml(c.filename)} (${c.size_mb} MB)</div>
                <button type="button" class="btn btn-xs btn-accent" data-use-stock="${idx}">➕ Chèn vào Timeline</button>
              </div>`
            )
            .join("");
          grid.querySelectorAll("[data-use-stock]").forEach((b) => {
            b.addEventListener("click", () => {
              const clip = clips[Number(b.dataset.useStock)];
              insertMediaScene(clip.kind, clip.path, "");
            });
          });
        }
        const projData = await apiFetch(`/api/studio/projects/${encodeURIComponent(state.projectId)}`);
        state.assets = projData.assets || [];
        renderAssetSidebar();
      } catch (err) {
        toast(`Lỗi tìm stock: ${err.message}`, true);
      } finally {
        btn.disabled = false;
      }
    });

    // Topbar: Render Video
    $("btn-render-video").addEventListener("click", async () => {
      if (!state.project || !(state.project.cuts || []).length) {
        toast("Hãy thêm ít nhất 1 cảnh vào Timeline trước khi Render!", true);
        return;
      }
      syncStateFromProjectForm();
      const btn = $("btn-render-video");
      btn.disabled = true;
      switchTab("render");

      const badge = $("render-status-badge");
      const bar = $("render-progress-bar");
      const logs = $("render-logs");
      badge.className = "pill pill-warn";
      badge.textContent = "Đang render...";
      bar.style.width = "5%";
      logs.textContent = "Đang gửi yêu cầu render lên server...\n";

      try {
        const res = await apiFetch("/api/studio/render", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            project_id: state.projectId,
            project: state.project,
          }),
        });

        pollJob(
          res.job_id,
          (job) => {
            bar.style.width = `${job.progress || 10}%`;
            logs.textContent = (job.logs || []).join("\n");
            logs.scrollTop = logs.scrollHeight;
          },
          async (job) => {
            btn.disabled = false;
            if (job.status === "completed" && job.result) {
              badge.className = "pill pill-ok";
              badge.textContent = "Hoàn tất!";
              bar.style.width = "100%";
              playRenderedVideo(job.result);
              if (job.result.aligned_cuts) {
                state.project.cuts = state.project.cuts.map((c, i) => ({
                  ...c,
                  ...(job.result.aligned_cuts[i] || {}),
                }));
                renderTimeline();
              }
              const projData = await apiFetch(`/api/studio/projects/${encodeURIComponent(state.projectId)}`);
              state.assets = projData.assets || [];
              state.renders = projData.renders || [];
              renderAssetSidebar();
              renderRenderSidebar();
              await refreshProjectsList();
              toast("🎉 Render video thành công!");
            } else {
              badge.className = "pill pill-warn";
              badge.textContent = "Thất bại";
              toast(`❌ Render lỗi: ${job.error || "Unknown"}`, true);
            }
          }
        );
      } catch (err) {
        btn.disabled = false;
        badge.className = "pill pill-warn";
        badge.textContent = "Lỗi";
        logs.textContent += `\nLỗi: ${err.message}`;
        toast(`Lỗi Render: ${err.message}`, true);
      }
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
