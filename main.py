import re
import requests
import os
import shutil
from datetime import datetime
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

load_dotenv()

API_TOKEN = os.getenv('API_TOKEN')
CANVAS_BASE_URL = os.getenv('CANVAS_BASE_URL')
OUTPUT_PATH = os.getenv('OUTPUT_PATH')

CHANGELOG_NAME = "CHANGELOG.md"
TIMEZONE = ZoneInfo("Asia/Singapore")
LAST_SYNC_PREFIX = "# Last Sync: "

_raw_courses = (os.getenv('COURSES') or '').strip()
if not _raw_courses or _raw_courses.lower() == 'all':
    SYNC_ALL_COURSES = True
    COURSE_CODES = None
else:
    SYNC_ALL_COURSES = False
    COURSE_CODES = {c.strip() for c in _raw_courses.split(',') if c.strip()}

headers = {
    "Authorization": f"Bearer {API_TOKEN}"
}


_ILLEGAL_FS_CHARS = re.compile(r'[<>:"/\\|?*]')

def sanitize_name(name):
    """Replace filesystem-illegal characters and trim whitespace/dots."""
    name = _ILLEGAL_FS_CHARS.sub('-', name)
    name = name.strip('. ')
    return name or '-'


def canvas_get(endpoint, description, *, on_forbidden_note=None):
    """
    Helper for GET requests to Canvas that:
    - builds the URL from CANVAS_BASE_URL
    - checks HTTP status
    - logs helpful error messages (including for hidden/disabled Files areas)
    - parses and returns JSON, or None on error.
    """
    url = f"{CANVAS_BASE_URL}{endpoint}"
    response = requests.get(url, headers=headers)

    if not response.ok:
        print(f"Error fetching {description}: {response.status_code} {response.reason}")
        if on_forbidden_note and response.status_code in (401, 403, 404):
            print(on_forbidden_note)
        try:
            print(response.text)
        except Exception:
            pass
        return None

    try:
        return response.json()
    except ValueError:
        print(f"Failed to parse {description} response as JSON.")
        print(response.text)
        return None


def get_all_courses():
    course_list = canvas_get(
        "/api/v1/courses?enrollment_state=active&per_page=100",
        "courses",
    )
    if course_list is None:
        return []
    courses = []
    for course in course_list:
        if "course_code" in course:
            courses.append((course['id'], course["course_code"]))
        else:
            print("Course with no Course code found")
    return courses


def get_all_folders(course_id, course_name):
    """
    Returns a mapping of id to the folder name
    """
    folder_list = canvas_get(
        f"/api/v1/courses/{course_id}/folders?per_page=100",
        f"folders for {course_name} ({course_id})",
        on_forbidden_note=(
            "Folders endpoint is not accessible. This often happens when the course 'Files' area "
            "is hidden/disabled. Files sync will be skipped for this course."
        ),
    )
    if folder_list is None:
        return

    folder_id_name_map = {}
    for folder in folder_list:
        folder_id = folder['id']
        folder_name = folder['name']
        parent_folder_id = folder['parent_folder_id']

        if folder_name == 'course files':
            folder_name = course_name

        folder_id_name_map[folder_id] = (sanitize_name(folder_name), parent_folder_id)
    
    return folder_id_name_map


def download_file(url, save_path):
    try:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(save_path, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)
        return True, None
    except requests.exceptions.RequestException as e:
        return False, str(e)

def build_run_entry(run_started_at, courses_log):
    run_time = run_started_at.astimezone(TIMEZONE)
    ts_str = run_time.strftime("%A %Y-%m-%d %H:%M:%S %Z")
    lines = []
    lines.append(f"## Sync run at {ts_str}")
    lines.append("")
    for course_code, summary_lines in courses_log:
        lines.append(f"### Course: {course_code}")
        lines.extend(summary_lines)
        lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def update_changelog(output_path, run_started_at, entry_md_or_none):
    """Update Last Sync header and optionally prepend a new entry."""
    changelog_path = os.path.join(output_path, CHANGELOG_NAME)
    os.makedirs(output_path, exist_ok=True)

    # Format Last Sync line 
    run_time = run_started_at.astimezone(TIMEZONE)
    last_sync_str = run_time.strftime("%A %Y-%m-%d %H:%M:%S %Z")
    header_line = f"{LAST_SYNC_PREFIX}{last_sync_str}\n\n"

    old_body = ""
    if os.path.exists(changelog_path):
        with open(changelog_path, "r", encoding="utf-8") as f:
            existing = f.read()
        # Strip old Last Sync header if present
        if existing.startswith(LAST_SYNC_PREFIX):
            # Remove first line and any following blank line(s)
            lines = existing.splitlines(True)
            # drop first line
            lines = lines[1:]
            # drop leading blank lines
            while lines and lines[0].strip() == "":
                lines.pop(0)
            old_body = "".join(lines)
        else:
            old_body = existing

    with open(changelog_path, "w", encoding="utf-8") as f:
        f.write(header_line)
        if entry_md_or_none:
            f.write(entry_md_or_none)
            if old_body:
                f.write(old_body)
        else:
            # No new entry; just keep existing body as-is
            if old_body:
                f.write(old_body)

def migrate_old_structure(course_code):
    """Move pre-existing flat downloads into the files/ subfolder.

    Idempotent: only moves entries that aren't already the new
    'files' / 'modules' directories, so partial runs or re-runs are safe.
    """
    course_dir = os.path.join(OUTPUT_PATH, course_code)
    if not os.path.isdir(course_dir):
        return

    to_move = [e for e in os.listdir(course_dir) if e not in ("files", "modules")]
    if not to_move:
        return

    files_dir = os.path.join(course_dir, "files")
    os.makedirs(files_dir, exist_ok=True)
    for entry in to_move:
        src = os.path.join(course_dir, entry)
        dst = os.path.join(files_dir, entry)
        shutil.move(src, dst)
    print(f"  Migrated {course_code} to new folder structure (old files moved to files/)")


def resolve_path_for_file(folder_id, folder_id_name_map):
    """
    Resolve the sub-path below the course root folder for a given file.
    The first element (the course root renamed from 'course files') is stripped
    so callers can prepend their own prefix (e.g. course_code/files/).
    """
    path = []

    while folder_id is not None:
        name, folder_id = folder_id_name_map[folder_id]
        path.append(name)
    
    path.reverse()
    sub_path = os.path.join(*path[1:]) if len(path) > 1 else ""
    return sub_path


def download_all_files(course_id, folder_id_name_map, course_code):
    file_list = canvas_get(
        f"/api/v1/courses/{course_id}/files?per_page=100",
        f"files for {course_code} ({course_id})",
        on_forbidden_note=(
            "Files endpoint is not accessible. This usually means the course 'Files' tab is "
            "hidden/disabled. Files sync will be skipped for this course."
        ),
    )
    if file_list is None:
        return

    downloaded = []
    updated = []
    skipped = []
    failed = []

    for file in file_list:
        display_name = sanitize_name(file['filename'])
        folder_path = resolve_path_for_file(file['folder_id'], folder_id_name_map)
        save_path = os.path.join(OUTPUT_PATH, course_code, "files", folder_path, display_name)

        if not os.path.exists(save_path):
            ok, err = download_file(file['url'], save_path)
            if ok:
                downloaded.append((display_name, folder_path))
            else:
                failed.append((display_name, folder_path, err or "Unknown error"))
            continue

        try:
            canvas_updated = datetime.fromisoformat(
                file['updated_at'].replace('Z', '+00:00')
            ).timestamp()
        except (KeyError, ValueError):
            canvas_updated = float('inf')

        local_mtime = os.path.getmtime(save_path)
        if canvas_updated > local_mtime:
            ok, err = download_file(file['url'], save_path)
            if ok:
                updated.append((display_name, folder_path))
            else:
                failed.append((display_name, folder_path, err or "Unknown error"))
        else:
            skipped.append((display_name, folder_path))

    # Optional: adjust console printing to show paths too
    print(f"\n--- {course_code} ---")
    print(f"Downloaded (new):    {len(downloaded)}")
    for name, folder in downloaded:
        print(f"  - {os.path.join(folder, name)}")
    print(f"Updated (replaced):  {len(updated)}")
    for name, folder in updated:
        print(f"  - {os.path.join(folder, name)}")
    print(f"Skipped (up to date): {len(skipped)}")
    if failed:
        print(f"Failed:             {len(failed)}")
        for name, folder, err in failed:
            print(f"  - {os.path.join(folder, name)}: {err}")

    return {
        "course_code": course_code,
        "downloaded": downloaded,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
    }


def get_all_modules(course_id, course_code):
    """Return list of (module_id, module_name), or None on API error."""
    module_list = canvas_get(
        f"/api/v1/courses/{course_id}/modules?per_page=100",
        f"modules for {course_code} ({course_id})",
        on_forbidden_note=(
            "Modules endpoint is not accessible. "
            "Modules sync will be skipped for this course."
        ),
    )
    if module_list is None:
        return None
    return [(m['id'], m['name']) for m in module_list]


def get_module_file_items(course_id, module_id):
    """Return module items where type == 'File'."""
    items = canvas_get(
        f"/api/v1/courses/{course_id}/modules/{module_id}/items?per_page=100",
        f"items for module {module_id}",
    )
    if items is None:
        return []
    file_items = [item for item in items if item.get('type') == 'File']
    return file_items


def get_file_details(file_api_url):
    """
    Fetch file metadata from the Canvas API url provided in a module item.
    Returns the file dict (with 'url', 'filename', 'updated_at', etc.) or None.
    """
    response = requests.get(file_api_url, headers=headers)
    if not response.ok:
        print(f"      Error fetching file details: {response.status_code} {response.text[:200]}")
        return None
    try:
        return response.json()
    except ValueError:
        print(f"      Failed to parse file details response")
        return None


def download_files_from_modules(course_id, course_code):
    """Download File-type items found in course modules. Returns result dict or None."""
    modules = get_all_modules(course_id, course_code)
    if modules is None:
        print(f"  Modules area not accessible, skipping modules sync.")
        return None
    if not modules:
        print(f"  No modules found for {course_code}.")
        return None

    downloaded = []
    updated = []
    skipped = []
    failed = []

    for module_id, raw_module_name in modules:
        module_name = sanitize_name(raw_module_name)
        module_folder = os.path.join("modules", module_name)
        file_items = get_module_file_items(course_id, module_id)
        if not file_items:
            continue

        for item in file_items:
            file_api_url = item.get('url')
            if not file_api_url:
                failed.append((item.get('title', '?'), module_folder, "No file API url in module item"))
                continue

            file_obj = get_file_details(file_api_url)
            if file_obj is None:
                failed.append((item.get('title', '?'), module_folder, "Could not fetch file details"))
                continue

            filename = sanitize_name(file_obj.get('filename') or file_obj.get('display_name', 'unknown'))
            download_url = file_obj.get('url')
            if not download_url:
                failed.append((filename, module_folder, "No download url in file object"))
                continue

            save_path = os.path.join(OUTPUT_PATH, course_code, "modules", module_name, filename)

            if not os.path.exists(save_path):
                ok, err = download_file(download_url, save_path)
                if ok:
                    downloaded.append((filename, module_folder))
                else:
                    failed.append((filename, module_folder, err or "Unknown error"))
                continue

            try:
                canvas_updated = datetime.fromisoformat(
                    file_obj['updated_at'].replace('Z', '+00:00')
                ).timestamp()
            except (KeyError, ValueError):
                canvas_updated = float('inf')

            local_mtime = os.path.getmtime(save_path)
            if canvas_updated > local_mtime:
                ok, err = download_file(download_url, save_path)
                if ok:
                    updated.append((filename, module_folder))
                else:
                    failed.append((filename, module_folder, err or "Unknown error"))
            else:
                skipped.append((filename, module_folder))

    print(f"\n--- {course_code} (from Modules) ---")
    print(f"Downloaded (new):    {len(downloaded)}")
    for name, folder in downloaded:
        print(f"  - {os.path.join(folder, name)}")
    print(f"Updated (replaced):  {len(updated)}")
    for name, folder in updated:
        print(f"  - {os.path.join(folder, name)}")
    print(f"Skipped (up to date): {len(skipped)}")
    if failed:
        print(f"Failed:             {len(failed)}")
        for name, folder, err in failed:
            print(f"  - {os.path.join(folder, name)}: {err}")

    return {
        "course_code": course_code,
        "downloaded": downloaded,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
    }


def format_course_summary(course_result):
    downloaded = course_result["downloaded"]   # list of (name, folder_path)
    updated = course_result["updated"]         # list of (name, folder_path)
    skipped = course_result["skipped"]         # list of (name, folder_path)
    failed = course_result["failed"]           # list of (name, folder_path, err)

    if not downloaded and not updated and not failed:
        return None

    lines = []
    lines.append(f"- Downloaded (new): {len(downloaded)}")
    for name, folder in downloaded:
        rel = os.path.join(folder, name)
        lines.append(f"  - `{rel}`")

    lines.append(f"- Updated (replaced): {len(updated)}")
    for name, folder in updated:
        rel = os.path.join(folder, name)
        lines.append(f"  - `{rel}`")

    lines.append(f"- Skipped (up to date): {len(skipped)}")

    if failed:
        lines.append(f"- Failed: {len(failed)}")
        for name, folder, err in failed:
            rel = os.path.join(folder, name)
            lines.append(f"  - `{rel}` — {err}")

    return lines



if __name__ == "__main__":
    run_started_at = datetime.now(TIMEZONE)
    courses = get_all_courses()

    print(f"Syncing courses: {COURSE_CODES if not SYNC_ALL_COURSES else 'all'}")

    courses_log = []
    any_changes_or_errors = False

    for course_id, course_code in courses:
        if not (SYNC_ALL_COURSES or course_code in COURSE_CODES):
            continue

        print(f"\nCourse: {course_code}")
        migrate_old_structure(course_code)

        folder_id_name_map = get_all_folders(course_id, course_code)
        if folder_id_name_map:
            files_result = download_all_files(course_id, folder_id_name_map, course_code)
        else:
            print(f"  Files area not accessible, skipping files sync.")
            files_result = None

        modules_result = download_files_from_modules(course_id, course_code)

        for result in (files_result, modules_result):
            if result is None:
                continue
            summary_lines = format_course_summary(result)
            if summary_lines is not None:
                any_changes_or_errors = True
                courses_log.append((course_code, summary_lines))

    if any_changes_or_errors:
        entry_md = build_run_entry(run_started_at, courses_log)
    else:
        entry_md = None

    update_changelog(OUTPUT_PATH, run_started_at, entry_md)
