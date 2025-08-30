# Native Stuff
import os,sys

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
if os.path.isdir(CFG.DATA_PATH):
    pass
else:
    os.mkdir(CFG.DATA_PATH)

# Initialize database connection and setup the API calls
db = DB.PostgresClass()
yt = C.YT_API(db)

#################################
### VIDEO AND CHAT PROCESSING ###
#################################

# Used for tracking video, chat, and user stats to be output at program completion.
vid_stats = C.VideoStats()
all_chat_stats = C.ChatStats()

LOG.logger.info("\nObtaining all videos from Youtube API...")
video_ids = yt.Get_All_Videos()
LOG.logger.info(f"Total of {len(video_ids)} video(s) aquired.")

LOG.logger.info("Processing videos for details, thumbnail, and chat messages...")
with LOG.TQDM_Logging():
    with tqdm(desc='Videos Processed',total=len(video_ids),bar_format='{desc}: {n_fmt}/{total_fmt} || {postfix}',ncols=80,postfix="",position=0,leave=False) as vidbar:
        # Get all videos in the hidden playlist then use generator to pass each id to loop
        for video_id in video_ids:

            def Update_Postfix_Videos():
                return f"Current Video: {video_id} | Sucessful: {vid_stats.success_videos} | Skipped: {vid_stats.skipped_videos} | No Chat: {vid_stats.no_chat_videos} | Unavailable: {vid_stats.unavailable_videos} | Errors: {vid_stats.error_videos}"

            vidbar.set_postfix_str(Update_Postfix_Videos())

            #--------------------------------#
            #-- LOOK FOR VIDEO IN DATABASE --#
            #--------------------------------#

            # Don't do any processing if the current video_id has already been processed
            complete_video = DB.GetEntries(db.cursor,"videos","title,processed",{"id":video_id,"processed":True})
            video_exists = True if len(DB.GetEntries(db.cursor,"videos","title",{"id":video_id})) > 0 else False
            if len(complete_video) > 0:
                vid_stats.skipped_videos += 1
                vidbar.set_postfix_str(Update_Postfix_Videos())
                vidbar.update(1)
                continue

            #-----------------------------#
            #-- GET DETAILED VIDEO INFO --#
            #-----------------------------#

            # Get video data from API, check if it's been updated, and return the data as a class
            try:
                vid = yt.Get_Video_Info(video_id)
            except Exception as u:
                vid_stats.error_videos += 1
                LOG.logger.error(f"Unknown error parsing video: {u}")
                vidbar.set_postfix_str(Update_Postfix_Videos())
                vidbar.update(1)
                continue

            # Make sure the video still exists before trying to process it.
            if vid is None:
                vid_stats.skipped_videos += 1
                vidbar.set_postfix_str(Update_Postfix_Videos())
                vidbar.update(1)
                continue

            #-------------------------#
            #-- GET VIDEO THUMBNAIL --#
            #-------------------------#

            # Currently has no relation to the database, just saving it to file
            vid.Get_Thumbnail()

            #---------------------------------------#
            #-- INSERT/UPDATE VIDEO INTO DATABASE --#
            #---------------------------------------#

            # Will update the video in database if something changed in the returned JSON data
            if video_exists == False:
                DB.InsertEntries(db.cursor,"videos",[vid.entry])
                db.database.commit()
            elif vid.status == "Update":
                for column, value in vid.entry.items():
                    DB.UpdateEntry(db.cursor,"videos",column,value,"id",vid.id)
                    db.database.commit()

            #-----------------------------#
            #-- GET VIDEO CHAT MESSAGES --#
            #-----------------------------#

            # Get the YTC messages from the video and put them into the database
            try:
                message_stats = yt.Get_Messages(vid)
                message_stats.append_all(all_chat_stats) # Update the global stats for chats and users
                if vid.livestream == False:
                    DB.UpdateEntry(db.cursor,"videos","processed",True,"id",vid.id)
                    db.database.commit()
                vid_stats.success_videos += 1
                if vid.livestream == True:
                    vid_stats.still_live += 1
            # Regular videos (or streams that have been edited) have no chat
            except chat_downloader.errors.NoChatReplay as e:
                if vid.livestream == False:
                    DB.UpdateEntry(db.cursor,"videos","processed",True,"id",vid.id)
                    db.database.commit()
                vid_stats.no_chat_videos += 1
                LOG.logger.warning(f"No Chat Replay available.")
            # Catch when a video goes private or is members-only
            except chat_downloader.errors.VideoUnplayable as f:
                vid_stats.unavailable_videos += 1
                LOG.logger.warning(f"Video inaccessible, skipping.")
            # Catch any unknown errors
            except Exception as u:
                vid_stats.error_videos += 1
                LOG.logger.error(f"Unknown error parsing video: {u}")

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
unique_users = DB.GetEntries(db.cursor,"user_ids","id",{"processed":False})
LOG.logger.info(f"Total of {len(unique_users)} unique user(s) aquired.")

# List of IDs
user_list = [str(v) for d in unique_users for v in d.values()]

if len(user_list) > 0:


    def Update_Postfix_Users():
        return f"Skipped: {all_chat_stats.invalid_users}"

    with LOG.TQDM_Logging():
        with tqdm(Batch_Users(user_list),desc='Users Processed',total=len(user_list),bar_format='{desc}: {n_fmt}/{total_fmt} || {postfix}',ncols=80,postfix=Update_Postfix_Users(),position=0,leave=False) as userbar:
            for users in userbar:
                all_chat_stats.invalid_users += yt.Get_User_Batch(users)
                userbar.set_postfix_str(Update_Postfix_Users())
                userbar.update(len(users))

LOG.logger.info("User processing complete.\n")

LOG.logger.info(f"""
---VIDEO STATISTICS---

Total Videos:   {len(video_ids)}
Existing:       {vid_stats.skipped_videos}
New/Updated:    {vid_stats.success_videos}
Still Live:     {vid_stats.still_live}
No Chat:        {vid_stats.no_chat_videos}
Unavailable:    {vid_stats.unavailable_videos}
Errors:         {vid_stats.error_videos}

---CHAT STATISTICS---

To Process:     {all_chat_stats.total_messages}
New:            {all_chat_stats.new_messages}
Existing:       {all_chat_stats.existing_messages}

---USER STATISTICS---

To Process:     {all_chat_stats.new_user_ids + all_chat_stats.exist_user_ids}
New:            {all_chat_stats.new_user_ids}
Existing:       {all_chat_stats.exist_user_ids}
Invalid:        {all_chat_stats.invalid_users}
""")