-------
Credits
-------

xenova created the amazing tool that near effortlessly downloads all the messages from a chat.
I never made it far enough to figure out how and judging by how infinitely better that program is, I probably would've given up.

https://github.com/xenova/chat-downloader

All the other bits and bobs were written by me and a LOT of heavy lifting by Googling...

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
