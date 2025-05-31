# Native Stuff
import sys,json
from sys import exit
from tkinter import filedialog

# Sets the working directories at launch. I don't recommend keeping secret stuff in the same spot as the data.
print("Specify directory for data to be downloaded to...")
DATA_DIRECTORY = filedialog.askdirectory(title="Specify directory for data to be downloaded to")
print(f"Data Directory: {DATA_DIRECTORY}")

print("Specify directory where Secrets and/or Cookies are...")
SECRETS_DIRECTORY = filedialog.askdirectory(title="Specify directory where Secrets and/or Cookies are")
print(f"Secrets Directory: {SECRETS_DIRECTORY}")

if not DATA_DIRECTORY or SECRETS_DIRECTORY:
    sys.exit()

# Settings for the Oauth 2.0 Configuration used by the YT_API Class.
CLIENT_SECRETS_FILE = f'{SECRETS_DIRECTORY}/client_secret.json'  # Download this from Google Cloud Console
TOKEN_PICKLE_FILE = f'{SECRETS_DIRECTORY}/token.pickle'# Will be created on first launch

#Set this if you want to write to a member's only database.
MEMBERS = False

# Needed to access chat messages from member's only videos. Use browser addins to generate, make sure name matches.
# NOTE: Once you've exported the cookies, CLOSE that browser (or user agent) and do not open/use until this program finishes.
# Keeping the browser open tends to make the YT cookies reset and break the access to member's only videos.
COOKIES = None if MEMBERS == False else f"{SECRETS_DIRECTORY}/cookies.txt"

# These folders will be automatically created if they don't exist. It's where the JSON files and thumbnails will be saved to.
DATA_FOLDER_NAME = "Data_Public" if MEMBERS == False else "Data_Members"

# The main data path used elsewhere in code
DATA_PATH = f"{DATA_DIRECTORY}/{DATA_FOLDER_NAME}"

# Database Configuration settngs
DB_VERBOSE = False

# Edit the template provided and stuff it in your secrets folder
with open(f"{SECRETS_DIRECTORY}/DB_Settings.json",'r') as file:
    db_settings = json.load(file)

DB_USR = db_settings["DB_USR"]
DB_PASS = db_settings["DB_PASS"]
DB_HOST = db_settings["DB_HOST"]
DB_PORT = db_settings["DB_PORT"]
DB_NAME = "YTDB" if MEMBERS == False else "YTDB_Members"

# YT Information
# Given just the UserID, all other IDs can be generated for the upload playlists and channel ID.
# If you only have the ChannelID, remove the "UC" at the start and put it into the UserID field
YT_USER_ID = "Hsx4Hqa-1ORjQTh9TYDhww" # Takanashi Kiara's UserID
YT_CHANNEL_ID = "UC" + YT_USER_ID
UPLOAD_PLAYLIST = "UU" + YT_USER_ID # Hidden playlist containing ALL publically accessible Youtube Videos, Livestream VODs, and Shorts.
MEMBERS_ONLY_PLAYLIST = "UUMO" + YT_USER_ID # Hiiden playlist containing ALL non-privated member's only Youtube Videos, Livestream VODs, and Shorts.

# Change this to a string of a Playlist ID if you want to download a custom playlist of video data.
CUSTOM_PLAYLIST = False

# Logging Configuration
DEBUG_LOG_FILE='Debug' # Used when DEBUG is set to True
LOG_VERBOSE = False # Any debug messages will appear
LOG_NAME = "LOG" # Log file prefix

# Currently only used to create one log file and not multiple
DEBUG = False

# Leave this be, edit CUSTOM PLAYLIST and MEMBERS values above instead.
if CUSTOM_PLAYLIST == False:
    PLAYLIST = UPLOAD_PLAYLIST if MEMBERS == False else MEMBERS_ONLY_PLAYLIST
else:
    PLAYLIST = CUSTOM_PLAYLIST