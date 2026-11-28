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

## Inspiration, not imitation

`references/` holds inspiration reels: videos the user loves. Use them to understand
*why* they work, never as templates to clone. The goal is an edit with its own
identity that doesn't look like everyone else's.

- **Pull out principles, not specifics.** Note what makes a reel feel good
  (energy, rhythm, restraint, contrast, how the hook lands) and apply that
  feeling in a new way.
- **Don't copy** exact caption styles, fonts, color grades, transitions,
  graphic layouts, sound effects or shot-by-shot structure.
- **Mix sources.** Combine ideas from several references so no single one
  dominates.
- **Avoid trend defaults.** Skip the overused look (e.g. bouncing yellow
  word-by-word captions, stock whoosh transitions, generic zoom punch-ins)
  unless there is a real reason for it.
- **Make one deliberate twist.** Every edit should have at least one choice
  that feels distinctive: an unusual caption treatment, a custom
  HyperFrames graphic, unexpected pacing, or a signature color.
- **Check before export.** Ask: "Could this be mistaken for a copy of one
  reference?" If yes, change it.

When summarizing references (workflow step 2), write it as
**"What I'm taking"** (principles) and **"What I'm doing differently"**
(how this edit will stand apart).

## Workflow

1. If given links, grab references with yt-dlp.
2. Watch/analyse everything in `references/` and write the "What I'm taking" / "What I'm doing differently" summary before starting.
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
