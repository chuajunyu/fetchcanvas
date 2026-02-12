import requests
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

API_TOKEN = os.getenv('API_TOKEN')
CANVAS_BASE_URL = os.getenv('CANVAS_BASE_URL')
OUTPUT_PATH = os.getenv('OUTPUT_PATH')

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
                downloaded.append(display_name)
            else:
                failed.append((display_name, err or "Unknown error"))
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
                updated.append(display_name)
            else:
                failed.append((display_name, err or "Unknown error"))
        else:
            skipped.append(display_name)

    print(f"\n--- {course_code} ---")
    print(f"Downloaded (new):    {len(downloaded)}")
    for name in downloaded:
        print(f"  - {name}")
    print(f"Updated (replaced):  {len(updated)}")
    for name in updated:
        print(f"  - {name}")
    print(f"Skipped (up to date): {len(skipped)}")
    if failed:
        print(f"Failed:             {len(failed)}")
        for name, err in failed:
            print(f"  - {name}: {err}")


if __name__ == "__main__":
    courses = get_all_courses()

    print(f"Syncing courses: {COURSE_CODES if not SYNC_ALL_COURSES else 'all'}")
    
    for course_id, course_code in courses:
        if SYNC_ALL_COURSES or course_code in COURSE_CODES:
            print(f"\nCourse: {course_code}")
            folder_id_name_map = get_all_folders(course_id, course_code)
            download_all_files(course_id, folder_id_name_map, course_code)
        else:
            continue