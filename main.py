import requests
import os
from dotenv import load_dotenv

load_dotenv()

API_TOKEN = os.getenv('API_TOKEN')
CANVAS_BASE_URL = os.getenv('CANVAS_BASE_URL')
OUTPUT_PATH = os.getenv('OUTPUT_PATH')

headers = {
    "Authorization": f"Bearer {API_TOKEN}"
}


def get_all_courses():
    url = f"{CANVAS_BASE_URL}/api/v1/courses?enrollment_state=active&per_page=100"
    course_list = requests.get(url, headers=headers).json()
    print(f"{len(course_list)} courses found")
    courses = []
    for course in course_list:
        print((course['id'], course["course_code"]))
        if "course_code" in course:
            print("here")
            courses.append((course['id'], course["course_code"]))
        else:
            print("Course with no Course code found")
    print(courses)
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
        # Create the directory if it doesn't exist
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        
        # Send a GET request to the URL
        response = requests.get(url, stream=True)
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)

        # Open the file in binary write mode and write the content in chunks
        with open(save_path, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)
        print(f"File successfully downloaded and saved to {save_path}")

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")


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


def download_all_files(course_id, folder_id_name_map):
    url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/files?per_page=100"
    file_list = requests.get(url, headers=headers).json()

    print(f"{len(file_list)} files found")
    print(file_list)

    for file in file_list:
        print(file)
        print(file['folder_id'], file['filename'], file['url'], file['folder_id'])
        
        folder_path = resolve_path_for_file(file['folder_id'], folder_id_name_map)
        filename = os.path.join(OUTPUT_PATH, folder_path, file['filename'])

        download_file(file['url'], filename)


if __name__ == "__main__":
    for course_id, course_code in get_all_courses():
        folder_id_name_map = get_all_folders(course_id, course_code)
        download_all_files(course_id, folder_id_name_map)
