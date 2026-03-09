# fetchcanvas

Sync course files from Canvas LMS to a local folder.

## Config

Set these in `.env` (see `.env-sample`):

- **API_TOKEN** — Canvas API token (Account > Settings > Generate New Token).
- **CANVAS_BASE_URL** — Canvas instance URL (e.g. `https://canvas.nus.edu.sg`).
- **OUTPUT_PATH** — Local folder where course files are saved.
- **COURSES** — Comma-separated course codes to sync (e.g. `CS3223,CS3234`), or `all` to sync every active course. Empty or missing = sync all.

## Known Issues

- **Courses not found:** The script does not handle the case when configured course codes (in `COURSES`) do not match any enrollment — e.g. a typo or a course the user isn't enrolled in. Consider validating configured codes against the list returned by `get_all_courses()` and warning (or erroring) when none match or some are unknown.
