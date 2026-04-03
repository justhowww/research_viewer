# Research Viewer

A browser-based tool for side-by-side comparison of videos and images in a configurable grid layout, with ffmpeg-powered export.

## Features

- Configurable grid (rows x cols) for comparing media files side-by-side
- File search with autocomplete across configured directories
- Per-cell captions rendered via ffmpeg drawtext
- Row/column headers with customizable separators
- Synchronized playback controls (play/pause/seek all cells together)
- Session save/load (persisted in `history.json`)
- Export grid as a single composited video via ffmpeg `xstack`

## Setup

**Requirements:** Python 3, ffmpeg

```bash
# From the repo root (lf-ev-turb/)
python viewer/server.py
```

The server starts on port 8000. Open `http://localhost:8000` in your browser.

## Usage

1. Set the grid size (rows x cols) using the controls at the top.
2. Click a cell and type to search for a video/image file — suggestions are drawn from the configured search directories.
3. Add optional captions per cell and row/column headers.
4. Use **Export** to render the grid as a composited `.mp4` via ffmpeg.
5. Use **Save Session** / **Load Session** to persist and restore layouts.

## Configuration

Search directories are stored in `viewer/search_dirs.json`. Edit this file or use the in-app directory manager to add/remove paths.

## File Structure

```
viewer/
  viewer.html        # Single-page frontend
  server.py          # Python HTTP server + ffmpeg endpoints
  history.json       # Saved sessions
  search_dirs.json   # Directories to search for media files
  font.ttf           # Font used by ffmpeg drawtext
```
