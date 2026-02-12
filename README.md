# fetchcanvas

Sync course files from Canvas LMS to a local folder.

## Config

Set these in `.env` (see `.env-sample`):

- **API_TOKEN** — Canvas API token (Account > Settings > Generate New Token).
- **CANVAS_BASE_URL** — Canvas instance URL (e.g. `https://canvas.nus.edu.sg`).
- **OUTPUT_PATH** — Local folder where course files are saved.
- **COURSES** — Comma-separated course codes to sync (e.g. `CS3223,CS3234`), or `all` to sync every active course. Empty or missing = sync all.
