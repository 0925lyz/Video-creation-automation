#!/usr/bin/env python3
import asyncio
import csv
import importlib.util
import json
import math
import os
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
BASE_SCRIPT = ROOT / "scripts" / "build_corinthians_rosario_video.py"
spec = importlib.util.spec_from_file_location("jtv_video_base", BASE_SCRIPT)
base = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(base)

PROJECT_NAME = "Arsenal_vs_Coventry_City_260821"
OUT_DIR = ROOT / "workspace" / "jianying_arsenal_coventry_260821"
TTS_DIR = OUT_DIR / "voice_segments"
SCENE_DIR = OUT_DIR / "scene_stills"

DOCX_PATH = Path("/Users/jaguar/Downloads/lyz/Arsenal_vs_Coventry_City_260821_逐字稿.docx")
LOGO_PATH = Path("/Users/jaguar/Downloads/lyz/logo图/20260820-084548.png")
ADBAR_PATH = Path("/Users/jaguar/Downloads/lyz/logo图/20260820-084541.jpg")
POSTER_PATH = Path("/Users/jaguar/Downloads/lyz/Arsenal_vs_Coventry_City_260821_海报.png")
REFERENCE_PATH = Path(
    "/var/folders/xj/yk2515x93hv84d7rltqw9ckh0000gp/T/"
    "codex-clipboard-42545e35-2445-446a-a6b7-1414f3d95ae0.png"
)

SCENE_PLANS = [
    "Abertura com poster Arsenal x Coventry, gancho de armadilha e push-in suave.",
    "Cartao de partida: 21/08, 16:00 Sao Paulo, Emirates Stadium.",
    "Grafico limpo de probabilidades: Arsenal 68%, empate 20%, Coventry City 12%.",
    "Favorito forte com pressao de estreia e necessidade de calma.",
    "Alerta de valor: favorito nao significa aposta boa; visual sem exagero de cassino.",
    "Placar previsto 2-0 com scoreboard moderno.",
    "Interacao: casa, empate ou visitante em tres opcoes.",
    "Escalacao oficial como variavel antes da bola rolar.",
    "Chamada natural para JaguarTV com marca reforcada.",
    "Encerramento responsavel: informacao, nao promessa de lucro.",
]

OFFICIAL_CONTEXT_SOURCES = [
    {
        "source_url": "https://www.arsenal.com/ticket-information/ticket-information-arsenal-v-coventry-city-aqVwT4k98B7Z",
        "source_site": "arsenal.com",
        "use_scene": "fact_check_fixture",
        "note": "Arsenal official ticket page: Emirates Stadium, Friday 21 August, 8 pm UK.",
    },
    {
        "source_url": "https://www.arsenal.com/news/how-to-watch-arsenal-v-coventry-city-live-on-tv-aqj230y5bXCB",
        "source_site": "arsenal.com",
        "use_scene": "fact_check_fixture",
        "note": "Arsenal official watch page: Coventry at Emirates Stadium, 8 pm UK.",
    },
    {
        "source_url": "https://www.premierleague.com/en/match/2645195",
        "source_site": "premierleague.com",
        "use_scene": "fact_check_fixture",
        "note": "Premier League match page: Arsenal vs Coventry City, 2026/27.",
    },
    {
        "source_url": "https://www.espn.com/soccer/match/_/gameId/401879301/coventry-city-arsenal",
        "source_site": "espn.com",
        "use_scene": "secondary_fact_check",
        "note": "Secondary schedule/live score listing for Arsenal v Coventry.",
    },
]


def configure_base():
    base.PROJECT_NAME = PROJECT_NAME
    base.OUT_DIR = OUT_DIR
    base.TTS_DIR = TTS_DIR
    base.SCENE_DIR = SCENE_DIR
    base.DOCX_PATH = DOCX_PATH
    base.LOGO_PATH = LOGO_PATH
    base.ADBAR_PATH = ADBAR_PATH
    base.POSTER_PATH = POSTER_PATH
    base.REFERENCE_PATH = REFERENCE_PATH
    base.SCENE_PLANS = SCENE_PLANS


def extract_portuguese_sentences() -> list[str]:
    doc = Document(DOCX_PATH)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    try:
        start = paragraphs.index("9. 葡语—中文逐句对照全文") + 1
    except ValueError as exc:
        raise SystemExit("Could not find section 9 in DOCX.") from exc
    end = next((i for i in range(start, len(paragraphs)) if paragraphs[i].startswith("10.")), len(paragraphs))
    section = paragraphs[start:end]
    portuguese = section[0::2]
    chinese = section[1::2]
    if len(portuguese) != 10:
        raise SystemExit(f"Expected DOCX-confirmed 10 Portuguese sentences, got {len(portuguese)}")
    if len(chinese) != 10:
        raise SystemExit(f"Expected 10 Chinese check lines, got {len(chinese)}")
    if any(any("\u4e00" <= ch <= "\u9fff" for ch in line) for line in portuguese):
        raise SystemExit("Chinese text detected in extracted Portuguese list.")
    return portuguese


def draw_scene(im, scene_idx, progress):
    draw = ImageDraw.Draw(im, "RGBA")
    title_font = base.load_font(base.FONT_IMPACT, 72)
    h1 = base.load_font(base.FONT_IMPACT, 112)
    h2 = base.load_font(base.FONT_BOLD, 56)
    small = base.load_font(base.FONT_BOLD, 36)
    gold = (255, 216, 74, 255)
    red = (232, 55, 66, 255)
    sky = (102, 214, 255, 255)
    green = (65, 235, 130, 255)
    white = (246, 248, 242, 255)

    base.draw_panel(draw, (84, 92, 498, 152), fill=(6, 18, 14, 150), radius=28)
    draw.text((126, 106), "PREMIER LEAGUE • ANÁLISE", font=small, fill=gold)

    if scene_idx == 1:
        base.draw_panel(draw, (130, 300, 950, 682))
        base.draw_centered_text(draw, (160, 322, 920, 430), "ARSENAL x COVENTRY CITY", title_font, white)
        base.draw_centered_text(draw, (180, 470, 900, 620), "21/08 • 16:00", h1, gold, 2, (0, 0, 0))
        base.draw_centered_text(draw, (180, 610, 900, 668), "HORÁRIO DE SÃO PAULO", small, white)
    elif scene_idx == 2:
        draw.text((130, 236), "MODELO DO DIA", font=title_font, fill=white)
        probs = [("Arsenal", 68, red), ("Empate", 20, white), ("Coventry", 12, sky)]
        for i, (name, pct, color) in enumerate(probs):
            x = 92 + i * 330
            base.draw_panel(draw, (x, 372, x + 290, 700))
            base.draw_centered_text(draw, (x + 15, 405, x + 275, 468), name, small, white)
            base.draw_centered_text(draw, (x + 15, 500, x + 275, 615), f"{int(pct * progress)}%", h1, color)
            draw.rounded_rectangle((x + 45, 640, x + 245, 665), radius=12, fill=(255, 255, 255, 45))
            draw.rounded_rectangle((x + 45, 640, x + 45 + int(200 * pct / 70 * progress), 665), radius=12, fill=color)
    elif scene_idx == 3:
        draw.text((120, 250), "FAVORITO FORTE", font=h1, fill=red, stroke_width=2, stroke_fill=(0, 0, 0))
        base.draw_centered_text(draw, (110, 420, 970, 610), "MAS A PRESSÃO COBRA CALMA", h2, white)
        draw.line((180, 675, 900, 675), fill=gold, width=8)
    elif scene_idx == 4:
        draw.text((110, 245), "NÃO É SINÔNIMO", font=title_font, fill=white)
        draw.text((140, 350), "DE APOSTA BOA", font=h1, fill=gold, stroke_width=2, stroke_fill=(0, 0, 0))
        base.draw_panel(draw, (170, 545, 910, 675), outline=(232, 55, 66, 230))
        base.draw_centered_text(draw, (205, 565, 875, 650), "ALERTA DE VALOR", h2, red)
    elif scene_idx == 5:
        draw.text((130, 245), "PLACAR PROVÁVEL", font=title_font, fill=white)
        base.draw_panel(draw, (150, 390, 930, 660))
        base.draw_centered_text(draw, (180, 420, 900, 630), "ARSENAL 2–0", h1, gold, 2, (0, 0, 0))
    elif scene_idx == 6:
        draw.text((135, 238), "COMENTA AÍ", font=h1, fill=gold, stroke_width=2, stroke_fill=(0, 0, 0))
        choices = [("CASA", red), ("EMPATE", white), ("VISITANTE", sky)]
        for i, (label, color) in enumerate(choices):
            y = 390 + i * 120
            base.draw_panel(draw, (160, y, 920, y + 92), outline=color)
            base.draw_centered_text(draw, (180, y + 10, 900, y + 78), label, h2, color)
    elif scene_idx == 7:
        draw.text((105, 250), "CONFERE A ESCALAÇÃO", font=title_font, fill=gold)
        base.draw_panel(draw, (160, 390, 920, 710))
        for i, label in enumerate(["GOLEIRO", "DEFESA", "MEIO", "ATAQUE"]):
            y = 430 + i * 62
            draw.text((220, y), label, font=small, fill=white)
            draw.rounded_rectangle((430, y + 8, 840, y + 38), radius=15, fill=(255, 255, 255, 42))
            draw.rounded_rectangle((430, y + 8, 430 + int(410 * min(1, progress)), y + 38), radius=15, fill=green)
        base.draw_centered_text(draw, (190, 650, 890, 700), "PODE MUDAR O CENÁRIO", small, red)
    elif scene_idx == 8:
        draw.text((120, 245), "HORÁRIO SALVO", font=title_font, fill=white)
        draw.text((145, 365), "PARA VER NO", font=h2, fill=gold)
        draw.text((145, 455), "JAGUARTV", font=h1, fill=green, stroke_width=2, stroke_fill=(0, 0, 0))
    elif scene_idx == 9:
        draw.text((118, 265), "JOGUE COM", font=title_font, fill=gold)
        base.draw_centered_text(draw, (118, 405, 962, 575), "RESPONSABILIDADE", h2, white)
        base.draw_panel(draw, (145, 625, 935, 730), outline=(255, 216, 74, 180))
        base.draw_centered_text(draw, (175, 645, 905, 710), "INFORMAÇÃO, NÃO PROMESSA", small, white)
    else:
        draw.text((120, 280), "ARSENAL x COVENTRY", font=title_font, fill=white)


def write_asset_manifest(sentences):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    local_assets = [
        ("jaguartv_logo", LOGO_PATH, "all_body_branding"),
        ("bottom_ad_bar", ADBAR_PATH, "bottom_ad_bar"),
        ("opening_poster", POSTER_PATH, "0_to_2s_opening"),
        ("brand_reference", REFERENCE_PATH, "layout_reference_only"),
    ]
    rows = []
    for asset_type, path, scene in local_assets:
        with Image.open(path) as im:
            width, height = im.size
            fmt = im.format
        rows.append(
            {
                "local_safe_filename": str(path),
                "original_filename": path.name,
                "source_url": "local_user_provided_attachment",
                "source_site": "user_provided",
                "retrieved_at": now,
                "team_or_player": "JaguarTV/Arsenal/Coventry City" if asset_type == "opening_poster" else "JaguarTV",
                "use_scene": scene,
                "resolution": f"{width}x{height}",
                "format": fmt,
                "license_status": "user_confirmed_authorized",
                "download_validation": "local_file_exists_readable_image_verified",
            }
        )
    for source in OFFICIAL_CONTEXT_SOURCES:
        rows.append(
            {
                "local_safe_filename": "",
                "original_filename": "",
                "source_url": source["source_url"],
                "source_site": source["source_site"],
                "retrieved_at": now,
                "team_or_player": "Arsenal/Coventry City",
                "use_scene": source["use_scene"],
                "resolution": "",
                "format": "web_page",
                "license_status": "user_confirmed_authorized",
                "download_validation": "read_only_fact_source_no_media_download",
                "note": source["note"],
            }
        )
    rows.append(
        {
            "local_safe_filename": str(OUT_DIR / f"{PROJECT_NAME}_noaudio_clean.mp4"),
            "original_filename": f"{PROJECT_NAME}_generated_visuals",
            "source_url": "local_generated_with_user_authorized_context",
            "source_site": "local_generation",
            "retrieved_at": now,
            "team_or_player": "Arsenal/Coventry City",
            "use_scene": "body_visuals",
            "resolution": "1080x1920",
            "format": "mp4",
            "license_status": "user_confirmed_authorized",
            "download_validation": "generated_locally_from_verified_transcript_and_user_assets",
        }
    )
    manifest = OUT_DIR / "asset_manifest.csv"
    fields = [
        "local_safe_filename",
        "original_filename",
        "source_url",
        "source_site",
        "retrieved_at",
        "team_or_player",
        "use_scene",
        "resolution",
        "format",
        "license_status",
        "download_validation",
        "note",
    ]
    with manifest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (OUT_DIR / "asset_manifest.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def create_scene_stills(rows, blocks, total_duration):
    times = [0.5, 1.8, 2.4, rows[2]["start"] + 0.5, rows[5]["start"] + 0.5, rows[6]["start"] + 0.5, rows[9]["start"] + 0.3]
    for n, t in enumerate(times, 1):
        if t < 2:
            im = base.cover_image(POSTER_PATH).convert("RGBA")
        else:
            row = base.active_row(rows, t)
            scene_idx = max(1, row["index"] - 1)
            im = base.base_background(int(t * base.FPS), int(total_duration * base.FPS), scene_idx).convert("RGBA")
            draw_scene(im, scene_idx, 1.0)
            base.paste_brand(im, t, total_duration)
        base.draw_subtitle(im, base.active_subtitle(blocks, t))
        im.convert("RGB").save(SCENE_DIR / f"check_{n:02d}_{t:.2f}s.png", quality=95)


def main():
    configure_base()
    base.draw_scene = draw_scene
    base.create_scene_stills = create_scene_stills
    random.seed(260821)
    base.ensure_dirs()
    base.validate_inputs()
    sentences = extract_portuguese_sentences()
    print("Portuguese sentences:")
    for idx, sentence in enumerate(sentences, 1):
        print(f"{idx:02d}. {sentence}")
    asyncio.run(base.generate_tts(sentences))
    rows, wav_paths, pauses, total_duration = base.convert_tts_to_wav(sentences)
    narration_path = base.assemble_narration(wav_paths, pauses, total_duration)
    bgm_path = base.make_bgm(total_duration)
    blocks = base.make_subtitle_blocks(rows)
    srt_path = base.write_srt(blocks)
    base.write_metadata(sentences, rows, blocks, total_duration)
    manifest = write_asset_manifest(sentences)
    subtitled_noaudio_path = base.render_video(rows, blocks, total_duration, captions=True)
    clean_noaudio_path = base.render_video(rows, blocks, total_duration, captions=False)
    final_path = base.mix_final_video(subtitled_noaudio_path, narration_path, bgm_path)
    create_scene_stills(rows, blocks, total_duration)
    draft_result = base.create_jianying_draft(clean_noaudio_path, narration_path, bgm_path, srt_path, total_duration)
    validation = base.validate_outputs(final_path, clean_noaudio_path, narration_path, srt_path, total_duration)
    summary = {
        "final_video": str(final_path),
        "noaudio_subtitled_video": str(subtitled_noaudio_path),
        "noaudio_clean_video": str(clean_noaudio_path),
        "narration": str(narration_path),
        "bgm": str(bgm_path),
        "srt": str(srt_path),
        "timeline_csv": str(OUT_DIR / "timeline_segments.csv"),
        "timeline_json": str(OUT_DIR / "timeline_segments.json"),
        "asset_manifest": str(manifest),
        "voice_segments_dir": str(TTS_DIR),
        "scene_stills_dir": str(SCENE_DIR),
        "jianying_draft": draft_result,
        "validation": validation,
    }
    (OUT_DIR / "delivery_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
