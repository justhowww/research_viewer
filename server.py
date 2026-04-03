import http.server
import socketserver
import json
import subprocess
import os
import glob
import mimetypes
import shutil
import time
import re
# Add threading to your existing imports
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
# --- CONFIGURATION ---
PORT = 8000
VIEWER_DIR = "viewer"
HISTORY_FILE = os.path.join(VIEWER_DIR, "history.json")
SEARCH_DIRS_FILE = os.path.join(VIEWER_DIR, "search_dirs.json")
# ---------------------
# GLOBAL STATE
FILE_CACHE = []
SEARCH_DIRS = []


def load_search_dirs():
    """Load search directories from file, or use defaults"""
    global SEARCH_DIRS
    if os.path.exists(SEARCH_DIRS_FILE):
        try:
            with open(SEARCH_DIRS_FILE, "r") as f:
                SEARCH_DIRS = json.load(f)
                return
        except:
            pass
    # Default directories if no file exists
    SEARCH_DIRS = [
        "/fs/nexus-scratch/huangyh/lf-ev-turb-inference",
        "/fs/nexus-projects/event-asym-pupil/lf_ev_turb_memmap/real_data",
    ]


def save_search_dirs():
    """Save search directories to file"""
    with open(SEARCH_DIRS_FILE, "w") as f:
        json.dump(SEARCH_DIRS, f, indent=2)


def get_font_path():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_font = os.path.join(script_dir, "font.ttf")
    if os.path.exists(local_font):
        return local_font
    common_paths = [
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p
    return None


def natural_sort_key(s):
    return [
        int(text) if text.isdigit() else text.lower()
        for text in re.split("([0-9]+)", s)
    ]


def probe_duration(path):
    """Return duration in seconds for a video file, or None on failure."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return float(result.stdout.strip())
    except:
        return None


def get_cell_durations(cell_data):
    """Return list of video durations (seconds) per cell; None for images/empty cells."""
    durations = []
    for cell in cell_data:
        f = cell["file"]
        if f:
            path = f.replace("../", "") if f.startswith("../") else f
            if path.lower().endswith((".png", ".jpg", ".jpeg")):
                durations.append(None)
            else:
                durations.append(probe_duration(path))
        else:
            durations.append(None)
    return durations


def get_max_duration(cell_data):
    max_duration = 5.0
    for cell in cell_data:
        f = cell["file"]
        if f:
            path = f.replace("../", "") if f.startswith("../") else f
            if not path.lower().endswith((".png", ".jpg", ".jpeg")):
                d = probe_duration(path)
                if d is not None and d > max_duration:
                    max_duration = d
    return max_duration


def build_file_cache():
    """Scans disk and populates global FILE_CACHE"""
    global FILE_CACHE
    print("⏳ Scanning files... (This happens once)")
    start_t = time.time()
    temp_files = []

    # Helper to scan a root
    def scan_root(root_path, prefix=""):
        # 1. Add Files (including .h5 event files)
        for ext in ["*.mp4", "*.png", "*.jpg", "*.jpeg", "*.h5"]:
            # recursive glob can be slow, but essential for deep trees
            for f in glob.glob(os.path.join(root_path, "**", ext), recursive=True):
                f = f.replace(os.sep, "/")
                if "grid_output" in f or "viewer/" in f:
                    continue
                rel_path = f if prefix == "" else f
                if prefix == ".." and not f.startswith(".."):
                    rel_path = f"../{f}"
                temp_files.append(rel_path)

        # 2. Add Sequence Folders
        for root, dirs, files in os.walk(root_path):
            if "viewer" in root:
                continue
            image_count = sum(1 for x in files if x.lower().endswith((".png", ".jpg")))
            if image_count > 2:
                folder_path = root.replace(os.sep, "/")
                if prefix == ".." and not folder_path.startswith(".."):
                    folder_path = f"../{folder_path}"
                temp_files.append(folder_path + "/")

    scan_root(".", prefix="..")
    for d in SEARCH_DIRS:
        if os.path.exists(d):
            scan_root(d, prefix="")

    temp_files.sort()
    FILE_CACHE = temp_files
    print(f"✅ Indexed {len(FILE_CACHE)} files in {time.time()-start_t:.2f}s")


def render_single_grid(req_data, output_filename):
    """Render a single grid session to an MP4 file. Used by both /render and /render_stack."""
    cols = int(req_data["cols"])
    rows = int(req_data["rows"])
    cell_data = req_data["cells"]
    fps = int(req_data.get("fps", 30))
    font_path = get_font_path()
    VIDEO_W, VIDEO_H, CAPTION_H = 1280, 720, 80
    TOTAL_H = VIDEO_H + CAPTION_H

    normalize_duration = bool(req_data.get("normalizeDuration", False))

    title = req_data.get("title", "").strip()
    TITLE_H = 120 if title else 0
    col_headers = req_data.get("colHeaders", [])
    row_headers = req_data.get("rowHeaders", [])
    separators = req_data.get("separators", {})
    has_col_headers = any(
        h.strip() for h in col_headers if isinstance(h, str)
    )
    has_row_headers = any(
        h.strip() for h in row_headers if isinstance(h, str)
    )
    HDR_W = 300 if has_row_headers else 0
    HDR_H = 80 if has_col_headers else 0
    sep_color = separators.get("color", "#666666")
    sep_width = separators.get("width", 1)
    col_sep_style = separators.get("colStyle", "none")
    row_sep_style = separators.get("rowStyle", "none")

    sep_color_ff = sep_color.replace("#", "0x") if sep_color.startswith("#") else sep_color

    GRID_W = VIDEO_W * cols
    GRID_H = TOTAL_H * rows
    FINAL_W = HDR_W + GRID_W
    FINAL_H = TITLE_H + HDR_H + GRID_H

    if normalize_duration:
        cell_durations = get_cell_durations(cell_data)
        video_durations = [d for d in cell_durations if d is not None]
        max_duration = max(video_durations) if video_durations else 5.0
    else:
        cell_durations = [None] * len(cell_data)
        max_duration = get_max_duration(cell_data)
    cmd = ["ffmpeg", "-y"]
    valid_count = 0
    for cell in cell_data:
        f = cell["file"]
        if f:
            path = f.replace("../", "") if f.startswith("../") else f
            if path.lower().endswith((".png", ".jpg", ".jpeg")):
                cmd.extend(["-loop", "1", "-t", str(max_duration), "-i", path])
            else:
                cmd.extend(["-i", path])
            valid_count += 1
        else:
            cmd.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=black:s={VIDEO_W}x{VIDEO_H}:d={max_duration}",
                ]
            )
            valid_count += 1

    filter_chain = ""
    input_tags = ""
    font_size = 56
    for i in range(valid_count):
        caption = cell_data[i]["caption"].replace(":", "\\:").replace("'", "")
        text_filter = ""
        if caption and font_path:
            text_filter = f",drawtext=text='{caption}':fontfile={font_path}:fontcolor=white:fontsize={font_size}:x=(w-text_w)/2:y=(({CAPTION_H}-text_h)/2)"
        # Optional: time-stretch this cell to match max_duration
        setpts_filter = ""
        if normalize_duration and cell_durations[i] is not None and cell_durations[i] > 0:
            # setpts=N*PTS: N>1 slows down (stretches to max_duration), N<1 speeds up
            stretch_factor = max_duration / cell_durations[i]
            setpts_filter = f",setpts={stretch_factor:.6f}*PTS"
        # Optional: flip filter (hflip, vflip, or both)
        flip = cell_data[i].get("flip", "")
        flip_filter = ""
        if flip in ("hflip", "vflip", "hflip,vflip"):
            flip_filter = "," + flip
        filter_chain += f"[{i}:v]scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=decrease,pad={VIDEO_W}:{TOTAL_H}:(ow-iw)/2:{CAPTION_H}:color=black{setpts_filter}{flip_filter},fps={fps},format=yuv420p,setsar=1{text_filter}[v{i}];"
        input_tags += f"[v{i}]"

    layout_strs = [
        f"{c*VIDEO_W}_{r*TOTAL_H}" for r in range(rows) for c in range(cols)
    ]
    xstack_filter = f"{filter_chain}{input_tags}xstack=inputs={valid_count}:layout={'|'.join(layout_strs)}"

    CONTENT_Y_OFF = TITLE_H + HDR_H
    if TITLE_H > 0 or HDR_W > 0 or HDR_H > 0 or col_sep_style != "none" or row_sep_style != "none":
        xstack_filter += f",pad={FINAL_W}:{FINAL_H}:{HDR_W}:{CONTENT_Y_OFF}:color=black"

        if title and font_path:
            safe_title = title.replace(":", "\\:").replace("'", "")
            xstack_filter += (
                f",drawtext=text='{safe_title}'"
                f":fontfile={font_path}"
                f":fontcolor=white:fontsize=64"
                f":x=(w-text_w)/2"
                f":y={TITLE_H // 2}-(text_h/2)"
            )

        if has_col_headers and font_path:
            for c_idx, text in enumerate(col_headers):
                if not isinstance(text, str) or not text.strip():
                    continue
                safe_text = text.strip().replace(":", "\\:").replace("'", "")
                x_center = HDR_W + c_idx * VIDEO_W + VIDEO_W // 2
                y_center = TITLE_H + HDR_H // 2
                xstack_filter += (
                    f",drawtext=text='{safe_text}'"
                    f":fontfile={font_path}"
                    f":fontcolor=white:fontsize=56"
                    f":x={x_center}-(text_w/2)"
                    f":y={y_center}-(text_h/2)"
                )

        if has_row_headers and font_path:
            for r_idx, text in enumerate(row_headers):
                if not isinstance(text, str) or not text.strip():
                    continue
                safe_text = text.strip().replace(":", "\\:").replace("'", "")
                x_right = HDR_W - 20
                y_center = CONTENT_Y_OFF + r_idx * TOTAL_H + TOTAL_H // 2
                xstack_filter += (
                    f",drawtext=text='{safe_text}'"
                    f":fontfile={font_path}"
                    f":fontcolor=white:fontsize=50"
                    f":x={x_right}-text_w"
                    f":y={y_center}-(text_h/2)"
                )

        if col_sep_style != "none":
            for c_idx in range(1, cols):
                x = HDR_W + c_idx * VIDEO_W
                xstack_filter += (
                    f",drawbox=x={x - sep_width // 2}:y={CONTENT_Y_OFF}"
                    f":w={sep_width}:h={GRID_H}"
                    f":color={sep_color_ff}:t=fill"
                )

        if row_sep_style != "none":
            for r_idx in range(1, rows):
                y = CONTENT_Y_OFF + r_idx * TOTAL_H
                xstack_filter += (
                    f",drawbox=x={HDR_W}:y={y - sep_width // 2}"
                    f":w={GRID_W}:h={sep_width}"
                    f":color={sep_color_ff}:t=fill"
                )

    full_complex = xstack_filter + "[outv]"
    cmd.extend(
        [
            "-filter_complex",
            full_complex,
            "-map",
            "[outv]",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            output_filename,
        ]
    )
    print(f"Render command: ffmpeg filter_complex length = {len(full_complex)}")
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class ResearchHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # API: Search Files (Server-side filtering)
        if self.path.startswith("/search"):
            from urllib.parse import urlparse, parse_qs

            query = parse_qs(urlparse(self.path).query).get("q", [""])[0].lower()
            limit = int(parse_qs(urlparse(self.path).query).get("limit", [50])[0])

            if not query:
                results = FILE_CACHE[:limit]
            else:
                # Fuzzy-ish search: split query into parts, match files containing all parts
                parts = query.split()
                results = [f for f in FILE_CACHE if all(p in f.lower() for p in parts)][
                    :limit
                ]

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(results).encode())
            return

        # API: Get Files (Served from RAM)
        if self.path.endswith("/files.json"):
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(FILE_CACHE).encode())
            return

        # API: Force Refresh
        if self.path.endswith("/refresh_files"):
            build_file_cache()
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"status": "ok", "count": len(FILE_CACHE)}).encode()
            )
            return

        if self.path.endswith("/history"):
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "r") as f:
                    self.wfile.write(f.read().encode())
            else:
                self.wfile.write(json.dumps([]).encode())
            return

        # API: Get Search Directories
        if self.path.endswith("/search_dirs"):
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(SEARCH_DIRS).encode())
            return

        req_path = self.path
        if req_path.startswith("/"):
            real_path = req_path
            is_allowed = False
            if (
                os.path.commonpath(
                    [os.getcwd(), os.path.abspath(os.getcwd() + real_path)]
                )
                == os.getcwd()
            ):
                is_allowed = True
            for d in SEARCH_DIRS:
                if real_path.startswith(d):
                    is_allowed = True
                    break
            if is_allowed and os.path.isfile(real_path):
                self.serve_file_directly(real_path)
                return
        super().do_GET()

    def serve_file_directly(self, path):
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404)
            return
        file_size = os.fstat(f.fileno())[6]
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        range_header = self.headers.get("Range")
        if range_header:
            # Parse "bytes=start-end"
            m = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if not m:
                self.send_error(416, "Range Not Satisfiable")
                f.close()
                return
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1
            f.seek(start)
            self.send_response(206)
            self.send_header("Content-type", ctype)
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        else:
            self.send_response(200)
            self.send_header("Content-type", ctype)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            shutil.copyfileobj(f, self.wfile)
        f.close()

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        req_data = json.loads(self.rfile.read(length))

        if self.path == "/convert_sequence":
            folder_path = req_data.get("folder")
            fps = int(req_data.get("fps", 30))
            grayscale = bool(req_data.get("grayscale", False))
            if folder_path.startswith("../"):
                folder_path = folder_path.replace("../", "")
            images = [
                f
                for f in os.listdir(folder_path)
                if f.lower().endswith((".png", ".jpg", ".jpeg"))
            ]
            images.sort(key=natural_sort_key)
            if not images:
                self.send_error(400, "No images")
                return

            frame_duration = 1.0 / fps
            list_file = os.path.join(folder_path, "files.txt")
            with open(list_file, "w") as f:
                for img in images:
                    f.write(f"file '{img}'\nduration {frame_duration:.6f}\n")

            output_mp4 = os.path.join(folder_path, "sequence_output.mp4")
            print(f"Converting sequence in {folder_path}...")
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    list_file,
                    "-vf",
                    ("scale=trunc(iw/2)*2:trunc(ih/2)*2,format=gray" if grayscale
                     else "scale=trunc(iw/2)*2:trunc(ih/2)*2"),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    str(fps),
                    output_mp4,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode != 0:
                print(f"ffmpeg error:\n{result.stderr}")
            os.remove(list_file)

            browser_path = (
                output_mp4 if folder_path.startswith("/") else f"../{output_mp4}"
            )

            # Hot-fix the cache so user sees new file immediately
            if browser_path not in FILE_CACHE:
                FILE_CACHE.append(browser_path)
                FILE_CACHE.sort()

            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps({"status": "ok", "file": browser_path}).encode()
            )
            return

        # API: Convert video to browser-compatible format (H.264/yuv420p)
        if self.path == "/convert_video":
            video_path = req_data.get("file")
            fps = req_data.get("fps")  # optional; None means keep source fps
            grayscale = bool(req_data.get("grayscale", False))
            if video_path.startswith("../"):
                video_path = video_path.replace("../", "")
            if not os.path.isfile(video_path):
                self.send_error(404, "Video not found")
                return

            # Output path: same directory, _web suffix
            base, ext = os.path.splitext(video_path)
            output_path = f"{base}_web.mp4"

            print(f"Converting video for browser: {video_path} -> {output_path}")

            ffmpeg_cmd = [
                "ffmpeg", "-y", "-i", video_path,
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-an",  # no audio for now
            ]
            if grayscale:
                ffmpeg_cmd.extend(["-vf", "format=gray"])
            if fps is not None:
                ffmpeg_cmd.extend(["-r", str(int(fps))])
            ffmpeg_cmd.append(output_path)

            result = subprocess.run(
                ffmpeg_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

            if result.returncode != 0:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {"status": "error", "message": "FFmpeg conversion failed"}
                    ).encode()
                )
                return

            browser_path = (
                output_path if video_path.startswith("/") else f"../{output_path}"
            )

            # Hot-fix the cache
            if browser_path not in FILE_CACHE:
                FILE_CACHE.append(browser_path)
                FILE_CACHE.sort()

            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps({"status": "ok", "file": browser_path}).encode()
            )
            return

        # API: Convert H5 event file to video
        if self.path == "/convert_h5":
            import cv2 as cv
            from event_utils import h5_to_event_frames

            h5_path = req_data.get("file")
            num_frames = req_data.get("num_frames", 100)
            fps = req_data.get("fps", 30)
            grayscale = bool(req_data.get("grayscale", False))

            if h5_path.startswith("../"):
                h5_path = h5_path.replace("../", "")
            if not os.path.isfile(h5_path):
                self.send_error(404, "H5 file not found")
                return

            print(f"Converting H5 events to video: {h5_path}")

            try:
                frames, sensor_size = h5_to_event_frames(
                    h5_path, num_frames=num_frames, fps=fps
                )

                # Output path: same directory, _events.mp4 suffix
                base, _ = os.path.splitext(h5_path)
                output_path = f"{base}_events.mp4"

                # Write frames to video using OpenCV
                height, width = sensor_size
                fourcc = cv.VideoWriter_fourcc(*"mp4v")
                out = cv.VideoWriter(output_path, fourcc, fps, (width, height))

                for frame in frames:
                    # OpenCV expects BGR
                    bgr_frame = cv.cvtColor(frame, cv.COLOR_RGB2BGR)
                    out.write(bgr_frame)
                out.release()

                # Re-encode with ffmpeg for browser compatibility
                temp_path = output_path
                output_path = f"{base}_events_web.mp4"
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        temp_path,
                        "-c:v",
                        "libx264",
                        "-preset",
                        "fast",
                        "-crf",
                        "23",
                        "-pix_fmt",
                        "yuv420p",
                        "-movflags",
                        "+faststart",
                        "-r",
                        str(fps),
                        *(["-vf", "format=gray"] if grayscale else []),
                        output_path,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                os.remove(temp_path)

                browser_path = (
                    output_path if h5_path.startswith("/") else f"../{output_path}"
                )

                # Hot-fix the cache
                if browser_path not in FILE_CACHE:
                    FILE_CACHE.append(browser_path)
                    FILE_CACHE.sort()

                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    json.dumps({"status": "ok", "file": browser_path}).encode()
                )
            except Exception as e:
                print(f"H5 conversion error: {e}")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(
                    json.dumps({"status": "error", "message": str(e)}).encode()
                )
            return

        # API: Get H5 file info
        if self.path == "/h5_info":
            from event_utils import get_h5_info

            h5_path = req_data.get("file")
            if h5_path.startswith("../"):
                h5_path = h5_path.replace("../", "")

            info = get_h5_info(h5_path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(info).encode())
            return

        if self.path == "/history":
            history = []
            if os.path.exists(HISTORY_FILE):
                try:
                    with open(HISTORY_FILE, "r") as f:
                        history = json.load(f)
                except:
                    pass
            idx = req_data.get("index")
            entry = {
                "name": req_data.get("name", "Untitled"),
                "timestamp": time.time(),
                "cols": req_data["cols"],
                "rows": req_data["rows"],
                "cells": req_data["cells"],
            }
            # Save header/separator settings if present
            if "showHeaders" in req_data:
                entry["showHeaders"] = req_data["showHeaders"]
            if "colHeaders" in req_data:
                entry["colHeaders"] = req_data["colHeaders"]
            if "rowHeaders" in req_data:
                entry["rowHeaders"] = req_data["rowHeaders"]
            if "separators" in req_data:
                entry["separators"] = req_data["separators"]
            if "normalizeDuration" in req_data:
                entry["normalizeDuration"] = req_data["normalizeDuration"]
            if idx is not None and isinstance(idx, int) and 0 <= idx < len(history):
                history[idx] = entry
            else:
                history.insert(0, entry)
            with open(HISTORY_FILE, "w") as f:
                json.dump(history, f, indent=2)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
            return

        # API: Update Search Directories
        if self.path == "/search_dirs":
            global SEARCH_DIRS
            SEARCH_DIRS = req_data.get("dirs", [])
            save_search_dirs()
            build_file_cache()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps({"status": "ok", "count": len(FILE_CACHE)}).encode()
            )
            return

        if self.path == "/rename_history":
            idx = req_data.get("index")
            new_name = req_data.get("name", "").strip()
            if idx is None or not new_name:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "index and name required"}).encode())
                return
            history = []
            if os.path.exists(HISTORY_FILE):
                try:
                    with open(HISTORY_FILE, "r") as f:
                        history = json.load(f)
                except:
                    pass
            if not (0 <= idx < len(history)):
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "invalid index"}).encode())
                return
            history[idx]["name"] = new_name
            with open(HISTORY_FILE, "w") as f:
                json.dump(history, f, indent=2)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
            return

        if self.path == "/render":
            output_filename = os.path.join(VIEWER_DIR, "grid_output.mp4")
            render_single_grid(req_data, output_filename)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps({"status": "ok", "file": "grid_output.mp4"}).encode()
            )
            return

        if self.path == "/render_stack":
            blocks = req_data.get("blocks", [])
            direction = req_data.get("direction", "vertical")
            output_name = req_data.get("output_name", "stack_output")
            if not blocks:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "no blocks"}).encode())
                return

            temp_files = []
            try:
                for i, block in enumerate(blocks):
                    temp_path = os.path.join(VIEWER_DIR, f"_stack_temp_{i}.mp4")
                    render_single_grid(block, temp_path)
                    temp_files.append(temp_path)

                output_path = os.path.join(VIEWER_DIR, f"{output_name}.mp4")

                if len(temp_files) == 1:
                    shutil.move(temp_files[0], output_path)
                    temp_files = []
                else:
                    # Use ffmpeg to stack videos
                    cmd = ["ffmpeg", "-y"]
                    for tf in temp_files:
                        cmd.extend(["-i", tf])

                    n = len(temp_files)
                    # Determine max width/height for padding
                    stack_mode = "vstack" if direction == "vertical" else "hstack"

                    # Probe each temp file for dimensions
                    widths = []
                    heights = []
                    for tf in temp_files:
                        probe_cmd = [
                            "ffprobe", "-v", "error",
                            "-select_streams", "v:0",
                            "-show_entries", "stream=width,height",
                            "-of", "csv=p=0",
                            tf,
                        ]
                        result = subprocess.run(probe_cmd, capture_output=True, text=True)
                        parts = result.stdout.strip().split(",")
                        if len(parts) == 2:
                            widths.append(int(parts[0]))
                            heights.append(int(parts[1]))
                        else:
                            widths.append(1280)
                            heights.append(800)

                    max_w = max(widths)
                    max_h = max(heights)

                    # Build filter: pad each input to match, then stack
                    filter_parts = []
                    stack_inputs = ""
                    for i in range(n):
                        if direction == "vertical":
                            filter_parts.append(
                                f"[{i}:v]pad={max_w}:ih:(ow-iw)/2:0:color=black[p{i}]"
                            )
                        else:
                            filter_parts.append(
                                f"[{i}:v]pad=iw:{max_h}:0:(oh-ih)/2:color=black[p{i}]"
                            )
                        stack_inputs += f"[p{i}]"

                    filter_parts.append(f"{stack_inputs}{stack_mode}=inputs={n}[outv]")
                    filter_str = ";".join(filter_parts)

                    cmd.extend([
                        "-filter_complex", filter_str,
                        "-map", "[outv]",
                        "-c:v", "libx264",
                        "-crf", "20",
                        "-pix_fmt", "yuv420p",
                        output_path,
                    ])

                    print(f"Stack render: {n} blocks, direction={direction}")
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    json.dumps({"status": "ok", "file": f"{output_name}.mp4"}).encode()
                )
            finally:
                for tf in temp_files:
                    if os.path.exists(tf):
                        os.remove(tf)
            return
if __name__ == "__main__":
    print(f"Research Viewer running on http://localhost:{PORT}/viewer/viewer.html")

    # 1. Load configuration
    load_search_dirs()

    # 2. Run file scan in a background thread so the server starts IMMEDIATELY.
    #    The UI might show empty files for a few seconds until this finishes.
    print("⏳ Starting background file scan...")
    scan_thread = threading.Thread(target=build_file_cache, daemon=True)
    scan_thread.start()

    # 3. Use ThreadingHTTPServer instead of standard HTTPServer
    #    This prevents FFmpeg or large downloads from freezing the UI.
    socketserver.TCPServer.allow_reuse_address = True
    
    # Check specifically for Python 3.7+ availability of ThreadingHTTPServer
    try:
        ThreadingHTTPServer.allow_reuse_address = True
        server = ThreadingHTTPServer(("", PORT), ResearchHandler)
    except NameError:
        # Fallback for older Python versions
        class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
            daemon_threads = True
        server = ThreadedHTTPServer(("", PORT), ResearchHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        server.shutdown()