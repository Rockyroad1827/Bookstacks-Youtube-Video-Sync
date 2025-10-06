from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle # Used to securely save and load the token
import os.path

# --- Configuration --- #
# You need to define the path to your OAuth Client credentials file
CLIENT_SECRET_FILE = 'credentials.json' 
# The file where the token will be saved after the first run
TOKEN_FILE = 'token.json'
# Scope required to see non-public channel data
SCOPES = ['https://www.googleapis.com/auth/youtube.readonly'] 
# --------------------- #


def get_authenticated_service():
    """Initializes the YouTube API service using OAuth2 credentials."""
    creds = None
    
    # 1. Load existing token if available (to skip interactive login)
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)

    # 2. If no valid credentials, handle refresh or initial flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # 2a. Credentials exist but are expired -> Use Refresh Token
            print("Refreshing access token...")
            creds.refresh(Request())
        else:
            # 2b. Initial run or no refresh token -> Start interactive flow
            print("Starting interactive OAuth flow. A browser window will open...")
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # 3. Save the new credentials (including the updated Refresh Token)
        with open(TOKEN_FILE, 'wb') as token:
            print(f"Saving new token to {TOKEN_FILE}")
            pickle.dump(creds, token)

    # 4. Initialize and return the authenticated service
    return build('youtube', 'v3', credentials=creds)


# --- Example Use in Your Script ---
# Replace this line in your global setup:
# YOUTUBE_SERVICE = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

# With this:
YOUTUBE_SERVICE = get_authenticated_service()

# --- Your main run_sync() function will now use the authenticated YOUTUBE_SERVICE ---