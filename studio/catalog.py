"""OpenMontage Studio Catalog & Multi-Persona Voice Engine.

Provides:
1. Unified 780+ Style, Playbook, Pipeline & Skill Catalog scanned from
   OpenMontage (`styles/`, `pipeline_defs/`, `skills/`, `.agents/skills/`) plus
   30 curated visual themes/color palettes with dynamically rendered SVG demo
   thumbnails (`render_catalog_thumb_svg`) and ready-to-apply scene/theme specs.
2. Multi-Genre Voice Persona Studio (`VOICE_PERSONAS`, `TIMBRE_PRESETS`,
   `synthesize_studio_voice`) supporting Male/Female/Kid/Elder/Robot voices
   across multiple languages & tones via Edge Neural TTS + offline Piper TTS
   fallback + FFmpeg pitch/timbre/EQ acoustic shaping.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml

from backlot.state import REPO_ROOT
from tools.audio.audio_enhance import PRESETS as AUDIO_ENHANCE_PRESETS
from tools.enhancement.color_grade import PROFILES as COLOR_GRADE_PROFILES

LOGGER = logging.getLogger(__name__)

STYLES_DIR = REPO_ROOT / "styles"
PIPELINE_DEFS_DIR = REPO_ROOT / "pipeline_defs"
SKILLS_DIR = REPO_ROOT / "skills"
AGENTS_SKILLS_DIR = REPO_ROOT / ".agents" / "skills"

# ---------------------------------------------------------------------------
# Voice Acoustic Timbre Presets & Color / Audio Option Metadata
# ---------------------------------------------------------------------------

TIMBRE_PRESETS: dict[str, dict[str, str]] = {
    "balanced": {
        "label": "Cân bằng tự nhiên (Mặc định)",
        "af": "anull",
    },
    "deep_warm": {
        "label": "Trầm ấm, dày giọng (Review / Podcast)",
        "af": "equalizer=f=130:t=q:w=1.2:g=4.5,equalizer=f=320:t=q:w=1.2:g=2,equalizer=f=4500:t=q:w=1.5:g=-1.5",
    },
    "bright_crisp": {
        "label": "Trong trẻo, nổi bật (TikTok / Trẻ)",
        "af": "equalizer=f=220:t=q:w=1.2:g=-2,equalizer=f=2800:t=q:w=1.2:g=3.5,equalizer=f=6500:t=q:w=1.4:g=3",
    },
    "radio_broadcast": {
        "label": "Phát thanh viên / Thời sự (Nén chặt, rõ nét)",
        "af": "highpass=f=90,equalizer=f=180:t=q:w=1.0:g=3,equalizer=f=3500:t=q:w=1.2:g=3.5,acompressor=threshold=-18dB:ratio=4:attack=3:release=60:makeup=2",
    },
    "elder_mellow": {
        "label": "Trầm lắng từng trải (Ông / Bà kể chuyện)",
        "af": "lowpass=f=7500,equalizer=f=160:t=q:w=1.2:g=3.5,equalizer=f=2500:t=q:w=1.0:g=-2.5",
    },
    "cinema_boom": {
        "label": "Hùng hồn điện ảnh (Trailer rạp chiếu phim)",
        "af": "equalizer=f=95:t=q:w=1.0:g=6,equalizer=f=220:t=q:w=1.2:g=2.5,aecho=0.8:0.85:45:0.18",
    },
    "cartoon_light": {
        "label": "Nhí nhảnh vui tươi (Thiếu nhi / Hoạt hình)",
        "af": "highpass=f=150,equalizer=f=1400:t=q:w=1.2:g=3,equalizer=f=4800:t=q:w=1.2:g=4",
    },
    "sci_fi_robot": {
        "label": "AI Robot / Giọng máy viễn tưởng",
        "af": "highpass=f=180,lowpass=f=6000,aecho=0.8:0.8:18:0.35,flanger=delay=4:depth=3:speed=1.5",
    },
}

VOICE_GENDERS = [
    {"id": "all", "label": "Tất cả giới tính"},
    {"id": "male", "label": "👨 Giọng Nam"},
    {"id": "female", "label": "👩 Giọng Nữ"},
    {"id": "kid", "label": "🧒 Thiếu nhi / Hoạt hình"},
    {"id": "special", "label": "🤖 AI / Nhân vật đặc biệt"},
]

VOICE_AGE_GROUPS = [
    {"id": "all", "label": "Mọi độ tuổi"},
    {"id": "child", "label": "Thiếu nhi"},
    {"id": "young", "label": "Thanh niên / Gen-Z"},
    {"id": "adult", "label": "Trưởng thành"},
    {"id": "middle", "label": "Trung niên"},
    {"id": "elder", "label": "Lớn tuổi (Ông / Bà)"},
]

VOICE_TONES = [
    {"id": "all", "label": "Mọi thể loại tone"},
    {"id": "explainer", "label": "🎓 Thuyết minh giải thích"},
    {"id": "movie_recap", "label": "🎬 Review / Tóm tắt phim"},
    {"id": "news", "label": "📰 Thời sự / Bản tin"},
    {"id": "story", "label": "📖 Kể chuyện truyền cảm"},
    {"id": "tiktok", "label": "⚡ Sôi động TikTok / Reels"},
    {"id": "podcast", "label": "🎧 Podcast / Tâm sự đêm"},
    {"id": "trailer", "label": "🔥 Trailer Hùng hồn"},
    {"id": "fairytale", "label": "🪔 Cổ tích Ông / Bà"},
    {"id": "cartoon", "label": "🧸 Vui nhộn / Hoạt hình"},
    {"id": "expert", "label": "💼 Chuyên gia / Doanh nhân"},
    {"id": "robot", "label": "🤖 AI Sci-Fi"},
]

VOICE_PERSONAS: list[dict[str, Any]] = [
    # ---- VIETNAMESE MALE ----
    {
        "id": "vi-nam-minh-chuan",
        "name": "Nam Minh — Thuyết minh chuẩn",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "adult",
        "age_label": "Trưởng thành (28-35)",
        "tone": "explainer",
        "tone_label": "Thuyết minh",
        "language": "vi",
        "avatar": "👨",
        "description": "Giọng nam miền Bắc rõ ràng, mạch lạc, phù hợp video công nghệ & giáo dục.",
        "sample_text": "Xin chào, hôm nay chúng ta sẽ cùng khám phá công nghệ dựng phim AI hoàn toàn tự động.",
        "edge_voice": "vi-VN-NamMinhNeural",
        "piper_model": "vi_VN-vivos-x_low",
        "speaker_id": 1,
        "pitch_semitones": 0.0,
        "speed": 1.0,
        "timbre": "balanced",
        "enhance_preset": "clean_speech",
    },
    {
        "id": "vi-nam-tram-review",
        "name": "Tuấn Vũ — Review Phim Trầm Gắt",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "adult",
        "age_label": "Trưởng thành (30-40)",
        "tone": "movie_recap",
        "tone_label": "Review Phim",
        "language": "vi",
        "avatar": "🎞️",
        "description": "Giọng nam trầm ấm, nhịp dứt khoát cực hợp tóm tắt phim & kể chuyện trinh thám.",
        "sample_text": "Không ai ngờ rằng sau cánh cửa bí mật ấy lại ẩn giấu một kế hoạch thay đổi cả thế giới.",
        "edge_voice": "vi-VN-NamMinhNeural",
        "piper_model": "vi_VN-vivos-x_low",
        "speaker_id": 2,
        "pitch_semitones": -1.8,
        "speed": 1.08,
        "timbre": "deep_warm",
        "enhance_preset": "broadcast",
    },
    {
        "id": "vi-nam-thoi-su",
        "name": "Hoàng Long — Phát Thanh Viên VTV",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "middle",
        "age_label": "Trung niên (38-48)",
        "tone": "news",
        "tone_label": "Thời sự / Bản tin",
        "language": "vi",
        "avatar": "📰",
        "description": "Âm sắc chuẩn đài phát thanh, đĩnh đạc, tin cậy cao cho tin tức & doanh nghiệp.",
        "sample_text": "Bản tin công nghệ hôm nay ghi nhận bước tiến mới trong xử lý đồ họa AI ngay trên máy cá nhân.",
        "edge_voice": "vi-VN-NamMinhNeural",
        "piper_model": "vi_VN-vivos-x_low",
        "speaker_id": 3,
        "pitch_semitones": -0.8,
        "speed": 1.03,
        "timbre": "radio_broadcast",
        "enhance_preset": "broadcast",
    },
    {
        "id": "vi-nam-tre-tiktok",
        "name": "Khang GenZ — TikTok Năng Động",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "young",
        "age_label": "Thanh niên (20-25)",
        "tone": "tiktok",
        "tone_label": "Sôi động TikTok",
        "language": "vi",
        "avatar": "⚡",
        "description": "Tốc độ nhanh gọn, giọng sáng trẻ trung thu hút 3 giây đầu Shorts/Reels.",
        "sample_text": "Khoan đã, đừng lướt qua! Đây là ba mẹo dựng video AI cực nhanh mà ai cũng làm được.",
        "edge_voice": "vi-VN-NamMinhNeural",
        "piper_model": "vi_VN-vivos-x_low",
        "speaker_id": 5,
        "pitch_semitones": 1.2,
        "speed": 1.16,
        "timbre": "bright_crisp",
        "enhance_preset": "voice_clarity",
    },
    {
        "id": "vi-nam-podcast",
        "name": "Minh Triết — Podcast Đêm Khuya",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "middle",
        "age_label": "Trung niên (35-45)",
        "tone": "podcast",
        "tone_label": "Podcast Trầm Ấm",
        "language": "vi",
        "avatar": "🎧",
        "description": "Nhịp chậm rãi, dày âm trầm, thư giãn sâu cho sách nói và chia sẻ cảm xúc.",
        "sample_text": "Có những buổi tối bình yên, ta chỉ cần lắng lại một chút để nghe nhịp thở của chính mình.",
        "edge_voice": "vi-VN-NamMinhNeural",
        "piper_model": "vi_VN-vivos-x_low",
        "speaker_id": 7,
        "pitch_semitones": -2.2,
        "speed": 0.90,
        "timbre": "deep_warm",
        "enhance_preset": "podcast",
    },
    {
        "id": "vi-nam-trailer",
        "name": "Hùng Cường — Trailer Điện Ảnh",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "middle",
        "age_label": "Trung niên (40-50)",
        "tone": "trailer",
        "tone_label": "Trailer Hùng Hồn",
        "language": "vi",
        "avatar": "🔥",
        "description": "Giọng bass sâu vang uy lực rạp chiếu phim cho intro game, trailer thương hiệu.",
        "sample_text": "Khi giới hạn bị phá vỡ, chỉ những người tiên phong mới viết nên kỷ nguyên mới.",
        "edge_voice": "vi-VN-NamMinhNeural",
        "piper_model": "vi_VN-vivos-x_low",
        "speaker_id": 9,
        "pitch_semitones": -3.4,
        "speed": 0.92,
        "timbre": "cinema_boom",
        "enhance_preset": "broadcast",
    },
    {
        "id": "vi-nam-chuyen-gia",
        "name": "Tiến Sĩ Thành — Phân Tích Chuyên Gia",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "middle",
        "age_label": "Trung niên (42-55)",
        "tone": "expert",
        "tone_label": "Chuyên gia / Tài chính",
        "language": "vi",
        "avatar": "💼",
        "description": "Chậm chắc, điềm tĩnh, tạo độ tin cậy cao cho báo cáo số liệu & phân tích.",
        "sample_text": "Theo thống kê quý vừa qua, chi phí sản xuất nội dung giảm hơn tám mươi phần trăm nhờ AI cục bộ.",
        "edge_voice": "vi-VN-NamMinhNeural",
        "piper_model": "vi_VN-vivos-x_low",
        "speaker_id": 10,
        "pitch_semitones": -1.0,
        "speed": 0.95,
        "timbre": "balanced",
        "enhance_preset": "clean_speech",
    },
    {
        "id": "vi-ong-gia-ke-chuyen",
        "name": "Ông Năm — Cụ Ông Kể Cổ Tích",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "elder",
        "age_label": "Lớn tuổi (65+)",
        "tone": "fairytale",
        "tone_label": "Kể chuyện Ông/Bà",
        "language": "vi",
        "avatar": "👴",
        "description": "Giọng ông lão trầm đục ấm áp, thong thả giàu hoài niệm cho lịch sử & truyện dân gian.",
        "sample_text": "Ngày xửa ngày xưa, ở một ngôi làng nhỏ ven sông, người ta vẫn truyền tai nhau câu chuyện ấy.",
        "edge_voice": "vi-VN-NamMinhNeural",
        "piper_model": "vi_VN-vivos-x_low",
        "speaker_id": 12,
        "pitch_semitones": -3.2,
        "speed": 0.85,
        "timbre": "elder_mellow",
        "enhance_preset": "podcast",
    },
    {
        "id": "vi-nam-offline-vivos",
        "name": "Việt Anh — Nam Offline 100% (Vivos)",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "adult",
        "age_label": "Trưởng thành",
        "tone": "story",
        "tone_label": "Kể chuyện Offline",
        "language": "vi",
        "avatar": "🎙️",
        "description": "Chạy hoàn toàn trên CPU không cần internet với bộ đọc Piper Vivos.",
        "sample_text": "Giọng đọc nội bộ không phụ thuộc internet, sẵn sàng mọi lúc trên máy tính của bạn.",
        "edge_voice": "",
        "piper_model": "vi_VN-vivos-x_low",
        "speaker_id": 1,
        "pitch_semitones": -0.5,
        "speed": 1.0,
        "timbre": "balanced",
        "enhance_preset": "clean_speech",
    },

    # ---- VIETNAMESE FEMALE ----
    {
        "id": "vi-nu-hoai-my-chuan",
        "name": "Hoài My — Thuyết Minh Truyền Cảm",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "adult",
        "age_label": "Trưởng thành (25-33)",
        "tone": "explainer",
        "tone_label": "Thuyết minh",
        "language": "vi",
        "avatar": "👩",
        "description": "Giọng nữ phổ biến nhất, mượt mà tự nhiên cho mọi video giải thích & giới thiệu.",
        "sample_text": "Chào mừng bạn đến với xưởng phim AI tại nhà, nơi mọi ý tưởng đều hóa thành thước phim sống động.",
        "edge_voice": "vi-VN-HoaiMyNeural",
        "piper_model": "vi_VN-vais1000-medium",
        "speaker_id": 0,
        "pitch_semitones": 0.0,
        "speed": 1.0,
        "timbre": "balanced",
        "enhance_preset": "clean_speech",
    },
    {
        "id": "vi-nu-vais-offline",
        "name": "Thu Hà — Nữ Offline Chuẩn (Piper)",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "adult",
        "age_label": "Trưởng thành",
        "tone": "explainer",
        "tone_label": "Thuyết minh Offline",
        "language": "vi",
        "avatar": "👩‍🏫",
        "description": "Mô hình AI Piper chạy cục bộ không cần mạng, phát âm tiếng Việt rất tự nhiên.",
        "sample_text": "Hệ thống lồng tiếng cục bộ hoàn toàn miễn phí và bảo mật tuyệt đối dữ liệu của bạn.",
        "edge_voice": "",
        "piper_model": "vi_VN-vais1000-medium",
        "speaker_id": 0,
        "pitch_semitones": 0.0,
        "speed": 1.0,
        "timbre": "voice_clarity" if False else "balanced",
        "enhance_preset": "clean_speech",
    },
    {
        "id": "vi-nu-25h-offline",
        "name": "Lan Chi — Nữ Văn Học (25h Offline)",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "adult",
        "age_label": "Trưởng thành",
        "tone": "story",
        "tone_label": "Đọc truyện Offline",
        "language": "vi",
        "avatar": "📖",
        "description": "Chất giọng đọc truyện dài hơi, chạy 100% local từ bộ dữ liệu 25hours tiếng Việt.",
        "sample_text": "Những trang sách mở ra chân trời mới, đưa người nghe đi qua bao miền ký ức dịu êm.",
        "edge_voice": "",
        "piper_model": "vi_VN-25hours_single-low",
        "speaker_id": 0,
        "pitch_semitones": 0.0,
        "speed": 1.0,
        "timbre": "deep_warm",
        "enhance_preset": "podcast",
    },
    {
        "id": "vi-nu-tre-tiktok",
        "name": "Bảo Ngọc — Hot TikToker Trẻ Trung",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "young",
        "age_label": "Thanh niên (19-25)",
        "tone": "tiktok",
        "tone_label": "Sôi động TikTok",
        "language": "vi",
        "avatar": "✨",
        "description": "Giọng nữ thanh, sáng, nhịp nhanh cuốn hút cho review mỹ phẩm, ẩm thực, Shorts.",
        "sample_text": "Mê thật sự luôn mọi người ơi! Chỉ mất một phút mà ra ngay chiếc video xịn xò thế này.",
        "edge_voice": "vi-VN-HoaiMyNeural",
        "piper_model": "vi_VN-vais1000-medium",
        "speaker_id": 0,
        "pitch_semitones": 1.6,
        "speed": 1.15,
        "timbre": "bright_crisp",
        "enhance_preset": "voice_clarity",
    },
    {
        "id": "vi-nu-review-phim",
        "name": "Hải Yến — Review Phim & Drama",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "adult",
        "age_label": "Trưởng thành (26-34)",
        "tone": "movie_recap",
        "tone_label": "Review Phim",
        "language": "vi",
        "avatar": "🎭",
        "description": "Giọng nữ sắc sảo, nhấn nhá kịch tính cho tóm tắt phim & phân tích tâm lý.",
        "sample_text": "Cô gái tưởng rằng mọi chuyện đã khép lại, nhưng tin nhắn lúc nửa đêm mới là khởi đầu.",
        "edge_voice": "vi-VN-HoaiMyNeural",
        "piper_model": "vi_VN-vais1000-medium",
        "speaker_id": 0,
        "pitch_semitones": -0.8,
        "speed": 1.09,
        "timbre": "radio_broadcast",
        "enhance_preset": "broadcast",
    },
    {
        "id": "vi-nu-podcast",
        "name": "Nhã Phương — Podcast Chữa Lành",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "adult",
        "age_label": "Trưởng thành (28-36)",
        "tone": "podcast",
        "tone_label": "Podcast Nhẹ Nhàng",
        "language": "vi",
        "avatar": "🌙",
        "description": "Êm dịu, ấm áp, nhịp thư thái cho video thiền, chữa lành và tâm sự.",
        "sample_text": "Hôm nay của bạn thế nào? Dù có mệt mỏi ra sao, hãy cho phép mình nghỉ ngơi một lát nhé.",
        "edge_voice": "vi-VN-HoaiMyNeural",
        "piper_model": "vi_VN-vais1000-medium",
        "speaker_id": 0,
        "pitch_semitones": -1.4,
        "speed": 0.90,
        "timbre": "deep_warm",
        "enhance_preset": "podcast",
    },
    {
        "id": "vi-nu-btv-thoi-su",
        "name": "Thanh Thảo — Biên Tập Viên Thời Sự",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "middle",
        "age_label": "Trung niên (35-45)",
        "tone": "news",
        "tone_label": "Thời sự / Doanh nghiệp",
        "language": "vi",
        "avatar": "📺",
        "description": "Phong thái chuyên nghiệp, chuẩn mực cho bản tin doanh nghiệp và đào tạo.",
        "sample_text": "Quý vị đang theo dõi bản tin tổng hợp các công cụ trí tuệ nhân tạo ứng dụng trong sản xuất.",
        "edge_voice": "vi-VN-HoaiMyNeural",
        "piper_model": "vi_VN-vais1000-medium",
        "speaker_id": 0,
        "pitch_semitones": -0.6,
        "speed": 1.02,
        "timbre": "radio_broadcast",
        "enhance_preset": "broadcast",
    },
    {
        "id": "vi-ba-noi-ke-chuyen",
        "name": "Bà Tám — Cụ Bà Kể Chuyện Xưa",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "elder",
        "age_label": "Lớn tuổi (62+)",
        "tone": "fairytale",
        "tone_label": "Kể chuyện Ông/Bà",
        "language": "vi",
        "avatar": "👵",
        "description": "Giọng bà ấm, hơi trầm và chậm rãi truyền cảm cho cổ tích & gia đình.",
        "sample_text": "Cháu ngồi xuống đây nghe bà kể chuyện hồi xưa, khi cả xóm còn quây quần bên bếp lửa.",
        "edge_voice": "vi-VN-HoaiMyNeural",
        "piper_model": "vi_VN-25hours_single-low",
        "speaker_id": 0,
        "pitch_semitones": -2.6,
        "speed": 0.85,
        "timbre": "elder_mellow",
        "enhance_preset": "podcast",
    },

    # ---- VIETNAMESE KIDS / CARTOON / SPECIAL ----
    {
        "id": "vi-be-gai-hoat-hinh",
        "name": "Bé Bông — Thiếu Nhi Nhí Nhảnh",
        "gender": "kid",
        "gender_label": "Thiếu nhi",
        "age_group": "child",
        "age_label": "Thiếu nhi (6-10)",
        "tone": "cartoon",
        "tone_label": "Hoạt hình / Trẻ em",
        "language": "vi",
        "avatar": "🧒",
        "description": "Tông giọng cao trong trẻo, đáng yêu cho phim hoạt hình và video học tập cho bé.",
        "sample_text": "Oa! Các bạn nhỏ ơi, cùng khám phá khu rừng phép thuật đầy màu sắc với mình nhé!",
        "edge_voice": "vi-VN-HoaiMyNeural",
        "piper_model": "vi_VN-vais1000-medium",
        "speaker_id": 0,
        "pitch_semitones": 4.2,
        "speed": 1.08,
        "timbre": "cartoon_light",
        "enhance_preset": "voice_clarity",
    },
    {
        "id": "vi-be-trai-nang-no",
        "name": "Bé Bi — Cậu Bé Hiếu Động",
        "gender": "kid",
        "gender_label": "Thiếu nhi",
        "age_group": "child",
        "age_label": "Thiếu nhi (8-12)",
        "tone": "cartoon",
        "tone_label": "Hoạt hình / Vui nhộn",
        "language": "vi",
        "avatar": "👦",
        "description": "Giọng cậu bé tinh nghịch, lanh lợi cho nhân vật hoạt hình & game.",
        "sample_text": "Tuyệt vời! Chúng ta vừa mở khóa được bảo bối mới cực kỳ lợi hại rồi đây!",
        "edge_voice": "vi-VN-NamMinhNeural",
        "piper_model": "vi_VN-vivos-x_low",
        "speaker_id": 4,
        "pitch_semitones": 4.5,
        "speed": 1.12,
        "timbre": "cartoon_light",
        "enhance_preset": "voice_clarity",
    },
    {
        "id": "vi-ai-robot-scifi",
        "name": "JARVIS VN — Trợ Lý AI Viễn Tưởng",
        "gender": "special",
        "gender_label": "AI Đặc biệt",
        "age_group": "adult",
        "age_label": "Hệ thống AI",
        "tone": "robot",
        "tone_label": "AI Sci-Fi",
        "language": "vi",
        "avatar": "🤖",
        "description": "Giọng tổng hợp hiệu ứng viễn tưởng nhẹ cho video công nghệ tương lai & game.",
        "sample_text": "Hệ thống đã sẵn sàng. Toàn bộ mô hình GPU đang hoạt động ở trạng thái tối ưu.",
        "edge_voice": "vi-VN-NamMinhNeural",
        "piper_model": "vi_VN-vivos-x_low",
        "speaker_id": 0,
        "pitch_semitones": -1.5,
        "speed": 1.03,
        "timbre": "sci_fi_robot",
        "enhance_preset": "broadcast",
    },

    # ---- ENGLISH VOICES (MALE / FEMALE / ELDER / KID) ----
    {
        "id": "en-male-guy-explainer",
        "name": "Guy (US Male) — Tech Explainer",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "adult",
        "age_label": "Adult (28-38)",
        "tone": "explainer",
        "tone_label": "Explainer",
        "language": "en",
        "avatar": "🇺🇸",
        "description": "Crisp American male voice ideal for YouTube SaaS demos and tutorials.",
        "sample_text": "Welcome to your local AI studio, where ideas transform into polished videos in minutes.",
        "edge_voice": "en-US-GuyNeural",
        "piper_model": "en_US-ryan-medium",
        "speaker_id": 0,
        "pitch_semitones": 0.0,
        "speed": 1.0,
        "timbre": "balanced",
        "enhance_preset": "clean_speech",
    },
    {
        "id": "en-male-christopher-doc",
        "name": "Christopher (US Male) — Deep Documentary",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "middle",
        "age_label": "Middle-aged",
        "tone": "movie_recap",
        "tone_label": "Documentary / Recap",
        "language": "en",
        "avatar": "🎥",
        "description": "Authoritative deep American narrator built for documentaries & movie recaps.",
        "sample_text": "Deep beneath the surface, a silent revolution was about to reshape every industry.",
        "edge_voice": "en-US-ChristopherNeural",
        "piper_model": "en_US-ryan-medium",
        "speaker_id": 0,
        "pitch_semitones": -1.0,
        "speed": 0.97,
        "timbre": "deep_warm",
        "enhance_preset": "broadcast",
    },
    {
        "id": "en-male-roger-elder",
        "name": "Roger (US Elder Male) — Wise Storyteller",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "elder",
        "age_label": "Elder (60+)",
        "tone": "fairytale",
        "tone_label": "Elder Storyteller",
        "language": "en",
        "avatar": "👴",
        "description": "Seasoned elder statesman voice with gravitas for history & trailers.",
        "sample_text": "Decades ago, nobody imagined a desktop computer could direct an entire motion picture.",
        "edge_voice": "en-US-RogerNeural",
        "piper_model": "en_US-ryan-medium",
        "speaker_id": 0,
        "pitch_semitones": -2.0,
        "speed": 0.90,
        "timbre": "elder_mellow",
        "enhance_preset": "podcast",
    },
    {
        "id": "en-male-ryan-offline",
        "name": "Ryan (Offline Piper US Male)",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "adult",
        "age_label": "Adult",
        "tone": "news",
        "tone_label": "Offline Broadcast",
        "language": "en",
        "avatar": "🎙️",
        "description": "100% offline local US Male Piper model.",
        "sample_text": "Offline neural speech synthesis running directly on your local workstation.",
        "edge_voice": "",
        "piper_model": "en_US-ryan-medium",
        "speaker_id": 0,
        "pitch_semitones": 0.0,
        "speed": 1.0,
        "timbre": "radio_broadcast",
        "enhance_preset": "clean_speech",
    },
    {
        "id": "en-female-jenny-warm",
        "name": "Jenny (US Female) — Warm Product Host",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "young",
        "age_label": "Young Adult",
        "tone": "explainer",
        "tone_label": "Warm Explainer",
        "language": "en",
        "avatar": "👩",
        "description": "Friendly natural female host for onboarding, promos & education.",
        "sample_text": "Let us walk through three effortless ways to automate your video editing workflow.",
        "edge_voice": "en-US-JennyNeural",
        "piper_model": "en_US-lessac-medium",
        "speaker_id": 0,
        "pitch_semitones": 0.0,
        "speed": 1.02,
        "timbre": "balanced",
        "enhance_preset": "clean_speech",
    },
    {
        "id": "en-female-aria-news",
        "name": "Aria (US Female) — Crisp News / Social",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "adult",
        "age_label": "Adult",
        "tone": "tiktok",
        "tone_label": "Social / News",
        "language": "en",
        "avatar": "⚡",
        "description": "Dynamic expressive female voice great for viral shorts and news reels.",
        "sample_text": "Wait until you see what happens when local rendering meets smart motion graphics!",
        "edge_voice": "en-US-AriaNeural",
        "piper_model": "en_US-amy-medium",
        "speaker_id": 0,
        "pitch_semitones": 0.5,
        "speed": 1.1,
        "timbre": "bright_crisp",
        "enhance_preset": "voice_clarity",
    },
    {
        "id": "en-kid-ana-cartoon",
        "name": "Ana (US Child) — Cheerful Kid Voice",
        "gender": "kid",
        "gender_label": "Thiếu nhi",
        "age_group": "child",
        "age_label": "Child (7-10)",
        "tone": "cartoon",
        "tone_label": "Kids / Animation",
        "language": "en",
        "avatar": "🧒",
        "description": "Playful American child voice for animations, stories & kids channels.",
        "sample_text": "Come look! Every drawing on our screen just came to life with music!",
        "edge_voice": "en-US-AnaNeural",
        "piper_model": "en_US-amy-medium",
        "speaker_id": 0,
        "pitch_semitones": 2.5,
        "speed": 1.05,
        "timbre": "cartoon_light",
        "enhance_preset": "voice_clarity",
    },
    {
        "id": "en-female-lessac-offline",
        "name": "Lessac (Offline Piper US Female)",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "adult",
        "age_label": "Adult",
        "tone": "story",
        "tone_label": "Offline Narrator",
        "language": "en",
        "avatar": "🎙️",
        "description": "High-clarity 100% offline US Female Piper voice.",
        "sample_text": "The world is changing faster than ever, and your studio runs right on your desk.",
        "edge_voice": "",
        "piper_model": "en_US-lessac-medium",
        "speaker_id": 0,
        "pitch_semitones": 0.0,
        "speed": 1.0,
        "timbre": "balanced",
        "enhance_preset": "clean_speech",
    },
    {
        "id": "en-female-amy-offline",
        "name": "Amy (Offline Piper Youthful Female)",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "young",
        "age_label": "Young Adult",
        "tone": "explainer",
        "tone_label": "Offline Crisp",
        "language": "en",
        "avatar": "🌸",
        "description": "Light, friendly offline US Female Piper model.",
        "sample_text": "Create crisp voiceovers without touching a cloud API.",
        "edge_voice": "",
        "piper_model": "en_US-amy-medium",
        "speaker_id": 0,
        "pitch_semitones": 0.4,
        "speed": 1.02,
        "timbre": "bright_crisp",
        "enhance_preset": "clean_speech",
    },
    {
        "id": "vi-nam-kinh-di",
        "name": "Huy Hoàng — Kể Chuyện Kinh Dị / Bí Ẩn",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "middle",
        "age_label": "Trung niên (40-50)",
        "tone": "story",
        "tone_label": "Kể chuyện",
        "language": "vi",
        "avatar": "🕯️",
        "description": "Giọng trầm thấp, chậm rãi tạo không khí rùng rợn, kỳ bí cho truyện đêm khuya.",
        "sample_text": "Nửa đêm hôm ấy, tiếng bước chân lại vang lên ngoài hành lang vắng lặng không một bóng người.",
        "edge_voice": "vi-VN-NamMinhNeural",
        "piper_model": "vi_VN-vivos-x_low",
        "speaker_id": 14,
        "pitch_semitones": -2.4,
        "speed": 0.90,
        "timbre": "deep_warm",
        "enhance_preset": "podcast",
    },
    {
        "id": "vi-nu-tam-su-dem",
        "name": "Ngọc Lan — Radio Tâm Sự Đêm",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "young",
        "age_label": "Trẻ (22-28)",
        "tone": "podcast",
        "tone_label": "Podcast / Tâm sự",
        "language": "vi",
        "avatar": "🌙",
        "description": "Chất giọng nhẹ nhàng, thì thầm ấm áp dành cho podcast chữa lành và tâm sự.",
        "sample_text": "Có những ngày mệt mỏi, chỉ cần dừng lại một nhịp và lắng nghe chính trái tim mình.",
        "edge_voice": "vi-VN-HoaiMyNeural",
        "piper_model": "vi_VN-25hours_single-low",
        "speaker_id": 0,
        "pitch_semitones": -0.6,
        "speed": 0.93,
        "timbre": "deep_warm",
        "enhance_preset": "podcast",
    },
    {
        "id": "vi-nam-blv-the-thao",
        "name": "Hùng Cường — BLV Thể Thao Sôi Động",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "young",
        "age_label": "Thanh niên (24-30)",
        "tone": "tiktok",
        "tone_label": "Sôi động TikTok",
        "language": "vi",
        "avatar": "⚽",
        "description": "Nhịp độ nhanh, cao trào dồn dập cho video highlights thể thao, esports, reaction.",
        "sample_text": "Một pha xử lý quá đẳng cấp ở phút bù giờ cuối cùng, không thể tin nổi!",
        "edge_voice": "vi-VN-NamMinhNeural",
        "piper_model": "vi_VN-vivos-x_low",
        "speaker_id": 19,
        "pitch_semitones": 0.9,
        "speed": 1.18,
        "timbre": "bright_crisp",
        "enhance_preset": "broadcast",
    },
    {
        "id": "vi-nu-co-giao",
        "name": "Cô Thu Hà — Giảng Bài Dịu Dàng",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "adult",
        "age_label": "Trưởng thành (28-36)",
        "tone": "explainer",
        "tone_label": "Thuyết minh",
        "language": "vi",
        "avatar": "📚",
        "description": "Phát âm tròn vành rõ chữ, âm điệu thân thiện như cô giáo hướng dẫn bài học.",
        "sample_text": "Để nắm vững bài học hôm nay, các bạn hãy chú ý ba bước cơ bản ngay trên màn hình.",
        "edge_voice": "vi-VN-HoaiMyNeural",
        "piper_model": "vi_VN-vais1000-medium",
        "speaker_id": 0,
        "pitch_semitones": 0.3,
        "speed": 0.98,
        "timbre": "balanced",
        "enhance_preset": "clean_speech",
    },
    {
        "id": "vi-ong-lao-vivos-offline",
        "name": "Ông Tư — Kể Chuyện Offline (Vivos #23)",
        "gender": "male",
        "gender_label": "Nam",
        "age_group": "elder",
        "age_label": "Lớn tuổi (65+)",
        "tone": "fairytale",
        "tone_label": "Cổ tích Ông/Bà",
        "language": "vi",
        "avatar": "🫖",
        "description": "Biến thể giọng ông lão từ bộ Vivos 65 người đọc, hoạt động 100% không cần mạng.",
        "sample_text": "Ngày xưa bên bến sông nhỏ, có một ông lão đánh cá hiền lành sống cùng đàn cò trắng.",
        "edge_voice": "vi-VN-NamMinhNeural",
        "piper_model": "vi_VN-vivos-x_low",
        "speaker_id": 23,
        "pitch_semitones": -2.8,
        "speed": 0.87,
        "timbre": "elder_mellow",
        "enhance_preset": "podcast",
    },
    {
        "id": "vi-ba-ngoai-25h-offline",
        "name": "Bà Ngoại — Truyện Dân Gian (Offline)",
        "gender": "female",
        "gender_label": "Nữ",
        "age_group": "elder",
        "age_label": "Lớn tuổi (65+)",
        "tone": "fairytale",
        "tone_label": "Cổ tích Ông/Bà",
        "language": "vi",
        "avatar": "🧶",
        "description": "Chất giọng bà ấm áp hiền từ từ model Piper 25h Offline, thích hợp truyện cổ tích.",
        "sample_text": "Cháu ngoan lại đây bà kể cho nghe sự tích cây vú sữa thuở xa xưa.",
        "edge_voice": "vi-VN-HoaiMyNeural",
        "piper_model": "vi_VN-25hours_single-low",
        "speaker_id": 0,
        "pitch_semitones": -1.6,
        "speed": 0.88,
        "timbre": "elder_mellow",
        "enhance_preset": "clean_speech",
    },
    {
        "id": "en-male-trailer-epic",
        "name": "Maximus — Epic Cinema Trailer (EN)",
        "gender": "male",
        "gender_label": "Nam (EN)",
        "age_group": "middle",
        "age_label": "Trung niên (40+)",
        "tone": "trailer",
        "tone_label": "Trailer Hùng hồn",
        "language": "en",
        "avatar": "🎥",
        "description": "Deep booming Hollywood movie trailer voice with dramatic echo & sub bass.",
        "sample_text": "In a world rewritten by artificial intelligence, one creator will change the future.",
        "edge_voice": "en-US-DavisNeural",
        "piper_model": "en_US-ryan-medium",
        "speaker_id": 0,
        "pitch_semitones": -3.0,
        "speed": 0.92,
        "timbre": "cinema_boom",
        "enhance_preset": "broadcast",
    },
    {
        "id": "en-female-podcast-velvet",
        "name": "Sophia — Velvet Tech Podcast (EN)",
        "gender": "female",
        "gender_label": "Nữ (EN)",
        "age_group": "adult",
        "age_label": "Trưởng thành (28-35)",
        "tone": "podcast",
        "tone_label": "Podcast",
        "language": "en",
        "avatar": "🎙️",
        "description": "Warm, articulate English studio podcast voice ideal for AI explainers and essays.",
        "sample_text": "Welcome back to the studio. Today we are exploring how local models transform filmmaking.",
        "edge_voice": "en-US-AriaNeural",
        "piper_model": "en_US-amy-medium",
        "speaker_id": 0,
        "pitch_semitones": -0.5,
        "speed": 1.0,
        "timbre": "deep_warm",
        "enhance_preset": "podcast",
    },
]

VOICE_PERSONAS_BY_ID: dict[str, dict[str, Any]] = {p["id"]: p for p in VOICE_PERSONAS}


def _find_ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def _build_atempo_chain(tempo: float) -> str:
    """Decompose arbitrary speed factor into valid FFmpeg atempo stages [0.5, 2.0]."""
    t = max(0.35, min(2.5, float(tempo)))
    stages: list[str] = []
    while t > 2.0:
        stages.append("atempo=2.0")
        t /= 2.0
    while t < 0.5:
        stages.append("atempo=0.5")
        t /= 0.5
    stages.append(f"atempo={t:.4f}")
    return ",".join(stages)


def _apply_voice_dsp(
    raw_audio_path: Path,
    output_wav: Path,
    pitch_semitones: float = 0.0,
    speed: float = 1.0,
    timbre: str = "balanced",
    enhance_preset: str = "clean_speech",
) -> None:
    """Apply pitch shifting, tempo compensation, EQ timbre & AudioEnhance cleanly via FFmpeg."""
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        if raw_audio_path.suffix.lower() == ".wav":
            shutil.copy2(raw_audio_path, output_wav)
            return
        raise RuntimeError("Không tìm thấy ffmpeg để xử lý giọng đọc")

    sr = 24000
    pitch = max(-8.0, min(8.0, float(pitch_semitones or 0.0)))
    spd = max(0.65, min(1.5, float(speed or 1.0)))
    pitch_factor = 2.0 ** (pitch / 12.0)

    filters: list[str] = [f"aresample={sr}"]
    if abs(pitch) > 0.05:
        shifted_sr = int(round(sr * pitch_factor))
        filters.append(f"asetrate={shifted_sr}")
        filters.append(f"aresample={sr}")

    needed_tempo = spd / pitch_factor if abs(pitch) > 0.05 else spd
    if abs(needed_tempo - 1.0) > 0.02:
        filters.append(_build_atempo_chain(needed_tempo))

    timbre_af = (TIMBRE_PRESETS.get(timbre) or {}).get("af", "anull")
    if timbre_af and timbre_af != "anull":
        filters.append(timbre_af)

    enh_af = (AUDIO_ENHANCE_PRESETS.get(enhance_preset) or {}).get("af")
    if enh_af:
        filters.append(enh_af)

    filter_chain = ",".join(filters)
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(raw_audio_path),
            "-af",
            filter_chain,
            "-ac",
            "1",
            "-ar",
            str(sr),
            "-c:a",
            "pcm_s16le",
            str(output_wav),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    if proc.returncode != 0 or not output_wav.exists():
        # Fallback to plain decode without complex filter chain if edge audio was empty/tiny
        subprocess.run(
            [
                ffmpeg, "-y", "-i", str(raw_audio_path),
                "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(output_wav),
            ],
            capture_output=True,
            check=True,
            timeout=60,
        )


def _synth_edge_tts_to_file(text: str, voice: str, out_mp3: Path) -> None:
    import edge_tts

    async def _run() -> None:
        comm = edge_tts.Communicate(text=text, voice=voice)
        await comm.save(str(out_mp3))

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        new_loop = asyncio.new_event_loop()
        try:
            new_loop.run_until_complete(_run())
        finally:
            new_loop.close()
    else:
        asyncio.run(_run())


def synthesize_studio_voice(
    text: str,
    output_wav: Path,
    persona_id: Optional[str] = None,
    piper_model: Optional[str] = None,
    speaker_id: Optional[int] = None,
    pitch_semitones: Optional[float] = None,
    speed: Optional[float] = None,
    timbre: Optional[str] = None,
    enhance_preset: Optional[str] = None,
    prefer_offline: bool = False,
) -> dict[str, Any]:
    """Synthesize expressive Studio voiceover (Edge Neural primary + local Piper fallback + DSP)."""
    persona = VOICE_PERSONAS_BY_ID.get(persona_id or "") or VOICE_PERSONAS[0]

    use_piper_model = piper_model or persona["piper_model"]
    use_speaker = int(speaker_id if speaker_id is not None else persona.get("speaker_id", 0))
    use_pitch = float(pitch_semitones if pitch_semitones is not None else persona.get("pitch_semitones", 0.0))
    use_speed = float(speed if speed is not None else persona.get("speed", 1.0))
    use_timbre = timbre or persona.get("timbre", "balanced")
    use_enhance = enhance_preset or persona.get("enhance_preset", "clean_speech")
    edge_voice = "" if prefer_offline else str(persona.get("edge_voice") or "")

    # If user explicitly picked a raw offline Piper model without persona match
    if piper_model and not persona_id:
        edge_voice = ""

    with tempfile.TemporaryDirectory(prefix="om_tts_") as tmpdir:
        tmp_dir = Path(tmpdir)
        raw_audio: Optional[Path] = None
        engine_used = "piper"

        if edge_voice:
            raw_mp3 = tmp_dir / "edge_raw.mp3"
            try:
                _synth_edge_tts_to_file(text, edge_voice, raw_mp3)
                if raw_mp3.exists() and raw_mp3.stat().st_size > 200:
                    raw_audio = raw_mp3
                    engine_used = f"edge-tts ({edge_voice})"
            except Exception as exc:
                LOGGER.warning("Edge TTS failed (%s), falling back to local Piper: %s", edge_voice, exc)

        if raw_audio is None:
            from tools.audio.piper_tts import PiperTTS

            raw_wav = tmp_dir / "piper_raw.wav"
            # Piper supports base speed cleanly, but keep DSP pitch+speed unified
            res = PiperTTS().execute({
                "text": text,
                "model": use_piper_model,
                "speaker_id": use_speaker,
                "length_scale": 1.0,
                "sentence_silence": 0.22,
                "output_path": str(raw_wav),
            })
            if not res.success or not raw_wav.exists():
                raise RuntimeError(res.error or f"PiperTTS failed with model {use_piper_model}")
            raw_audio = raw_wav
            engine_used = f"piper ({use_piper_model}#{use_speaker})"

        _apply_voice_dsp(
            raw_audio_path=raw_audio,
            output_wav=output_wav,
            pitch_semitones=use_pitch,
            speed=use_speed,
            timbre=use_timbre,
            enhance_preset=use_enhance,
        )

    return {
        "persona_id": persona["id"] if persona_id else None,
        "persona_name": persona["name"] if persona_id else use_piper_model,
        "engine_used": engine_used,
        "pitch_semitones": use_pitch,
        "speed": use_speed,
        "timbre": use_timbre,
        "enhance_preset": use_enhance,
    }


# ---------------------------------------------------------------------------
# 750+ OpenMontage Style, Pipeline & Skill Catalog
# ---------------------------------------------------------------------------

CATALOG_CATEGORIES = [
    {"id": "all", "label": "🌐 Tất cả thư viện"},
    {"id": "style-palette", "label": "🎨 Phong Cách & Bảng Màu"},
    {"id": "pipeline", "label": "🎬 Pipeline Sản Xuất"},
    {"id": "motion-kinetic", "label": "✨ Motion & Kinetic Typo"},
    {"id": "chart-data", "label": "📊 Biểu Đồ & Số Liệu"},
    {"id": "cinematic-color", "label": "🎥 Điện Ảnh & Chỉnh Màu"},
    {"id": "code-diagram", "label": "💻 Code & Sơ Đồ"},
    {"id": "audio-voice", "label": "🎙️ Âm Thanh & Nhạc"},
    {"id": "3d-vfx", "label": "🧊 3D, VFX & Avatar"},
    {"id": "ai-workflow", "label": "🤖 Kỹ Năng AI & Workflow"},
]
CATEGORY_LABELS = {c["id"]: c["label"] for c in CATALOG_CATEGORIES}


def _make_theme_config(
    primary: str,
    accent: str,
    bg: str,
    text: str,
    surface: str | None = None,
    muted: str = "#94A3B8",
    heading_font: str = "Inter",
    body_font: str = "Inter",
    mono_font: str = "JetBrains Mono",
    chart_colors: list[str] | None = None,
    pace: str = "moderate",
) -> dict[str, Any]:
    surf = surface or bg
    is_light = bg.upper() in ("#FFFFFF", "#FAFAFA", "#F9FAFB", "#FFFBEB", "#F8FAFC", "#F1F5F9")
    spring = (
        {"damping": 12, "stiffness": 90, "mass": 1}
        if pace == "fast"
        else {"damping": 24, "stiffness": 140, "mass": 1}
        if pace == "slow"
        else {"damping": 18, "stiffness": 120, "mass": 1}
    )
    return {
        "primaryColor": primary,
        "accentColor": accent,
        "backgroundColor": bg,
        "surfaceColor": surf,
        "textColor": text,
        "mutedTextColor": muted if not is_light else "#475569",
        "headingFont": heading_font,
        "bodyFont": body_font,
        "monoFont": mono_font,
        "chartColors": chart_colors or [primary, accent, "#10B981", "#8B5CF6", "#F59E0B"],
        "springConfig": spring,
        "transitionDuration": 0.3 if pace == "fast" else (0.55 if pace == "slow" else 0.4),
        "captionHighlightColor": accent,
        "captionBackgroundColor": "rgba(255, 255, 255, 0.88)" if is_light else "rgba(15, 23, 42, 0.78)",
    }


CURATED_STYLE_PRESETS: list[dict[str, Any]] = [
    ("flat-motion-graphics", "Flat Motion Graphics", "Khối màu hiện đại chuyển động nảy cho TikTok, Startup & Explainer", "flat-motion-graphics", "#7C3AED", "#EC4899", "#0F172A", "#F8FAFC", "Space Grotesk", "fast", "high_contrast", "broadcast"),
    ("clean-professional", "Clean Professional", "Tối giản sáng sủa chuẩn doanh nghiệp, giáo dục & SaaS", "clean-professional", "#2563EB", "#F59E0B", "#FFFFFF", "#1F2937", "Inter", "moderate", "bright_clean", "clean_speech"),
    ("anime-ghibli", "Anime Ghibli Warm", "Sắc vàng ấm áp nhẹ nhàng phong cách hoạt hình Nhật Bản", "anime-ghibli", "#D97706", "#059669", "#FFFBEB", "#1C1917", "Plus Jakarta Sans", "slow", "cinematic_warm", "podcast"),
    ("minimalist-diagram", "Minimalist Diagram", "Đường nét kỹ thuật tinh gọn cho sơ đồ hệ thống & AI", "minimalist-diagram", "#0EA5E9", "#10B981", "#0B1120", "#F1F5F9", "JetBrains Mono", "moderate", "neutral", "clean_speech"),
    ("neon-cyberpunk", "Neon Cyberpunk 2077", "Tím hồng rực rỡ trên nền đêm đô thị tương lai", "flat-motion-graphics", "#00F5D4", "#F72585", "#09021A", "#F8FAFC", "Space Grotesk", "fast", "cinematic_cool", "broadcast"),
    ("cinema-teal-orange", "Điện Ảnh Teal & Orange", "Tông màu rạp phim Hollywood tương phản xanh - cam", "flat-motion-graphics", "#06B6D4", "#FB923C", "#091116", "#F8FAFC", "Inter", "slow", "cinematic_cool", "podcast"),
    ("tiktok-viral-pop", "TikTok Viral Pop", "Chữ lớn, màu chanh & tím bùng nổ giữ chân người xem", "flat-motion-graphics", "#A3E635", "#EC4899", "#111827", "#FFFFFF", "Space Grotesk", "fast", "high_contrast", "voice_clarity"),
    ("dark-thriller-recap", "Review Phim Bí Ẩn", "Nền tối sâu + đỏ rực cho tóm tắt phim trinh thám & kinh dị", "flat-motion-graphics", "#EF4444", "#F59E0B", "#08090C", "#F1F5F9", "Inter", "fast", "moody_dark", "broadcast"),
    ("retro-vintage-film", "Hoài Cổ Vintage 1990", "Tông nâu ấm dịu nhẹ phong cách cuộn phim ký ức", "anime-ghibli", "#B45309", "#DC2626", "#1F1610", "#FEF3C7", "Georgia", "slow", "vintage_film", "podcast"),
    ("bloomberg-finance", "Tài Chính & Chứng Khoán", "Xanh dương đậm kết hợp xanh tăng trưởng cho báo cáo kinh tế", "clean-professional", "#38BDF8", "#22C55E", "#0A192F", "#E2E8F0", "Inter", "moderate", "neutral", "broadcast"),
    ("medical-clean-white", "Y Tế & Khoa Học Sáng", "Trắng thanh khiết cùng xanh ngọc tin cậy cao", "clean-professional", "#0D9488", "#0284C7", "#F8FAFC", "#0F172A", "Inter", "moderate", "bright_clean", "clean_speech"),
    ("matrix-terminal", "Matrix Hacker Terminal", "Xanh lá huỳnh quang trên nền đen tuyệt đối cho lập trình", "minimalist-diagram", "#22C55E", "#4ADE80", "#020603", "#DCFCE7", "Fira Code", "fast", "high_contrast", "voice_clarity"),
    ("luxury-gold-noir", "Xa Xỉ Gold & Đen Tuyền", "Vàng kim tuyến sang trọng cho bất động sản & đồng hồ", "clean-professional", "#EAB308", "#FDE047", "#0C0A09", "#FAFAF9", "Georgia", "slow", "moody_dark", "podcast"),
    ("pastel-education", "Giáo Dục Pastel Dịu Mắt", "Tông màu nhẹ nhàng dễ đọc cho bài giảng trực tuyến", "clean-professional", "#6366F1", "#F472B6", "#F8FAFC", "#1E293B", "Plus Jakarta Sans", "moderate", "bright_clean", "clean_speech"),
    ("esports-gaming-rgb", "Gaming Esports RGB", "Đỏ - Tím rực lửa chuẩn giải đấu game chuyên nghiệp", "flat-motion-graphics", "#F43F5E", "#8B5CF6", "#09090B", "#FAFAFA", "Space Grotesk", "fast", "high_contrast", "broadcast"),
    ("sunset-vlog-warm", "Vlog Hoàng Hôn Ấm Áp", "Cam đào rạng rỡ phù hợp du lịch, ẩm thực & lifestyle", "anime-ghibli", "#FB923C", "#F43F5E", "#1C1210", "#FFF7ED", "Plus Jakarta Sans", "moderate", "cinematic_warm", "clean_speech"),
    ("nordic-minimal", "Tối Giản Bắc Âu", "Nền xám sáng thanh lịch kiểu kiến trúc Scandinavia", "clean-professional", "#0F172A", "#64748B", "#F1F5F9", "#0F172A", "Inter", "slow", "neutral", "clean_speech"),
    ("nature-eco-green", "Thiên Nhiên & Sinh Thái", "Xanh lá rừng tươi mát cho nông nghiệp, du lịch xanh", "anime-ghibli", "#16A34A", "#84CC16", "#071A0E", "#ECFDF5", "Inter", "moderate", "cinematic_warm", "podcast"),
    ("scifi-hud-blue", "Sci-Fi HUD Tương Lai", "Giao diện kính quang học xanh băng cho AI & robot", "minimalist-diagram", "#22D3EE", "#3B82F6", "#030B17", "#E0F2FE", "JetBrains Mono", "fast", "cinematic_cool", "broadcast"),
    ("ocean-deep-blue", "Đại Dương Sâu Thẳm", "Xanh navy kết hợp ngọc bích êm dịu dễ tập trung", "clean-professional", "#06B6D4", "#38BDF8", "#061529", "#F0F9FF", "Inter", "moderate", "cinematic_cool", "clean_speech"),
    ("academic-research", "Nghiên Cứu Học Thuật", "Đỏ đô trang nghiêm trên giấy kem chuẩn đại học", "clean-professional", "#991B1B", "#D97706", "#FAF8F5", "#1C1917", "Georgia", "slow", "neutral", "clean_speech"),
    ("late-night-podcast", "Podcast Đêm Trầm Lắng", "Nền tím than ấm nhẹ ánh đèn studio buổi đêm", "clean-professional", "#A78BFA", "#FBBF24", "#120F1D", "#EDE9FE", "Inter", "slow", "cinematic_warm", "podcast"),
    ("kids-rainbow-play", "Thiếu Nhi Cầu Vồng", "Màu sắc rực rỡ vui nhộn dành cho kênh giải trí bé", "flat-motion-graphics", "#F59E0B", "#EC4899", "#1E1B4B", "#FFFFFF", "Plus Jakarta Sans", "fast", "bright_clean", "voice_clarity"),
    ("film-noir-bw", "Đen Trắng Film Noir", "Tương phản cao cổ điển không màu cho nghệ thuật", "clean-professional", "#E5E5E5", "#A3A3A3", "#090909", "#FAFAFA", "Georgia", "slow", "moody_dark", "podcast"),
    ("startup-pitch-bold", "Gọi Vốn Startup Pitch", "Xanh dương sáng & cam năng động thuyết phục nhà đầu tư", "flat-motion-graphics", "#3B82F6", "#F97316", "#0F172A", "#FFFFFF", "Space Grotesk", "fast", "bright_clean", "broadcast"),
    ("ai-neural-violet", "Mạng Nơ-ron AI Tím", "Tím indigo và cyan sắc nét cho chủ đề trí tuệ nhân tạo", "minimalist-diagram", "#8B5CF6", "#22D3EE", "#0B0A1F", "#F5F3FF", "Space Grotesk", "moderate", "cinematic_cool", "clean_speech"),
    ("vn-heritage-gold", "Di Sản Việt Nam Ấm Áp", "Đỏ sơn mài & vàng đồng sang trọng mang đậm bản sắc Việt", "anime-ghibli", "#DC2626", "#FBBF24", "#1F0B0B", "#FEF2F2", "Georgia", "moderate", "cinematic_warm", "podcast"),
    ("real-estate-marble", "Bất Động Sản Đẳng Cấp", "Xanh lục bảo & vàng đồng cho dự án nhà đất cao cấp", "clean-professional", "#10B981", "#FBBF24", "#061A14", "#ECFDF5", "Inter", "slow", "cinematic_warm", "clean_speech"),
    ("fitness-adrenaline", "Thể Thao & Gym Năng Lượng", "Cam lửa rực cháy & đen carbon nhịp cắt cực nhanh", "flat-motion-graphics", "#F97316", "#EF4444", "#0C0A09", "#FFFFFF", "Space Grotesk", "fast", "high_contrast", "broadcast"),
    ("minimal-slate-ui", "Studio Slate Pro", "Màu xám đá phiến cân bằng đặc trưng của OpenMontage Studio", "flat-motion-graphics", "#22D3EE", "#34D399", "#0F172A", "#F1F5F9", "Inter", "moderate", "neutral", "clean_speech"),
]

_CURATED_ITEMS_BUILT: list[dict[str, Any]] = []
for row in CURATED_STYLE_PRESETS:
    slug, title, desc, base_theme, pri, acc, bg, txt, font, pace, cgrade, apreset = row
    tcfg = _make_theme_config(pri, acc, bg, txt, heading_font=font, body_font=font, pace=pace)
    _CURATED_ITEMS_BUILT.append({
        "id": f"style--{slug}",
        "title": title,
        "vi_title": title,
        "category": "style-palette",
        "category_label": CATEGORY_LABELS["style-palette"],
        "summary": desc,
        "source_file": f"styles/{base_theme}.yaml",
        "style_config": {
            "theme": base_theme,
            "themeConfig": tcfg,
            "pace": pace,
            "color_grade": cgrade,
            "color_intensity": 0.75,
            "audio_preset": apreset,
            "caption_style": {"fontSize": 54, "highlightColor": acc, "wordsPerPage": 5},
        },
        "sample_cut": {
            "type": "hero_title",
            "text": title,
            "subtitle": desc,
            "backgroundColor": bg,
            "accentColor": pri,
            "narration": f"Đây là phong cách {title} với bảng màu và nhịp điệu đặc trưng.",
        },
    })


_CATALOG_CACHE: Optional[list[dict[str, Any]]] = None


def _classify_skill_path(rel_path: str, text_lower: str) -> tuple[str, tuple[str, str, str, str]]:
    p = rel_path.lower()
    if any(k in p for k in ("chart", "stat", "data", "d3-viz", "kpi")):
        return "chart-data", ("#38BDF8", "#34D399", "#0A192F", "#F0F9FF")
    if any(k in p for k in ("code", "diagram", "mermaid", "terminal")):
        return "code-diagram", ("#22C55E", "#22D3EE", "#07111E", "#ECFDF5")
    if any(k in p for k in ("cinematic", "color", "broll", "film", "camera", "montage", "video-edit")):
        return "cinematic-color", ("#F59E0B", "#06B6D4", "#090D16", "#F8FAFC")
    if any(k in p for k in ("tts", "voice", "audio", "music", "sound", "whisper", "subtitle", "elevenlabs", "doubao", "fish", "suno", "acestep", "lyria")):
        return "audio-voice", ("#EC4899", "#A78BFA", "#160D24", "#FDF4FF")
    if any(k in p for k in ("3d", "character", "avatar", "lip-sync", "faceswap", "talking-head", "upscale", "bg-remove", "face-restore", "comfyui", "flux", "kling", "minimax", "ltx2", "pika", "manim")):
        return "3d-vfx", ("#A855F7", "#22D3EE", "#0E0720", "#F5F3FF")
    if any(k in p for k in ("motion", "kinetic", "gsap", "hyperframes", "remotion", "framer", "lottie", "typo", "logo", "lower-third", "news", "tweet", "animation", "draw", "map")):
        return "motion-kinetic", ("#7C3AED", "#EC4899", "#0F172A", "#F8FAFC")
    return "ai-workflow", ("#22D3EE", "#10B981", "#0F172A", "#F1F5F9")


def _extract_md_title_summary(path: Path) -> tuple[str, str]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")[:3500]
    except Exception:
        return path.stem.replace("-", " ").title(), ""

    title = ""
    desc = ""
    # Check YAML frontmatter if present
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            fm, body = parts[1], parts[2]
            m_name = re.search(r"(?m)^name:\s*(.+)$", fm)
            m_desc = re.search(r"(?m)^description:\s*(.+)$", fm)
            if m_name:
                title = m_name.group(1).strip().strip("\"'")
            if m_desc:
                desc = m_desc.group(1).strip().strip("\"'")
        else:
            body = raw
    else:
        body = raw

    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("#") and not title:
            title = ln.lstrip("# ").strip()
            break

    if not desc:
        for ln in lines:
            if not ln.startswith(("#", "```", "|", "-", "*", ">")) and len(ln) > 16:
                desc = ln
                break

    if not title:
        parent = path.parent.name
        stem = path.stem
        title = f"{parent} / {stem}" if stem in ("SKILL", "module", "index") else stem.replace("-", " ").replace("_", " ").title()
    return title[:80], desc[:170]


def _make_sample_cut_for_category(cat: str, title: str, summary: str, bg: str, pri: str, acc: str) -> dict[str, Any]:
    if cat == "chart-data":
        return {
            "type": "bar_chart",
            "title": title[:48],
            "chartData": [
                {"label": "Giai đoạn A", "value": 45},
                {"label": "Giai đoạn B", "value": 78},
                {"label": "Giai đoạn C", "value": 96},
            ],
            "chartColors": [pri, acc, "#10B981"],
            "showGrid": True,
            "showValues": True,
            "backgroundColor": bg,
            "narration": summary or f"Minh họa biểu đồ theo phong cách {title}.",
        }
    if cat == "code-diagram":
        return {
            "type": "callout",
            "title": title[:48],
            "text": summary or "Mẫu thẻ nhấn kỹ thuật OpenMontage.",
            "callout_type": "info",
            "accentColor": pri,
            "backgroundColor": bg,
            "color": "#F8FAFC",
            "narration": summary or f"Trình bày kỹ thuật với {title}.",
        }
    if cat == "motion-kinetic":
        return {
            "type": "stat_card",
            "stat": title.split()[0][:10].upper(),
            "subtitle": f"{title} — {summary[:70]}",
            "accentColor": pri,
            "backgroundColor": bg,
            "narration": summary or f"Hiệu ứng chuyển động {title}.",
        }
    return {
        "type": "hero_title",
        "text": title[:54],
        "subtitle": summary[:90] or "Mẫu thiết kế từ thư viện OpenMontage",
        "backgroundColor": bg,
        "accentColor": pri,
        "narration": summary or f"Cảnh giới thiệu phong cách {title}.",
    }


def _scan_full_catalog() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = list(_CURATED_ITEMS_BUILT)
    seen_paths: set[str] = set()

    # 1) Playbook YAMLs in styles/
    for yml in sorted(STYLES_DIR.rglob("*.yaml")):
        rel = yml.relative_to(REPO_ROOT).as_posix()
        seen_paths.add(rel)
        try:
            pb = yaml.safe_load(yml.read_text(encoding="utf-8", errors="replace")) or {}
        except Exception:
            pb = {}
        ident = pb.get("identity") or {}
        vl = (pb.get("visual_language") or {}).get("color_palette") or {}
        pri_raw = vl.get("primary") or ["#22D3EE"]
        acc_raw = vl.get("accent") or ["#34D399"]
        pri = pri_raw[0] if isinstance(pri_raw, list) else str(pri_raw)
        acc = acc_raw[0] if isinstance(acc_raw, list) else str(acc_raw)
        bg = str(vl.get("background") or "#0F172A")
        txt = str(vl.get("text") or "#F8FAFC")
        pace = str(ident.get("pace") or "moderate")
        name = str(ident.get("name") or yml.stem)
        best_for = str(ident.get("best_for") or "OpenMontage Style Playbook YAML")
        tcfg = _make_theme_config(pri, acc, bg, txt, pace=pace)
        items.append({
            "id": f"pb--{yml.stem}",
            "title": f"[Playbook] {name}",
            "vi_title": f"Playbook: {name}",
            "category": "style-palette",
            "category_label": CATEGORY_LABELS["style-palette"],
            "summary": best_for,
            "source_file": rel,
            "style_config": {
                "theme": yml.stem,
                "themeConfig": tcfg,
                "pace": pace,
                "color_grade": "neutral",
                "color_intensity": 0.7,
                "audio_preset": "clean_speech",
                "caption_style": {"fontSize": 54, "highlightColor": acc, "wordsPerPage": 5},
            },
            "sample_cut": _make_sample_cut_for_category("style-palette", name, best_for, bg, pri, acc),
        })

    # 2) Production Pipelines in pipeline_defs/
    if PIPELINE_DEFS_DIR.exists():
        for pyml in sorted(PIPELINE_DEFS_DIR.glob("*.yaml")):
            rel = pyml.relative_to(REPO_ROOT).as_posix()
            seen_paths.add(rel)
            try:
                pdata = yaml.safe_load(pyml.read_text(encoding="utf-8", errors="replace")) or {}
            except Exception:
                pdata = {}
            pname = str(pdata.get("name") or pyml.stem)
            pdesc = str(pdata.get("description") or "").strip().replace("\n", " ")[:180]
            rec = ((pdata.get("compatible_playbooks") or {}).get("recommended") or ["flat-motion-graphics"])[0]
            matching = next((x for x in _CURATED_ITEMS_BUILT if rec in x["id"]), _CURATED_ITEMS_BUILT[0])
            scfg = matching["style_config"]
            pal = scfg["themeConfig"]
            items.append({
                "id": f"pipe--{pyml.stem}",
                "title": f"Pipeline: {pname}",
                "vi_title": f"Quy trình: {pname}",
                "category": "pipeline",
                "category_label": CATEGORY_LABELS["pipeline"],
                "summary": pdesc or f"Quy trình dựng phim chuẩn OpenMontage ({pname})",
                "source_file": rel,
                "style_config": scfg,
                "sample_cut": _make_sample_cut_for_category(
                    "pipeline", pname.title(), pdesc, pal["backgroundColor"], pal["primaryColor"], pal["accentColor"]
                ),
            })

    # 3) All skills/ & .agents/skills/ markdown modules (~738+ files)
    scan_roots = [SKILLS_DIR, AGENTS_SKILLS_DIR]
    for root in scan_roots:
        if not root.exists():
            continue
        for md in sorted(root.rglob("*.md")):
            rel = md.relative_to(REPO_ROOT).as_posix()
            if rel in seen_paths:
                continue
            seen_paths.add(rel)
            title, desc = _extract_md_title_summary(md)
            cat, (pri, acc, bg, txt) = _classify_skill_path(rel, (title + " " + desc).lower())
            # Subtle hue shift derived from path hash so adjacent cards look varied
            h = int(hashlib.md5(rel.encode("utf-8")).hexdigest()[:4], 16)
            palettes = [
                (pri, acc, bg, txt),
                (acc, pri, bg, txt),
                ("#22D3EE", "#A78BFA", "#0B1120", "#F8FAFC"),
                ("#F59E0B", "#EC4899", "#14111E", "#F8FAFC"),
                ("#34D399", "#38BDF8", "#0A171C", "#F0FDF4"),
            ]
            c_pri, c_acc, c_bg, c_txt = palettes[h % len(palettes)]
            pace = "fast" if cat in ("motion-kinetic", "chart-data") else ("slow" if cat == "cinematic-color" else "moderate")
            cgrade = "cinematic_warm" if cat == "cinematic-color" else ("high_contrast" if cat == "motion-kinetic" else "neutral")
            tcfg = _make_theme_config(c_pri, c_acc, c_bg, c_txt, pace=pace)
            item_id = "mod--" + re.sub(r"[^a-zA-Z0-9_-]+", "-", rel[:-3]).strip("-")
            items.append({
                "id": item_id[:96],
                "title": title,
                "vi_title": title,
                "category": cat,
                "category_label": CATEGORY_LABELS[cat],
                "summary": desc or f"Kỹ năng OpenMontage ({rel})",
                "source_file": rel,
                "style_config": {
                    "theme": "flat-motion-graphics" if pace == "fast" else "clean-professional",
                    "themeConfig": tcfg,
                    "pace": pace,
                    "color_grade": cgrade,
                    "color_intensity": 0.65,
                    "audio_preset": "clean_speech",
                    "caption_style": {"fontSize": 52, "highlightColor": c_acc, "wordsPerPage": 5},
                },
                "sample_cut": _make_sample_cut_for_category(cat, title, desc, c_bg, c_pri, c_acc),
            })

    return items


def get_catalog_items() -> list[dict[str, Any]]:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        _CATALOG_CACHE = _scan_full_catalog()
    return _CATALOG_CACHE


def query_catalog(
    category: str = "all",
    q: str = "",
    page: int = 1,
    page_size: int = 48,
) -> dict[str, Any]:
    items = get_catalog_items()

    counts: dict[str, int] = {"all": len(items)}
    for item in items:
        c = item["category"]
        counts[c] = counts.get(c, 0) + 1

    categories_out = [
        {"id": c["id"], "label": c["label"], "count": counts.get(c["id"], 0)}
        for c in CATALOG_CATEGORIES
    ]

    filtered = items
    if category and category != "all":
        filtered = [x for x in filtered if x["category"] == category]
    query_text = (q or "").strip().lower()
    if query_text:
        filtered = [
            x
            for x in filtered
            if query_text in x["title"].lower()
            or query_text in x["summary"].lower()
            or query_text in x["source_file"].lower()
            or query_text in x["category_label"].lower()
        ]

    total_filtered = len(filtered)
    p = max(1, int(page))
    ps = max(12, min(120, int(page_size)))
    start = (p - 1) * ps
    page_items = filtered[start : start + ps]
    total_pages = max(1, (total_filtered + ps - 1) // ps)

    return {
        "ok": True,
        "total": len(items),
        "filtered_total": total_filtered,
        "page": p,
        "page_size": ps,
        "total_pages": total_pages,
        "categories": categories_out,
        "items": page_items,
    }


def get_catalog_item(item_id: str) -> Optional[dict[str, Any]]:
    for item in get_catalog_items():
        if item["id"] == item_id:
            return item
    return None


def get_catalog_item_source(item: dict[str, Any]) -> str:
    rel = item.get("source_file") or ""
    p = (REPO_ROOT / rel).resolve()
    try:
        p.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return ""
    if not p.exists() or not p.is_file():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")[:12000]


def render_catalog_thumb_svg(item: dict[str, Any]) -> str:
    """Generate a distinctive 320x180 SVG preview thumbnail illustrating each style/module."""
    cfg = item.get("style_config") or {}
    tc = cfg.get("themeConfig") or {}
    bg = html.escape(str(tc.get("backgroundColor") or "#0F172A"), quote=True)
    pri = html.escape(str(tc.get("primaryColor") or "#22D3EE"), quote=True)
    acc = html.escape(str(tc.get("accentColor") or "#EC4899"), quote=True)
    txt = html.escape(str(tc.get("textColor") or "#F8FAFC"), quote=True)
    charts = tc.get("chartColors") or [pri, acc, "#34D399"]
    c3 = html.escape(str(charts[2] if len(charts) > 2 else acc), quote=True)
    cat = item.get("category") or "style-palette"
    title = html.escape((item.get("title") or "Style")[:28], quote=True)
    badge = html.escape(cat.upper()[:16], quote=True)
    pace = html.escape(str(cfg.get("pace") or "moderate").upper(), quote=True)

    if cat == "chart-data":
        artwork = f"""
        <rect x="28" y="96" width="28" height="44" rx="4" fill="{pri}" opacity="0.9"/>
        <rect x="66" y="72" width="28" height="68" rx="4" fill="{acc}" opacity="0.95"/>
        <rect x="104" y="56" width="28" height="84" rx="4" fill="{c3}" opacity="0.9"/>
        <circle cx="235" cy="96" r="34" fill="none" stroke="{pri}" stroke-width="12" stroke-dasharray="145 70"/>
        <circle cx="235" cy="96" r="34" fill="none" stroke="{acc}" stroke-width="12" stroke-dasharray="60 160" stroke-dashoffset="-145"/>
        <text x="235" y="101" text-anchor="middle" fill="{txt}" font-size="14" font-weight="700" font-family="sans-serif">88%</text>
        """
    elif cat == "code-diagram":
        artwork = f"""
        <rect x="24" y="48" width="272" height="96" rx="8" fill="#05080F" stroke="{pri}" stroke-opacity="0.55"/>
        <circle cx="40" cy="62" r="4" fill="#FB7185"/><circle cx="54" cy="62" r="4" fill="#FBBF24"/><circle cx="68" cy="62" r="4" fill="#34D399"/>
        <rect x="40" y="78" width="120" height="8" rx="3" fill="{pri}" opacity="0.85"/>
        <rect x="52" y="94" width="180" height="8" rx="3" fill="{acc}" opacity="0.85"/>
        <rect x="52" y="110" width="140" height="8" rx="3" fill="{txt}" opacity="0.55"/>
        <rect x="40" y="126" width="90" height="8" rx="3" fill="{c3}" opacity="0.85"/>
        """
    elif cat == "cinematic-color":
        artwork = f"""
        <rect x="0" y="0" width="320" height="22" fill="#000000" opacity="0.75"/>
        <rect x="0" y="158" width="320" height="22" fill="#000000" opacity="0.75"/>
        <circle cx="245" cy="88" r="46" fill="{acc}" opacity="0.22"/>
        <circle cx="95" cy="96" r="54" fill="{pri}" opacity="0.22"/>
        <rect x="24" y="54" width="205" height="6" rx="3" fill="{pri}"/>
        <text x="24" y="92" fill="{txt}" font-size="20" font-weight="800" font-family="sans-serif">{title}</text>
        <rect x="24" y="114" width="60" height="10" rx="2" fill="{pri}"/>
        <rect x="90" y="114" width="60" height="10" rx="2" fill="{acc}"/>
        <rect x="156" y="114" width="60" height="10" rx="2" fill="{c3}"/>
        """
    elif cat == "audio-voice":
        bars = []
        heights = [18, 36, 54, 28, 66, 44, 72, 34, 58, 40, 62, 24, 48, 30, 20]
        for i, h in enumerate(heights):
            bx = 34 + i * 17
            by = 98 - h // 2
            col = pri if i % 2 == 0 else acc
            bars.append(f'<rect x="{bx}" y="{by}" width="10" height="{h}" rx="5" fill="{col}" opacity="0.9"/>')
        artwork = "".join(bars)
    elif cat == "3d-vfx":
        artwork = f"""
        <polygon points="160,44 218,74 218,128 160,154 102,128 102,74" fill="{pri}" fill-opacity="0.16" stroke="{pri}" stroke-width="2.5"/>
        <line x1="160" y1="44" x2="160" y2="100" stroke="{acc}" stroke-width="2"/>
        <line x1="218" y1="74" x2="160" y2="100" stroke="{acc}" stroke-width="2"/>
        <line x1="102" y1="74" x2="160" y2="100" stroke="{acc}" stroke-width="2"/>
        <text x="160" y="146" text-anchor="middle" fill="{txt}" font-size="13" font-weight="700" font-family="sans-serif">{title}</text>
        """
    else:
        artwork = f"""
        <rect x="22" y="54" width="6" height="58" rx="3" fill="{pri}"/>
        <text x="38" y="84" fill="{txt}" font-size="19" font-weight="800" font-family="sans-serif">{title}</text>
        <rect x="38" y="96" width="165" height="8" rx="4" fill="{acc}" opacity="0.85"/>
        <rect x="38" y="114" width="115" height="6" rx="3" fill="{txt}" opacity="0.35"/>
        <rect x="220" y="54" width="76" height="72" rx="10" fill="{pri}" fill-opacity="0.18" stroke="{acc}" stroke-width="1.5"/>
        <polygon points="250,76 250,104 274,90" fill="{acc}"/>
        """

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">
  <defs>
    <radialGradient id="g" cx="80%" cy="20%" r="75%">
      <stop offset="0%" stop-color="{pri}" stop-opacity="0.28"/>
      <stop offset="100%" stop-color="{bg}" stop-opacity="1"/>
    </radialGradient>
  </defs>
  <rect width="320" height="180" fill="{bg}"/>
  <rect width="320" height="180" fill="url(#g)"/>
  <g stroke="{txt}" stroke-opacity="0.06" stroke-width="1">
    <line x1="0" y1="45" x2="320" y2="45"/><line x1="0" y1="90" x2="320" y2="90"/><line x1="0" y1="135" x2="320" y2="135"/>
    <line x1="80" y1="0" x2="80" y2="180"/><line x1="160" y1="0" x2="160" y2="180"/><line x1="240" y1="0" x2="240" y2="180"/>
  </g>
  <rect x="14" y="12" width="124" height="20" rx="10" fill="{pri}" fill-opacity="0.2" stroke="{pri}" stroke-opacity="0.5"/>
  <text x="24" y="26" fill="{txt}" font-size="10" font-weight="700" font-family="sans-serif">{badge}</text>
  <rect x="242" y="12" width="64" height="20" rx="10" fill="{acc}" fill-opacity="0.22"/>
  <text x="274" y="26" text-anchor="middle" fill="{txt}" font-size="9.5" font-weight="700" font-family="sans-serif">{pace}</text>
  {artwork}
  <circle cx="24" cy="162" r="6" fill="{pri}"/>
  <circle cx="42" cy="162" r="6" fill="{acc}"/>
  <circle cx="60" cy="162" r="6" fill="{c3}"/>
  <circle cx="78" cy="162" r="6" fill="{txt}"/>
</svg>"""


def list_audio_enhance_options() -> list[dict[str, str]]:
    labels = {
        "none": "Không xử lý thêm (Giữ nguyên)",
        "clean_speech": "Clean Speech (Lọc ồn + Nén thoại chuẩn)",
        "voice_clarity": "Voice Clarity (Tăng độ sáng, rõ âm)",
        "podcast": "Podcast (Ấm, êm, đồng đều âm lượng)",
        "broadcast": "Broadcast (Chặt tiếng chuẩn phát thanh)",
        "noise_reduce": "Noise Reduce (Khử tạp âm mạnh)",
        "normalize_only": "Normalize (Chuẩn hóa âm lượng -16 LUFS)",
    }
    out = [{"id": "none", "label": labels["none"]}]
    for k, v in AUDIO_ENHANCE_PRESETS.items():
        out.append({"id": k, "label": labels.get(k, f"{k} — {v['description']}")})
    return out


def list_color_grade_options() -> list[dict[str, str]]:
    labels = {
        "none": "Không áp bộ lọc màu (Mặc định Remotion)",
        "cinematic_warm": "Cinematic Warm (Vàng ấm điện ảnh)",
        "cinematic_cool": "Cinematic Cool (Teal & Orange rạp phim)",
        "moody_dark": "Moody Dark (Tối sâu bí ẩn, kịch tính)",
        "bright_clean": "Bright Clean (Sáng rực, tươi tắn)",
        "vintage_film": "Vintage Film (Phim màu hoài cổ)",
        "high_contrast": "High Contrast (Tương phản mạnh sắc nét)",
        "neutral": "Neutral (Cân bằng nhẹ nhàng)",
    }
    out = [{"id": "none", "label": labels["none"]}]
    for k, v in COLOR_GRADE_PROFILES.items():
        out.append({"id": k, "label": labels.get(k, f"{k} — {v['description']}")})
    return out


# ---------------------------------------------------------------------------
# Typography Studio: 40+ Font Library, 12 Font Pairings & Custom Font Loader
# ---------------------------------------------------------------------------

CUSTOM_FONTS_DIR = REPO_ROOT / "projects" / "_fonts"
ALLOWED_FONT_EXTS = {".ttf", ".otf", ".woff", ".woff2"}

FONT_CATEGORIES: list[dict[str, str]] = [
    {"id": "all", "label": "🌐 Tất cả Font chữ"},
    {"id": "sans-modern", "label": "✨ Hiện đại & Đa dụng (Sans-Serif)"},
    {"id": "display-impact", "label": "🔥 Tiêu đề & TikTok / Poster (Display)"},
    {"id": "serif-editorial", "label": "📰 Báo chí, Điện ảnh & Văn học (Serif)"},
    {"id": "script-handwriting", "label": "✍️ Viết tay & Thư pháp (Script)"},
    {"id": "cute-cartoon", "label": "🧸 Hoạt hình & Vui nhộn (Cartoon)"},
    {"id": "tech-mono", "label": "💻 Công nghệ, Code & Sci-Fi"},
    {"id": "custom", "label": "📁 Font Tải Lên Của Bạn"},
]

_FONT_CAT_LABEL_MAP = {c["id"]: c["label"] for c in FONT_CATEGORIES}


def _make_font(
    fid: str,
    family: str,
    label: str,
    category: str,
    weights: list[str],
    best_for: str,
    sample_text: str,
    fallback: str = "sans-serif",
) -> dict[str, Any]:
    q_family = family.replace(" ", "+")
    wght_str = ";".join(sorted(set(weights), key=lambda w: int(w)))
    google_url = f"https://fonts.googleapis.com/css2?family={q_family}:wght@{wght_str}&display=swap"
    return {
        "id": fid,
        "family": family,
        "label": label,
        "category": category,
        "category_label": _FONT_CAT_LABEL_MAP.get(category, category),
        "vi_support": True,
        "weights": weights,
        "best_for": best_for,
        "sample_text": sample_text,
        "google_css_url": google_url,
        "css_stack": f"'{family}', system-ui, {fallback}",
        "is_custom": False,
        "file_url": None,
    }


FONT_CATALOG: list[dict[str, Any]] = [
    # --- sans-modern (11) ---
    _make_font("be-vietnam-pro", "Be Vietnam Pro", "Be Vietnam Pro — Chuẩn tiếng Việt Hiện đại", "sans-modern", ["400", "600", "700", "800"], "Explainer, Giáo dục, Doanh nghiệp Việt", "Xưởng phim AI OpenMontage — Dựng video tiếng Việt sắc nét từng dấu."),
    _make_font("plus-jakarta-sans", "Plus Jakarta Sans", "Plus Jakarta Sans — Tròn trịa Cao cấp", "sans-modern", ["400", "600", "700", "800"], "Startup SaaS, Tech Review, UI Demo", "Biến ý tưởng thành thước phim chuyên nghiệp ngay trên máy tính cá nhân."),
    _make_font("inter", "Inter", "Inter — Rõ nét Mọi độ phân giải", "sans-modern", ["400", "600", "700", "800"], "Biểu đồ số liệu, Infographic, Phụ đề chuẩn", "Số liệu tăng trưởng 320% với quy trình sản xuất hoàn toàn tự động."),
    _make_font("montserrat", "Montserrat", "Montserrat — Đậm chất Điện ảnh & Quảng cáo", "sans-modern", ["400", "600", "700", "800"], "Trailer, Kênh YouTube lớn, Tiêu đề chính", "CÔNG NGHỆ DỰNG PHIM THẾ HỆ MỚI TRONG TẦM TAY BẠN."),
    _make_font("lexend", "Lexend", "Lexend — Cực dễ đọc cho Phụ đề dài", "sans-modern", ["400", "600", "700"], "Video tài liệu, Hướng dẫn chi tiết, Tin tức", "Tốc độ đọc mượt mà giúp khán giả theo dõi trọn vẹn câu chuyện."),
    _make_font("manrope", "Manrope", "Manrope — Hiện đại Tinh tế", "sans-modern", ["400", "600", "700", "800"], "Tài chính, Podcast Tech, Case Study", "Tối ưu hóa hiệu suất đồ họa GPU RTX với chi phí 0 đồng."),
    _make_font("nunito", "Nunito", "Nunito — Thân thiện Ấm áp", "sans-modern", ["400", "600", "700", "800"], "Chia sẻ kiến thức, Đời sống, Gia đình", "Học dựng video thật nhẹ nhàng cùng trợ lý trí tuệ nhân tạo."),
    _make_font("quicksand", "Quicksand", "Quicksand — Bo tròn Thanh thoát", "sans-modern", ["400", "600", "700"], "Vlog du lịch, Ẩm thực, Phong cách sống", "Một buổi sáng bình yên bên góc làm việc sáng tạo tại nhà."),
    _make_font("work-sans", "Work Sans", "Work Sans — Gọn gàng Chuyên nghiệp", "sans-modern", ["400", "600", "700"], "Báo cáo KPI, Thuyết trình, Bản tin", "Tổng kết quý III: hiệu suất render tăng gấp năm lần."),
    _make_font("rubik", "Rubik", "Rubik — Hình học Độc đáo", "sans-modern", ["400", "600", "700", "800"], "Shorts công nghệ, Gamification, App Review", "Khám phá 780+ phong cách đồ họa chỉ với một cú nhấp chuột."),
    _make_font("mulish", "Mulish", "Mulish — Tối giản Thanh lịch", "sans-modern", ["400", "600", "700"], "Minimalist, Thương hiệu thời trang, Vlog", "Đơn giản là đỉnh cao của sự tinh tế trong từng khung hình."),

    # --- display-impact (9) ---
    _make_font("oswald", "Oswald", "Oswald — Cao thon Giật tít Mạnh", "display-impact", ["400", "600", "700"], "YouTube Thumbnail, Tóm tắt phim, Tin nóng", "BÍ MẬT ĐẰNG SAU CÔNG NGHỆ TẠO VIDEO KHÔNG CẦN STUDIO."),
    _make_font("anton", "Anton", "Anton — Cực dày Gắt TikTok / Reels", "display-impact", ["400"], "Phụ đề TikTok, Hook 3 giây đầu, Shorts viral", "KHÔNG THỂ TIN NỔI! CHẠY 100% LOCAL KHÔNG MẤT PHÍ."),
    _make_font("bebas-neue", "Bebas Neue", "Bebas Neue — Poster Hollywood", "display-impact", ["400"], "Trailer phim, Thể thao, Action Highlights", "OPENMONTAGE STUDIO — PHIÊN BẢN CHUYÊN NGHIỆP 2026."),
    _make_font("bungee", "Bungee", "Bungee — Đô thị Nổi khối Phá cách", "display-impact", ["400"], "Gaming, Hip-hop, Streetwear, Gen-Z Shorts", "CHƠI LỚN CÙNG HỆ THỐNG DỰNG PHIM AI TẠI NHÀ!"),
    _make_font("teko", "Teko", "Teko — Dáng vuông Góc cạnh Thể thao", "display-impact", ["400", "600", "700"], "Esports, Bóng đá, Bảng tỷ số & Highlights", "PHÚT BÙ GIỜ ĐỊNH ĐOẠT CHIẾN THẮNG LỊCH SỬ."),
    _make_font("kanit", "Kanit", "Kanit — Hiện đại Mạnh mẽ Đa năng", "display-impact", ["400", "600", "700", "800"], "Review Xe, Công nghệ, Khởi nghiệp", "Tốc độ, sắc nét và linh hoạt trên mọi tỷ lệ khung hình."),
    _make_font("chakra-petch", "Chakra Petch", "Chakra Petch — Cyberpunk Góc cạnh", "display-impact", ["400", "600", "700"], "Sci-Fi, Cyberpunk, Game UI, AI Future", "KHỞI ĐỘNG HỆ THỐNG RENDER NEURAL TƯƠNG LAI."),
    _make_font("paytone-one", "Paytone One", "Paytone One — Béo tròn Vui nhộn TikTok", "display-impact", ["400"], "Giải trí, Reaction, Mẹo vặt hằng ngày", "3 Mẹo Hay Giúp Video Triệu View Chỉ Trong 5 Phút!"),
    _make_font("alfa-slab-one", "Alfa Slab One", "Alfa Slab One — Chân dày Cổ điển Nổi bật", "display-impact", ["400"], "Poster Retro, Tin chấn động, Tiêu đề lớn", "TUYỆT TÁC ĐIỆN ẢNH TỪ PHÒNG LÀM VIỆC TẠI NHÀ.", "serif"),

    # --- serif-editorial (7) ---
    _make_font("playfair-display", "Playfair Display", "Playfair Display — Sang trọng Thời trang & Nghệ thuật", "serif-editorial", ["400", "600", "700"], "Tạp chí, Thời trang, Collage nghệ thuật", "Vẻ đẹp vượt thời gian trong từng khung hình nghệ thuật.", "serif"),
    _make_font("merriweather", "Merriweather", "Merriweather — Báo chí & Tài liệu Chuyên sâu", "serif-editorial", ["400", "700"], "Phim tài liệu, Lịch sử, Đọc tin chuyên luận", "Hành trình thay đổi lịch sử ngành sáng tạo nội dung số.", "serif"),
    _make_font("lora", "Lora", "Lora — Văn học Truyền cảm Êm ái", "serif-editorial", ["400", "600", "700"], "Kể chuyện đêm khuya, Sách nói, Podcast văn học", "Những trang ký ức dịu dàng vang vọng qua giọng kể trầm ấm.", "serif"),
    _make_font("cormorant-garamond", "Cormorant Garamond", "Cormorant Garamond — Quý phái Châu Âu Cổ điển", "serif-editorial", ["400", "600", "700"], "Luxury Brand, Nước hoa, Kiến trúc, Cổ điển", "Nghệ thuật kể chuyện tinh hoa dành riêng cho người thưởng thức.", "serif"),
    _make_font("noto-serif", "Noto Serif", "Noto Serif — Chuẩn mực Học thuật Tiếng Việt", "serif-editorial", ["400", "600", "700"], "Lịch sử Việt Nam, Truyện dân gian, Sách giáo khoa", "Nghìn năm văn hiến lưu giữ trọn vẹn nét chữ Việt.", "serif"),
    _make_font("spectral", "Spectral", "Spectral — Sách in & Tiểu luận Sắc sảo", "serif-editorial", ["400", "600", "700"], "Video Essay, Phân tích phim, Phê bình", "Góc nhìn sâu sắc phía sau mỗi khung hình điện ảnh.", "serif"),
    _make_font("eb-garamond", "EB Garamond", "EB Garamond — Cổ thư & Sử thi Trang trọng", "serif-editorial", ["400", "600", "700"], "Cổ tích Ông/Bà, Lịch sử cổ đại, Thư pháp in", "Ngày xửa ngày xưa, bên triền sông êm đềm lộng gió...", "serif"),

    # --- script-handwriting (5) ---
    _make_font("dancing-script", "Dancing Script", "Dancing Script — Chữ ký Mềm mại Bay bổng", "script-handwriting", ["400", "600", "700"], "Thiệp mời, Cưới hỏi, Tâm sự tình cảm", "Gửi trọn yêu thương qua từng dòng chữ viết tay mềm mại.", "cursive"),
    _make_font("pacifico", "Pacifico", "Pacifico — Cổ điển Bãi biển Retro Vui tươi", "script-handwriting", ["400"], "Du lịch hè, Quán cafe, Lifestyle phóng khoáng", "Mùa hè rực rỡ cùng những chuyến đi không bao giờ quên.", "cursive"),
    _make_font("great-vibes", "Great Vibes", "Great Vibes — Thư pháp Hoàng gia Lộng lẫy", "script-handwriting", ["400"], "Lời đề tặng, Intro cao cấp, Sự kiện sang trọng", "Trân trọng kính chào quý khán giả thân mến.", "cursive"),
    _make_font("patrick-hand", "Patrick Hand", "Patrick Hand — Ghi chú Sổ tay Tự nhiên", "script-handwriting", ["400"], "Whiteboard, Ghi chú bài giảng, Sổ tay", "Ghi chú nhanh 5 bí quyết học tập hiệu quả mỗi ngày!", "cursive"),
    _make_font("caveat", "Caveat", "Caveat — Bút lông Nhanh nhẹn Hiện đại", "script-handwriting", ["400", "600", "700"], "Chú thích mũi tên, Social Quote, Callout", "Mẹo nhỏ cực hay mà ai cũng nên biết ngay hôm nay!", "cursive"),

    # --- cute-cartoon (4) ---
    _make_font("baloo-2", "Baloo 2", "Baloo 2 — Bầu bĩnh Dễ thương Hoạt hình", "cute-cartoon", ["400", "600", "700", "800"], "Thiếu nhi, Anime dễ thương, Thú cưng", "Cùng bé khám phá thế giới sắc màu kỳ thú mỗi ngày!", "sans-serif"),
    _make_font("fredoka", "Fredoka", "Fredoka — Tròn mũm Đồ chơi Vui nhộn", "cute-cartoon", ["400", "600", "700"], "Kênh thiếu nhi, Game casual, Sticker", "Xin chào các bạn nhỏ! Hôm nay chúng ta chơi gì nào?", "sans-serif"),
    _make_font("mali", "Mali", "Mali — Nét viết Vở học trò Tinh nghịch", "cute-cartoon", ["400", "600", "700"], "Truyện tranh, Học đường, Nhật ký đáng yêu", "Nhật ký mùa hè đáng nhớ của hội bạn thân lớp mình.", "cursive"),
    _make_font("comfortaa", "Comfortaa", "Comfortaa — Hình tròn Nhẹ nhàng Hiện đại", "cute-cartoon", ["400", "600", "700"], "Spa, Sức khỏe, Chữa lành, Minimal Cute", "Thư giãn tâm trí và lắng nghe những thanh âm dịu êm.", "sans-serif"),

    # --- tech-mono (4) ---
    _make_font("space-grotesk", "Space Grotesk", "Space Grotesk — Mặc định Studio Công nghệ AI", "tech-mono", ["400", "600", "700"], "AI Explainer, Motion Graphics, Robotics", "KIẾN TRÚC PIPELINE ĐỒ HỌA CHUYỂN ĐỘNG THẾ HỆ MỚI.", "sans-serif"),
    _make_font("jetbrains-mono", "JetBrains Mono", "JetBrains Mono — Lập trình & Terminal Chuẩn", "tech-mono", ["400", "600", "700"], "Code Card, Terminal CLI, Dev Tutorial", "const video = await renderOpenMontage({ fps: 30 });", "monospace"),
    _make_font("fira-code", "Fira Code", "Fira Code — Mã nguồn & Giao diện Hacker", "tech-mono", ["400", "600", "700"], "Coding Tutorial, Cybersecurity, Tech Blog", "def synthesize_voice(persona='vi-nam-minh'): return True", "monospace"),
    _make_font("orbitron", "Orbitron", "Orbitron — Tàu vũ trụ Sci-Fi Tương lai", "tech-mono", ["400", "600", "700", "800"], "Sci-Fi HUD, AI Robot, Gaming Intro", "SYSTEM ONLINE // RTX GPU ACCELERATION ACTIVE.", "sans-serif"),
]

FONT_PAIRINGS: list[dict[str, str]] = [
    {
        "id": "pair-vi-explainer",
        "title": "🎓 Thuyết Minh Tiếng Việt Chuẩn Studio",
        "badge": "Phổ biến nhất",
        "headingFont": "Be Vietnam Pro",
        "bodyFont": "Inter",
        "captionFont": "Plus Jakarta Sans",
        "description": "Rõ dấu tiếng Việt hoàn hảo, cân bằng giữa tiêu đề sắc sảo và nội dung sạch sẽ.",
    },
    {
        "id": "pair-tiktok-viral",
        "title": "⚡ TikTok / Reels Bùng Nổ Viral",
        "badge": "Short-form",
        "headingFont": "Anton",
        "bodyFont": "Kanit",
        "captionFont": "Paytone One",
        "description": "Chữ cực dày giật hook 3 giây đầu kết hợp phụ đề bầu bĩnh bắt mắt.",
    },
    {
        "id": "pair-cinema-trailer",
        "title": "🎬 Điện Ảnh Hollywood & Review Phim",
        "badge": "Điện ảnh",
        "headingFont": "Bebas Neue",
        "bodyFont": "Montserrat",
        "captionFont": "Oswald",
        "description": "Dáng chữ poster rạp chiếu phim mạnh mẽ cho trailer và tóm tắt phim kịch tính.",
    },
    {
        "id": "pair-cyberpunk-ai",
        "title": "🤖 Cyberpunk AI & Tương Lai",
        "badge": "Tech / Sci-Fi",
        "headingFont": "Orbitron",
        "bodyFont": "Space Grotesk",
        "captionFont": "Chakra Petch",
        "description": "Đậm chất viễn tưởng HUD, robotics và công nghệ trí tuệ nhân tạo.",
    },
    {
        "id": "pair-luxury-editorial",
        "title": "💎 Sang Trọng Tạp Chí & Thời Trang",
        "badge": "Nghệ thuật",
        "headingFont": "Playfair Display",
        "bodyFont": "Mulish",
        "captionFont": "Cormorant Garamond",
        "description": "Phối Serif quý phái cùng Sans tối giản chuẩn phong cách Vogue / Luxury.",
    },
    {
        "id": "pair-documentary",
        "title": "📰 Tài Liệu Lịch Sử & Thời Sự",
        "badge": "Báo chí",
        "headingFont": "Merriweather",
        "bodyFont": "Lexend",
        "captionFont": "Noto Serif",
        "description": "Độ tin cậy cao, cực kỳ dễ đọc cho phim tài liệu và bình luận chuyên sâu.",
    },
    {
        "id": "pair-fairytale-story",
        "title": "🪔 Kể Chuyện Cổ Tích & Sách Nói",
        "badge": "Truyền cảm",
        "headingFont": "EB Garamond",
        "bodyFont": "Lora",
        "captionFont": "Spectral",
        "description": "Ấm áp, giàu chất văn học cho truyện cổ tích Ông/Bà và audio book.",
    },
    {
        "id": "pair-cute-cartoon",
        "title": "🧸 Hoạt Hình Thiếu Nhi & Vui Nhộn",
        "badge": "Kids / Anime",
        "headingFont": "Baloo 2",
        "bodyFont": "Nunito",
        "captionFont": "Fredoka",
        "description": "Đáng yêu, tròn trịa, thân thiện cho nội dung thiếu nhi, thú cưng & vlog cute.",
    },
    {
        "id": "pair-podcast-chill",
        "title": "🎧 Podcast Chữa Lành & Tâm Sự Đêm",
        "badge": "Chill / ASMR",
        "headingFont": "Dancing Script",
        "bodyFont": "Quicksand",
        "captionFont": "Comfortaa",
        "description": "Nhẹ nhàng, thư thái cho chia sẻ cảm xúc và radio tâm sự cuối ngày.",
    },
    {
        "id": "pair-dev-terminal",
        "title": "💻 Lập Trình, Code Card & Devops",
        "badge": "Developer",
        "headingFont": "Space Grotesk",
        "bodyFont": "JetBrains Mono",
        "captionFont": "Fira Code",
        "description": "Đồng bộ hoàn hảo cho video hướng dẫn code, sơ đồ hệ thống & CLI.",
    },
    {
        "id": "pair-esports-sports",
        "title": "⚽ Thể Thao & Esports Highlights",
        "badge": "Hành động",
        "headingFont": "Teko",
        "bodyFont": "Rubik",
        "captionFont": "Oswald",
        "description": "Góc cạnh tốc độ cao phù hợp bảng tỷ số, reaction trận đấu & highlights.",
    },
    {
        "id": "pair-whiteboard-study",
        "title": "✍️ Sổ Tay Ghi Chú & Bài Giảng",
        "badge": "Học tập",
        "headingFont": "Patrick Hand",
        "bodyFont": "Mali",
        "captionFont": "Caveat",
        "description": "Cảm giác viết tay tự nhiên như bảng trắng lớp học và sổ tay sáng tạo.",
    },
]


def _custom_font_descriptor(path: Path) -> dict[str, Any]:
    stem = path.stem
    pretty = re.sub(r"[_-]+", " ", stem).strip() or stem
    return {
        "id": f"custom-{stem.lower()}",
        "family": stem,
        "label": f"{pretty} (Font tải lên • {path.suffix.lstrip('.').upper()})",
        "category": "custom",
        "category_label": _FONT_CAT_LABEL_MAP["custom"],
        "vi_support": True,
        "weights": ["400", "700"],
        "best_for": "Font tùy chỉnh cá nhân / Thương hiệu riêng",
        "sample_text": "Xin chào! Thử nghiệm font chữ cá nhân hóa trong OpenMontage Studio.",
        "google_css_url": "",
        "css_stack": f"'{stem}', sans-serif",
        "is_custom": True,
        "file_url": f"/api/studio/fonts/file/{path.name}",
    }


def list_studio_fonts() -> list[dict[str, Any]]:
    fonts: list[dict[str, Any]] = []
    if CUSTOM_FONTS_DIR.exists():
        for p in sorted(CUSTOM_FONTS_DIR.iterdir()):
            if p.is_file() and p.suffix.lower() in ALLOWED_FONT_EXTS:
                fonts.append(_custom_font_descriptor(p))
    fonts.extend(FONT_CATALOG)
    return fonts


def save_custom_font_file(filename: str, data: bytes) -> dict[str, Any]:
    raw_name = Path(filename or "custom_font.ttf").name
    ext = Path(raw_name).suffix.lower()
    if ext not in ALLOWED_FONT_EXTS:
        raise ValueError(f"Định dạng font {ext or '(trống)'} chưa hỗ trợ. Hãy chọn .ttf, .otf, .woff hoặc .woff2")
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(raw_name).stem).strip("_") or "custom_font"
    CUSTOM_FONTS_DIR.mkdir(parents=True, exist_ok=True)
    target = CUSTOM_FONTS_DIR / f"{safe_stem}{ext}"
    target.write_bytes(data)
    return _custom_font_descriptor(target)
