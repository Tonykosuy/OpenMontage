"""OpenMontage Studio — Interactive local AI video production backend & web UI.

Mounts onto Backlot's FastAPI application (`backlot.server.create_app`) so both
the production monitor (`/`, `/media/{project_id}/...`, `/api/projects`) and the
interactive Studio UI (`/studio`, `/api/studio/*`) share one unified server on
`http://127.0.0.1:4760/studio`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

# Ensure repo root and virtualenv Scripts/ are on PATH before importing tools.
STUDIO_DIR = Path(__file__).resolve().parent
REPO_ROOT_DIR = STUDIO_DIR.parent
VENV_SCRIPTS = REPO_ROOT_DIR / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
if str(REPO_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT_DIR))
if VENV_SCRIPTS.exists():
    current_path = os.environ.get("PATH", "")
    if str(VENV_SCRIPTS) not in current_path.split(os.pathsep):
        os.environ["PATH"] = f"{VENV_SCRIPTS}{os.pathsep}{current_path}"

from backlot.server import create_app as create_backlot_app  # noqa: E402
from backlot.state import (  # noqa: E402
    MEDIA_AUDIO_EXT,
    MEDIA_IMAGE_EXT,
    MEDIA_VIDEO_EXT,
    PROJECTS_DIR,
    REPO_ROOT,
)
from lib.media_profiles import ALL_PROFILES, get_profile  # noqa: E402
from studio.catalog import (  # noqa: E402
    CUSTOM_FONTS_DIR,
    FONT_CATEGORIES,
    FONT_PAIRINGS,
    TIMBRE_PRESETS,
    VOICE_AGE_GROUPS,
    VOICE_GENDERS,
    VOICE_PERSONAS,
    VOICE_PERSONAS_BY_ID,
    VOICE_TONES,
    get_catalog_item,
    get_catalog_item_source,
    list_audio_enhance_options,
    list_color_grade_options,
    list_studio_fonts,
    query_catalog,
    render_catalog_thumb_svg,
    save_custom_font_file,
    synthesize_studio_voice,
)
from tools.base_tool import ToolStatus  # noqa: E402
from tools.tool_registry import ToolRegistry, registry  # noqa: E402

ToolRegistry._load_dotenv()

UI_DIR = STUDIO_DIR / "ui"
MUSIC_DIR = REPO_ROOT / "music_library"
DEMO_PROPS_DIR = REPO_ROOT / "remotion-composer" / "public" / "demo-props"

STUDIO_PROJECT_FILE = "studio_project.json"
PROJECT_MARKER_FILE = "project.json"

ALLOWED_UPLOAD_EXTS = MEDIA_IMAGE_EXT | MEDIA_VIDEO_EXT | MEDIA_AUDIO_EXT

REMO_THEMES = [
    {"id": "flat-motion-graphics", "label": "Flat Motion Graphics (Hiện đại, nổi bật)"},
    {"id": "clean-professional", "label": "Clean Professional (Tối giản chuyên nghiệp)"},
    {"id": "minimalist-diagram", "label": "Minimalist Diagram (Sơ đồ công nghệ)"},
    {"id": "anime-ghibli", "label": "Anime Ghibli (Ấm áp, nghệ thuật)"},
]

SCENE_TYPE_META = [
    {"id": "hero_title", "label": "Tiêu đề mở màn (Hero Title)"},
    {"id": "text_card", "label": "Thẻ chữ lớn (Text Card)"},
    {"id": "stat_card", "label": "Thẻ số liệu nổi bật (Stat Card)"},
    {"id": "callout", "label": "Hộp nhấn mạnh (Callout)"},
    {"id": "comparison", "label": "So sánh 2 cột (Comparison)"},
    {"id": "bar_chart", "label": "Biểu đồ cột (Bar Chart)"},
    {"id": "pie_chart", "label": "Biểu đồ tròn (Pie / Donut Chart)"},
    {"id": "kpi_grid", "label": "Lưới KPI nhiều chỉ số (KPI Grid)"},
    {"id": "progress_bar", "label": "Thanh tiến độ (Progress Bar)"},
    {"id": "image", "label": "Hình ảnh / Slide / AI Art"},
    {"id": "video", "label": "Video Clip (MP4/WebM)"},
]

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
_CACHED_TOOL_STATUS: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Path & subprocess helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_project_id(raw: str) -> str:
    """Convert Vietnamese/freeform names into a safe directory slug."""
    normalized = unicodedata.normalize("NFKD", (raw or "").strip())
    normalized = normalized.replace("đ", "d").replace("Đ", "D")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", ascii_text).strip("-_")
    if not slug:
        slug = f"project-{int(time.time())}"
    if not re.match(r"^[a-z0-9]", slug):
        slug = f"p-{slug}"
    return slug[:64]


def _project_dir(project_id: str) -> Path:
    safe_id = _sanitize_project_id(project_id)
    if safe_id != project_id and not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$", project_id or ""):
        raise HTTPException(status_code=400, detail=f"Tên project không hợp lệ: {project_id!r}")
    target = (PROJECTS_DIR / project_id).resolve()
    root = PROJECTS_DIR.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Đường dẫn project nằm ngoài PROJECTS_DIR") from exc
    return target


def _ensure_project_dirs(pdir: Path) -> None:
    for sub in ("", "assets", "audio", "uploads", "renders", "transcripts"):
        (pdir / sub if sub else pdir).mkdir(parents=True, exist_ok=True)


def _media_url(project_id: str, path: Path) -> str:
    pdir = (PROJECTS_DIR / project_id).resolve()
    resolved = path.resolve()
    rel = resolved.relative_to(pdir).as_posix()
    return f"/media/{project_id}/{rel}"


def _resolve_studio_input_path(raw_path: str, project_id: Optional[str] = None) -> Path:
    """Resolve either an absolute file path, project-relative path, or `/media/{pid}/...` URL."""
    val = (raw_path or "").strip()
    if not val:
        raise HTTPException(status_code=400, detail="Thiếu đường dẫn tệp đầu vào")

    if val.startswith("/media/"):
        parts = val[len("/media/"):].split("/", 1)
        if len(parts) == 2:
            candidate = (PROJECTS_DIR / parts[0] / parts[1]).resolve()
            if _is_within_allowed_roots(candidate) and candidate.exists():
                return candidate

    if val.startswith("/api/studio/music/"):
        fname = val[len("/api/studio/music/"):].strip("/")
        candidate = (MUSIC_DIR / fname).resolve()
        if _is_within(candidate, MUSIC_DIR.resolve()) and candidate.exists():
            return candidate

    candidate = Path(val)
    if not candidate.is_absolute() and project_id:
        candidate = PROJECTS_DIR / project_id / candidate
    candidate = candidate.resolve()

    if not _is_within_allowed_roots(candidate):
        raise HTTPException(status_code=400, detail="Chỉ cho phép truy cập tệp trong projects/ hoặc music_library/")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy tệp: {raw_path}")
    return candidate


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _is_within_allowed_roots(path: Path) -> bool:
    return (
        _is_within(path, PROJECTS_DIR)
        or _is_within(path, MUSIC_DIR)
        or _is_within(path, REPO_ROOT / "remotion-composer" / "public")
    )


def _find_bin(*names: str) -> Optional[str]:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _probe_media(path: Path) -> dict[str, Any]:
    """Run ffprobe to inspect duration, resolution, and streams."""
    info: dict[str, Any] = {
        "duration_seconds": 0.0,
        "width": None,
        "height": None,
        "has_audio": False,
        "has_video": False,
        "size_mb": round(path.stat().st_size / (1024 * 1024), 3) if path.exists() else 0.0,
    }
    ffprobe = _find_bin("ffprobe", "ffprobe.exe")
    if not ffprobe or not path.exists():
        if path.suffix.lower() == ".wav":
            try:
                with wave.open(str(path), "rb") as wf:
                    info["duration_seconds"] = round(wf.getnframes() / float(wf.getframerate()), 3)
                    info["has_audio"] = True
            except Exception:
                pass
        return info

    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        if proc.returncode == 0 and proc.stdout:
            data = json.loads(proc.stdout)
            fmt = data.get("format") or {}
            if fmt.get("duration"):
                info["duration_seconds"] = round(float(fmt["duration"]), 3)
            for stream in data.get("streams") or []:
                ctype = stream.get("codec_type")
                if ctype == "video":
                    info["has_video"] = True
                    if info["width"] is None and stream.get("width"):
                        info["width"] = int(stream["width"])
                        info["height"] = int(stream["height"])
                elif ctype == "audio":
                    info["has_audio"] = True
    except Exception:
        pass
    return info


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Voiceover & caption helpers
# ---------------------------------------------------------------------------

def _voice_dir() -> Path:
    return Path.home() / ".piper" / "models"


def _list_piper_voices() -> list[dict[str, Any]]:
    vdir = _voice_dir()
    voices: list[dict[str, Any]] = []
    if not vdir.exists():
        return voices
    for onnx in sorted(vdir.glob("*.onnx")):
        stem = onnx.stem
        lang = "vi" if stem.startswith("vi_") else ("en" if stem.startswith("en_") else "other")
        label = stem
        if stem == "vi_VN-vais1000-medium":
            label = "Tiếng Việt — Nữ (vais1000 medium)"
        elif stem == "vi_VN-25hours_single-low":
            label = "Tiếng Việt — Nữ Văn Học (25hours)"
        elif stem == "vi_VN-vivos-x_low":
            label = "Tiếng Việt — Đa Giọng Nam/Nữ (65 speakers)"
        elif stem == "en_US-lessac-medium":
            label = "English US — Female (Lessac medium)"
        elif stem == "en_US-ryan-medium":
            label = "English US — Male (Ryan medium)"
        elif stem == "en_US-amy-medium":
            label = "English US — Young Female (Amy medium)"
        voices.append({
            "id": stem,
            "label": label,
            "language": lang,
            "path": str(onnx),
            "size_mb": round(onnx.stat().st_size / (1024 * 1024), 1),
        })
    return voices


def _list_music_tracks() -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    if not MUSIC_DIR.exists():
        return tracks
    for item in sorted(MUSIC_DIR.iterdir()):
        if not item.is_file() or item.suffix.lower() not in MEDIA_AUDIO_EXT:
            continue
        probe = _probe_media(item)
        tracks.append({
            "filename": item.name,
            "name": item.stem.replace("_", " ").title(),
            "path": str(item.resolve()),
            "url": f"/api/studio/music/{item.name}",
            "duration_seconds": probe["duration_seconds"],
            "size_mb": probe["size_mb"],
        })
    return tracks


def _compute_word_captions(
    text: str,
    start_seconds: float,
    end_seconds: float,
    words_per_page: int = 5,
) -> list[dict[str, Any]]:
    """Build Remotion CaptionOverlay WordCaption dicts with millisecond timings."""
    words = [w for w in (text or "").strip().split() if w]
    total_dur = max(0.2, float(end_seconds) - float(start_seconds))
    if not words:
        return []

    weights = [max(2, len(re.sub(r"[^\w]", "", w))) for w in words]
    total_weight = float(sum(weights))
    cursor = float(start_seconds)
    out: list[dict[str, Any]] = []

    for idx, (word, weight) in enumerate(zip(words, weights)):
        slice_dur = total_dur * (weight / total_weight)
        w_start = cursor
        w_end = cursor + slice_dur
        cursor = w_end
        is_last = idx == len(words) - 1
        ends_sentence = word.endswith((".", "!", "?", ";"))
        page_break = is_last or ends_sentence or ((idx + 1) % max(2, words_per_page) == 0)
        entry: dict[str, Any] = {
            "word": word,
            "startMs": int(round(w_start * 1000)),
            "endMs": max(int(round(w_start * 1000)) + 60, int(round(w_end * 1000))),
        }
        if page_break:
            entry["pageBreakAfter"] = True
        out.append(entry)
    return out


def _synthesize_piper_wav(
    text: str,
    output_path: Path,
    model: str = "vi_VN-vais1000-medium",
    speaker_id: int = 0,
    length_scale: float = 1.0,
    sentence_silence: float = 0.25,
) -> tuple[Path, float]:
    from tools.audio.piper_tts import PiperTTS

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tool = PiperTTS()
    result = tool.execute({
        "text": text,
        "model": model,
        "speaker_id": int(speaker_id),
        "length_scale": float(length_scale),
        "sentence_silence": float(sentence_silence),
        "output_path": str(output_path),
    })
    if not result.success:
        raise RuntimeError(result.error or "PiperTTS failed")
    probe = _probe_media(output_path)
    return output_path, float(probe.get("duration_seconds") or 0.0)


def _stitch_narration_wav(
    segments: list[tuple[float, Path]],
    total_duration: float,
    output_path: Path,
) -> Path:
    """Place per-scene WAV files on a single continuous mono 24kHz WAV track."""
    ffmpeg = _find_bin("ffmpeg", "ffmpeg.exe")
    if not ffmpeg:
        raise RuntimeError("Không tìm thấy ffmpeg trên PATH")

    sample_rate = 24000
    channels = 1
    sampwidth = 2
    norm_dir = output_path.parent / "_norm"
    norm_dir.mkdir(parents=True, exist_ok=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame_cursor = 0
    with wave.open(str(output_path), "wb") as out_wf:
        out_wf.setnchannels(channels)
        out_wf.setsampwidth(sampwidth)
        out_wf.setframerate(sample_rate)

        for idx, (start_sec, src_wav) in enumerate(sorted(segments, key=lambda x: x[0])):
            norm_wav = norm_dir / f"seg_{idx}.wav"
            subprocess.run(
                [
                    ffmpeg, "-y", "-i", str(src_wav),
                    "-ac", "1", "-ar", str(sample_rate), "-sample_fmt", "s16",
                    str(norm_wav),
                ],
                capture_output=True,
                check=True,
                timeout=60,
            )
            target_frame = max(frame_cursor, int(round(max(0.0, start_sec) * sample_rate)))
            gap_frames = target_frame - frame_cursor
            if gap_frames > 0:
                out_wf.writeframes(b"\x00" * (gap_frames * channels * sampwidth))
                frame_cursor += gap_frames

            with wave.open(str(norm_wav), "rb") as in_wf:
                frames = in_wf.readframes(in_wf.getnframes())
                out_wf.writeframes(frames)
                frame_cursor += in_wf.getnframes()

        total_frames = int(round(max(total_duration, frame_cursor / sample_rate) * sample_rate))
        tail_frames = total_frames - frame_cursor
        if tail_frames > 0:
            out_wf.writeframes(b"\x00" * (tail_frames * channels * sampwidth))

    shutil.rmtree(norm_dir, ignore_errors=True)
    return output_path


# ---------------------------------------------------------------------------
# Templates & Project State Builders
# ---------------------------------------------------------------------------

def _default_music_path() -> str:
    tracks = _list_music_tracks()
    return tracks[0]["path"] if tracks else ""


def _build_templates() -> list[dict[str, Any]]:
    default_music = _default_music_path()

    vi_ai = {
        "template_id": "vi-ai-studio",
        "title": "Xưởng Phim AI Local Tại Nhà",
        "description": "Mẫu video tiếng Việt đầy đủ: Tiêu đề, Thống kê, So sánh, Biểu đồ cột & Lồng tiếng Piper.",
        "project": {
            "title": "Xưởng Phim AI Local Tại Nhà",
            "theme": "flat-motion-graphics",
            "profile": "youtube_landscape",
            "voice_model": "vi_VN-vais1000-medium",
            "voice_persona": "vi-nu-hoai-my-chuan",
            "voice_speed": 1.0,
            "voice_pitch": 0.0,
            "voice_timbre": "",
            "audio_enhance_preset": "clean_speech",
            "color_grade_profile": "none",
            "color_grade_intensity": 0.75,
            "auto_tts": True,
            "auto_captions": True,
            "auto_fit_timing": True,
            "music_path": default_music,
            "music_volume": 0.14,
            "style_preset_id": "style--flat-motion-graphics",
            "theme_config": None,
            "caption_props": {"fontSize": 54, "highlightColor": "#22D3EE", "wordsPerPage": 5},
            "cuts": [
                {
                    "id": "sc-1",
                    "type": "hero_title",
                    "in_seconds": 0.0,
                    "out_seconds": 4.2,
                    "text": "Xưởng Phim AI Tại Nhà",
                    "subtitle": "Tự động tạo kịch bản, giọng đọc, đồ họa và render video 100% offline",
                    "backgroundColor": "#0F172A",
                    "accentColor": "#22D3EE",
                    "narration": "Chào mừng bạn đến với xưởng phim AI chạy hoàn toàn trên máy tính cá nhân.",
                    "source": "",
                },
                {
                    "id": "sc-2",
                    "type": "stat_card",
                    "in_seconds": 4.2,
                    "out_seconds": 8.4,
                    "stat": "0 ĐỒNG",
                    "subtitle": "Chi phí API cloud mỗi tháng — toàn bộ AI chạy local bằng GPU",
                    "backgroundColor": "#0F172A",
                    "accentColor": "#34D399",
                    "narration": "Với OpenMontage, bạn không tốn chi phí API hàng tháng vì mọi công cụ đều chạy trực tiếp tại nhà.",
                    "source": "",
                },
                {
                    "id": "sc-3",
                    "type": "comparison",
                    "in_seconds": 8.4,
                    "out_seconds": 13.4,
                    "title": "Quy trình sản xuất video",
                    "leftLabel": "Dựng thủ công",
                    "leftValue": "4 - 6 giờ",
                    "rightLabel": "AI Studio Local",
                    "rightValue": "Vài phút",
                    "backgroundColor": "#0F172A",
                    "accentColor": "#A78BFA",
                    "color": "#F8FAFC",
                    "narration": "Thay vì mất hàng giờ cắt ghép thủ công, bạn chỉ cần nhập ý tưởng và bấm render.",
                    "source": "",
                },
                {
                    "id": "sc-4",
                    "type": "bar_chart",
                    "in_seconds": 13.4,
                    "out_seconds": 18.4,
                    "title": "Năng lực hệ thống đã cài đặt",
                    "chartData": [
                        {"label": "Giọng đọc Piper", "value": 95},
                        {"label": "Đồ họa Remotion", "value": 100},
                        {"label": "Whisper & Enhance", "value": 90},
                    ],
                    "chartColors": ["#22D3EE", "#34D399", "#F59E0B"],
                    "showGrid": True,
                    "showValues": True,
                    "backgroundColor": "#0F172A",
                    "narration": "Hệ thống tích hợp sẵn giọng đọc tiếng Việt, biểu đồ động, nâng nét ảnh và xuất file MP4 sắc nét.",
                    "source": "",
                },
                {
                    "id": "sc-5",
                    "type": "callout",
                    "in_seconds": 18.4,
                    "out_seconds": 22.8,
                    "title": "Sẵn sàng sáng tạo",
                    "text": "Chỉnh sửa từng cảnh ngay bên trái, thêm ảnh hoặc clip rồi nhấn Render Video!",
                    "callout_type": "tip",
                    "accentColor": "#22D3EE",
                    "backgroundColor": "#0F172A",
                    "color": "#F8FAFC",
                    "narration": "Hãy tùy chỉnh kịch bản theo ý bạn và tạo video đầu tiên ngay hôm nay.",
                    "source": "",
                },
            ],
            "overlays": [],
        },
    }

    tech_vi = {
        "template_id": "tech-code-explain",
        "title": "Giải thích Công nghệ & Code",
        "description": "Phong cách Clean Professional phù hợp làm video lập trình, kiến trúc hệ thống, review sản phẩm.",
        "project": {
            "title": "Kiến Trúc Agent Tự Động Hóa",
            "theme": "clean-professional",
            "profile": "youtube_landscape",
            "voice_model": "vi_VN-vais1000-medium",
            "voice_speed": 1.0,
            "auto_tts": True,
            "auto_captions": True,
            "auto_fit_timing": True,
            "music_path": default_music,
            "music_volume": 0.12,
            "cuts": [
                {
                    "id": "t-1",
                    "type": "hero_title",
                    "in_seconds": 0.0,
                    "out_seconds": 4.0,
                    "text": "Từ Ý Tưởng Đến Video",
                    "subtitle": "Pipeline tự động 4 bước dành cho nhà phát triển",
                    "backgroundColor": "#111827",
                    "accentColor": "#38BDF8",
                    "narration": "Đây là cách một pipeline AI biến ghi chú kỹ thuật thành video giải thích chuyên nghiệp.",
                    "source": "",
                },
                {
                    "id": "t-2",
                    "type": "progress_bar",
                    "in_seconds": 4.0,
                    "out_seconds": 8.5,
                    "title": "Mức độ tự động hóa quy trình",
                    "progress": 88,
                    "progressLabel": "88% công đoạn lặp lại được tự động hóa",
                    "progressColor": "#10B981",
                    "backgroundColor": "#111827",
                    "narration": "Tám mươi tám phần trăm công đoạn dựng slide, căn chữ và phụ đề được xử lý tự động.",
                    "source": "",
                },
                {
                    "id": "t-3",
                    "type": "kpi_grid",
                    "in_seconds": 8.5,
                    "out_seconds": 13.5,
                    "title": "Thông số hiệu năng local",
                    "chartData": [
                        {"label": "Độ phân giải", "value": 1080, "suffix": "p"},
                        {"label": "Tốc độ khung hình", "value": 30, "suffix": " fps"},
                        {"label": "VRAM GPU", "value": 8, "suffix": " GB"},
                    ],
                    "columns": 3,
                    "chartColors": ["#38BDF8", "#10B981", "#F59E0B"],
                    "backgroundColor": "#111827",
                    "narration": "Video xuất ra ở chuẩn Full HD ba mươi khung hình trên giây ngay trên RTX 2060 Super.",
                    "source": "",
                },
                {
                    "id": "t-4",
                    "type": "text_card",
                    "in_seconds": 13.5,
                    "out_seconds": 17.5,
                    "text": "Chèn Code Card & Sơ đồ Mermaid ở tab AI Workbench!",
                    "subtitle": "Hỗ trợ Pygments highlight & Mermaid CLI tốc độ cao",
                    "backgroundColor": "#111827",
                    "color": "#F8FAFC",
                    "narration": "Bạn có thể tạo ảnh chụp code đẹp mắt và sơ đồ hệ thống ngay trong tab công cụ AI.",
                    "source": "",
                },
            ],
            "overlays": [],
        },
    }

    shorts_vi = {
        "template_id": "shorts-vertical",
        "title": "Video Ngắn Dọc 9:16 (Shorts / TikTok)",
        "description": "Khung hình dọc 1080x1920 tối ưu cho YouTube Shorts, TikTok và Facebook Reels.",
        "project": {
            "title": "3 Mẹo Tối Ưu AI Tại Nhà",
            "theme": "anime-ghibli",
            "profile": "youtube_shorts",
            "voice_model": "vi_VN-vais1000-medium",
            "voice_speed": 1.0,
            "auto_tts": True,
            "auto_captions": True,
            "auto_fit_timing": True,
            "music_path": default_music,
            "music_volume": 0.16,
            "cuts": [
                {
                    "id": "s-1",
                    "type": "hero_title",
                    "in_seconds": 0.0,
                    "out_seconds": 3.5,
                    "text": "Tạo Video AI Miễn Phí",
                    "subtitle": "Chạy 100% trên máy cá nhân",
                    "backgroundColor": "#1E1B4B",
                    "accentColor": "#F59E0B",
                    "narration": "Bạn hoàn toàn có thể tự tạo video AI chất lượng cao mà không cần trả phí hàng tháng.",
                    "source": "",
                },
                {
                    "id": "s-2",
                    "type": "pie_chart",
                    "in_seconds": 3.5,
                    "out_seconds": 8.0,
                    "title": "Thời gian tiết kiệm được",
                    "chartData": [
                        {"label": "AI tự dựng", "value": 78},
                        {"label": "Bạn tinh chỉnh", "value": 22},
                    ],
                    "chartColors": ["#34D399", "#60A5FA"],
                    "donut": True,
                    "centerLabel": "Tự động",
                    "centerValue": "78%",
                    "showLegend": True,
                    "backgroundColor": "#1E1B4B",
                    "narration": "Trí tuệ nhân tạo đảm nhận phần lớn khâu đọc thoại, ghép cảnh và phụ đề.",
                    "source": "",
                },
                {
                    "id": "s-3",
                    "type": "callout",
                    "in_seconds": 8.0,
                    "out_seconds": 11.5,
                    "title": "Bắt đầu ngay",
                    "text": "Chọn khung hình dọc 9:16 và bấm Render Video!",
                    "callout_type": "tip",
                    "accentColor": "#F59E0B",
                    "backgroundColor": "#1E1B4B",
                    "color": "#F8FAFC",
                    "narration": "Thử thay đổi nội dung ngay bây giờ và xem thành quả chỉ sau ít giây.",
                    "source": "",
                },
            ],
            "overlays": [],
        },
    }

    en_demo_data = _read_json(DEMO_PROPS_DIR / "world-in-numbers.json") or {}
    en_cuts = []
    for idx, c in enumerate(en_demo_data.get("cuts") or []):
        cut_copy = dict(c)
        cut_copy.setdefault("id", f"wn-{idx + 1}")
        cut_copy.setdefault("narration", cut_copy.get("subtitle") or cut_copy.get("text") or "")
        en_cuts.append(cut_copy)
    world_en = {
        "template_id": "world-in-numbers",
        "title": "World in Numbers (English Showcase)",
        "description": "Mẫu demo chuẩn quốc tế dùng giọng đọc tiếng Anh Lessac với đồ họa thống kê đa dạng.",
        "project": {
            "title": "The World in Numbers",
            "theme": en_demo_data.get("theme", "clean-professional"),
            "profile": "youtube_landscape",
            "voice_model": "en_US-lessac-medium",
            "voice_speed": 1.0,
            "auto_tts": True,
            "auto_captions": True,
            "auto_fit_timing": True,
            "music_path": default_music,
            "music_volume": 0.14,
            "cuts": en_cuts,
            "overlays": en_demo_data.get("overlays") or [],
        },
    }

    return [vi_ai, tech_vi, shorts_vi, world_en]


def _scan_project_files(pdir: Path, project_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assets: list[dict[str, Any]] = []
    renders: list[dict[str, Any]] = []

    render_dir = pdir / "renders"
    if render_dir.exists():
        for mp4 in sorted(render_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
            st = mp4.stat()
            renders.append({
                "filename": mp4.name,
                "path": str(mp4.resolve()),
                "url": _media_url(project_id, mp4),
                "size_mb": round(st.st_size / (1024 * 1024), 2),
                "mtime": st.st_mtime,
            })

    for folder_name in ("assets", "uploads", "audio", "transcripts"):
        sub = pdir / folder_name
        if not sub.exists():
            continue
        for item in sorted(sub.rglob("*"), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True):
            if not item.is_file() or "_norm" in item.parts:
                continue
            ext = item.suffix.lower()
            kind = (
                "image" if ext in MEDIA_IMAGE_EXT
                else "video" if ext in MEDIA_VIDEO_EXT
                else "audio" if ext in MEDIA_AUDIO_EXT
                else "other"
            )
            if kind == "other":
                continue
            st = item.stat()
            assets.append({
                "filename": item.name,
                "folder": folder_name,
                "kind": kind,
                "path": str(item.resolve()),
                "url": _media_url(project_id, item),
                "size_mb": round(st.st_size / (1024 * 1024), 3),
                "mtime": st.st_mtime,
            })

    return assets, renders


def _load_or_build_studio_project(project_id: str) -> dict[str, Any]:
    pdir = _project_dir(project_id)
    if not pdir.exists():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy project {project_id!r}")

    saved = _read_json(pdir / STUDIO_PROJECT_FILE)
    marker = _read_json(pdir / PROJECT_MARKER_FILE) or {}
    if saved and isinstance(saved.get("cuts"), list):
        saved["project_id"] = project_id
        saved.setdefault("voice_persona", "vi-nu-hoai-my-chuan")
        saved.setdefault("voice_pitch", 0.0)
        saved.setdefault("voice_timbre", "")
        saved.setdefault("audio_enhance_preset", "clean_speech")
        saved.setdefault("color_grade_profile", "none")
        saved.setdefault("color_grade_intensity", 0.75)
        saved.setdefault("style_preset_id", "")
        saved.setdefault("theme_config", None)
        saved.setdefault("caption_props", {"fontSize": 54, "highlightColor": "#22D3EE", "wordsPerPage": 5})
        return saved

    default_tpl = _build_templates()[0]["project"]
    return {
        "project_id": project_id,
        "title": marker.get("title") or marker.get("name") or project_id,
        "theme": default_tpl["theme"],
        "profile": "youtube_landscape",
        "voice_model": default_tpl["voice_model"],
        "voice_persona": default_tpl.get("voice_persona", "vi-nu-hoai-my-chuan"),
        "voice_speed": 1.0,
        "voice_pitch": 0.0,
        "voice_timbre": "",
        "audio_enhance_preset": "clean_speech",
        "color_grade_profile": "none",
        "color_grade_intensity": 0.75,
        "style_preset_id": "",
        "theme_config": None,
        "caption_props": {"fontSize": 54, "highlightColor": "#22D3EE", "wordsPerPage": 5},
        "auto_tts": True,
        "auto_captions": True,
        "auto_fit_timing": True,
        "music_path": default_music_path_safe(),
        "music_volume": 0.14,
        "cuts": [],
        "overlays": [],
    }


def default_music_path_safe() -> str:
    try:
        return _default_music_path()
    except Exception:
        return ""


def _save_studio_project(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    pdir = _project_dir(project_id)
    _ensure_project_dirs(pdir)
    now = _now_iso()

    existing_marker = _read_json(pdir / PROJECT_MARKER_FILE) or {}
    title = str(payload.get("title") or existing_marker.get("title") or project_id)
    marker = {
        "project_id": project_id,
        "name": title,
        "title": title,
        "pipeline_type": existing_marker.get("pipeline_type") or "explainer",
        "created_at": existing_marker.get("created_at") or now,
        "updated_at": now,
    }
    _write_json(pdir / PROJECT_MARKER_FILE, marker)

    clean_project = {
        "project_id": project_id,
        "title": title,
        "theme": payload.get("theme") or "flat-motion-graphics",
        "profile": payload.get("profile") or "youtube_landscape",
        "voice_model": payload.get("voice_model") or "vi_VN-vais1000-medium",
        "voice_persona": str(payload.get("voice_persona") or "vi-nu-hoai-my-chuan"),
        "voice_speed": float(payload.get("voice_speed") or 1.0),
        "voice_pitch": float(payload.get("voice_pitch") or 0.0),
        "voice_timbre": str(payload.get("voice_timbre") or ""),
        "audio_enhance_preset": str(payload.get("audio_enhance_preset") or "clean_speech"),
        "color_grade_profile": str(payload.get("color_grade_profile") or "none"),
        "color_grade_intensity": float(payload.get("color_grade_intensity", 0.75)),
        "style_preset_id": str(payload.get("style_preset_id") or ""),
        "theme_config": payload.get("theme_config") if isinstance(payload.get("theme_config"), dict) else None,
        "caption_props": payload.get("caption_props") if isinstance(payload.get("caption_props"), dict) else None,
        "auto_tts": bool(payload.get("auto_tts", True)),
        "auto_captions": bool(payload.get("auto_captions", True)),
        "auto_fit_timing": bool(payload.get("auto_fit_timing", True)),
        "music_path": str(payload.get("music_path") or ""),
        "music_volume": float(payload.get("music_volume", 0.14)),
        "cuts": list(payload.get("cuts") or []),
        "overlays": list(payload.get("overlays") or []),
        "updated_at": now,
    }
    _write_json(pdir / STUDIO_PROJECT_FILE, clean_project)
    return clean_project


# ---------------------------------------------------------------------------
# Cut normalization & Remotion composition builder
# ---------------------------------------------------------------------------

def _normalize_cut_for_remotion(
    raw_cut: dict[str, Any],
    idx: int,
    project_id: str,
    job_logs: list[str],
) -> dict[str, Any]:
    cut = json.loads(json.dumps(raw_cut))
    cut_type = str(cut.get("type") or "text_card").strip()
    cut["id"] = str(cut.get("id") or f"scene-{idx + 1}")
    cut["in_seconds"] = round(float(cut.get("in_seconds", 0.0)), 3)
    cut["out_seconds"] = round(max(cut["in_seconds"] + 0.5, float(cut.get("out_seconds", cut["in_seconds"] + 4.0))), 3)

    # Resolve any media URL back to local filesystem path for VideoCompose staging.
    for path_key in ("source", "backgroundImage", "backgroundVideo"):
        val = cut.get(path_key)
        if isinstance(val, str) and val.strip():
            if val.startswith("/media/") or (not Path(val).is_absolute() and "/" in val):
                try:
                    cut[path_key] = str(_resolve_studio_input_path(val, project_id))
                except HTTPException:
                    cut[path_key] = ""

    if cut_type in ("image", "video"):
        src = str(cut.get("source") or "").strip()
        if not src or not Path(src).exists():
            job_logs.append(f"⚠️ Cảnh #{idx + 1} ({cut_type}) chưa có tệp hợp lệ, tự chuyển về text_card.")
            cut["type"] = "text_card"
            cut["source"] = ""
            cut.setdefault("text", cut.get("title") or f"Cảnh {idx + 1}")

    elif cut_type == "comparison":
        cut["leftLabel"] = str(cut.get("leftLabel") or "Trước")
        cut["leftValue"] = str(cut.get("leftValue") or "Thủ công")
        cut["rightLabel"] = str(cut.get("rightLabel") or "Sau")
        cut["rightValue"] = str(cut.get("rightValue") or "AI Studio")

    elif cut_type == "callout":
        cut["callout_type"] = str(cut.get("callout_type") or "tip")
        cut["text"] = str(cut.get("text") or "Điểm nhấn quan trọng")

    elif cut_type == "stat_card":
        cut["stat"] = str(cut.get("stat") or "100%")

    elif cut_type in ("bar_chart", "pie_chart", "kpi_grid"):
        if not isinstance(cut.get("chartData"), list) or not cut["chartData"]:
            cut["chartData"] = [{"label": "A", "value": 60}, {"label": "B", "value": 90}]

    elif cut_type == "progress_bar":
        try:
            cut["progress"] = float(cut.get("progress", 75))
        except (TypeError, ValueError):
            cut["progress"] = 75.0

    # Strip Studio-only narration/voice fields before passing clean props.
    cut.pop("narration", None)
    cut.pop("voice_persona", None)
    return cut


def _set_job(job_id: str, **updates: Any) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(updates)
            JOBS[job_id]["updated_at"] = _now_iso()


def _append_job_log(job_id: str, message: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            stamp = datetime.now().strftime("%H:%M:%S")
            job["logs"].append(f"[{stamp}] {message}")
            job["updated_at"] = _now_iso()


def _run_render_job(
    job_id: str,
    project_id: str,
    project_data: dict[str, Any],
    render_options: dict[str, Any],
) -> None:
    logs: list[str] = []
    try:
        from tools.video.video_compose import VideoCompose

        _set_job(job_id, status="running", progress=5)
        _append_job_log(job_id, f"Khởi chạy render cho project '{project_id}'...")

        pdir = _project_dir(project_id)
        _ensure_project_dirs(pdir)
        saved_project = _save_studio_project(project_id, project_data)

        raw_cuts = list(saved_project.get("cuts") or [])
        if not raw_cuts:
            raise ValueError("Timeline chưa có cảnh nào để render.")

        auto_tts = bool(render_options.get("auto_tts", saved_project.get("auto_tts", True)))
        auto_captions = bool(render_options.get("auto_captions", saved_project.get("auto_captions", True)))
        auto_fit = bool(render_options.get("auto_fit_timing", saved_project.get("auto_fit_timing", True)))
        voice_model = str(saved_project.get("voice_model") or "vi_VN-vais1000-medium")
        project_persona = str(saved_project.get("voice_persona") or "vi-nu-hoai-my-chuan")
        voice_speed = float(saved_project.get("voice_speed") or 1.0)
        voice_pitch = float(saved_project.get("voice_pitch") or 0.0)
        voice_timbre = str(saved_project.get("voice_timbre") or "").strip()
        audio_enhance_preset = str(saved_project.get("audio_enhance_preset") or "clean_speech").strip()
        color_grade_profile = str(saved_project.get("color_grade_profile") or "none").strip()
        color_grade_intensity = float(saved_project.get("color_grade_intensity", 0.75))
        caption_props = saved_project.get("caption_props") or {}
        words_per_page = int((caption_props or {}).get("wordsPerPage") or 5)
        profile = str(saved_project.get("profile") or "youtube_landscape")
        theme = str(saved_project.get("theme") or "flat-motion-graphics")

        # Validate profile early
        profile_obj = get_profile(profile)
        _append_job_log(
            job_id,
            f"Cấu hình: {profile} ({profile_obj.width}x{profile_obj.height}), theme={theme}, voice={project_persona}",
        )

        # 1) Synthesize voiceovers per scene & compute aligned timings
        _set_job(job_id, progress=15)
        timeline_cursor = 0.0
        aligned_cuts: list[dict[str, Any]] = []
        tts_segments: list[tuple[float, Path]] = []
        all_captions: list[dict[str, Any]] = []

        for idx, raw_cut in enumerate(raw_cuts):
            orig_in = float(raw_cut.get("in_seconds", timeline_cursor))
            orig_out = float(raw_cut.get("out_seconds", orig_in + 4.0))
            base_dur = max(1.5, orig_out - orig_in)

            scene_start = timeline_cursor if auto_fit else max(timeline_cursor, orig_in)
            narration = str(raw_cut.get("narration") or "").strip()
            scene_persona = str(raw_cut.get("voice_persona") or "").strip() or project_persona
            is_project_default_voice = scene_persona == project_persona
            wav_dur = 0.0

            if auto_tts and narration:
                sig_pitch = voice_pitch if is_project_default_voice else "p"
                sig_timbre = voice_timbre if is_project_default_voice else "p"
                sig_spd = voice_speed if is_project_default_voice else "p"
                digest = hashlib.sha1(
                    f"{scene_persona}|{voice_model}|{sig_spd}|{sig_pitch}|{sig_timbre}|{narration}".encode("utf-8")
                ).hexdigest()[:12]
                wav_path = pdir / "audio" / f"scene_{idx + 1:02d}_{digest}.wav"
                if not wav_path.exists():
                    _append_job_log(job_id, f"Tạo giọng đọc cảnh {idx + 1}/{len(raw_cuts)} ({scene_persona})...")
                    synthesize_studio_voice(
                        text=narration,
                        output_wav=wav_path,
                        persona_id=scene_persona if scene_persona in VOICE_PERSONAS_BY_ID else None,
                        piper_model=None if scene_persona in VOICE_PERSONAS_BY_ID else voice_model,
                        pitch_semitones=voice_pitch if (is_project_default_voice and abs(voice_pitch) > 0.05) else None,
                        speed=voice_speed if (is_project_default_voice and abs(voice_speed - 1.0) > 0.02) else None,
                        timbre=voice_timbre if (is_project_default_voice and voice_timbre) else None,
                    )
                wav_dur = float(_probe_media(wav_path).get("duration_seconds") or 0.0)

                if auto_fit and wav_dur > 0 and wav_dur + 0.65 > base_dur:
                    base_dur = round(wav_dur + 0.85, 2)

                tts_start = scene_start + 0.15
                tts_segments.append((tts_start, wav_path))
                if auto_captions and wav_dur > 0:
                    all_captions.extend(
                        _compute_word_captions(narration, tts_start, tts_start + wav_dur, words_per_page=words_per_page)
                    )
            elif auto_captions and narration:
                all_captions.extend(
                    _compute_word_captions(narration, scene_start + 0.15, scene_start + base_dur - 0.15, words_per_page=words_per_page)
                )

            scene_end = round(scene_start + base_dur, 3)
            cut_copy = dict(raw_cut)
            cut_copy["in_seconds"] = round(scene_start, 3)
            cut_copy["out_seconds"] = scene_end
            aligned_cuts.append(cut_copy)
            timeline_cursor = scene_end

        total_duration = timeline_cursor

        # 2) Build master narration audio if TTS segments exist
        _set_job(job_id, progress=35)
        audio_cfg: dict[str, Any] = {}
        if tts_segments:
            master_wav = pdir / "audio" / f"narration_{job_id}.wav"
            _stitch_narration_wav(tts_segments, total_duration, master_wav)
            if audio_enhance_preset and audio_enhance_preset != "none":
                try:
                    from tools.audio.audio_enhance import AudioEnhance

                    enh_wav = pdir / "audio" / f"narration_{job_id}_enh.wav"
                    enh_res = AudioEnhance().execute({
                        "input_path": str(master_wav),
                        "output_path": str(enh_wav),
                        "preset": audio_enhance_preset,
                        "audio_codec": "pcm_s16le",
                    })
                    if enh_res.success and enh_wav.exists():
                        master_wav = enh_wav
                        _append_job_log(job_id, f"🎚️ Đã áp dụng lọc âm ({audio_enhance_preset}).")
                except Exception as enh_exc:
                    _append_job_log(job_id, f"⚠️ Bỏ qua AudioEnhance: {enh_exc}")
            audio_cfg["narration"] = {"src": str(master_wav.resolve()), "volume": 1.0}
            _append_job_log(job_id, f"Đã ghép file lồng tiếng tổng ({total_duration:.1f}s).")

        music_path_raw = str(saved_project.get("music_path") or "").strip()
        if music_path_raw:
            try:
                music_file = _resolve_studio_input_path(music_path_raw, project_id)
                audio_cfg["music"] = {
                    "src": str(music_file),
                    "volume": float(saved_project.get("music_volume", 0.14)),
                    "fadeInSeconds": 1.5,
                    "fadeOutSeconds": 2.5,
                    "loop": True,
                }
            except HTTPException:
                _append_job_log(job_id, "⚠️ Không tìm thấy tệp nhạc nền, bỏ qua nhạc nền.")

        # 3) Normalize cuts for Explainer composition
        norm_cuts = [
            _normalize_cut_for_remotion(c, idx, project_id, logs)
            for idx, c in enumerate(aligned_cuts)
        ]
        for msg in logs:
            _append_job_log(job_id, msg)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_name = str(render_options.get("output_filename") or f"{project_id}_{stamp}.mp4")
        if not out_name.lower().endswith(".mp4"):
            out_name += ".mp4"
        out_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", out_name)
        output_path = pdir / "renders" / out_name

        edit_decisions: dict[str, Any] = {
            "renderer_family": "explainer-data",
            "theme": theme,
            "cuts": norm_cuts,
            "overlays": list(saved_project.get("overlays") or []),
            "captions": all_captions if auto_captions else [],
            "audio": audio_cfg,
            "wordsPerPage": words_per_page,
        }
        if isinstance(saved_project.get("theme_config"), dict) and saved_project["theme_config"]:
            edit_decisions["themeConfig"] = saved_project["theme_config"]
        if isinstance(caption_props, dict) and caption_props:
            clean_cprops = {k: v for k, v in caption_props.items() if k != "wordsPerPage" and v not in (None, "")}
            if clean_cprops:
                edit_decisions["subtitles"] = {"enabled": True, "caption_props": clean_cprops}

        _set_job(job_id, progress=45)
        _append_job_log(job_id, "Đang chạy Remotion Composer + FFmpeg để kết xuất MP4...")

        compose_inputs: dict[str, Any] = {
            "operation": "remotion_render",
            "output_path": str(output_path),
            "profile": profile,
            "edit_decisions": edit_decisions,
            "remotion_timeout_ms": 600000,
        }
        result = VideoCompose().execute(compose_inputs)
        if not result.success:
            raise RuntimeError(result.error or "VideoCompose remotion_render thất bại")

        if not output_path.exists():
            raise RuntimeError("Render xong nhưng không thấy tệp MP4 đầu ra.")

        if color_grade_profile and color_grade_profile != "none":
            try:
                from tools.enhancement.color_grade import ColorGrade

                _set_job(job_id, progress=90)
                _append_job_log(job_id, f"🎨 Đang áp dụng bộ lọc màu {color_grade_profile}...")
                graded_tmp = pdir / "renders" / f"_graded_{out_name}"
                cg_res = ColorGrade().execute({
                    "input_path": str(output_path),
                    "output_path": str(graded_tmp),
                    "profile": color_grade_profile,
                    "intensity": color_grade_intensity,
                })
                if cg_res.success and graded_tmp.exists():
                    graded_tmp.replace(output_path)
                else:
                    _append_job_log(job_id, f"⚠️ Bỏ qua bộ lọc màu: {cg_res.error}")
            except Exception as cg_exc:
                _append_job_log(job_id, f"⚠️ Bỏ qua bộ lọc màu: {cg_exc}")

        probe = _probe_media(output_path)
        media_url = _media_url(project_id, output_path)
        _append_job_log(
            job_id,
            f"✅ Hoàn tất! {output_path.name} ({probe['duration_seconds']}s, "
            f"{probe['width']}x{probe['height']}, {probe['size_mb']} MB)",
        )
        _set_job(
            job_id,
            status="completed",
            progress=100,
            result={
                "filename": output_path.name,
                "path": str(output_path.resolve()),
                "url": media_url,
                "probe": probe,
                "aligned_cuts": aligned_cuts,
                "captions_count": len(all_captions) if auto_captions else 0,
            },
        )
    except Exception as exc:
        _append_job_log(job_id, f"❌ Lỗi render: {exc}")
        _set_job(job_id, status="failed", error=str(exc))


def _run_local_diffusion_job(job_id: str, project_id: str, payload: dict[str, Any]) -> None:
    try:
        from tools.graphics.local_diffusion import LocalDiffusion

        _set_job(job_id, status="running", progress=15)
        _append_job_log(job_id, "Đang tạo ảnh AI bằng Stable Diffusion local (RTX GPU)...")

        pdir = _project_dir(project_id)
        _ensure_project_dirs(pdir)
        out_path = pdir / "assets" / f"ai_art_{int(time.time())}.png"

        tool_inputs: dict[str, Any] = {
            "prompt": str(payload.get("prompt") or "").strip(),
            "negative_prompt": str(payload.get("negative_prompt") or "blurry, low quality, watermark, text"),
            "width": int(payload.get("width") or 768),
            "height": int(payload.get("height") or 512),
            "num_inference_steps": int(payload.get("steps") or 20),
            "guidance_scale": float(payload.get("guidance_scale") or 7.5),
            "output_path": str(out_path),
        }
        if payload.get("seed") not in (None, ""):
            tool_inputs["seed"] = int(payload["seed"])

        result = LocalDiffusion().execute(tool_inputs)
        if not result.success or not out_path.exists():
            raise RuntimeError(result.error or "LocalDiffusion thất bại")

        url = _media_url(project_id, out_path)
        _append_job_log(job_id, f"✅ Đã tạo ảnh AI: {out_path.name}")
        _set_job(
            job_id,
            status="completed",
            progress=100,
            result={
                "kind": "image",
                "filename": out_path.name,
                "path": str(out_path.resolve()),
                "url": url,
            },
        )
    except Exception as exc:
        _append_job_log(job_id, f"❌ Lỗi tạo ảnh AI: {exc}")
        _set_job(job_id, status="failed", error=str(exc))


# ---------------------------------------------------------------------------
# FastAPI App Factory (extends Backlot cleanly)
# ---------------------------------------------------------------------------

def create_studio_app() -> FastAPI:
    app = create_backlot_app()

    @app.middleware("http")
    async def studio_ui_no_cache(request: Request, call_next):
        response = await call_next(request)
        p = request.url.path
        if p == "/studio" or p.startswith("/studio-ui/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    if UI_DIR.exists():
        app.mount("/studio-ui", StaticFiles(directory=str(UI_DIR)), name="studio-ui")

    @app.get("/studio")
    def studio_index():
        idx = UI_DIR / "index.html"
        if not idx.exists():
            raise HTTPException(status_code=404, detail="Không tìm thấy studio/ui/index.html")
        return FileResponse(idx, headers={"Cache-Control": "no-cache"})

    @app.get("/api/studio/music/{filename}")
    def serve_music_track(filename: str):
        safe = Path(filename).name
        target = (MUSIC_DIR / safe).resolve()
        if not _is_within(target, MUSIC_DIR.resolve()) or not target.exists():
            raise HTTPException(status_code=404, detail="Không tìm thấy nhạc nền")
        return FileResponse(target)

    @app.get("/api/studio/status")
    def studio_status():
        gpu_info: dict[str, Any] = {"cuda": False, "device_name": "CPU", "vram_total_gb": 0.0}
        try:
            import torch

            if torch.cuda.is_available():
                props = torch.cuda.get_device_properties(0)
                gpu_info = {
                    "cuda": True,
                    "device_name": props.name,
                    "vram_total_gb": round(props.total_memory / (1024**3), 2),
                }
        except Exception:
            pass

        disk_usage = shutil.disk_usage(REPO_ROOT)
        disk_info = {
            "free_gb": round(disk_usage.free / (1024**3), 1),
            "total_gb": round(disk_usage.total / (1024**3), 1),
        }

        if not _CACHED_TOOL_STATUS:
            registry.ensure_discovered()
            menu_summary = registry.provider_menu_summary()
            watched_names = [
                "piper_tts",
                "code_snippet",
                "diagram_gen",
                "direct_clip_search",
                "local_diffusion",
                "bg_remove",
                "upscale",
                "face_restore",
                "transcriber",
                "video_compose",
                "hyperframes_compose",
            ]
            tool_flags: dict[str, bool] = {}
            for name in watched_names:
                t = registry.get(name)
                tool_flags[name] = bool(t.get_status() == ToolStatus.AVAILABLE) if t is not None else False
            _CACHED_TOOL_STATUS["tools"] = tool_flags
            _CACHED_TOOL_STATUS["composition_runtimes"] = menu_summary.get("composition_runtimes", {})
            _CACHED_TOOL_STATUS["runtime_warnings"] = menu_summary.get("runtime_warnings", [])

        tool_flags = _CACHED_TOOL_STATUS["tools"]
        menu_summary = {
            "composition_runtimes": _CACHED_TOOL_STATUS["composition_runtimes"],
            "runtime_warnings": _CACHED_TOOL_STATUS["runtime_warnings"],
        }

        profiles_out = []
        for pname in (
            "youtube_landscape",
            "youtube_shorts",
            "instagram_feed",
            "tiktok",
            "instagram_reels",
            "youtube_4k",
            "linkedin",
            "cinematic",
        ):
            if pname in ALL_PROFILES:
                p = get_profile(pname)
                profiles_out.append({
                    "id": pname,
                    "width": p.width,
                    "height": p.height,
                    "label": f"{pname} ({p.width}x{p.height})",
                })

        return {
            "ok": True,
            "gpu": gpu_info,
            "disk": disk_info,
            "voices": _list_piper_voices(),
            "music_tracks": _list_music_tracks(),
            "themes": REMO_THEMES,
            "profiles": profiles_out,
            "scene_types": SCENE_TYPE_META,
            "tools": tool_flags,
            "composition_runtimes": menu_summary.get("composition_runtimes", {}),
            "runtime_warnings": menu_summary.get("runtime_warnings", []),
        }

    @app.get("/api/studio/catalog")
    def studio_catalog(category: str = "all", q: str = "", page: int = 1, page_size: int = 48):
        return query_catalog(category=category, q=q, page=page, page_size=page_size)

    @app.get("/api/studio/catalog/{item_id}")
    def studio_catalog_detail(item_id: str):
        item = get_catalog_item(item_id)
        if not item:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy mẫu {item_id!r}")
        return {"ok": True, "item": item, "content": get_catalog_item_source(item)}

    @app.get("/api/studio/catalog/{item_id}/thumb.svg")
    def studio_catalog_thumb(item_id: str):
        item = get_catalog_item(item_id)
        if not item:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy thumbnail cho {item_id!r}")
        svg = render_catalog_thumb_svg(item)
        return Response(
            content=svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/api/studio/voices")
    def studio_voices():
        return {
            "ok": True,
            "personas": VOICE_PERSONAS,
            "genders": VOICE_GENDERS,
            "age_groups": VOICE_AGE_GROUPS,
            "tones": VOICE_TONES,
            "timbre_presets": [{"id": k, "label": v["label"]} for k, v in TIMBRE_PRESETS.items()],
            "audio_enhance_presets": list_audio_enhance_options(),
            "color_grade_profiles": list_color_grade_options(),
            "raw_piper_voices": _list_piper_voices(),
        }

    @app.get("/api/studio/fonts")
    def studio_fonts():
        return {
            "ok": True,
            "fonts": list_studio_fonts(),
            "categories": FONT_CATEGORIES,
            "pairings": FONT_PAIRINGS,
        }

    @app.post("/api/studio/fonts/upload")
    async def studio_font_upload(file: UploadFile = File(...)):
        data = await file.read()
        if not data or len(data) > 25 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File font không hợp lệ hoặc vượt quá 25 MB.")
        try:
            saved = save_custom_font_file(file.filename or "custom_font.ttf", data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "font": saved, "fonts": list_studio_fonts()}

    @app.get("/api/studio/fonts/file/{filename}")
    def studio_font_file(filename: str):
        safe_name = Path(filename).name
        target = (CUSTOM_FONTS_DIR / safe_name).resolve()
        base_dir = CUSTOM_FONTS_DIR.resolve()
        if not str(target).startswith(str(base_dir)) or not target.is_file():
            raise HTTPException(status_code=404, detail="Không tìm thấy tệp font.")
        mime_map = {
            ".ttf": "font/ttf",
            ".otf": "font/otf",
            ".woff": "font/woff",
            ".woff2": "font/woff2",
        }
        return FileResponse(
            target,
            media_type=mime_map.get(target.suffix.lower(), "application/octet-stream"),
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=86400"},
        )

    @app.get("/api/studio/templates")
    def studio_templates():
        return {"ok": True, "templates": _build_templates()}

    @app.get("/api/studio/projects")
    def list_studio_projects():
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        projects: list[dict[str, Any]] = []
        for entry in sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not entry.is_dir():
                continue
            pid = entry.name
            saved = _read_json(entry / STUDIO_PROJECT_FILE)
            marker = _read_json(entry / PROJECT_MARKER_FILE) or {}
            _, renders = _scan_project_files(entry, pid)
            projects.append({
                "project_id": pid,
                "title": (saved or {}).get("title") or marker.get("title") or marker.get("name") or pid,
                "theme": (saved or {}).get("theme") or "flat-motion-graphics",
                "profile": (saved or {}).get("profile") or "youtube_landscape",
                "scene_count": len((saved or {}).get("cuts") or []),
                "render_count": len(renders),
                "latest_render": renders[0] if renders else None,
                "updated_at": (saved or {}).get("updated_at") or marker.get("updated_at"),
            })
        return {"ok": True, "projects": projects}

    @app.get("/api/studio/projects/{project_id}")
    def get_studio_project(project_id: str):
        pdir = _project_dir(project_id)
        project = _load_or_build_studio_project(project_id)
        assets, renders = _scan_project_files(pdir, project_id)
        return {"ok": True, "project": project, "assets": assets, "renders": renders}

    @app.post("/api/studio/projects")
    def save_or_create_project(payload: dict[str, Any] = Body(...)):
        raw_id = str(payload.get("project_id") or "").strip()
        title = str(payload.get("title") or "").strip()
        project_id = _sanitize_project_id(raw_id or title or f"studio-{int(time.time())}")

        if payload.get("template_id"):
            templates = {t["template_id"]: t["project"] for t in _build_templates()}
            tpl = templates.get(str(payload["template_id"]))
            if tpl:
                merged = dict(tpl)
                if title:
                    merged["title"] = title
                payload = merged

        saved = _save_studio_project(project_id, payload)
        assets, renders = _scan_project_files(_project_dir(project_id), project_id)
        return {"ok": True, "project": saved, "assets": assets, "renders": renders}

    @app.post("/api/studio/tts")
    async def synthesize_tts(payload: dict[str, Any] = Body(...)):
        project_id = _sanitize_project_id(str(payload.get("project_id") or "quick-studio"))
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Vui lòng nhập nội dung văn bản để đọc thoại.")

        voice_persona = str(payload.get("voice_persona") or "").strip() or None
        model = str(payload.get("model") or "").strip() or None
        speaker_id = int(payload["speaker_id"]) if payload.get("speaker_id") is not None else None
        pitch_semitones = float(payload["pitch_semitones"]) if payload.get("pitch_semitones") is not None else None

        speed: Optional[float] = None
        if payload.get("speed") is not None:
            speed = float(payload["speed"])
        elif payload.get("length_scale") is not None:
            ls = float(payload["length_scale"]) or 1.0
            speed = round(1.0 / ls, 3)

        timbre = str(payload.get("timbre") or "").strip() or None
        enhance_preset = str(payload.get("enhance_preset") or "").strip() or None
        prefer_offline = bool(payload.get("prefer_offline", False))

        pdir = _project_dir(project_id)
        _ensure_project_dirs(pdir)
        raw_name = str(payload.get("filename") or f"tts_{int(time.time())}.wav")
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", Path(raw_name).name)
        if not safe_name.lower().endswith(".wav"):
            safe_name += ".wav"
        out_path = pdir / "audio" / safe_name

        try:
            voice_meta = await asyncio.to_thread(
                synthesize_studio_voice,
                text,
                out_path,
                voice_persona,
                model,
                speaker_id,
                pitch_semitones,
                speed,
                timbre,
                enhance_preset,
                prefer_offline,
            )
            duration = float(_probe_media(out_path).get("duration_seconds") or 0.0)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Lỗi tổng hợp giọng nói: {exc}") from exc

        captions = _compute_word_captions(text, 0.0, duration or max(1.0, len(text.split()) * 0.45))
        return {
            "ok": True,
            "filename": out_path.name,
            "path": str(out_path.resolve()),
            "url": _media_url(project_id, out_path),
            "duration_seconds": round(duration, 2),
            "captions": captions,
            "persona_used": voice_meta,
        }

    @app.post("/api/studio/visual")
    async def generate_visual(payload: dict[str, Any] = Body(...)):
        project_id = _sanitize_project_id(str(payload.get("project_id") or "quick-studio"))
        mode = str(payload.get("mode") or "").strip()
        pdir = _project_dir(project_id)
        _ensure_project_dirs(pdir)
        ts = int(time.time())

        if mode == "code_snippet":
            from tools.graphics.code_snippet import CodeSnippet

            code = str(payload.get("code") or "")
            if not code.strip():
                raise HTTPException(status_code=400, detail="Vui lòng nhập đoạn code.")
            out_path = pdir / "assets" / f"code_{ts}.png"
            res = await asyncio.to_thread(
                CodeSnippet().execute,
                {
                    "code": code,
                    "language": str(payload.get("language") or "python"),
                    "theme": str(payload.get("theme") or "monokai"),
                    "title": payload.get("title") or None,
                    "font_size": int(payload.get("font_size") or 20),
                    "output_path": str(out_path),
                },
            )
            if not res.success or not out_path.exists():
                raise HTTPException(status_code=500, detail=res.error or "Tạo ảnh code thất bại")
            return {
                "ok": True,
                "mode": mode,
                "asset": {
                    "kind": "image",
                    "filename": out_path.name,
                    "path": str(out_path.resolve()),
                    "url": _media_url(project_id, out_path),
                },
            }

        if mode == "diagram":
            from tools.graphics.diagram_gen import DiagramGen

            definition = str(payload.get("definition") or "")
            if not definition.strip():
                raise HTTPException(status_code=400, detail="Vui lòng nhập nội dung sơ đồ Mermaid.")
            out_path = pdir / "assets" / f"diagram_{ts}.png"
            res = await asyncio.to_thread(
                DiagramGen().execute,
                {
                    "diagram_type": str(payload.get("diagram_type") or "mermaid"),
                    "definition": definition,
                    "title": payload.get("title") or None,
                    "theme": str(payload.get("theme") or "dark"),
                    "width": int(payload.get("width") or 1600),
                    "height": int(payload.get("height") or 900),
                    "output_path": str(out_path),
                },
            )
            if not res.success or not out_path.exists():
                raise HTTPException(status_code=500, detail=res.error or "Tạo sơ đồ thất bại")
            return {
                "ok": True,
                "mode": mode,
                "asset": {
                    "kind": "image",
                    "filename": out_path.name,
                    "path": str(out_path.resolve()),
                    "url": _media_url(project_id, out_path),
                },
            }

        if mode == "stock_video":
            from tools.video.direct_clip_search import DirectClipSearch

            query = str(payload.get("query") or "").strip()
            if not query:
                raise HTTPException(status_code=400, detail="Vui lòng nhập từ khóa tìm kiếm stock.")
            kind = str(payload.get("kind") or "video")
            stock_dir = pdir / "assets" / f"stock_{ts}"
            stock_dir.mkdir(parents=True, exist_ok=True)
            res = await asyncio.to_thread(
                DirectClipSearch().execute,
                {
                    "output_dir": str(stock_dir),
                    "queries": [{"query": query, "slot_id": f"q_{ts}", "kind": kind}],
                    "clips_per_query": int(payload.get("limit") or 3),
                    "extract_thumbnails": True,
                },
            )
            if not res.success:
                raise HTTPException(status_code=500, detail=res.error or "Tìm stock thất bại")

            found_clips: list[dict[str, Any]] = []
            clips_dir = stock_dir / "clips"
            if clips_dir.exists():
                for clip in sorted(clips_dir.iterdir()):
                    if not clip.is_file():
                        continue
                    ext = clip.suffix.lower()
                    ckind = "video" if ext in MEDIA_VIDEO_EXT else ("image" if ext in MEDIA_IMAGE_EXT else "other")
                    if ckind == "other":
                        continue
                    found_clips.append({
                        "kind": ckind,
                        "filename": clip.name,
                        "path": str(clip.resolve()),
                        "url": _media_url(project_id, clip),
                        "size_mb": round(clip.stat().st_size / (1024 * 1024), 2),
                    })
            return {"ok": True, "mode": mode, "clips": found_clips, "data": res.data}

        if mode == "local_diffusion":
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                raise HTTPException(status_code=400, detail="Vui lòng nhập prompt mô tả hình ảnh.")
            job_id = uuid.uuid4().hex[:10]
            with JOBS_LOCK:
                JOBS[job_id] = {
                    "job_id": job_id,
                    "type": "local_diffusion",
                    "project_id": project_id,
                    "status": "queued",
                    "progress": 0,
                    "logs": ["Đã đưa yêu cầu tạo ảnh AI vào hàng đợi..."],
                    "result": None,
                    "error": None,
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                }
            thread = threading.Thread(
                target=_run_local_diffusion_job,
                args=(job_id, project_id, payload),
                daemon=True,
            )
            thread.start()
            return {"ok": True, "async": True, "job_id": job_id}

        raise HTTPException(status_code=400, detail=f"Chế độ tạo visual không hợp lệ: {mode!r}")

    @app.post("/api/studio/upload")
    async def upload_media(
        project_id: str = Form("quick-studio"),
        file: UploadFile = File(...),
    ):
        safe_pid = _sanitize_project_id(project_id)
        pdir = _project_dir(safe_pid)
        _ensure_project_dirs(pdir)

        orig_name = Path(file.filename or f"upload_{int(time.time())}").name
        ext = Path(orig_name).suffix.lower()
        if ext not in ALLOWED_UPLOAD_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"Định dạng tệp {ext or '(không đuôi)'} không được hỗ trợ.",
            )

        safe_stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", Path(orig_name).stem).strip("._-") or "media"
        dest = pdir / "uploads" / f"{safe_stem}_{int(time.time())}{ext}"
        content = await file.read()
        dest.write_bytes(content)

        kind = (
            "image" if ext in MEDIA_IMAGE_EXT
            else "video" if ext in MEDIA_VIDEO_EXT
            else "audio"
        )
        probe = _probe_media(dest)
        return {
            "ok": True,
            "kind": kind,
            "filename": dest.name,
            "path": str(dest.resolve()),
            "url": _media_url(safe_pid, dest),
            "probe": probe,
        }

    @app.post("/api/studio/enhance")
    async def enhance_media(payload: dict[str, Any] = Body(...)):
        project_id = _sanitize_project_id(str(payload.get("project_id") or "quick-studio"))
        op = str(payload.get("operation") or "").strip()
        input_path = _resolve_studio_input_path(str(payload.get("input_path") or ""), project_id)
        pdir = _project_dir(project_id)
        _ensure_project_dirs(pdir)
        ts = int(time.time())

        if op == "bg_remove":
            from tools.enhancement.bg_remove import BgRemove

            out_path = pdir / "assets" / f"{input_path.stem}_nobg_{ts}.png"
            res = await asyncio.to_thread(
                BgRemove().execute,
                {
                    "input_path": str(input_path),
                    "output_path": str(out_path),
                    "model": str(payload.get("model") or "u2net"),
                },
            )
            if not res.success or not out_path.exists():
                raise HTTPException(status_code=500, detail=res.error or "Tách nền thất bại")
            return {
                "ok": True,
                "operation": op,
                "asset": {
                    "kind": "image",
                    "filename": out_path.name,
                    "path": str(out_path.resolve()),
                    "url": _media_url(project_id, out_path),
                },
            }

        if op == "upscale":
            from tools.enhancement.upscale import Upscale

            out_path = pdir / "assets" / f"{input_path.stem}_up_{ts}.png"
            res = await asyncio.to_thread(
                Upscale().execute,
                {
                    "input_path": str(input_path),
                    "output_path": str(out_path),
                    "scale": int(payload.get("scale") or 2),
                    "model": str(payload.get("model") or "RealESRGAN_x4plus"),
                    "face_enhance": bool(payload.get("face_enhance", False)),
                },
            )
            if not res.success or not out_path.exists():
                raise HTTPException(status_code=500, detail=res.error or "Upscale ảnh thất bại")
            return {
                "ok": True,
                "operation": op,
                "asset": {
                    "kind": "image",
                    "filename": out_path.name,
                    "path": str(out_path.resolve()),
                    "url": _media_url(project_id, out_path),
                },
            }

        if op == "face_restore":
            from tools.enhancement.face_restore import FaceRestore

            out_path = pdir / "assets" / f"{input_path.stem}_face_{ts}.png"
            res = await asyncio.to_thread(
                FaceRestore().execute,
                {
                    "input_path": str(input_path),
                    "output_path": str(out_path),
                    "model": "GFPGAN",
                    "upscale": int(payload.get("upscale") or 2),
                    "bg_upsampler": bool(payload.get("bg_upsampler", False)),
                },
            )
            if not res.success or not out_path.exists():
                raise HTTPException(status_code=500, detail=res.error or "Phục chế khuôn mặt thất bại")
            return {
                "ok": True,
                "operation": op,
                "asset": {
                    "kind": "image",
                    "filename": out_path.name,
                    "path": str(out_path.resolve()),
                    "url": _media_url(project_id, out_path),
                },
            }

        if op == "transcribe":
            from tools.analysis.transcriber import Transcriber

            out_dir = pdir / "transcripts"
            out_dir.mkdir(parents=True, exist_ok=True)
            lang = str(payload.get("language") or "").strip() or None
            res = await asyncio.to_thread(
                Transcriber().execute,
                {
                    "input_path": str(input_path),
                    "model_size": str(payload.get("model_size") or "base"),
                    "language": lang,
                    "output_dir": str(out_dir),
                },
            )
            if not res.success:
                raise HTTPException(status_code=500, detail=res.error or "Whisper nhận diện giọng nói thất bại")
            return {"ok": True, "operation": op, "data": res.data}

        raise HTTPException(status_code=400, detail=f"Tác vụ enhance không hợp lệ: {op!r}")

    @app.post("/api/studio/render")
    def start_render(payload: dict[str, Any] = Body(...)):
        project_obj = payload.get("project") if isinstance(payload.get("project"), dict) else payload
        raw_id = str(payload.get("project_id") or project_obj.get("project_id") or "studio-demo")
        project_id = _sanitize_project_id(raw_id)

        job_id = uuid.uuid4().hex[:10]
        with JOBS_LOCK:
            JOBS[job_id] = {
                "job_id": job_id,
                "type": "render",
                "project_id": project_id,
                "status": "queued",
                "progress": 0,
                "logs": ["Đã tạo tác vụ render video..."],
                "result": None,
                "error": None,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }

        render_options = {
            "auto_tts": payload.get("auto_tts", project_obj.get("auto_tts", True)),
            "auto_captions": payload.get("auto_captions", project_obj.get("auto_captions", True)),
            "auto_fit_timing": payload.get("auto_fit_timing", project_obj.get("auto_fit_timing", True)),
            "output_filename": payload.get("output_filename"),
        }

        thread = threading.Thread(
            target=_run_render_job,
            args=(job_id, project_id, project_obj, render_options),
            daemon=True,
        )
        thread.start()
        return {"ok": True, "job_id": job_id, "project_id": project_id}

    @app.get("/api/studio/jobs/{job_id}")
    def get_job_status(job_id: str):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                raise HTTPException(status_code=404, detail=f"Không tìm thấy job {job_id!r}")
            return {"ok": True, "job": json.loads(json.dumps(job))}

    return app


app = create_studio_app()
