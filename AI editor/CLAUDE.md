# AI Editor

This folder is a workspace for Claude Code to edit media and content.

## Folders

- `raw/` — Unedited source material (footage, images, audio, drafts). Treat as read-only: never modify or delete files here.
- `references/` — Examples of the target style (pacing, captions, color, tone, format). Study these before editing.
- `done/` — Finished output. Write all final edits here.

## Workflow

1. Look at everything in `references/` and summarize the style before starting.
2. Take the source file(s) from `raw/`.
3. Edit them to match the reference style.
4. Save the result to `done/` with a clear name, e.g. `<original-name>_edited.<ext>`.
5. Briefly report what was changed.
