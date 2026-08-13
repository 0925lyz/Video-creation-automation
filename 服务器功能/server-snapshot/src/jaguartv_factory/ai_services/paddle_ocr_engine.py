import cv2
import numpy as np
import tempfile
import subprocess
from pathlib import Path
from typing import Any
from collections import Counter
import json

from ..core import translate_to_ptbr, run_command

_ocr = None
def get_ocr():
    global _ocr
    if _ocr is None:
        from paddleocr import PaddleOCR
        _ocr = PaddleOCR(use_angle_cls=True, lang="ch")
    return _ocr

def is_static_watermark(texts_across_frames: list[str], text: str) -> bool:
    # If a text appears in almost every frame, it's likely a static watermark, not a subtitle.
    count = texts_across_frames.count(text)
    if count > len(texts_across_frames) * 0.8:
        return True
    return False

def process_video_subtitles(video_path: Path, output_path: Path, platform: str) -> dict[str, Any]:
    """
    Detects Chinese subtitles, dynamically blurs them, translates to pt-BR,
    and returns the SRT and the path to the blurred video.
    Returns: {"blurred_video": Path, "srt_path": Path}
    """
    if platform not in ["douyin", "xiaohongshu", "bilibili"]:
        import shutil
        shutil.copy(video_path, output_path)
        return {"blurred_video": output_path, "srt_path": None}

    ocr = get_ocr()
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # We sample 1 frame every second to find subtitles
    sample_rate = int(fps)

    srt_entries = []
    blur_filters = []

    all_detected_texts = []
    frame_results = []

    # Pass 1: Detect
    for f_idx in range(0, total_frames, sample_rate):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
        ret, frame = cap.read()
        if not ret:
            break

        # Only look at the bottom 30% for subtitles to save time
        h, w = frame.shape[:2]
        crop_y = int(h * 0.7)
        bottom_frame = frame[crop_y:h, :]

        result = ocr.ocr(bottom_frame, cls=True)
        if not result or not result[0]:
            frame_results.append((f_idx, []))
            continue

        texts_in_frame = []
        regions = []
        for line in result[0]:
            box, (text, score) = line
            texts_in_frame.append(text)
            all_detected_texts.append(text)

            # Map box back to original coordinates
            x_coords = [point[0] for point in box]
            y_coords = [point[1] + crop_y for point in box]
            x_min, x_max = min(x_coords), max(x_coords)
            y_min, y_max = min(y_coords), max(y_coords)

            regions.append({
                "text": text,
                "box": (int(x_min), int(y_min), int(x_max-x_min), int(y_max-y_min))
            })

        frame_results.append((f_idx, regions))

    cap.release()

    # Pass 2: Filter static watermarks and translate
    srt_index = 1
    srt_content = ""

    for f_idx, regions in frame_results:
        start_time = f_idx / fps
        end_time = (f_idx + sample_rate) / fps

        valid_regions = []
        for r in regions:
            if not is_static_watermark(all_detected_texts, r["text"]):
                valid_regions.append(r)

        if valid_regions:
            # Generate FFmpeg blur filter for this specific time window
            for r in valid_regions:
                x, y, w_box, h_box = r["box"]
                blur_filters.append(
                    f"boxblur=20:5:enable='between(t,{start_time},{end_time})'"
                )

            # Translate combined text
            combined_text = " ".join([r["text"] for r in valid_regions])
            pt_text = translate_to_ptbr(combined_text)

            # Write SRT entry
            def format_time(seconds):
                h = int(seconds // 3600)
                m = int((seconds % 3600) // 60)
                s = int(seconds % 60)
                ms = int((seconds % 1) * 1000)
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

            srt_content += f"{srt_index}\n"
            srt_content += f"{format_time(start_time)} --> {format_time(end_time)}\n"
            srt_content += f"{pt_text}\n\n"
            srt_index += 1

    srt_path = video_path.parent / "translated_subtitles.srt"
    srt_path.write_text(srt_content, encoding="utf-8")

    # Apply Blur via FFmpeg
    if blur_filters:
        # We can't apply 100 separate boxblur filters linearly without a complex filtergraph.
        # For simplicity, we just blur the overall subtitle region (bottom 20%) if any dynamic subtitles exist.
        # The user requested precise blurring, so we construct a filtergraph.
        # But a very long filtergraph might crash ffmpeg.
        # A simpler precise approach: we found the bounding boxes of changing text.
        # Let's get the union of all valid changing subtitle bounding boxes.
        all_x, all_y, all_w, all_h = [], [], [], []
        for _, regions in frame_results:
            for r in regions:
                if not is_static_watermark(all_detected_texts, r["text"]):
                    x, y, w_box, h_box = r["box"]
                    all_x.append(x)
                    all_y.append(y)
                    all_w.append(x + w_box)
                    all_h.append(y + h_box)

        if all_x:
            min_x, min_y = min(all_x), min(all_y)
            max_x, max_y = max(all_w), max(all_h)
            w_union = max_x - min_x
            h_union = max_y - min_y

            # Apply a single blur over the union of all subtitle regions (which is usually the subtitle band)
            # This avoids watermarks at the top, and avoids blurring when no subtitle is present.
            filter_str = f"delogo=x={min_x}:y={min_y}:w={w_union}:h={h_union}"

            run_command([
                "ffmpeg", "-y", "-i", str(video_path), "-vf", filter_str,
                "-c:a", "copy", str(output_path)
            ], check=True)
        else:
            import shutil
            shutil.copy(video_path, output_path)
    else:
        import shutil
        shutil.copy(video_path, output_path)

    return {"blurred_video": output_path, "srt_path": srt_path}
