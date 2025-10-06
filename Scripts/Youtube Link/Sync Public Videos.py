import requests
import json
import time
import urllib3
import logging
import subprocess # Added for shell script execution
import sys # Added for shell script execution
import os # Added for shell script execution
from googleapiclient.discovery import build
from requests.adapters import HTTPAdapter, Retry
from urllib.parse import urlparse
import re 
# --- NEW OAUTH IMPORTS ---
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle # Used to securely save and load the token
import os.path
#--------------------------


# Suppress warnings about skipping SSL verification (necessary due to non-standard port/cert)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging for better error visibility
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')




# --- CONFIGURATION ---
# IMPORTANT: Replace these placeholder values with your actual credentials and settings.

# BookStack API Settings
BOOKSTACK_URL = ""  #YOUR BASE URL
BOOKSTACK_TOKEN_ID = ""  #YOUR BOOKSTACK API ID
BOOKSTACK_TOKEN_SECRET = ""  #YOUR BOOKSTACK API TOKEN
TARGET_BOOK_ID = 000 # <-- Book ID where Chapters and Pages will be created

# YouTube API Settings
YOUTUBE_CHANNEL_ID = "" # The channel ID whose playlists will be synced
PURGE_SCRIPT_PATH = "./purge_recycle_bin.sh"

#----------------------------------------------------------------------#




# --- NEW OAUTH CONFIGURATION ---
# MUST match the full, exact name of the downloaded client file
CLIENT_SECRET_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'
SCOPES = ['https://www.googleapis.com/auth/youtube.readonly'] 
# -----------------------------

# Sync Parameters
REQUEST_TIMEOUT_SECONDS = 15 # Timeout for all BookStack API calls
API_DELAY_SECONDS = 0.5 # Delay between BookStack API calls to prevent flooding

# NEW CONFIG: Control whether to append the YouTube ID to the page title
APPEND_YOUTUBE_ID_TO_TITLE = False 

# CRITICAL: If True, all existing pages and chapters in TARGET_BOOK_ID will be deleted before re-syncing.
# NOTE: Pages deleted during FORCE_RESYNC will now be permanently deleted (hard_delete=true).
FORCE_RESYNC = True 


# --- GLOBAL SETUP ---

# Headers for BookStack API authentication
BOOKSTACK_HEADERS = {
    "Authorization": f"Token {BOOKSTACK_TOKEN_ID}:{BOOKSTACK_TOKEN_SECRET}",
    "Accept": "application/json",
    "Connection": "close",
    "Content-Type": "application/json"
}

# --- OAUTH AUTHENTICATION FUNCTION ---
def get_authenticated_service():
    """Initializes the YouTube API service using OAuth2 credentials."""
    creds = None
    
    # 1. Load existing token if available
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)

    # 2. Handle token refresh or initial interactive flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Token expired but can be refreshed silently
            print("Refreshing access token...")
            creds.refresh(Request())
        else:
            # Token missing or refresh failed, start interactive flow
            print("Starting interactive OAuth flow. A browser window will open...")
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # 3. Save the new/refreshed credentials
        with open(TOKEN_FILE, 'wb') as token:
            print(f"Saving new token to {TOKEN_FILE}")
            pickle.dump(creds, token)

    # 4. Initialize and return the authenticated service
    return build('youtube', 'v3', credentials=creds)

# Initialize YouTube API client using OAuth
YOUTUBE_SERVICE = get_authenticated_service()

# Regex to find the YouTube ID in the saved page content's iframe embed URL
YOUTUBE_EMBED_REGEX = re.compile(r'youtube\.com/embed/([a-zA-Z0-9_-]{11})')


# --- UTILITY FUNCTIONS ---
def create_bookstack_session():
    """Creates a requests session with retries and timeout configuration."""
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries, pool_connections=1, pool_maxsize=1)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def extract_items_from_structure(structure, item_type='page'):
    """
    Recursively extracts all items of a specific type (page or chapter)
    from the BookStack structure ('contents' key).
    """
    items = []
    for item in structure:
        if item.get('type') == item_type:
            items.append(item)
        elif item.get('type') == 'chapter':
            # Recurse into chapter's pages if we are looking for pages
            if item_type == 'page':
                items.extend(extract_items_from_structure(item.get('pages', []), 'page'))
            # Include the chapter itself if we are looking for chapters
            elif item_type == 'chapter':
                items.extend(extract_items_from_structure(item.get('pages', []), 'chapter')) # Checks for nested chapters (though rare)
    return items

def get_book_items_for_deletion(book_id):
    """
    Fetches all pages AND chapters in the book for deletion when FORCE_RESYNC is true.
    Returns: Two lists (pages, chapters)
    """
    session = create_bookstack_session()
    url = f"{BOOKSTACK_URL.rstrip('/')}/api/books/{book_id}"
    print(f"-> Fetching full contents for Book ID {book_id} to prepare deletion...")
    try:
        response = session.get(url, headers=BOOKSTACK_HEADERS, verify=False, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        contents = response.json().get('contents', [])
        
        # BookStack structure can be complex, extract items explicitly
        pages_to_delete = extract_items_from_structure(contents, item_type='page')
        chapters_to_delete = extract_items_from_structure(contents, item_type='chapter')
        
        # Important: Extract chapters directly from the top level of 'contents' 
        top_level_chapters = [item for item in contents if item.get('type') == 'chapter']
        
        # Combine top level and any potentially nested chapters
        all_chapters = list({c['id']: c for c in chapters_to_delete + top_level_chapters}.values())

        print(f"  Found {len(pages_to_delete)} pages and {len(all_chapters)} chapters to delete.")
        return pages_to_delete, all_chapters

    except requests.exceptions.RequestException as e:
        print(f"Error fetching page list for deletion: {e}")
        return [], []

def get_existing_chapters(book_id):
    """
    Fetches all existing chapters in the book.
    Returns: A dictionary mapping {chapter_name: chapter_id, ...}
    """
    session = create_bookstack_session()
    chapter_map = {}
    url_book_structure = f"{BOOKSTACK_URL.rstrip('/')}/api/books/{book_id}"
    
    try:
        response = session.get(
            url_book_structure, 
            headers=BOOKSTACK_HEADERS, 
            verify=False, 
            timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        
        # The /api/books/{id} endpoint uses 'contents' key
        contents = response.json().get('contents', [])
        
        # Chapters are top-level items in contents
        chapters = [item for item in contents if item.get('type') == 'chapter']
        
        for chapter in chapters:
            chapter_map[chapter['name']] = chapter['id']
            
    except requests.exceptions.RequestException as e:
        print(f"Error getting book structure to find chapters: {e}")
        
    print(f"  Found {len(chapter_map)} existing BookStack chapters to check against.")
    return chapter_map

def create_bookstack_chapter(book_id, chapter_name):
    """
    Creates a new chapter in BookStack.
    Returns: The new chapter ID (integer) or None on failure.
    """
    session = create_bookstack_session()
    url = f"{BOOKSTACK_URL.rstrip('/')}/api/chapters"
    
    payload = {
        'name': chapter_name,
        'book_id': book_id,
        'description': f"Videos from YouTube playlist: {chapter_name}",
    }
    
    print(f"  -> Creating new chapter: '{chapter_name}'")

    try:
        time.sleep(API_DELAY_SECONDS)
        response = session.post(
            url, 
            headers=BOOKSTACK_HEADERS, 
            json=payload, 
            verify=False, 
            timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        
        new_chapter_data = response.json()
        print(f"  SUCCESS: Created Chapter ID {new_chapter_data['id']}.")
        return new_chapter_data['id']
        
    except requests.exceptions.RequestException as e:
        http_status = e.response.status_code if e.response is not None else "Unknown"
        error_details = e.response.text if e.response is not None else str(e)
        
        print(f"  FAILED to create chapter '{chapter_name}'. HTTP Error {http_status}: {error_details}")
        return None

def delete_bookstack_item(item_id, item_type, item_title):
    """
    Deletes a page or chapter from BookStack.
    
    NOTE: For pages, 'hard_delete=true' is added to bypass the recycle bin.
    """
    session = create_bookstack_session()
    
    params = {}
    
    # API endpoints for deletion are different
    if item_type == 'page':
        url = f"{BOOKSTACK_URL.rstrip('/')}/api/pages/{item_id}"
        # Setting hard_delete=true to skip the recycle bin
        params = {'hard_delete': 'true'} 
    elif item_type == 'chapter':
        url = f"{BOOKSTACK_URL.rstrip('/')}/api/chapters/{item_id}"
        # Chapters often bypass the bin when deleted from a book, but we don't 
        # need hard_delete here as the default DELETE is usually sufficient for chapters.
    else:
        return False

    try:
        response = session.delete(
            url, 
            headers=BOOKSTACK_HEADERS, 
            params=params, # Pass the parameters (including hard_delete)
            verify=False, 
            timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        
        delete_type = "PERMANENTLY DELETED" if item_type == 'page' and params.get('hard_delete') == 'true' else "DELETED"
        print(f"  {delete_type}: {item_type.capitalize()} ID {item_id} ('{item_title}')")
        return True
        
    except requests.exceptions.RequestException as e:
        http_status = e.response.status_code if e.response is not None else "Unknown"
        error_details = e.response.text if e.response is not None else str(e)
        
        print(f"  FAILED to delete {item_type} ID {item_id} ('{item_title}'). HTTP Error {http_status}: {error_details}")
        
        return False


def get_channel_uploads_playlist_id():
    """
    Fetches the special playlist ID that contains ALL channel uploads.
    Returns: The uploads playlist ID (string) or None on failure.
    """
    print("-> Fetching Channel Uploads Playlist ID...")
    try:
        # Request for the channel details to get the uploads playlist ID
        channels_request = YOUTUBE_SERVICE.channels().list(
            id=YOUTUBE_CHANNEL_ID,
            part='contentDetails',
            maxResults=1
        )
        channels_response = channels_request.execute()
        
        # The uploads playlist ID is nested in contentDetails
        uploads_playlist_id = channels_response['items'][0]['contentDetails']['relatedPlaylists']['uploads']
        
        print(f"  Found Uploads Playlist ID: {uploads_playlist_id}")
        return uploads_playlist_id
        
    except Exception as e:
        print(f"Error fetching channel uploads playlist ID: {e}")
        return None

def fetch_all_videos_for_playlist(playlist_id, playlist_title):
    """
    Helper function to paginate through all items in a given playlist 
    and fetch full video details for all items.
    Returns: list of video detail dictionaries.
    """
    current_playlist_video_ids = []
    next_video_token = None
    
    # 1. Collect all video IDs from the playlist
    while True:
        try:
            playlist_items_request = YOUTUBE_SERVICE.playlistItems().list(
                playlistId=playlist_id,
                part='contentDetails',
                maxResults=50,
                pageToken=next_video_token
            )
            items_response = playlist_items_request.execute()
            
            current_playlist_video_ids.extend([
                item['contentDetails']['videoId'] 
                for item in items_response.get('items', []) 
                if item.get('contentDetails', {}).get('videoId')
            ])
            
            next_video_token = items_response.get('nextPageToken')
            if not next_video_token:
                break

        except Exception as e:
            print(f"  Error fetching items for playlist '{playlist_title}': {e}")
            break
            
    # 2. Fetch full details for the collected video IDs (in batches of 50)
    video_details = []
    for i in range(0, len(current_playlist_video_ids), 50):
        video_ids_chunk = current_playlist_video_ids[i:i + 50]
        try:
            video_request = YOUTUBE_SERVICE.videos().list(
                id=','.join(video_ids_chunk),
                part='snippet,contentDetails'
            )
            video_response = video_request.execute()
            video_details.extend(video_response.get('items', []))
        except Exception as e:
            print(f"  Error fetching video details for chunk in '{playlist_title}': {e}")
            
    return video_details


def get_playlists_and_videos():
    """
    Fetches user-created playlists and determines the set of videos 
    that are uploaded but NOT in any user-created playlist (uncategorized).
    
    Returns: A tuple: 
             (List of structured playlist data, List of uncategorized video details)
    """
    user_playlists_sync_list = []
    
    # 1. Get the Master List of ALL Uploads
    uploads_playlist_id = get_channel_uploads_playlist_id()
    master_uploads_video_data = []
    all_uploads_video_ids = set()
    
    if uploads_playlist_id:
        print("\n-> Fetching ALL videos from the master uploads feed to establish the channel inventory...")
        master_uploads_video_data = fetch_all_videos_for_playlist(uploads_playlist_id, "Master Uploads Feed")
        all_uploads_video_ids.update({v['id'] for v in master_uploads_video_data if 'id' in v})
    else:
        print("WARNING: Could not fetch master uploads playlist ID. Cannot determine uncategorized videos.")
        return [], []
        
    
    # 2. Fetch User-Created Playlists Metadata
    user_playlists_metadata = []
    all_user_playlist_video_ids = set()
    next_playlist_token = None
    print(f"\n-> Fetching all user-created playlists for Channel ID: {YOUTUBE_CHANNEL_ID}")

    while True:
        try:
            playlists_request = YOUTUBE_SERVICE.playlists().list(
                channelId=YOUTUBE_CHANNEL_ID,
                part='snippet,contentDetails',
                maxResults=50,
                pageToken=next_playlist_token
            )
            playlists_response = playlists_request.execute()
            
            for playlist in playlists_response.get('items', []):
                title = playlist['snippet']['title']
                p_id = playlist['id']
                
                if title:
                    user_playlists_metadata.append({
                        'playlist_id': p_id,
                        'playlist_title': title,
                        'videos': [], 
                        'is_uploads_feed': False
                    })

            next_playlist_token = playlists_response.get('nextPageToken')
            if not next_playlist_token:
                break
                
        except Exception as e:
            print(f"Error during YouTube user-created playlist fetch: {e}")
            break
            
    print(f"  Found {len(user_playlists_metadata)} user-created playlists.")
    
    # 3. Process User-Created Playlists (Fetch Videos and Track IDs)
    print("\n-> Fetching videos and recording IDs for each user-created playlist...")
    
    for playlist_data in user_playlists_metadata:
        videos_in_playlist = fetch_all_videos_for_playlist(playlist_data['playlist_id'], playlist_data['playlist_title'])
        playlist_data['videos'] = videos_in_playlist
        
        # Collect all video IDs in user playlists
        all_user_playlist_video_ids.update({v['id'] for v in videos_in_playlist if 'id' in v})
        print(f"    Found {len(videos_in_playlist)} videos in playlist '{playlist_data['playlist_title']}'.")
        
        user_playlists_sync_list.append(playlist_data)


    # 4. Create the "Uncategorized Channel Uploads" List
    uncategorized_videos = []
    print(f"\n-> Filtering {len(master_uploads_video_data)} total uploads to find uncategorized videos...")
    
    # Filter videos that are in the master upload list but NOT in any user playlist
    for video in master_uploads_video_data:
        video_id = video.get('id')
        if video_id and video_id not in all_user_playlist_video_ids:
            uncategorized_videos.append(video)

    if uncategorized_videos:
        print(f"  Found {len(uncategorized_videos)} uncategorized videos to be added to the Book root.")
    else:
        print("  No uncategorized videos found.")

    total_videos_to_sync = len(all_user_playlist_video_ids) + len(uncategorized_videos)
    print(f"\nSuccessfully compiled {len(user_playlists_sync_list)} playlists and a unique set of {len(uncategorized_videos)} uncategorized videos (Total unique videos: {total_videos_to_sync}).")
    
    # Return two separate lists
    return user_playlists_sync_list, uncategorized_videos


def get_existing_page_details(book_id):
    """
    Fetches the content of every existing page in the book to extract the 
    synced YouTube video ID for robust duplication checking.
    
    Returns: A dictionary mapping {youtube_id: page_id, ...} for all synced pages.
    """
    session = create_bookstack_session()
    pages_map = {}
    
    print(f"-> Scanning existing BookStack page content for embedded YouTube IDs...")
    url_book_structure = f"{BOOKSTACK_URL.rstrip('/')}/api/books/{book_id}"
    
    try:
        response = session.get(
            url_book_structure, 
            headers=BOOKSTACK_HEADERS, 
            verify=False, 
            timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        
        # Get all pages, including those nested in chapters
        pages_to_check = extract_items_from_structure(response.json().get('contents', []), item_type='page')
    except requests.exceptions.RequestException as e:
        print(f"Error getting book structure: {e}")
        return pages_map 
    
    print(f"  Found {len(pages_to_check)} existing pages to check.")
    
    count_scanned = 0
    count_found = 0
    
    for page_data in pages_to_check:
        page_id = page_data.get('id')
        page_name = page_data.get('name')
        
        url_page_content = f"{BOOKSTACK_URL.rstrip('/')}/api/pages/{page_id}"
        
        try:
            # Politeness delay before fetching content for the next page
            time.sleep(API_DELAY_SECONDS)
            
            response = session.get(
                url_page_content, 
                headers=BOOKSTACK_HEADERS, 
                verify=False, 
                timeout=REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            content_data = response.json()
            
            html_content = content_data.get('html', '')
            match = YOUTUBE_EMBED_REGEX.search(html_content)
            
            if match:
                youtube_id = match.group(1)
                pages_map[youtube_id] = page_id
                count_found += 1
            
        except requests.exceptions.RequestException as e:
            http_status = e.response.status_code if e.response is not None else "Unknown"
            print(f"  FAILED to fetch content for page '{page_name}' (ID: {page_id}). HTTP Error {http_status}.")
            
        count_scanned += 1
        
    print(f"  Scan complete. Found {count_found} YouTube IDs mapped across {count_scanned} pages in the BookStack content.")
    return pages_map

def create_bookstack_page(video_data, book_id, chapter_id=None):
    """
    Constructs the page content and calls the BookStack API to create a new page
    within the specified chapter OR directly in the book if chapter_id is None.
    """
    session = create_bookstack_session()
    snippet = video_data['snippet']
    video_id = video_data['id']
    
    # --- TITLE GENERATION ---
    title = snippet['title']
    full_title = title.strip()
    
    if APPEND_YOUTUBE_ID_TO_TITLE:
        full_title = f"{full_title} (YouTube ID: {video_id})"

    print(f"    Attempting to create page for: '{full_title}'")

    video_url = f"https://www.youtube.com/watch?v={video_id}"
    embed_url = f"https://www.youtube.com/embed/{video_id}"
    
    # --- HTML Page Content Generation ---
    html_content = f"""
    <div style="text-align: center; margin-bottom: 25px;">
        <iframe width="853" height="480" 
            src="{embed_url}" 
            title="{full_title}" 
            frameborder="0" 
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" 
            allowfullscreen>
        </iframe>
    </div>

    <p style="font-size: 1.2em; font-weight: bold;">Video Description:</p>
    <hr>
    <p>{snippet['description'].replace('\\n', '<br>')}</p>

    <p style="font-size: 1.2em; font-weight: bold; margin-top: 20px;">Video Details:</p>
    <ul>
        <li><strong>YouTube URL:</strong> <a href="{video_url}" target="_blank">{video_url}</a></li>
        <li><strong>Published:</strong> {snippet['publishedAt']}</li>
        <li><strong>Channel:</strong> {snippet['channelTitle']}</li>
    </ul>
    """
    
    # Base payload structure (Python dict)
    payload = {
        'name': full_title,
        'html': html_content,
        'book_id': book_id,
        'tags': [{'name': tag, 'value': '', 'order': 0} for tag in snippet.get('tags', [])]
    }
    
    # Only include chapter_id if it is provided
    if chapter_id is not None:
        payload['chapter_id'] = chapter_id
        
    url = f"{BOOKSTACK_URL.rstrip('/')}/api/pages"
    
    try:
        response = session.post(
            url, 
            headers=BOOKSTACK_HEADERS, 
            json=payload, 
            verify=False, 
            timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        
        new_page_data = response.json()
        new_page_slug = new_page_data.get('slug')
        
        print(f"    SUCCESS: Created page '{full_title}'.")
        return True
        
    except requests.exceptions.RequestException as e:
        http_status = e.response.status_code if e.response is not None else "Unknown"
        error_details = e.response.text if e.response is not None else str(e)
        
        print(f"    FAILED to create page '{full_title}'. HTTP Error {http_status}: {error_details}")
        
        return False

def run_purge_script():
    """
    Executes the external shell script (purge_recycle_bin.sh) using subprocess.
    """
    print(f"\n--- Calling external purge script: {PURGE_SCRIPT_PATH} ---")
    
    # 1. Check if the script exists
    if not os.path.exists(PURGE_SCRIPT_PATH):
        print(f"ERROR: Purge script not found at {PURGE_SCRIPT_PATH}. Recycle bin was NOT purged.", file=sys.stderr)
        return False

    # 2. Execute the shell script
    try:
        # check=True ensures that if the shell script returns a non-zero error code, 
        # Python raises a CalledProcessError, stopping execution here.
        result = subprocess.run(
            [PURGE_SCRIPT_PATH],
            check=True,
            capture_output=True,
            text=True,
            shell=False # Safer execution
        )
        
        print("Purge Script Output:")
        # Print the output from the shell script
        print(result.stdout.strip())
        print("--- Recycle bin purge script executed successfully! ---")
        return True
        
    except subprocess.CalledProcessError as e:
        # If the shell script itself failed (e.g., MySQL error)
        print(f"ERROR: The purge script failed during execution (Exit Code {e.returncode}). Recycle bin was NOT purged.", file=sys.stderr)
        print(f"Stderr from shell script:\n{e.stderr.strip()}", file=sys.stderr)
        return False
    except Exception as e:
        # Catch other potential errors (like permissions)
        print(f"An unexpected error occurred while trying to run the script: {e}. Recycle bin was NOT purged.", file=sys.stderr)
        return False

# --- MAIN SYNC LOGIC ---

def run_sync():
    """
    Orchestrates the entire synchronization process, including chapters and pages.
    """
    print("--- YouTube Playlist to BookStack Chapter Sync Tool ---")
    
    book_id = TARGET_BOOK_ID

    if book_id is None:
        print("Sync stopped: TARGET_BOOK_ID is not set. Please set the integer ID.")
        return

    # 1. Fetch ALL YouTube Playlists and their Videos (includes Uncategorized)
    playlists_data, uncategorized_videos_data = get_playlists_and_videos()

    if not playlists_data and not uncategorized_videos_data:
        print("Sync stopped: No playlists or uncategorized videos found or API error.")
        return
        
    
    # 2. Check for Force Resync / Deletion
    pages_deleted = 0
    chapters_deleted = 0
    
    if FORCE_RESYNC:
        print("\n--- WARNING: DESTROY/RESYNC MODE ACTIVE (Deleting All Existing Pages/Chapters) ---")
        
        pages_to_delete, chapters_to_delete = get_book_items_for_deletion(book_id)
        
        # 2a. Delete all pages first
        if pages_to_delete:
            print(f"-> Deleting {len(pages_to_delete)} existing pages...")
            for page_info in pages_to_delete:
                # Page deletion now includes hard_delete=true for permanent removal
                if delete_bookstack_item(page_info['id'], 'page', page_info['name']):
                    pages_deleted += 1
                time.sleep(API_DELAY_SECONDS) 
        else:
            print("-> No existing pages found to delete.")
            
        # 2b. Delete all chapters
        if chapters_to_delete:
            print(f"-> Deleting {len(chapters_to_delete)} existing chapters...")
            for chapter_info in chapters_to_delete:
                if delete_bookstack_item(chapter_info['id'], 'chapter', chapter_info['name']):
                    chapters_deleted += 1
                time.sleep(API_DELAY_SECONDS)
        else:
            print("-> No existing chapters found to delete.")

        # When FORCE_RESYNC is true, skip duplication check
        existing_pages_map = {}
        print("-> Duplication check bypassed for creation phase.")
        
    else:
        # 3. Scan existing pages for YouTube IDs (Robust Duplication Check)
        print("\n--- Scanning Existing BookStack Page Content ---")
        existing_pages_map = get_existing_page_details(book_id)
        
    
    # 4. Check/Create BookStack Chapters (only for playlists)
    print("\n--- Checking/Creating BookStack Chapters for Playlists ---")
    # Fetch existing chapters to prevent re-creation
    bookstack_chapter_map = get_existing_chapters(book_id)
    
    # This will store the final mapping: YouTube Playlist Title -> BookStack Chapter ID
    chapter_sync_map = {}
    chapters_created = 0
    
    for playlist in playlists_data:
        p_title = playlist['playlist_title']
        
        if p_title in bookstack_chapter_map:
            # Chapter exists, use its ID
            chapter_id = bookstack_chapter_map[p_title]
            print(f"  Found existing Chapter '{p_title}' (ID: {chapter_id}).")
        else:
            # Chapter does not exist, create it
            chapter_id = create_bookstack_chapter(book_id, p_title)
            if chapter_id:
                chapters_created += 1
            
        chapter_sync_map[p_title] = chapter_id
        
    
    # 5. Start Page Creation Process
    print("\n--- Starting Page Creation Process ---")
    
    total_videos_processed = 0
    pages_created = 0
    pages_skipped = 0
    
    # 5a. Process videos inside user-created chapters
    for playlist in playlists_data:
        p_title = playlist['playlist_title']
        chapter_id = chapter_sync_map.get(p_title)
        
        if not chapter_id:
            print(f"WARNING: Could not determine chapter ID for playlist '{p_title}'. Skipping videos in this playlist.")
            continue
            
        print(f"\n-> Syncing videos into Chapter: '{p_title}' (ID: {chapter_id})")
        
        for video in playlist['videos']:
            video_id = video['id']
            total_videos_processed += 1
            
            # Check for duplication using the YouTube ID found in BookStack content
            if video_id in existing_pages_map:
                pages_skipped += 1
                print(f"    Skipping: Video ID {video_id} is already present in BookStack (Page ID: {existing_pages_map[video_id]}).")
                continue
            
            # If not found, create the page and associate it with the chapter
            created = create_bookstack_page(video, book_id, chapter_id)
            
            if created:
                pages_created += 1
            
            # Politeness delay
            time.sleep(API_DELAY_SECONDS)
            
    # 5b. Process uncategorized videos (direct to book root)
    if uncategorized_videos_data:
        print(f"\n-> Syncing {len(uncategorized_videos_data)} uncategorized videos directly into the Book root.")
        for video in uncategorized_videos_data:
            video_id = video['id']
            total_videos_processed += 1
            
            # Check for duplication
            if video_id in existing_pages_map:
                pages_skipped += 1
                print(f"    Skipping: Video ID {video_id} is already present in BookStack (Page ID: {existing_pages_map[video_id]}).")
                continue
                
            # Create page with chapter_id=None
            # NOTE: Pages created without a chapter_id will be placed at the root of the book.
            created = create_bookstack_page(video, book_id, chapter_id=None)
            
            if created:
                pages_created += 1
                
            time.sleep(API_DELAY_SECONDS)

    # 6. Final Summary
    print("\n--- Sync Complete ---")
    print(f"Total YouTube videos processed: {total_videos_processed}")
    if FORCE_RESYNC:
        print(f"Total existing pages permanently deleted: {pages_deleted}")
        print(f"Total existing chapters deleted: {chapters_deleted}")
    print(f"New chapters created: {chapters_created}")
    print(f"New pages created: {pages_created}")
    if not FORCE_RESYNC:
        print(f"Videos skipped (already synced): {pages_skipped}")

    run_purge_script()
if __name__ == "__main__":
    run_sync()