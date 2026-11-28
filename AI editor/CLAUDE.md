# AI Editor

This folder is a workspace for Claude Code to edit video.

## Folders

- `raw/` — Unedited source footage and audio. Treat as read-only: never modify or delete files here.
- `references/` — Example videos showing the target style (pacing, captions, graphics, tone). Study these before editing.
- `done/` — Finished output. Write all final edits here.

## Tools

| Job | Tool |
| --- | --- |
| Grab reference videos | **yt-dlp** |
| Cut, trim, join, encode | **FFmpeg** |
| Write subtitles | **Whisper** |
| Build graphics (titles, lower thirds, overlays) | **HyperFrames** |

### yt-dlp — references
When given a link, download it into `references/`:
```
yt-dlp -f "bv*+ba/b" -o "references/%(title)s.%(ext)s" <URL>
```

### FFmpeg — cutting
- Trim: `ffmpeg -ss <start> -to <end> -i raw/in.mp4 -c:v libx264 -c:a aac out.mp4`
- Find silences to cut: `ffmpeg -i raw/in.mp4 -af silencedetect=noise=-30dB:d=0.5 -f null -`
- Join clips: concat demuxer with a list file.
- Burn subtitles: `ffmpeg -i in.mp4 -vf subtitles=in.srt out.mp4`
- Never overwrite files in `raw/`. Put work-in-progress files in `done/work/`.

### Whisper — subtitles
```
whisper <file> --model medium --output_format srt --output_dir done/work
```
Use `--word_timestamps True` when captions need word-by-word timing. Check the transcript for misheard names and terms before burning it in.

### HyperFrames — graphics
Build graphics as HTML compositions with HyperFrames, render them to video,
then layer them over the edit with FFmpeg (`overlay` filter, or use
transparent renders). Match fonts, colors and motion to the references.

## Workflow

1. If given links, grab references with yt-dlp.
2. Watch/analyse everything in `references/` and summarize the style (pacing, cut frequency, caption look, graphics) before starting.
3. Cut the footage from `raw/` with FFmpeg.
4. Generate subtitles with Whisper, fix errors, style them to match references.
5. Build graphics with HyperFrames.
6. Combine everything with FFmpeg and export to `done/` as `<original-name>_edited.mp4`.
7. Briefly report what was changed.

## Setup

```
pip install -U openai-whisper yt-dlp   # Whisper + yt-dlp
# FFmpeg: apt install ffmpeg / brew install ffmpeg
# HyperFrames: npx hyperframes (needs Node 22+)
```
