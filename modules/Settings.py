# Native Stuff
import json
from sys import exit
from tkinter import filedialog,messagebox

##############################
### USER EDITABLE SETTINGS ###
##############################

# YT INFORMATION
# Given just the UserID, all other IDs can be generated for the upload playlists and channel ID.
# If you only have the ChannelID, remove the "UC" at the start and put it into the UserID field.
# NOTE: Custom handles DO NOT work, you need the ID number with all the random characters.
MEMBER_DIRECTORY = {
    "Calli":{
        "user_id":"L_qhgtOy0dy1Agp8vkySQg",
        "database":"YTDB_Calli",
        "database_members":"YTDB_Calli_Members",
    },
    "Kiara":{
        "user_id":"Hsx4Hqa-1ORjQTh9TYDhww",
        "database":"YTDB_Kiara",
        "database_members":"YTDB_Kiara_Members",
    },
    "Ina":{
        "user_id":"MwGHR0BTZuLsmjY_NT5Pwg",
        "database":"YTDB_Ina",
        "database_members":"YTDB_Ina_Members",
    },
    "Gura":{
        "user_id":"oSrY_IQQVpmIRZ9Xf-y93g",
        "database":"YTDB_Gura",
        "database_members":"YTDB_Gura_Members",
    },
    "Ame":{
        "user_id":"yl1z3jo3XHR1riLFKG5UAg",
        "database":"YTDB_Ame",
        "database_members":"YTDB_Ame_Members",
    },
}

# Pick the member entry you'd like here
MEMBER_SELECTOR = MEMBER_DIRECTORY["Kiara"]

# Database Configuration settngs
DB_VERBOSE = False

# Logging Configuration
DEBUG_LOG_FILE='Chat_Process_Log' # Used when CONTINUOUS_LOG is set to True
LOG_VERBOSE = False # Any debug messages will appear
LOG_NAME = "LOG" # Log file prefix
CONTINUOUS_LOG = True # Create one continuous log file and not separate ones per-run

#####################################
### OTHER SETTINGS (DO NOT TOUCH) ###
#####################################

# Will ask if a log file will be created at all
LOG = messagebox.askyesno("Logging","Do you want to write the console log to file?") # Create a log file (In script location)

# Sets the working directories at launch. I don't recommend keeping secret stuff in the same spot as the data.
DATA_DIRECTORY = filedialog.askdirectory(title="Specify directory for data to be downloaded to")
SECRETS_DIRECTORY = filedialog.askdirectory(title="Specify directory where Secrets and/or Cookies are")

# Will exit if either folder dialog boxes were closed
if DATA_DIRECTORY == "" or SECRETS_DIRECTORY == "":
    exit()

# Settings for the Oauth 2.0 Configuration used by the YT_API Class.
CLIENT_SECRETS_FILE = f'{SECRETS_DIRECTORY}/client_secret.json'  # Download this from Google Cloud Console
TOKEN_PICKLE_FILE = f'{SECRETS_DIRECTORY}/token.pickle'# Will be created on first launch

# Set this if you want to write to a member's only database.
MEMBERS = messagebox.askyesno("Members-Only","Do you want to download Members-Only video data? (BE SURE COOKIES ARE UP-TO-DATE)")

# The chat scraper can timeout if there is a livestream going and no new messages arrive.
# Good for if there's a livestream (either live or waiting), but getting all other videos are desired.
TIMEOUT = messagebox.askyesno("Chat-Timeout","Do you want the chat scraper to timeout?\n(Pick no if you want it to keep watching a livestream.)")

# Needed to access chat messages from member's only videos. Use browser addins to generate, make sure name matches.
# NOTE: Once you've exported the cookies, CLOSE that browser (or user agent) and do not open/use until this program finishes.
# Keeping the browser open tends to make the YT cookies reset and break the access to member's only videos.
COOKIES = None if MEMBERS == False else f"{SECRETS_DIRECTORY}/cookies.txt"

# These folders will be automatically created if they don't exist. It's where the JSON files and thumbnails will be saved to.
DATA_FOLDER_NAME = "Data_Public" if MEMBERS == False else "Data_Members"

# The main data path used elsewhere in code
DATA_PATH = f"{DATA_DIRECTORY}/{DATA_FOLDER_NAME}"

# Edit the template provided and stuff it in your secrets folder
with open(f"{SECRETS_DIRECTORY}/DB_Settings.json",'r') as file:
    db_settings = json.load(file)

# Auto-filled out data from the settings file and other settings
DB_USR = db_settings["DB_USR"]
DB_PASS = db_settings["DB_PASS"]
DB_HOST = db_settings["DB_HOST"]
DB_PORT = db_settings["DB_PORT"]
DB_NAME = MEMBER_SELECTOR["database"] if MEMBERS == False else MEMBER_SELECTOR["database_members"]

# Auto-filled out data for Youtube data
YT_USER_ID = MEMBER_SELECTOR["user_id"] # UserID of the selected member
YT_CHANNEL_ID = "UC" + YT_USER_ID
UPLOAD_PLAYLIST = "UU" + YT_USER_ID # Hidden playlist containing ALL publically accessible Youtube Videos, Livestream VODs, and Shorts.
MEMBERS_ONLY_PLAYLIST = "UUMO" + YT_USER_ID # Hiiden playlist containing ALL non-privated member's only Youtube Videos, Livestream VODs, and Shorts.

# Leave this be, edit CUSTOM PLAYLIST and MEMBERS values above instead.
PLAYLIST = UPLOAD_PLAYLIST if MEMBERS == False else MEMBERS_ONLY_PLAYLIST