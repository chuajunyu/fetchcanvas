import requests
import os
from datetime import datetime, timezone
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


def get_all_courses():
    url = f"{CANVAS_BASE_URL}/api/v1/courses?enrollment_state=active&per_page=100"
    course_list = requests.get(url, headers=headers).json()
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
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/folders?per_page=100"

    folder_list = requests.get(url, headers=headers).json()

    folder_id_name_map = {}
    for folder in folder_list:
        folder_id = folder['id']
        folder_name = folder['name']
        parent_folder_id = folder['parent_folder_id']

        if folder_name == 'course files':
            folder_name = course_name

        folder_id_name_map[folder_id] = (folder_name, parent_folder_id)
    
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

def resolve_path_for_file(folder_id, folder_id_name_map):
    """
    given a file, resolve it's file path
    """
    path = []

    while folder_id is not None:
        name, folder_id = folder_id_name_map[folder_id]
        path.append(name)
    
    path.reverse()
    path = os.path.join(*path)
    return path


def download_all_files(course_id, folder_id_name_map, course_code):
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/files?per_page=100"
    file_list = requests.get(url, headers=headers).json()

    downloaded = []
    updated = []
    skipped = []
    failed = []

    for file in file_list:
        display_name = file['filename']
        folder_path = resolve_path_for_file(file['folder_id'], folder_id_name_map)
        save_path = os.path.join(OUTPUT_PATH, folder_path, display_name)

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
        folder_id_name_map = get_all_folders(course_id, course_code)
        result = download_all_files(course_id, folder_id_name_map, course_code)
        summary_lines = format_course_summary(result)

        if summary_lines is None:
            print("  No changes (all files up to date).")
        else:
            any_changes_or_errors = True
            for line in summary_lines:
                print(line)
            courses_log.append((course_code, summary_lines))

    # Decide whether to add a per-run entry
    if any_changes_or_errors:
        entry_md = build_run_entry(run_started_at, courses_log)
    else:
        entry_md = None  # no new log entry

    update_changelog(OUTPUT_PATH, run_started_at, entry_md)