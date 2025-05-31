# Native Stuff
import json,os

# Installed Stuff
import chat_downloader
import chat_downloader.errors

# Other Project Files
import Classes as C
import Database as DB
import logconfig as LOG
import Settings as CFG

"""
---------------
About This Tool
---------------

NOTE: THIS WILL NOT DOWNLOAD THE VIDEOS THEMSELVES! Use something like YT-DLP for that.

This tool is designed to download the following from every publically available YT video on a channel:
    1. Video Metadata (Upload Date, Unique ID, Title, etc.)
    2. Video Thumbnail (Best quality it is able to find)
    3. Every Chat, Superchat, Member's Message, and Super Sticker sent in:
        - Livestream VODs (If they haven't been edited and the chat thrown out as concequence)
        - Video Premieres

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
    c. Edit the Youtube User ID to the desired value. (MAKE SURE YOU'VE REMOVED THE PREFIXES, SEE NOTES)
    d. If you want to download a custom list of video/chat data, use the CUSTOM_PLAYLIST value.
5. Start this script. It *should* work fine? If not, have fun debugging.

-------
Credits
-------

xenova created the amazing tool that near effortlessly downloads all the messages from a chat.
I never made it far enough to figure out how and judging by how infinitely better that program is, I probably would've given up.

https://github.com/xenova/chat-downloader

"""

if os.path.isdir(CFG.DATA_PATH):
    pass
else:
    os.mkdir(CFG.DATA_PATH)

db = DB.PostgresClass()

yt = C.YT_API(db)

yt.Get_All_Videos()

def MainProcess():

    LOG.logger.info('Downloading chat messages for all videos')
    LOG.logger.info('----------------------------------------')
    with open(f"{CFG.DATA_PATH}/__All_Videos.json",'r') as file:
        all_video_json = json.load(file)

    success_videos = 0
    no_chat_videos = 0
    error_videos = 0
    skipped_videos = 0
    processed_videos = 0


    for video in all_video_json:
        
        vid = C.VideoClass(video)

        LOG.logger.info(f'\nProcessing {vid.url_id}')
        try:
            if len(DB.GetEntries(db.cursor,"videos","processed",{"videoId":vid.url_id,"processed":True})) > 0:
                LOG.logger.info(f'Video is already processed,skipping...')
                skipped_videos += 1
                processed_videos += 1
                LOG.logger.info(f"Video Progress: {processed_videos}/{len(all_video_json)} complete.\n{success_videos} Successfull\n{no_chat_videos} Without chat\n{error_videos} Errors\n{skipped_videos} Skipped")
                continue
            else:
                yt.Get_Messages(vid)
                DB.UpdateEntry(db.cursor,"videos","processed",True,"id",vid.id)
                db.database.commit()
                success_videos += 1
                processed_videos += 1
                LOG.logger.info("Video processed successfully.")
        except chat_downloader.errors.NoChatReplay as e:
            LOG.logger.warning(f"No chat replay present, skipping.")
            DB.UpdateEntry(db.cursor,"videos","processed",True,"id",vid.id)
            db.database.commit()
            no_chat_videos += 1
            processed_videos += 1
        except chat_downloader.errors.VideoUnplayable as f:
            LOG.logger.error(f"Can't load video.\n{f}")
            error_videos += 1
            processed_videos += 1
        LOG.logger.info(f"Video Progress: {processed_videos}/{len(all_video_json)} complete.\n{success_videos} Successfull\n{no_chat_videos} Without chat\n{error_videos} Errors\n{skipped_videos} Skipped")

MainProcess()