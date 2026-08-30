# Native Stuff
import json,os
from sys import exit
from tkinter import filedialog,messagebox
from psycopg2.extensions import cursor

##############################
### USER EDITABLE SETTINGS ###
##############################

PROCESS_ALL = True
CHANNEL_SELECTION = "Kiara"
USE_COOKIES = False

# Seconds to wait between starting each chat download. The rate limiter enforces this
# gap even across concurrent workers so YouTube isn't hit simultaneously. 0 to disable.
REQUEST_DELAY = 2.0

# Just process video/chat data and skip fetching auto-generated subtitles.
SKIP_SUBTITLE_DOWNLOAD = False

# Language codes to request auto-generated subtitles for (yt-dlp subtitleslangs).
SUBTITLE_LANGUAGES = ["en"]

# Starting point (floor) for the adaptive delay between subtitle download attempts --
# separate budget from REQUEST_DELAY above, since yt-dlp and chat_downloader are different
# clients hitting YouTube at different points in the pipeline. This is only a starting
# point, not fixed: AdaptiveRateLimiter (modules/Classes.py) grows it automatically whenever
# a throttle is observed, and slowly relaxes it back down after a long clean streak -- a
# flat few-second gap was still drawing HTTP 429s on a large fraction of videos during a
# real backfill run, so guessing one fixed number up front isn't reliable.
SUBTITLE_REQUEST_DELAY = 8.0

# Upper bound the adaptive delay above is allowed to grow to, no matter how much throttling
# is observed -- a sanity cap so a very strict period slows the run down rather than
# growing the gap indefinitely. Raised from 90s after a real backfill run showed throttling
# still occurring even once the delay had climbed all the way to that ceiling -- anonymous
# (no-cookies) requests appear to need more room than that to fully clear.
SUBTITLE_REQUEST_DELAY_CEILING = 180.0

# Number of attempts for a subtitle download before giving up -- only retried when the
# failure looks like YouTube throttling (see _is_throttled in Classes.py), not for
# videos that simply have no auto-captions.
SUBTITLE_MAX_ATTEMPTS = 3

# Base for the exponential backoff between throttled subtitle download retries
# (wait = SUBTITLE_BACKOFF_BASE ** attempt).
SUBTITLE_BACKOFF_BASE = 5

# Consecutive throttle-exhausted videos (within one channel's run) before giving up on
# subtitles for the rest of that run, so a blocked IP doesn't get hammered further.
SUBTITLE_CIRCUIT_BREAKER_THRESHOLD = 3

# Number of videos to process in parallel. 2 is the safe default — going above 3
# risks hitting YouTube's rate limits and getting temporarily blocked.
WORKER_COUNT = 2

# Number of users to process in parallel WITHIN each batch of 50. This only parallelizes
# file I/O (PFP downloads, S3 uploads) — the YouTube API call stays one-per-batch and
# sequential, so raising this value does NOT increase API call rate.
USER_WORKER_COUNT = 10

# Hours to keep retrying a just-ended livestream after chat_downloader reports NoChatReplay
# before giving up and marking it processed. YouTube's API flips a stream to "not live"
# before its chat replay actually finishes generating on their end, so a NoChatReplay seen
# too soon after the stream ended doesn't mean the replay will never exist -- it means it
# isn't ready yet. Videos that were never a livestream aren't affected by this grace period
# (a NoChatReplay for those is always permanent).
CHAT_REPLAY_GRACE_HOURS = 24

# Same idea as CHAT_REPLAY_GRACE_HOURS above, but for auto-generated (ASR) subtitles --
# YouTube may not have finished processing captions for a video the instant it stops being
# live, so a video that comes back with no captions this soon after ending isn't marked
# subtitles_processed permanently; it's left for a later run to retry. A separate setting
# from CHAT_REPLAY_GRACE_HOURS since caption finalization timing isn't necessarily the same
# as chat replay finalization timing.
SUBTITLE_GRACE_HOURS = 24

# Do not prompt for directories or options, just run the file.
QUICK_SETTINGS = True
QUICK_SETTINGS_DATA = "D:/CodingProjects/_Datafiles"
QUICK_SETTINGS_SECRET = "D:/CodingProjects/_NoShare"

# Database Configuration settngs
DB_VERBOSE = False

# Logging Configuration
DEBUG_LOG_FILE='Chat_Process_Log' # Used when CONTINUOUS_LOG is set to True
LOG_VERBOSE = False # Any debug messages will appear
LOG_NAME = "LOG" # Log file prefix
CONTINUOUS_LOG = True # Create one continuous log file and not separate ones per-run

# File Object Prefixes
MEMBERS_ONLY_FLAG = "_members"
DETAIL_TAG = "details"
THUMBNAIL_TAG= "thumbnails"
MESSAGES_TAG = "messages"
SUBTITLE_TAG = "subtitles"
PFP_TAG = "pfp"

if QUICK_SETTINGS is True:
    # Create a log file (In script location)
    LOG = False
    # Sets the working directories at launch. I don't recommend keeping secret stuff in the same spot as the data.
    DATA_DIRECTORY = QUICK_SETTINGS_DATA
    SECRETS_DIRECTORY = QUICK_SETTINGS_SECRET
    # Set this if you want to write to a member's only database.
    GET_MEMBERS_ONLY = False
    # Just process video data and not chat messages (Good for getting just publicly available Member's Only info)
    SKIP_CHAT_DOWNLOAD = False
    # Allow the scraper to timeout if no new messages arrive (False: Good for sitting on a waiting room or stream)
    TIMEOUT = True
    # Don't process currently live or stream reservation chats (Sometimes TIMEOUT being True isn't enough to skip a waiting room or a livestream)
    SKIP_LIVESTREAMS = True
else:
    # Create a log file (In script location)
    LOG = messagebox.askyesno("Logging","Do you want to write the console log to file?")
    # Sets the working directories at launch. I don't recommend keeping secret stuff in the same spot as the data.
    DATA_DIRECTORY = filedialog.askdirectory(title="Specify directory for data to be downloaded to")
    SECRETS_DIRECTORY = filedialog.askdirectory(title="Specify directory where Secrets and/or Cookies are")
    # Set this if you want to write to a member's only database.
    GET_MEMBERS_ONLY = messagebox.askyesno("Members-Only","Do you want to download Members-Only video data? (BE SURE COOKIES ARE UP-TO-DATE)")
    # Just process video data and not chat messages (Good for getting just publicly available Member's Only info)
    SKIP_CHAT_DOWNLOAD = messagebox.askyesno("Skip Chat Download","Process only video data and not the chat messages?\n(Useful for publicly available Members Only video info.)")
    # Allow the scraper to timeout if no new messages arrive (False: Good for sitting on a waiting room or stream)
    TIMEOUT = messagebox.askyesno("Chat Timeout","Do you want the chat scraper to timeout?\n(Pick no if you want it to keep watching a livestream.)")
    # Don't process currently live or stream reservation chats (Sometimes TIMEOUT being True isn't enough to skip a waiting room or a livestream)
    SKIP_LIVESTREAMS = messagebox.askyesno("Skip Livestreams","Do you want to skip any livestreams or waiting rooms?")

#####################################
### OTHER SETTINGS (DO NOT TOUCH) ###
#####################################

# Will exit if either folder dialog boxes were closed
if DATA_DIRECTORY == "" or SECRETS_DIRECTORY == "":
    exit()

# Settings for the Oauth 2.0 Configuration used by the YT_API Class.
CLIENT_SECRETS_FILE = f'{SECRETS_DIRECTORY}/client_secret.json'  # Download this from Google Cloud Console
TOKEN_PICKLE_FILE = f'{SECRETS_DIRECTORY}/token.pickle'# Will be created on first launch

with open(f"{SECRETS_DIRECTORY}/Settings.json",'r') as file:
    gen_settings = json.load(file)

USER_DATA_NAME = gen_settings["user_data_name"]

# Needed to access chat messages from member's only videos. Use browser addins to generate, make sure name matches.
# NOTE: Once you've exported the cookies, CLOSE that browser (or user agent) and do not open/use until this program finishes.
# Keeping the browser open tends to make the YT cookies reset and break the access to member's only videos.
COOKIES = None if GET_MEMBERS_ONLY is False and USE_COOKIES is False else f"{SECRETS_DIRECTORY}/cookies.txt"

# These folders will be automatically created if they don't exist. It's where the JSON files and thumbnails will be saved to.
LOCAL_USER_PATH = f"{DATA_DIRECTORY}/{USER_DATA_NAME}"

DETAIL_TAG = f"{DETAIL_TAG}" if GET_MEMBERS_ONLY is False else f"{DETAIL_TAG}{MEMBERS_ONLY_FLAG}"
THUMBNAIL_TAG= f"{THUMBNAIL_TAG}" if GET_MEMBERS_ONLY is False else f"{THUMBNAIL_TAG}{MEMBERS_ONLY_FLAG}"

# Edit the template provided and stuff it in your secrets folder
with open(f"{SECRETS_DIRECTORY}/DB_Settings.json",'r') as file:
    db_settings = json.load(file)

# Auto-filled out data from the settings file and other settings
DB_USR = db_settings["DB_USR"]
DB_PASS = db_settings["DB_PASS"]
DB_HOST = db_settings["DB_HOST"]
DB_PORT = db_settings["DB_PORT"]
DB_NAME = db_settings["db_name"]

# Populated by load_channel_directory() once a DB connection exists (see Main.py).
CHANNEL_DIRECTORY:dict[str,dict[str,str]] = {}
CHANNELS_TO_PROCESS:list[str] = []

def load_channel_directory(cursor:cursor) -> None:
    """
    Loads channel metadata from the channel_directory table (replaces the old settings.json
    channel_directory section) and computes which channels to process this run. Call once with
    an open DB cursor before select_channel().
    """
    global CHANNEL_DIRECTORY,CHANNELS_TO_PROCESS

    cursor.execute('SELECT name, user_id, db_suffix, "group", process FROM channel_directory')
    rows = cursor.fetchall()
    CHANNEL_DIRECTORY = {name:{"user_id":user_id,"db_suffix":db_suffix,"group":group} for name,user_id,db_suffix,group,process in rows}

    # PROCESS_ALL loops over every entry in CHANNEL_DIRECTORY flagged "process" instead of just CHANNEL_SELECTION.
    CHANNELS_TO_PROCESS = [name for name,_,_,_,process in rows if process] if PROCESS_ALL else [CHANNEL_SELECTION]

def select_channel(channel_name:str) -> None:
    """Recomputes all channel-specific derived settings (paths, DB tables, YT IDs) for the given channel."""
    global CHANNEL_SELECTION,CHANNEL_SELECTOR,CHANNEL_SUFFIX,LOCAL_DATA_PATH,DATA_PATHS
    global DB_TABLES,YT_USER_ID,YT_CHANNEL_ID,UPLOAD_PLAYLIST,MEMBERS_ONLY_PLAYLIST,PLAYLIST

    CHANNEL_SELECTION = channel_name
    CHANNEL_SELECTOR = CHANNEL_DIRECTORY[CHANNEL_SELECTION]
    CHANNEL_SUFFIX = CHANNEL_SELECTOR["db_suffix"]

    # The main data path used elsewhere in code
    LOCAL_DATA_PATH = f"{DATA_DIRECTORY}/{CHANNEL_SUFFIX}"

    DATA_PATHS = [
        f"{LOCAL_DATA_PATH}/{DETAIL_TAG}",
        f"{LOCAL_DATA_PATH}/{THUMBNAIL_TAG}",
        f"{LOCAL_DATA_PATH}/{MESSAGES_TAG}",
        f"{LOCAL_DATA_PATH}/{SUBTITLE_TAG}",
        f"{LOCAL_USER_PATH}/{DETAIL_TAG}",
        f"{LOCAL_USER_PATH}/{PFP_TAG}"
    ]

    # emotes, nicknames, and nickname_matches were merged from per-channel
    # emotes_<suffix>/nicknames_<suffix>/nickname_matches_<suffix> tables into single
    # channel_id-keyed tables. The *_members variants were not part of that merge and
    # still use the old per-channel naming.
    #
    # videos was already fully merged (earlier, separately) into one channel_id-keyed
    # table for BOTH public and members-only videos, distinguished by its "members"
    # column -- there's no separate videos_<suffix>_members table at all anymore.
    if GET_MEMBERS_ONLY is False:
        DB_TABLES = {
            "emotes":"emotes",
            "messages":f"messages_{CHANNEL_SUFFIX}",
            "nickname_matches":"nickname_matches",
            "nicknames":"nicknames",
            "videos":"videos",
            "tags":"tags",
            "user_ids":"user_ids",
            "subtitles":"subtitles"
        }
    else:
        DB_TABLES = {
            "emotes":f"emotes_{CHANNEL_SUFFIX}{MEMBERS_ONLY_FLAG}",
            "messages":f"messages_{CHANNEL_SUFFIX}{MEMBERS_ONLY_FLAG}",
            "nickname_matches":f"nickname_matches_{CHANNEL_SUFFIX}{MEMBERS_ONLY_FLAG}",
            "nicknames":"nicknames",
            "videos":"videos",
            "tags":"tags",
            "user_ids":"user_ids",
            "subtitles":f"subtitles_{CHANNEL_SUFFIX}{MEMBERS_ONLY_FLAG}"
        }

    # Auto-filled out data for Youtube data
    YT_USER_ID = CHANNEL_SELECTOR["user_id"] # UserID of the selected member
    YT_CHANNEL_ID = "UC" + YT_USER_ID
    UPLOAD_PLAYLIST = "UU" + YT_USER_ID # Hidden playlist containing ALL publically accessible Youtube Videos, Livestream VODs, and Shorts.
    MEMBERS_ONLY_PLAYLIST = "UUMO" + YT_USER_ID # Hiiden playlist containing ALL non-privated member's only Youtube Videos, Livestream VODs, and Shorts.

    # Leave this be, edit CUSTOM PLAYLIST and MEMBERS values above instead.
    PLAYLIST = UPLOAD_PLAYLIST if GET_MEMBERS_ONLY is False else MEMBERS_ONLY_PLAYLIST

if os.path.exists(f"{SECRETS_DIRECTORY}/S3_Settings.json"):
    # Edit the template provided and stuff it in your secrets folder
    with open(f"{SECRETS_DIRECTORY}/S3_Settings.json",'r') as file:
        s3_settings = json.load(file)

    # Auto-filled out data from the settings file and other settings
    ENDPOINT_URL = s3_settings["endpoint_url"]
    ENDPOINT_ID = s3_settings["aws_access_key_id"]
    ENDPOINT_KEY = s3_settings["aws_secret_access_key"]
    ENDPOINT_REGION = s3_settings["region_name"]

    S3_ENABLED = True
else:
    S3_ENABLED = False