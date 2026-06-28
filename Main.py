# Native Stuff
import os,sys,shutil,time,threading
from concurrent.futures import ThreadPoolExecutor,as_completed

sys.path.append(os.getcwd())

# Installed Stuff
from tqdm import tqdm
import chat_downloader
import chat_downloader.errors

# Other Project Files
import modules.Settings as CFG
import modules.logconfig as LOG
import modules.Classes as C
import modules.Database as DB

"""
---------------
About This Tool
---------------

NOTE: THIS WILL NOT DOWNLOAD THE VIDEOS THEMSELVES! Use something like YT-DLP for that.

This tool is designed to download the following from every publically available YT video on a channel:
    1. Video Metadata (Upload Date, Unique ID, Title, etc.)
    2. Video Thumbnail (Best quality it's able to find)
    3. Every Chat, Superchat, Member's Message, and Super Sticker sent in:
        - Livestream VODs (If they haven't been edited and the chat thrown out as concequence)
        - Video Premieres
        - Pre/Post-chat if the stream is currently live, waiting to start, or has just finished

You can set a flag to download member's only video data as well into a separate database. (SEE SETTINGS)

----------------
Steps to Operate
----------------

1. Create PostgreSQL Database tables with the provided DDLs (Modify names as desired).
    a. NOTE: You'll need to create a separate DB for both Public and Member's Only data.
2. Create an app from the Google Cloud Console and get Oauth 2.0 setup. Download the secrets JSON for it.
3. Make sure all required dependancies are installed from requirements.txt.
4. Read through Settings.py and do the following minimum requirements:
    a. If you're going to download member's only content, get a cookies.txt file (READ THE NOTE IN SETTINGS).
    b. Edit the DB_Settings.json file as needed and place it in your secrets folder.
    c. Check the MEMBER_DIRECTORY list and be sure to pick the member you want.
5. Start this script. It *should* work fine? If not, have fun debugging.

-------
Credits
-------

xenova created the amazing tool that near effortlessly downloads all the messages from a chat.
I never made it far enough to figure out how and judging by how infinitely better that program is, I probably would've given up.

https://github.com/xenova/chat-downloader

"""

# Create the data paths if they don't exist
for d_path in CFG.DATA_PATHS:
    if os.path.isdir(d_path):
        pass
    else:
        os.makedirs(d_path)

# Initialize database connection and setup the API calls
db = DB.PostgresClass()
s3 = C.H3Client()
channel_bucket = C.H3Bucket(s3.client,CFG.CHANNEL_SUFFIX,CFG.LOCAL_DATA_PATH)
user_bucket = C.H3Bucket(s3.client,CFG.USER_DATA_NAME,CFG.LOCAL_USER_PATH)
yt = C.YT_API(db)

#################################
### VIDEO AND CHAT PROCESSING ###
#################################

# Used for tracking video, chat, and user stats to be output at program completion.
vid_stats = C.VideoStats()
all_chat_stats = C.ChatStats()

LOG.logger.info("\nObtaining all videos from Youtube API...")
video_ids = yt.Get_All_Videos(channel_bucket)
LOG.logger.info(f"Total of {len(video_ids):,} video(s) aquired.")

LOG.logger.info("Pre-loading database state...")
all_video_records = DB.GetEntries(db.cursor,CFG.DB_TABLES["videos"],"id,processed")
video_db_status = {r["id"]: r["processed"] for r in all_video_records}
LOG.logger.info(f"  {len(video_db_status):,} video record(s) loaded from database.")

known_user_ids:set = set(r["id"] for r in DB.GetEntries(db.cursor,CFG.DB_TABLES["user_ids"],"id"))
LOG.logger.info(f"  {len(known_user_ids):,} known user ID(s) loaded.")

nickname_entries = DB.GetEntries(db.cursor,CFG.DB_TABLES["nicknames"],"nickname")
sorted_nicknames:list[str] = sorted([e["nickname"] for e in nickname_entries], key=len, reverse=True)
LOG.logger.info(f"  {len(sorted_nicknames):,} nickname(s) loaded.")

unprocessed_ids = [vid_id for vid_id in video_ids if video_db_status.get(vid_id) is not True]
LOG.logger.info(f"  {len(unprocessed_ids):,} unprocessed video(s) to fetch.")

LOG.logger.info("Fetching video details in batches of 50...")
video_info_cache:dict = {}
with LOG.TQDM_Logging():
    with tqdm(desc='Video Info Fetched',total=len(unprocessed_ids),bar_format='{desc}: {n_fmt}/{total_fmt}',ncols=80,position=0,leave=False) as fetchbar:
        for i in range(0,len(unprocessed_ids),50):
            batch = unprocessed_ids[i:i+50]
            try:
                batch_result = yt.Get_Videos_Info_Batch(batch,channel_bucket)
                video_info_cache.update(batch_result)
            except Exception as e:
                LOG.logger.error(f"Batch fetch failed for {len(batch)} video(s), falling back to individual fetch: {e}")
                for vid_id in batch:
                    try:
                        vid = yt.Get_Video_Info(vid_id,channel_bucket)
                        if vid:
                            video_info_cache[vid_id] = vid
                    except Exception as e2:
                        LOG.logger.error(f"Individual fetch failed for {vid_id}: {e2}")
            fetchbar.update(len(batch))
LOG.logger.info(f"{len(video_info_cache):,} video(s) ready for processing.")

##################################
### THREAD POOL INFRASTRUCTURE ###
##################################

tqdm.set_lock(threading.RLock())  # make tqdm bar updates thread-safe

_thread_local = threading.local()
user_id_lock = threading.Lock()
_available_positions = list(range(1, CFG.WORKER_COUNT + 1))  # one bar slot per worker
_position_lock = threading.Lock()

def get_thread_db():
    """Returns a per-thread DB connection. Assigns a stable tqdm bar position on first call."""
    if not hasattr(_thread_local,'db'):
        with _position_lock:
            _thread_local.bar_position = _available_positions.pop(0)
        _thread_local.db = DB.PostgresClass()
    return _thread_local.db

class _RateLimiter:
    """Enforces a minimum gap between chat-download starts across all worker threads."""
    def __init__(self,delay:float):
        self._lock = threading.Lock()
        self._last = 0.0
        self._delay = delay
    def wait(self):
        if self._delay <= 0:
            return
        with self._lock:
            elapsed = time.monotonic() - self._last
            if elapsed < self._delay:
                time.sleep(self._delay - elapsed)
            self._last = time.monotonic()

rate_limiter = _RateLimiter(CFG.REQUEST_DELAY)

def process_video(video_id:str):
    """
    Processes a single video: thumbnail, DB insert/update, chat messages.
    Designed to run in a worker thread — uses its own DB connection via get_thread_db().
    Returns (local_vid_stats, local_chat_stats) so the main thread can aggregate them.
    """
    thread_db = get_thread_db()
    vid = video_info_cache[video_id]
    video_exists = video_id in video_db_status
    local_vid_stats = C.VideoStats()
    local_chat_stats = C.ChatStats()

    #-------------------------#
    #-- GET VIDEO THUMBNAIL --#
    #-------------------------#

    vid.Get_Thumbnail(channel_bucket)

    #---------------------------------------#
    #-- INSERT/UPDATE VIDEO INTO DATABASE --#
    #---------------------------------------#

    if not video_exists:
        DB.InsertEntries(cursor=thread_db.cursor,table=CFG.DB_TABLES["videos"],data_list=[vid.entry])
    else:
        update_data = {k: v for k, v in vid.entry.items() if k != "id"}
        DB.UpdateEntries(thread_db.cursor,CFG.DB_TABLES["videos"],update_data,"id",vid.id)
    thread_db.database.commit()

    #-----------------------------#
    #-- GET VIDEO CHAT MESSAGES --#
    #-----------------------------#

    try:
        message_stats = yt.Get_Messages(vid,channel_bucket,known_user_ids,sorted_nicknames,db=thread_db,user_id_lock=user_id_lock,bar_position=_thread_local.bar_position)
        message_stats.append_all(local_chat_stats)
        if vid.livestream == False:
            DB.UpdateEntry(thread_db.cursor,CFG.DB_TABLES["videos"],"processed",True,"id",vid.id)
            thread_db.database.commit()
        local_vid_stats.success_videos = 1
        if vid.livestream == True:
            local_vid_stats.still_live = 1
    except chat_downloader.errors.NoChatReplay:
        if vid.livestream == False:
            DB.UpdateEntry(thread_db.cursor,CFG.DB_TABLES["videos"],"processed",True,"id",vid.id)
            thread_db.database.commit()
        local_vid_stats.no_chat_videos = 1
        LOG.logger.warning(f"{video_id}: No Chat Replay available.")
        rate_limiter.wait()  # Stagger download starts by REQUEST_DELAY across all workers
    except chat_downloader.errors.VideoUnplayable:
        local_vid_stats.unavailable_videos = 1
        LOG.logger.warning(f"{video_id}: Video inaccessible, skipping.")
    except Exception as u:
        local_vid_stats.error_videos = 1
        LOG.logger.error(f"{video_id}: Unknown error: {u}")
        rate_limiter.wait()  # Stagger download starts by REQUEST_DELAY across all workers

    return local_vid_stats,local_chat_stats

#################################
### VIDEO AND CHAT PROCESSING ###
#################################

LOG.logger.info(f"Processing videos with {CFG.WORKER_COUNT} worker(s)...")
with LOG.TQDM_Logging():
    with tqdm(desc='Videos Processed',total=len(video_ids),bar_format='{desc}: {n_fmt}/{total_fmt} {postfix}',ncols=80,postfix="",position=0,leave=False) as vidbar:

        def Update_Postfix_Videos():
            return f"Successful: {vid_stats.success_videos:,} | Skipped: {vid_stats.skipped_videos:,} | No Chat: {vid_stats.no_chat_videos:,} | Unavailable: {vid_stats.unavailable_videos:,} | Errors: {vid_stats.error_videos:,}"

        with ThreadPoolExecutor(max_workers=CFG.WORKER_COUNT) as executor:
            futures:dict = {}

            # Pre-skip already-processed or unavailable videos without entering the pool
            for video_id in video_ids:
                if video_db_status.get(video_id) is True or video_info_cache.get(video_id) is None:
                    vid_stats.skipped_videos += 1
                    vidbar.update(1)
                else:
                    futures[executor.submit(process_video,video_id)] = video_id

            vidbar.set_postfix_str(Update_Postfix_Videos())

            # Collect results as each worker finishes
            for future in as_completed(futures):
                video_id = futures[future]
                try:
                    local_vid_stats,local_chat_stats = future.result()
                    vid_stats.success_videos += local_vid_stats.success_videos
                    vid_stats.no_chat_videos += local_vid_stats.no_chat_videos
                    vid_stats.unavailable_videos += local_vid_stats.unavailable_videos
                    vid_stats.error_videos += local_vid_stats.error_videos
                    vid_stats.still_live += local_vid_stats.still_live
                    local_chat_stats.append_all(all_chat_stats)
                except Exception as e:
                    vid_stats.error_videos += 1
                    LOG.logger.error(f"Uncaught error for video {video_id}: {e}")
                vidbar.set_postfix_str(Update_Postfix_Videos())
                vidbar.update(1)

LOG.logger.info("Video and chat processing complete.\n")

#######################
### USER PROCESSING ###
#######################

# Users have to be done in batches of 50 manually because the API call does not give a "next page" item like the videos....
def Batch_Users(users):
    """Yeilds users in batches of 50"""
    for i in range(0,len(users),50):
        yield users[i:i + 50]

LOG.logger.info("Obtaining all unprocessed users from database...")
# Get fresh users from the DB
unique_users = DB.GetEntries(db.cursor,CFG.DB_TABLES["user_ids"],"id",{"processed":False})
LOG.logger.info(f"Total of {len(unique_users):,} unique user(s) aquired.")

# List of IDs
user_list = [str(v) for d in unique_users for v in d.values()]

if len(user_list) > 0:


    def Update_Postfix_Users():
        return f"Skipped: {all_chat_stats.invalid_users:,}"

    with LOG.TQDM_Logging():
        with tqdm(total=len(user_list),desc='Users Processed',bar_format='{desc}: {n_fmt}/{total_fmt} {postfix}',ncols=80,postfix=Update_Postfix_Users(),position=0,leave=False) as userbar:
            for users in Batch_Users(user_list):
                all_chat_stats.invalid_users += yt.Get_User_Batch(users,user_bucket)
                userbar.set_postfix_str(Update_Postfix_Users())
                userbar.update(len(users))

LOG.logger.info("User processing complete.\n")

LOG.logger.info("Cleaning up local folders")
shutil.rmtree(channel_bucket.local_root)
shutil.rmtree(user_bucket.local_root)
LOG.logger.info("Local folders deleted")

LOG.logger.info(f"""
---VIDEO STATISTICS---

Total Videos:   {len(video_ids):,}
Existing:       {vid_stats.skipped_videos:,}
New/Updated:    {vid_stats.success_videos:,}
Still Live:     {vid_stats.still_live:,}
No Chat:        {vid_stats.no_chat_videos:,}
Unavailable:    {vid_stats.unavailable_videos:,}
Errors:         {vid_stats.error_videos:,}

---CHAT STATISTICS---

To Process:     {all_chat_stats.total_messages:,}
New:            {all_chat_stats.new_messages:,}
Existing:       {all_chat_stats.existing_messages:,}

---USER STATISTICS---

Unique Users:   {len(all_chat_stats.new_user_ids | all_chat_stats.exist_user_ids):,}
New:            {len(all_chat_stats.new_user_ids):,}
Existing:       {len(all_chat_stats.exist_user_ids - all_chat_stats.new_user_ids):,}
Invalid:        {all_chat_stats.invalid_users:,}
""")