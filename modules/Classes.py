# Native Stuff
import os,json,pickle,requests,re,xxhash
from typing import Any
from datetime import datetime

# Installed Stuff
from tqdm import tqdm
from chat_downloader import ChatDownloader

# Google Stuff
import google.auth
import google.auth.exceptions
import google.auth.external_account_authorized_user
import google.oauth2.credentials
import google
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Other Project Files
import modules.logconfig as LOG
import modules.Settings as CFG
import modules.Database as DB

class VideoStats:
    """Statistics about all the videos."""
    def __init__(self) -> None:
        self.still_live:int = 0
        self.success_videos:int = 0
        self.skipped_videos:int = 0
        self.no_chat_videos:int = 0
        self.unavailable_videos:int = 0
        self.error_videos:int = 0

class ChatStats:
    """Statistics about the chat of a video."""
    def __init__(self) -> None:
        self.total_messages:int = 0
        self.new_messages:int = 0
        self.existing_messages:int = 0
        self.new_user_ids:int = 0
        self.exist_user_ids = set()
        self.invalid_users:int = 0
    
    def append_all(self,all_chat_stats:'ChatStats'):
        """Updates the total stats with additional numbers"""
        all_chat_stats.total_messages += self.total_messages
        all_chat_stats.new_messages += self.new_messages
        all_chat_stats.existing_messages += self.existing_messages
        all_chat_stats.new_user_ids += self.new_user_ids
        all_chat_stats.exist_user_ids = all_chat_stats.exist_user_ids.union(self.exist_user_ids)

class VideoClass:
    """
    A Class that will nab all the data currently implemented into the database structure. 
    Creates a useful self.entry variable for piping into the database method(s) as needed.

    :param video: The JSON file (preferably loaded from json.load), ideally provided from the Get_All_Videos method in the YT_API class
    :type video: Dictionary

    :param status: Whether the video is existing, needs updating, or is new. Database operations are different depending on status
    :type status: String
    """
    def __init__(self,video:dict[str,Any],status:str):
        self.status = status
        try:

            self.id:str|None = video.get("id") # Back-end ID for video
            self.file = f"{CFG.DATA_PATH}/{self.id}.json" # Filename for JSON dump
            self._snippet:dict[str,Any]|None = video.get("snippet")
            self._liveStreamingDetails:dict[str,Any]|None = video.get("liveStreamingDetails")
            
            if self._snippet is not None:

                # Date video was released / VOD was generated
                self._publish_date:str|None = self._snippet.get("publishedAt")
                if self._publish_date is not None:
                    self.publish_date = _get_date_time(self._publish_date)
                else:
                    self.publish_date = None

                self.title = self._snippet.get("title") # Video Title

                self._liveBroadcastContent:str|None = self._snippet.get("liveBroadcastContent")
                if self._liveBroadcastContent is not None:
                    self._liveBroadcastContent = None if self._liveBroadcastContent == "none" else self._liveBroadcastContent

                if self._liveBroadcastContent is not None:
                    self.livestream = True
                    self.islive = True if self._liveBroadcastContent == "live" else False
                else:
                    self.livestream = False
                    self.islive = False

                if self._liveStreamingDetails is not None:
                    self._scheduled_start = self._liveStreamingDetails.get("scheduledStartTime")
                    self.scheduled_start = _get_date_time(self._scheduled_start) if self._scheduled_start is not None else None
                    self._actual_start = self._liveStreamingDetails.get("actualStartTime")
                    self.actual_start = _get_date_time(self._actual_start) if self._actual_start is not None else None
                    self._actual_end = self._liveStreamingDetails.get("actualEndTime")
                    self.actual_end = _get_date_time(self._actual_end) if self._actual_end is not None else None
                else:
                    self.scheduled_start = None
                    self.actual_start = None
                    self.actual_end = None

                # JSON file will only contain an object for a thumbnail if one exists. This will get the best quality one it can find.
                self.thumbnail_sizes:dict[str,Any]|None = self._snippet.get("thumbnails")

                if self.thumbnail_sizes is not None:
                    if "maxres" in self.thumbnail_sizes.keys():
                        self._thumbnail:dict[str,Any]|None = self.thumbnail_sizes.get("maxres")
                    elif "standard" in self.thumbnail_sizes.keys():
                        self._thumbnail:dict[str,Any]|None = self.thumbnail_sizes.get("standard")
                    elif "high" in self.thumbnail_sizes.keys():
                        self._thumbnail:dict[str,Any]|None = self.thumbnail_sizes.get("high")
                    elif "medium" in self.thumbnail_sizes.keys():
                        self._thumbnail:dict[str,Any]|None = self.thumbnail_sizes.get("medium")
                    elif "default" in self.thumbnail_sizes.keys():
                        self._thumbnail:dict[str,Any]|None = self.thumbnail_sizes.get("default")
                    else:
                        self._thumbnail:dict[str,Any]|None = None
                        LOG.logger.warning(f"Video {self.id} has no thumbnail URL!")

                    self.thumbnail:str|None = self._thumbnail.get("url") if self._thumbnail is not None else None

            self.entry:dict[str,Any] = {
                "id":self.id,
                "title":self.title,
                "publishedAt":self.publish_date,
                "livestream":self.livestream,
                "islive":self.islive,
                "scheduled_start":self.scheduled_start,
                "start_time":self.actual_start,
                "end_time":self.actual_end
            }
        except Exception as e:
            LOG.logger.error(f"Video file {self.id} not initialized:\n{e}")
            raise e
    
    def Get_Thumbnail(self):
        """Will download the video thumbnail. Checks if there's an updated one and renames the old one and downloads a new one."""
        if self.thumbnail is not None:
            temp_thumb = f"{CFG.DATA_PATH}/{self.id}_Thumbnail_TEMP.jpg"

            # Download a fresh thumbnail
            with open(temp_thumb,'wb') as handle:
                img_response = requests.get(self.thumbnail,stream=True)
                if not img_response.ok:
                    LOG.logger.info(img_response)
                for block in img_response.iter_content(1024):
                    if not block:
                        break
                    handle.write(block)
            # Hash the fresh thumbnail
            with open(temp_thumb,"rb") as image:
                new_hash = xxhash.xxh128_hexdigest(image.read())

            # Check for previously downloaded thumbnails
            thumbnail_hashes = set()
            thumb_path = f"{CFG.DATA_PATH}/{self.id}_Thumbnail.jpg"

            # Check if a thumbnail already exists
            if os.path.isfile(thumb_path):

                # Hash the file and store it for cross referencing
                with open(thumb_path,"rb") as image:
                    thumbnail_hashes.add(xxhash.xxh128_hexdigest(image.read()))

                # Create a new filename
                number = 1
                new_path = f"{CFG.DATA_PATH}/{self.id}_Thumbnail_{number}.jpg"

                # Increment until no overlapping name
                while os.path.isfile(new_path):
                    # Hash the file and store it for cross referencing
                    with open(new_path,"rb") as image:
                        thumbnail_hashes.add(xxhash.xxh128_hexdigest(image.read()))

                    number += 1
                    new_path = f"{CFG.DATA_PATH}/{self.id}_Thumbnail_{number}.jpg"

                # Delete it if it already matches another one
                if new_hash in thumbnail_hashes:
                    os.remove(temp_thumb)
                else:
                    os.rename(thumb_path,new_path)
                    os.rename(temp_thumb,thumb_path)
            else:
                os.rename(temp_thumb,thumb_path)

class MessageClass:
    """
    A Class that will nab all the data currently implemented into the database structure. 
    Creates a useful self.entry variable for piping into the database method(s) as needed.

    :param message: The JSON file (preferably loaded from json.load), ideally provided from the get_chat method from the ChatDownloader tool developed by xenova
    :type message: Dict
    :param video: The Video the messages are related to.
    :type video: VideoClass
    """
    def __init__(self,message:dict[str,Any],video:VideoClass):
        try:
            self.id = message.get("message_id") # Back-end ID for message
            self.message:str|None = message.get("message") # Message contents
            self._time_absolute = message.get("timestamp")
            if self._time_absolute is not None:
                self.time_absolute = self._time_absolute/1000000 # Exact time the message was sent (Timestamp is in microseconds so need to convert it)

            self.time_relative = message.get("time_in_seconds") # Time message was sent relative to VOD start time of 0s
            self.type = message.get("message_type") # Message, Superchat, etc.

            self.video_id = video.id

            self._author:dict[str,Any]|None = message.get("author")

            if self._author is not None:
                self.usr_id = self._author.get("id")
                self.usr_name = self._author.get("name")

                self._badges:list[dict[str,Any]]|None = self._author.get("badges")

                if self._badges is not None:
                    self.member_months = self._Membership_Level(self._badges)
                    for badge in self._badges:
                        _title = badge.get("title")
                        if _title == "Verified":
                            self.is_verified = True
                        else:
                            self.is_verified = False
                        if _title == "Moderator":
                            self.is_moderator = True
                        else:
                            self.is_moderator = False
                        if _title == "Owner":
                            self.is_owner = True
                        else:
                            self.is_owner = False
                else:
                    self.member_months = -1
                    self.is_moderator = False
                    self.is_verified = False
                    self.is_owner = False

            self._money:dict[str,Any]|None = message.get("money")

            if self._money is not None:
                self.amount = self._money.get("amount")
                self.currency = self._money.get("currency")
                self.currency_symbol = self._money.get("currency_symbol")
            else:
                self.amount = None
                self.currency = None
                self.currency_symbol = None

            self.header_background_colour = message.get("header_background_colour")

            self.e_emote_entries:list[dict] = []

            self._emotes:list[dict[str,Any]]|None = message.get("emotes")

            if self._emotes is not None:
                for emote in self._emotes:
                    e_id = emote.get("id")
                    e_name = emote.get("name")
                    e_custom = emote.get("is_custom_emoji")
                    e_images:list[dict[str,Any]]|None = emote.get("images")
                    e_url = None
                    if e_images is not None:
                        for image in e_images:
                            _e_img_id = image.get("id")
                            e_url = image.get("url")
                            if _e_img_id == "source":
                                break
                            elif _e_img_id == "48x48":
                                break
                            elif _e_img_id == "24x24":
                                break
                    e_entry:dict = {"id":e_id,"name":e_name,"url":e_url,"custom":e_custom}
                    self.e_emote_entries.append(e_entry)

            self.entry = {
                "message_id":self.id,
                "message":self.message,
                "timestamp":self.time_absolute,
                "time_in_seconds":self.time_relative,
                "type":self.type,
                "video_id":self.video_id,
                "user_id":self.usr_id,
                "user_name":self.usr_name,
                "user_member_status":self.member_months,
                "ismoderator":self.is_moderator,
                "isverified":self.is_verified,
                "isowner":self.is_owner,
                "amount":self.amount,
                "currency":self.currency,
                "symbol":self.currency_symbol,
                "color":self.header_background_colour
            }
        except Exception as e:
            LOG.logger.error(f"Message {self.id} not initialized:\n{e}")
            raise e

    def _Membership_Level(self,badge_data:list[dict[str,Any]]):
        """Membership data can have (4) states in the same entry:
        - No membership
        - New Member
        - Membership in Months
        - Membership in Years
        """
        for badge in badge_data:
            _title:str|None = badge.get("title")
            if _title is not None:
                if _title == "New member":
                    return 0
                elif "month" in _title:
                    return int(re.findall(r'\d+',_title)[0])
                elif "year" in _title:
                    return int(re.findall(r'\d+',_title)[0]) * 12
            else:
                return -1

class UserClass:
    """
    A Class that will nab all the data currently implemented into the database structure. 
    Creates a useful self.entry variable for piping into the database method(s) as needed.

    :param user: The JSON file (preferably loaded from json.load), containing the API response from a channel request from Youtube
    :type user: Dict
    """
    def __init__(self,user:dict[str,Any]):
        try:
            self.id = user.get("id") # Back-end ID for user
            self._snippet:dict[str,Any]|None = user.get("snippet")
            if self._snippet is not None:
                self.name = self._snippet.get("title") # Most current username
                self.custom_url = self._snippet.get("customUrl") # Custom URL if one was set
                self._created = self._snippet.get("publishedAt") # Date channel was created
                self.created = _get_date_time(self._created) if self._created is not None else None
                self.region = self._snippet.get("country")

                # JSON file will only contain an object for a profile picture if one exists. This will get the best quality one it can find.
                self.pfp_sizes:dict[str,Any]|None = self._snippet.get("thumbnails")

                if self.pfp_sizes is not None:
                    if "maxres" in self.pfp_sizes.keys():
                        self._pfp:dict[str,Any]|None = self.pfp_sizes.get("maxres")
                    elif "standard" in self.pfp_sizes.keys():
                        self._pfp:dict[str,Any]|None = self.pfp_sizes.get("standard")
                    elif "high" in self.pfp_sizes.keys():
                        self._pfp:dict[str,Any]|None = self.pfp_sizes.get("high")
                    elif "medium" in self.pfp_sizes.keys():
                        self._pfp:dict[str,Any]|None = self.pfp_sizes.get("medium")
                    elif "default" in self.pfp_sizes.keys():
                        self._pfp:dict[str,Any]|None = self.pfp_sizes.get("default")
                    else:
                        self._pfp:dict[str,Any]|None = None
                        LOG.logger.warning(f"Video {self.id} has no PFP URL!")

                    self.pfp:str|None = self._pfp.get("url") if self._pfp is not None else None

            self._stats:dict[str,Any]|None = user.get("statistics")
            if self._stats is not None:
                self.viewcount = self._stats.get("viewCount") # Number of views all content has
                self.subscribers = self._stats.get("subscriberCount")

            self.entry = {
                "latest_name":self.name,
                "custom_url":self.custom_url,
                "created":self.created,
                "viewcount":self.viewcount,
                "subscribers":self.subscribers,
                "region":self.region
            }

        except Exception as e:
            LOG.logger.error(f"Video file {self.id} not initialized:\n{e}")
            raise e

class YT_API:
    """
    Creates a usable API endpoint for making calls. Was initially going to handle ALL calls using your own provided credentials, 
    but xenova's ChatDownloader tool worked so well I pivoted to utilizing that for getting chat messages.

    :param database: Initialized Database Object the methods can use to make queries on.
    :type database: Database Object
    """
    def __init__(self,database:DB.PostgresClass):
        
        def get_authenticated_service():
            """
            Authenticates credientials onto the Youtube API.
            
            :return: Youtube API object for making calls with.
            :rtype: API Object
            """
            credentials:Any | google.auth.external_account_authorized_user.Credentials | google.oauth2.credentials.Credentials = None
            
            # Check if we have saved credentials
            if os.path.exists(CFG.TOKEN_PICKLE_FILE):
                with open(CFG.TOKEN_PICKLE_FILE, 'rb') as token:
                    credentials = pickle.load(token)
            
            # If credentials don't exist or are invalid, run the flow
            if not credentials or not credentials.valid:
                if credentials and credentials.expired and credentials.refresh_token:
                    try:
                        credentials.refresh(Request())
                    except google.auth.exceptions.RefreshError:
                        flow = InstalledAppFlow.from_client_secrets_file(CFG.CLIENT_SECRETS_FILE, ['https://www.googleapis.com/auth/youtube.readonly'])
                        credentials = flow.run_local_server(port=0)
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(CFG.CLIENT_SECRETS_FILE, ['https://www.googleapis.com/auth/youtube.readonly'])
                    credentials = flow.run_local_server(port=0)
                
                # Save credentials for future use
                with open(CFG.TOKEN_PICKLE_FILE, 'wb') as token:
                    pickle.dump(credentials, token)
            
            api_resource = build('youtube', 'v3', credentials=credentials)
            
            return api_resource

        self.api = get_authenticated_service()
        self.db = database

    def Get_Upload_Count(self):
        """
        Gets the number of videos in the uploads playlist
        """
        LOG.logger.info("Polling channel for upload count")
        LOG.logger.info('--------------------------------')

        request = self.api.playlists().list(part="contentDetails",channelId=CFG.YT_CHANNEL_ID,id=CFG.PLAYLIST)

        response = request.execute()

        video_count = response["contentDetails"]["itemCount"]

        LOG.logger.info(f"{video_count} video(s) found!")
        return video_count
    
    def Get_Video_Info(self,id:str):
        request = self.api.videos().list(part="contentDetails,id,snippet,status,liveStreamingDetails",id=id)

        response = request.execute()

        videos:list[dict] = response["items"]

        if len(videos) > 0:

            video = videos[0]

            del video["kind"]
            del video["etag"]

            temp_path = f"{CFG.DATA_PATH}/{id}_TEMP.json"

            # Write Video data to file
            with open(temp_path,'w') as file:
                file.write(json.dumps(video,indent=4))

            # Hash the video data
            with open(temp_path,"rb") as image:
                new_hash = xxhash.xxh128_hexdigest(image.read())

            data_hashes = set()
            filepath = f"{CFG.DATA_PATH}/{id}.json"

            # Check if video data already exists
            if os.path.isfile(filepath):

                # Hash the file and store it for cross referencing
                with open(filepath,"rb") as data:
                    data_hashes.add(xxhash.xxh128_hexdigest(data.read()))

                # Create a new filename
                number = 1
                new_path = f"{CFG.DATA_PATH}/{id}_{number}.json"

                # Increment until no overlapping name
                while os.path.isfile(new_path):
                    # Hash the file and store it for cross referencing
                    with open(new_path,"rb") as image:
                        data_hashes.add(xxhash.xxh128_hexdigest(image.read()))

                    number += 1
                    new_path = f"{CFG.DATA_PATH}/{id}_{number}.json"

                # Delete it if it already matches another one
                if new_hash in data_hashes:
                    os.remove(temp_path)
                    status = "Existing"
                else:
                    os.rename(filepath,new_path)
                    os.rename(temp_path,filepath)
                    status = "Update"
            else:
                os.rename(temp_path,filepath)
                status = "New"

            vid_obj = VideoClass(video,status)

            return vid_obj
        else:
            return None

    def Get_All_Videos(self):
        """
        Retrieves all YT videos from a playlist, and returns a list of video_ids

        Also writes a singular JSON formatted file containing basic information of ALL videos in a single file. File is named "__Video_Playlist.json"

        :return: video_ids of all videos from the playlist
        :rtype: List of Strings
        """
        
        with LOG.TQDM_Logging():
            with tqdm(desc='Video Data Downloaded',bar_format='{desc}: {n_fmt}',ncols=80,position=0,leave=False) as dl_vidbar:

                request = self.api.playlistItems().list(part="contentDetails,id,snippet,status",playlistId=CFG.PLAYLIST,maxResults=50)

                response = request.execute()

                next_page = response["nextPageToken"]

                video_list:list[dict[str,Any]] = response["items"]

                dl_vidbar.update(len(video_list))

                while True:
                    if next_page == None:
                        break
                    else:
                        next_request = self.api.playlistItems().list(part="contentDetails,id,snippet,status",playlistId=CFG.PLAYLIST,maxResults=50,pageToken=next_page)
                        next_response = next_request.execute()

                        try:
                            next_page = next_response["nextPageToken"]
                        except:
                            next_page = None

                        response_items:list[dict] = next_response["items"]

                        dl_vidbar.update(len(response_items))

                        video_list:list[dict[str,Any]] = video_list + response_items

        with open(f"{CFG.DATA_PATH}/__Video_Playlist.json",'w') as file:
            file.write(json.dumps(video_list,indent=4))

        ids:list[str] = []

        for video in video_list:
            vid_id:str = video["contentDetails"]["videoId"]
            ids.append(vid_id)
        
        return ids

    def Get_Messages(self,video:VideoClass,skip_download=False):
        """
        Retrieves all chat messages from a given video, saves them to JSON files, and enters them into the database.
        
        Writes a JSON formatted file for each video. File is named "[YT URL]_Messages.json"

        :param video: The video that is used to get the chats from
        :type video: Video Class Object
        :return: Stats about the messages that were parsed, and the users who sent them
        :rtype: ChatStats object
        """

        def _WriteFile():
            """
            Writes the downloaded chat data to a file with the name of the video ID.
            If the file already exists, load up all existsing chat meessages and
            add any new ones to the file.
            """
            
            message_path = f'{CFG.DATA_PATH}/{v.id}_Messages.json'

            if os.path.isfile(message_path):
                e_ids = set()
                
                with open(message_path,'r') as file:
                    all_messages = json.load(file) #type: list[dict]
                
                for ex_message in all_messages:
                    em_id = ex_message["message_id"]
                    e_ids.add(em_id)

                for n_message in message_list:
                    nm_id = n_message["message_id"]
                    if nm_id in e_ids:
                        continue
                    else:
                        all_messages.append(n_message)

                with open(message_path,'w') as file:
                    file.write(json.dumps(all_messages,indent=4))
            else:
                with open(message_path,'w') as file:
                    file.write(json.dumps(message_list,indent=4))
        
        v = video

        #-----------------------#
        #-- GET ALL CHAT DATA --#
        #-----------------------#

        if skip_download == True:
            message_path = f'{CFG.DATA_PATH}/{v.id}_Messages.json'
            if os.path.isfile(message_path):
                with open(message_path,'r') as file:
                    messages_on_file = json.load(file) #type: list[dict]
        else:
            # Timeout will prevent sitting endlessly on a waiting room or livestream
            if CFG.TIMEOUT == True:
                chat = ChatDownloader(cookies=CFG.COOKIES).get_chat(url=v.id, message_types=['text_message', 'membership_item', 'paid_message', 'paid_sticker'],inactivity_timeout=5)
            else:
                chat = ChatDownloader(cookies=CFG.COOKIES).get_chat(url=v.id, message_types=['text_message', 'membership_item', 'paid_message', 'paid_sticker'])

        message_list = []
        chat_stats = ChatStats()

        chat_list = chat if skip_download == False else messages_on_file

        if chat_list is None:
            return chat_stats

        def Update_Postfix_Messages():
            return f"New Messages: {chat_stats.new_messages} | Existing Messages: {chat_stats.existing_messages} | New Users: {chat_stats.new_user_ids} | Existing Users: {len(chat_stats.exist_user_ids)}"

        with LOG.TQDM_Logging():
            with tqdm(desc='Messages Processed',bar_format='{desc}: {n_fmt} || {postfix}',ncols=80, postfix=Update_Postfix_Messages() ,position=1, leave=False) as messbar:
                try:
                    unique_user_ids = set()
                    # Process all chats collected by Chat_Downloader
                    for message in chat_list:
                        chat_stats.total_messages += 1
                        try:
                            msg = MessageClass(message,v)

                            #----------------------#
                            #-- USER ID DATABASE --#
                            #----------------------#

                            # Add Unique UserIDs if they don't already exist in DB (User's names may change over time, but not the UniqueID)
                            if len(DB.GetEntries(self.db.cursor,"user_ids",filter={"id":msg.usr_id})) == 0:
                                DB.InsertEntries(self.db.cursor,"user_ids",[{"id":msg.usr_id}])
                                self.db.database.commit()
                                chat_stats.new_user_ids += 1
                                unique_user_ids.add(msg.usr_id)
                            else:
                                unique_user_ids.add(msg.usr_id)
                                chat_stats.exist_user_ids.add(msg.usr_id)

                            #--------------------#
                            #-- EMOTE DATABASE --#
                            #--------------------#

                            # Add Unique Emotes if they don't already exist in DB
                            if len(msg.e_emote_entries) > 0:
                                DB.InsertEntries(self.db.cursor,"emotes",msg.e_emote_entries,"id")

                            #----------------------#
                            #-- MESSAGE DATABASE --#
                            #----------------------#

                            # Add message to DB if it doesn't already exist
                            if len(DB.GetEntries(self.db.cursor,"messages",filter={"message_id":msg.id})) == 0:
                                DB.InsertEntries(self.db.cursor,"messages",[msg.entry])
                                self.db.database.commit()

                                #-----------------------#
                                #-- NICKNAME DATABASE --#
                                #-----------------------#

                                # Get all the nicknames to search for
                                nickname_entries = DB.GetEntries(self.db.cursor,"nicknames","nickname")
                                nicknames:list[str] = []
                                for nick_entry in nickname_entries:
                                    for key in nick_entry.keys():
                                        nicknames.append(key)
                                sorted_nicknames = sorted(nicknames, key=len, reverse=True)

                                entries = []
                                used_positions = set()

                                #-------------------------------#
                                #-- NICKNAME MATCHES DATABASE --#
                                #-------------------------------#

                                # Only look for nicknames if there are any to look for in the database
                                if len(sorted_nicknames) > 0:
                                    for nick in sorted_nicknames:
                                        search_pattern = r'\b' + re.escape(nick) + r'\b'
                                        if msg.message is not None:
                                            for match in re.finditer(pattern=search_pattern, string=msg.message, flags=re.IGNORECASE):
                                                start, end = match.span()
                                                if not any(pos in used_positions for pos in range(start, end)):
                                                    entry = {
                                                        "message_id":msg.id,
                                                        "matched_nickname":nick,
                                                        "index_start":start,
                                                        "index_end":end
                                                    }
                                                    used_positions.update(range(start, end))
                                                    entries.append(entry)
                                    
                                    DB.InsertEntries(self.db.cursor,"nickname_matches",entries,"message_id,index_start,index_end")
                                    self.db.database.commit()

                                chat_stats.new_messages += 1
                                messbar.set_postfix_str(Update_Postfix_Messages())
                                messbar.update(1)
                            else:
                                chat_stats.existing_messages += 1
                                messbar.set_postfix_str(Update_Postfix_Messages())
                                messbar.update(1)

                            message_list.append(message)
                        except Exception as e:
                            messbar.update(1)
                            raise e
                except Exception as r:
                    _WriteFile() # If it crashes, at least we get some of the messages to file so we can debug.
                    raise r

                _WriteFile()

        return chat_stats

    def Get_User_Batch(self,users:list[str]):
        """Gets data about all users in the list of users. Will keep track of invalid users.
        
        NOTE: the Youtube API call will only return 50 at most, break lists up into chunks of 50.

        :param users: List of 50 or less users
        :type users: List of Strings
        :return: Number of users with invalid accounts (usually because they got banned)
        :rtype: Integer
        """
        invalid:int = 0

        #-------------------#
        #-- GET USER DATA --#
        #-------------------#

        request = self.api.channels().list(part="id,snippet,statistics,status,brandingSettings",id=users)
        response = request.execute()
        if "items" in response:
            user_list = response["items"]
        else:
            user_list = []
        valid_ids = set()

        try:
            for user in user_list:
                
                del user["kind"]
                del user["etag"]

                u = UserClass(user)

                #-----------------------------#
                #-- WRITE USER DATA TO DISK --#
                #-----------------------------#

                temp_path = f"{CFG.DATA_PATH}/users/{u.id}_TEMP.json"

                # Write user data to file
                with open(temp_path,'w') as file:
                    file.write(json.dumps(user,indent=4))

                # Hash the user data
                with open(temp_path,"rb") as image:
                    new_hash = xxhash.xxh128_hexdigest(image.read())

                data_hashes = set()
                user_path = f"{CFG.DATA_PATH}/users/{u.id}.json"

                # Check if user data already exists
                if os.path.isfile(user_path):
                    # Hash the file and store it for cross referencing
                    with open(user_path,"rb") as data:
                        data_hashes.add(xxhash.xxh128_hexdigest(data.read()))
                    # Create a new filename
                    number = 1
                    new_path = f"{CFG.DATA_PATH}/users/{u.id}_{number}.json"

                    # Increment until no overlapping name
                    while os.path.isfile(new_path):
                        # Hash the file and store it for cross referencing
                        with open(new_path,"rb") as image:
                            data_hashes.add(xxhash.xxh128_hexdigest(image.read()))

                        number += 1
                        new_path = f"{CFG.DATA_PATH}/users/{u.id}_{number}.json"
                    # Delete it if it already matches another one
                    if new_hash in data_hashes:
                        os.remove(temp_path)
                    else:
                        os.rename(user_path,new_path)
                        os.rename(temp_path,user_path)
                else:
                    os.rename(temp_path,user_path)

                #------------------------------#
                #-- PROFILE PICTURE DOWNLOAD --#
                #------------------------------#

                if u.pfp is not None:
                    temp_pfp = f"{CFG.DATA_PATH}/users/{u.id}_pfp_TEMP.jpg"

                    # Download a fresh pfp
                    with open(temp_pfp,'wb') as handle:
                        img_response = requests.get(u.pfp,stream=True)
                        if not img_response.ok:
                            LOG.logger.info(img_response)
                        for block in img_response.iter_content(1024):
                            if not block:
                                break
                            handle.write(block)
                    # Hash the fresh profile picture
                    with open(temp_pfp,"rb") as image:
                        new_hash = xxhash.xxh128_hexdigest(image.read())

                    # Check for previously downloaded profile picture
                    pfp_hashes = set()
                    pfp_path = f"{CFG.DATA_PATH}/users/{u.id}_pfp.jpg"

                    # Check if a profile picture already exists
                    if os.path.isfile(pfp_path):

                        # Hash the file and store it for cross referencing
                        with open(pfp_path,"rb") as image:
                            pfp_hashes.add(xxhash.xxh128_hexdigest(image.read()))

                        # Create a new filename
                        number = 1
                        new_path = f"{CFG.DATA_PATH}/users/{u.id}_pfp_{number}.jpg"

                        # Increment until no overlapping name
                        while os.path.isfile(new_path):
                            # Hash the file and store it for cross referencing
                            with open(new_path,"rb") as image:
                                pfp_hashes.add(xxhash.xxh128_hexdigest(image.read()))

                            number += 1
                            new_path = f"{CFG.DATA_PATH}/users/{u.id}_pfp_{number}.jpg"

                        # Delete it if it already matches another one
                        if new_hash in pfp_hashes:
                            os.remove(temp_pfp)
                        else:
                            os.rename(pfp_path,new_path)
                            os.rename(temp_pfp,pfp_path)
                    else:
                        os.rename(temp_pfp,pfp_path)

                #------------------------------#
                #-- USER DATABASE OPERATIONS --#
                #------------------------------#

                # Update the unprocessed User_ID with additional information
                for column, value in u.entry.items():
                    DB.UpdateEntry(self.db.cursor,"user_ids",column,value,"id",u.id)
                    self.db.database.commit()

                DB.UpdateEntry(self.db.cursor,"user_ids","processed",True,"id",u.id)
                self.db.database.commit()

                valid_ids.add(u.id)
        except:
            invalid += 1
        
        all_users = users

        for user in all_users:
            if user not in valid_ids:
                DB.UpdateEntry(self.db.cursor,"user_ids","exists",False,"id",user)
                DB.UpdateEntry(self.db.cursor,"user_ids","processed",True,"id",user)
                self.db.database.commit()
                invalid += 1
        
        return invalid


def _get_date_time(timestamp:str):
    """Some timestamp strings in the API include fractions of a second."""
    pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?Z?$'
    match = re.match(pattern, timestamp)
    if not match:
        raise ValueError(f"Invalid datetime format: {timestamp}")
    
    base = match.group(1)
    microseconds = match.group(2)

    if microseconds:
        microseconds = microseconds.ljust(6, '0')[:6]
        formatted_string = f"{base}.{microseconds}"
    else:
        formatted_string = f"{base}.000000"
    return datetime.strptime(formatted_string, "%Y-%m-%dT%H:%M:%S.%f")