import http.server
import socketserver
import json
import subprocess
import os
import glob
import mimetypes
import shutil
import time

# --- CONFIGURATION ---
PORT = 9000
VIEWER_DIR = "viewer"
HISTORY_FILE = os.path.join(VIEWER_DIR, "history.json")
SEARCH_DIRS = [] 
# ---------------------

def get_font_path():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_font = os.path.join(script_dir, "font.ttf")
    if os.path.exists(local_font): return local_font
    common_paths = ["/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/liberation/LiberationSans-Bold.ttf"]
    for p in common_paths:
        if os.path.exists(p): return p
    return None

def get_max_duration(cell_data):
    """Scans all video files in the grid to find the longest duration."""
    max_duration = 5.0 # Default minimum (e.g. if only images)
    
    for cell in cell_data:
        f = cell['file']
        if f:
            path = f.replace("../", "") if f.startswith("../") else f
            # Skip images, only check real videos
            if not path.lower().endswith(('.png', '.jpg', '.jpeg')):
                try:
                    # Run ffprobe to get duration in seconds
                    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", 
                           "-of", "default=noprint_wrappers=1:nokey=1", path]
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    duration = float(result.stdout.strip())
                    if duration > max_duration:
                        max_duration = duration
                except Exception as e:
                    print(f"Could not probe {path}: {e}")
    
    return max_duration

class ResearchHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith('/files.json'):
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            all_files = []
            for ext in ['*.mp4', '*.png', '*.jpg', '*.jpeg']:
                for f in glob.glob('**/' + ext, recursive=True):
                    f = f.replace(os.sep, '/')
                    if 'grid_output' not in f and 'viewer/' not in f: all_files.append(f"../{f}")
            for d in SEARCH_DIRS:
                if os.path.exists(d):
                    for ext in ['*.mp4', '*.png', '*.jpg', '*.jpeg']:
                        for f in glob.glob(os.path.join(d, '**', ext), recursive=True): all_files.append(f.replace(os.sep, '/'))
            all_files.sort()
            self.wfile.write(json.dumps(all_files).encode())
            return

        if self.path.endswith('/history'):
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, 'r') as f: self.wfile.write(f.read().encode())
            else: self.wfile.write(json.dumps([]).encode())
            return

        req_path = self.path
        if req_path.startswith('/'):
            real_path = req_path
            is_allowed = False
            if os.path.commonpath([os.getcwd(), os.path.abspath(os.getcwd() + real_path)]) == os.getcwd(): is_allowed = True
            for d in SEARCH_DIRS:
                if real_path.startswith(d): is_allowed = True; break
            if is_allowed and os.path.isfile(real_path): self.serve_file_directly(real_path); return
        super().do_GET()

    def serve_file_directly(self, path):
        try: f = open(path, 'rb')
        except OSError: self.send_error(404); return
        self.send_response(200); self.send_header("Content-type", mimetypes.guess_type(path)[0] or 'application/octet-stream');
        self.send_header("Content-Length", str(os.fstat(f.fileno())[6])); self.end_headers(); shutil.copyfileobj(f, self.wfile); f.close()

    def do_POST(self):
        length = int(self.headers['Content-Length'])
        req_data = json.loads(self.rfile.read(length))
        
        # --- API: Save History ---
        if self.path == '/history':
            history = []
            if os.path.exists(HISTORY_FILE):
                try: 
                    with open(HISTORY_FILE, 'r') as f: 
                        history = json.load(f)
                except: pass
            
            target_idx = req_data.get("index")
            entry = { "name": req_data.get("name", "Untitled"), "timestamp": time.time(), "cols": req_data["cols"], "rows": req_data["rows"], "cells": req_data["cells"] }
            if target_idx is not None and isinstance(target_idx, int) and 0 <= target_idx < len(history): history[target_idx] = entry
            else: history.insert(0, entry)
            with open(HISTORY_FILE, 'w') as f: json.dump(history, f, indent=2)
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps({"status": "ok"}).encode())
            return

        # --- API: Render Video (ADAPTIVE DURATION) ---
        if self.path == '/render':
            output_filename = os.path.join(VIEWER_DIR, "grid_output.mp4")
            cols = int(req_data['cols']); rows = int(req_data['rows']); cell_data = req_data['cells']; font_path = get_font_path()
            
            VIDEO_W, VIDEO_H, CAPTION_H = 1280, 720, 80
            TOTAL_H = VIDEO_H + CAPTION_H
            
            # 1. Calculate Exact Duration
            # Find the longest video file so we can match the placeholders to it.
            print("Calculating max duration...")
            max_duration = get_max_duration(cell_data)
            print(f"Max duration set to: {max_duration} seconds")
            
            cmd = ["ffmpeg", "-y"]
            
            valid_count = 0
            for cell in cell_data:
                f = cell['file']
                if f:
                    path = f.replace("../", "") if f.startswith("../") else f
                    if path.lower().endswith(('.png', '.jpg', '.jpeg')):
                        # Loop images to match the longest video exactly
                        cmd.extend(["-loop", "1", "-t", str(max_duration), "-i", path])
                    else:
                        cmd.extend(["-i", path])
                    valid_count += 1
                else:
                    # Generate black video that matches the longest video exactly
                    cmd.extend(["-f", "lavfi", "-i", f"color=c=black:s={VIDEO_W}x{VIDEO_H}:d={max_duration}"])
                    valid_count += 1

            filter_chain = ""
            input_tags = ""
            font_size = 40
            
            for i in range(valid_count):
                caption = cell_data[i]['caption'].replace(":", "\\:").replace("'", "")
                text_filter = ""
                if caption and font_path:
                    text_filter = (f",drawtext=text='{caption}':fontfile={font_path}:fontcolor=white:fontsize={font_size}:"
                                   f"x=(w-text_w)/2:y=(({CAPTION_H}-text_h)/2)")

                filter_chain += (f"[{i}:v]scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=decrease,"
                                 f"pad={VIDEO_W}:{TOTAL_H}:(ow-iw)/2:{CAPTION_H}:color=black,"
                                 f"fps=30,format=yuv420p,setsar=1"
                                 f"{text_filter}[v{i}];")
                input_tags += f"[v{i}]"
            
            layout_strs = []
            for r in range(rows):
                for c in range(cols):
                    layout_strs.append(f"{c*VIDEO_W}_{r*TOTAL_H}")
            
            # NOTE: We REMOVED ":shortest=1"
            # Since we manually set the duration of images/blanks to match the max_duration,
            # we let xstack run until the longest input finishes naturally.
            full_complex = f"{filter_chain}{input_tags}xstack=inputs={valid_count}:layout={'|'.join(layout_strs)}[outv]"
            
            cmd.extend(["-filter_complex", full_complex, "-map", "[outv]", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", output_filename])
            
            print("Running FFmpeg...")
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps({"status": "ok", "file": "grid_output.mp4"}).encode())

print(f"Research Viewer running on http://localhost:{PORT}/viewer/viewer.html")
socketserver.TCPServer.allow_reuse_address = True
http.server.HTTPServer(("", PORT), ResearchHandler).serve_forever()