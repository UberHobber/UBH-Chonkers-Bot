# Native Stuff
import os,json,pickle,requests,re
from types import SimpleNamespace

# Installed Stuff
from tqdm import tqdm
from chat_downloader import ChatDownloader

# Google Stuff
import google.auth
import google.auth.exceptions
import google
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Other Project Files
import Classes as C
import logconfig as LOG
import Settings as CFG
import Database as DB

# Will be useful later when figuring out how to compare JSON files with one another to find changes in critical areas.
class NamespaceBaseClass:
    """
    The base class for Videos and Messages. Will take all parts of the JSON files and turn them into targetable items.

    :param json: The loaded JSON object, or just a dictionary to be converted into a SimpleNamespace.
    :type json: Dict
    :return: An object to be loaded into the other classes, which can pull data from it.
    :rtype: SimpleNamespace Object
    
    """
    def __init__(self, json:dict):
        def _NamespaceConvert(json_input):
            if isinstance(json_input,dict):
                return SimpleNamespace(**{key: _NamespaceConvert(value) for key, value in json_input.items()})
            elif isinstance(json_input,list):
                return [_NamespaceConvert(item) for item in json_input]
            else:
                return json_input
        self.data = _NamespaceConvert(json)


class VideoClass(NamespaceBaseClass):
    """
    A Class that will nab all the data currently implemented into the database structure. 
    Creates a useful self.entry variable for piping into the database method(s) as needed.

    :param json: The JSON file (preferably loaded from json.load), ideally provided from the Get_All_Videos method in the YT_API class
    :type json: Dict
    """
    def __init__(self,video:dict):
        super().__init__(video)
        try:
            self.id = self.data.id # Back-end ID for video
            self.publish_date = self.data.snippet.publishedAt # Date video was released / VOD was generated
            self.title = self.data.snippet.title # Video Title
            self.url_id = self.data.snippet.resourceId.videoId # URL ID code

            # JSON file will only contain an object for a thumbnail if one exists. This will get the best quality one it can find.
            self.thumbnail_sizes = [resolution for resolution in dir(self.data.snippet.thumbnails) if "__" not in resolution]
            if "maxres" in self.thumbnail_sizes:
                self.thumbnail = self.data.snippet.thumbnails.maxres.url
            elif "standard" in self.thumbnail_sizes:
                self.thumbnail = self.data.snippet.thumbnails.standard.url
            elif "high" in self.thumbnail_sizes:
                self.thumbnail = self.data.snippet.thumbnails.high.url
            elif "medium" in self.thumbnail_sizes:
                self.thumbnail = self.data.snippet.thumbnails.medium.url
            elif "default" in self.thumbnail_sizes:
                self.thumbnail = self.data.snippet.thumbnails.default.url
            else:
                LOG.logger.warning(f"Video {self.id} has no thumbnail URL!")

            self.file = f"{CFG.DATA_PATH}/{self.url_id}.json"
            self.entry = {
                "id":self.id,
                "title":self.title,
                "videoId":self.url_id,
                "publishedAt":self.publish_date
            }
        except Exception as e:
            LOG.logger.error(f"Video file {self.id} not initialized:\n{e}")
            raise e

class MessageClass(NamespaceBaseClass):
    """
    A Class that will nab all the data currently implemented into the database structure. 
    Creates a useful self.entry variable for piping into the database method(s) as needed.

    :param json: The JSON file (preferably loaded from json.load), ideally provided from the get_chat method from the ChatDownloader tool developed by xenova
    :type json: Dict
    """
    def __init__(self,message:dict,video:VideoClass):
        super().__init__(message)
        try:
            self.id = self.data.message_id # Back-end ID for message
            self.message = self.data.message # Message contents
            self.time_absolute = self.data.timestamp # Exact time the message was sent
            self.time_relative = self.data.time_in_seconds if "time_in_seconds" in dir(self.data) else None # Time message was sent relative to VOD start time of 0s
            self.type = self.data.message_type # Message, Superchat, etc.
            
            self.video_id = video.url_id
            
            self.usr_id = self.data.author.id
            self.usr_name = self.data.author.name

            if "badges" in dir(self.data.author):
                self.member_months = self._Membership_Level(self.data.author.badges[0].title)
            else:
                self.member_months = -1
            
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
            }
        except Exception as e:
            LOG.logger.error(f"Video file {self.id} not initialized:\n{e}")
            raise e

    def _Membership_Level(self,badge_data:str):
        if badge_data == "New member":
            return 0
        elif "month" in badge_data:
            return int(re.findall(r'\d+',badge_data)[0])
        elif "year" in badge_data:
            return int(re.findall(r'\d+',badge_data)[0]) * 12

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
            credentials = None
            
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


    def Get_All_Videos(self):
        """
        Retrieves all YT videos from a playlist, saves them to JSON files, and enters them into the database.
        
        Writes a JSON formatted file for each video. File is named "[YT URL]_[Video Title].json"

        Also writes a singular JSON formatted file containing ALL videos in a single file. File is named "_All_Videos.json"
        """
        LOG.logger.info('Downloading all video details')
        LOG.logger.info('-----------------------------')
        request = self.api.playlistItems().list(part="contentDetails,id,snippet,status",playlistId=CFG.PLAYLIST,maxResults=50)

        response = request.execute()

        next_page = response["nextPageToken"]

        video_list:list = response["items"]
        LOG.logger.info(f"{len(response["items"])} video(s) added to list. {len(video_list)} total.")

        while True:
            if next_page == None:
                LOG.logger.info('No next page, stopping loop.\n')
                break
            else:
                next_request = self.api.playlistItems().list(part="contentDetails,id,snippet,status",playlistId=CFG.PLAYLIST,maxResults=50,pageToken=next_page)
                next_response = next_request.execute()

                try:
                    next_page = next_response["nextPageToken"]
                except:
                    next_page = None

                video_list = video_list + next_response["items"]
                LOG.logger.info(f"{len(next_response["items"])} video(s) added to list. {len(video_list)} total.")

        LOG.logger.info('Writing all videos to single file...\n')
        with open(f"{CFG.DATA_PATH}/__All_Videos.json",'w') as file:
            file.write(json.dumps(video_list,indent=4))

        LOG.logger.info('Writing video details to individual files and adding to database')
        LOG.logger.info('----------------------------------------------------------------')
        for video in video_list:
            self.vid = C.VideoClass(video)

            # Skip the loop if the data JSON file already exists (TODO: Add code to parse JSON to look for differences)
            if os.path.isfile(self.vid.file) and len(DB.GetEntries(self.db.cursor,"videos","processed",{"videoId":self.vid.url_id,"processed":True})) > 0:
                LOG.logger.info(f'Video already exists and is processed - {self.vid.url_id}: {self.vid.title}, skipping.')
                continue
            elif os.path.isfile(self.vid.file):
                LOG.logger.info(f'File exists, but is unprocessed - {self.vid.url_id}: {self.vid.title}, adding to database.')

            # Write Video data to file
            with open(self.vid.file,'w') as file:
                file.write(json.dumps(video,indent=4))
            LOG.logger.info(f'Written to file - {self.vid.url_id}: {self.vid.title}')

            # Download Video Thumbnail
            with open(f"{CFG.DATA_PATH}/{self.vid.url_id}_Thumbnail.jpg",'wb') as handle:
                img_response = requests.get(self.vid.thumbnail,stream=True)
                if not img_response.ok:
                    LOG.logger.info(img_response)
                for block in img_response.iter_content(1024):
                    if not block:
                        break
                    handle.write(block)
            LOG.logger.info(f"Thumbnail downloaded: {self.vid.url_id}_Thumbnail.jpg")

            # Replaces the Video Entry in Database if it already exists
            # (Previous check above makes sure this only happens when the data has changed and needs replacing)
            if len(DB.GetEntries(self.db.cursor,"videos",filter={"id":self.vid.id})) == 0:
                LOG.logger.info(f"Inserting {self.vid.title} into database...\n")
                DB.InsertEntries(self.db.cursor,"videos",[self.vid.entry])
                self.db.database.commit()
            else:
                # Currently will only fire if new JSON is downloaded, but Video was added before
                # This is meant to be used when JSON comparison is implemented to find upates to video (and possibly more chats)
                LOG.logger.info(f"{self.vid.title} already exists, updating...\n")
                for key,value in self.vid.entry.items():
                    DB.UpdateEntry(self.db.cursor,"videos",key,value,"id",self.vid.id)
                    self.db.database.commit()
        LOG.logger.info(f'\nAll details for {len(video_list)} video(s) downloaded and processed.\n')

    def Get_Messages(self,video:VideoClass):
        """
        Retrieves all chat messages from a given video, saves them to JSON files, and enters them into the database.
        
        Writes a JSON formatted file for each video. File is named "[YT URL]_[Video Title]_Messages.json"

        :param video: The video that is used to get the chats from
        :type video: Video Class Object
        """

        def _WriteFile():
            LOG.logger.info(f'Writing messages to file...')
            with open(f'{CFG.DATA_PATH}/{v.url_id}_Messages.json','w') as file:
                file.write(json.dumps(message_list,indent=4))
        
        v = video
        
        LOG.logger.info(f'Downloading Chat...')
        chat = ChatDownloader(cookies=CFG.COOKIES).get_chat(v.url_id, message_types=['text_message', 'membership_item', 'paid_message', 'paid_sticker'],inactivity_timeout=5)

        message_list = []
        new_user_ids = 0
        exist_user_ids = 0
        new_messages = 0
        exist_messages = 0

        LOG.logger.info(f'Entering messages into database...')
        try:
            with tqdm(desc='Messages Processed',unit='messages',bar_format='{desc}: {n_fmt} {unit}',ncols=80) as pbar:
                for message in chat:
                    try:
                        message_list.append(message)

                        msg = MessageClass(message,v)

                        # Add Unique UserIDs if they don't already exist in DB (User's names may change over time, but not the UniqueID)
                        if len(DB.GetEntries(self.db.cursor,"user_ids",filter={"id":msg.usr_id})) == 0:
                            DB.InsertEntries(self.db.cursor,"user_ids",[{"id":msg.usr_id}])
                            self.db.database.commit()
                            new_user_ids += 1
                        else:
                            exist_user_ids += 1
                        
                        # Add message to DB if it doesn't already exist
                        # This is mostly here so that a livestream was still going, it can be updated again with new chats later.
                        # NOTE: Video DB must be manually edited to set Processed flag to 0. No JSON comparison checking code yet.
                        if len(DB.GetEntries(self.db.cursor,"messages",filter={"message_id":msg.id})) == 0:
                            LOG.logger.debug(f"Inserting {msg.id} into database...")
                            DB.InsertEntries(self.db.cursor,"messages",[msg.entry])
                            self.db.database.commit()
                            new_messages += 1
                            pbar.update(1)
                        else:
                            LOG.logger.debug(f"{msg.id} already exists, skipping...")
                            exist_messages += 1
                            pbar.update(1)
                    except Exception as e:
                        LOG.logger.error(f"Problem with message {msg.id}")
                        raise e
        except Exception as r:
            LOG.logger.error(f"Problem processing message(s) from {v.url_id}\n {r}")
            _WriteFile() # If it crashes, at least we get some of the messages to file so we can debug.
            raise r
        
        _WriteFile()

        LOG.logger.info(f"Processing messages complete for {video.url_id}:")
        LOG.logger.info(f"New Unique Users: {new_user_ids}")
        LOG.logger.info(f"Existing Unique Users: {exist_user_ids}")
        LOG.logger.info(f"New Messages: {new_messages}")
        LOG.logger.info(f"Existing Messages: {exist_messages}")
