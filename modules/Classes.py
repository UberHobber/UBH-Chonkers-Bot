# Native Stuff
import os,json,pickle,requests,re,xxhash,threading,queue,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from contextlib import contextmanager
from typing import Any
from datetime import datetime

# Installed Stuff
from tqdm import tqdm
from chat_downloader import ChatDownloader
import yt_dlp
from yt_dlp.utils import DownloadError
from yt_dlp.networking.impersonate import ImpersonateTarget

# Resolved once and reused for every subtitle request in this process, rather than calling
# ImpersonateTarget() fresh per attempt -- a real browser's TLS/HTTP fingerprint stays
# constant across a session, so presenting a different one on every single request (which an
# empty ImpersonateTarget() risks, depending on yt-dlp/curl_cffi's resolution) looks exactly
# like automated traffic to YouTube's abuse detection, independent of how well-paced the
# requests are.
_SUBTITLE_IMPERSONATE_TARGET = ImpersonateTarget()

# Google Stuff
import google.auth
import google.auth.exceptions
import google.auth.external_account_authorized_user
import google.oauth2.credentials
import google
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# S3 API
import boto3
from botocore.config import Config as BotocoreConfig
from types_boto3_s3.client import S3Client
from botocore.exceptions import ClientError

# Other Project Files
import modules.logconfig as LOG
import modules.Settings as CFG
import modules.Database as DB

@contextmanager
def Stage_Bar(desc:str,position:int):
    """
    Per-worker status line for a video-processing sub-step that has no natural count to track
    (thumbnail fetch, DB writes, connecting to chat, subtitle rate-limit waits) -- these used to
    run silently between the 'Videos Processed' and 'Messages Processed' bars, making a worker
    look stalled. Reuses that worker's existing bar slot (`position`) with leave=False, so the
    line is overwritten by whatever comes next and never accumulates as a completed bar once the
    stage finishes.
    """
    with tqdm(desc=desc,bar_format='{desc}',ncols=80,position=position,leave=False) as bar:
        yield bar

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
        self.new_user_ids:set = set()
        self.exist_user_ids:set = set()
        self.invalid_users:int = 0

    def append_all(self,all_chat_stats:'ChatStats'):
        """Updates the total stats with additional numbers"""
        all_chat_stats.total_messages += self.total_messages
        all_chat_stats.new_messages += self.new_messages
        all_chat_stats.existing_messages += self.existing_messages
        all_chat_stats.new_user_ids = all_chat_stats.new_user_ids.union(self.new_user_ids)
        all_chat_stats.exist_user_ids = all_chat_stats.exist_user_ids.union(self.exist_user_ids)

class SubtitleStats:
    """Statistics about subtitle downloads for a channel run."""
    def __init__(self) -> None:
        self.fetched:int = 0
        self.no_captions:int = 0
        self.throttled:int = 0
        self.errors:int = 0

class H3Client():
    def __init__(self) -> None:
        self.client:S3Client = boto3.client(
        's3',
        endpoint_url=CFG.ENDPOINT_URL,
        aws_access_key_id=CFG.ENDPOINT_ID,
        aws_secret_access_key=CFG.ENDPOINT_KEY,
        region_name=CFG.ENDPOINT_REGION,
        config=BotocoreConfig(max_pool_connections=max(25, CFG.WORKER_COUNT * 15))
    )
    def get_all_buckets(self):
        response = self.client.list_buckets()
        buckets = response.get('Buckets')
        bucket_list:list[str] = []
        for bucket in buckets:
            bucket_name = bucket.get("Name")
            if bucket_name:
                bucket_list.append(bucket_name)
        return bucket_list

class H3Bucket:
    def __init__(self,client:S3Client,bucket_name:str,local_dir:str) -> None:
        self.name = bucket_name
        self.client = client
        self.local_root = local_dir
        try:
            self.client.head_bucket(Bucket=self.name)
        except ClientError as e:
            error_code = e.response['Error']['Code'] #type:ignore
            if error_code == '404':
                LOG.logger.info(f"Bucket {self.name} does not exist, creating it...")
                try:
                    self.client.create_bucket(Bucket=self.name)
                    LOG.logger.info(f"Bucket {self.name} created.")
                except ClientError as create_error:
                    LOG.logger.error(f"Error creating bucket {self.name}:\n{create_error}")
                    raise
            elif error_code == '403':
                LOG.logger.error(f"No permission to access bucket {self.name}!")
            else:
                LOG.logger.error(f"Error accessing bucket {self.name}:\n{e}")
        except Exception as e:
            LOG.logger.error(f"Error accessing bucket {self.name}:\n{e}")

    def get_object_count(self):
        paginator = self.client.get_paginator('list_objects_v2')
        count_iterator = paginator.paginate(Bucket=self.name).search('KeyCount')
        total_keys = sum(count for count in count_iterator)
        return total_keys

    def iterate_all_objects(self,filter:str|None=None):
        paginator = self.client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=self.name,Prefix=filter) if filter else paginator.paginate(Bucket=self.name)
        for page in pages:
            page_packet = []
            if 'Contents' in page:
                for obj in page['Contents']:
                    page_packet.append({'Key': obj['Key']}) #type:ignore
            yield page_packet

    def delete_objects(self,filter:str|None=None):
        deleted_count:int = 0
        try:
            for obj_packet in self.iterate_all_objects(filter):
                response = self.client.delete_objects(
                    Bucket=self.name,
                    Delete={"Objects":obj_packet}
                )
                deleted = len(response.get('Deleted', []))
                deleted_count += deleted
                if 'Errors' in response:
                    for error in response['Errors']:
                        LOG.logger.warning(f"Error deleting {error['Key']}: {error['Message']}") #type:ignore
                LOG.logger.info(f"Deleted {deleted} object(s)")
            return deleted_count
        except ClientError as e:
            LOG.logger.error(f"Error deleting object(s): {e}")
            return -1

    def check_object_exists(self,object_name:str):
        try:
            self.client.head_object(Bucket=self.name,Key=object_name)
            return True # File DOES Exist
        except ClientError as e:
            if e.response['Error']['Code'] == '404': #type:ignore
                return False # File does NOT exist
            else:
                LOG.logger.error(f"Error checking object {object_name}: {e}")
                return e

    def upload_object(self,path:str,object_name:str):
        try:
            object_status = self.check_object_exists(object_name)
            if isinstance(object_status,ClientError):
                raise object_status
            if object_status is True:
                self.client.upload_file(path,self.name,object_name)
                return True # File Overwritten
            else:
                self.client.upload_file(path,self.name,object_name)
                return False # File Uploaded
        except ClientError as e:
            LOG.logger.error(f"Client Error while uploading {object_name}: {e}")
            return e # Error Uploading

    def download_object(self,object_name:str,file_path:str):
        try:
            object_status = self.check_object_exists(object_name)
            if isinstance(object_status,ClientError):
                raise object_status
            elif object_status is False:
                return False # No file to download
            else:
                self.client.download_file(self.name,object_name,file_path)
                return True # File downloaded
        except ClientError as e:
            LOG.logger.error(f"Client Error while downloading {object_name}: {e}")
            return e # Error Uploading

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
        self.members = True if CFG.GET_MEMBERS_ONLY is True else False
        try:

            self.id:str|None = video.get("id") # Back-end ID for video
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

                # Present only if YouTube's API returned it -- absent (not empty-list) means
                # either this video genuinely has no tags, or this VideoClass was built from an
                # older cached details JSON that predates tags being captured at all. Callers
                # that need to tell those two cases apart (see backfill_tags.py) must check the
                # raw snippet dict themselves; this attribute alone can't distinguish them.
                self.tags:list[str]|None = self._snippet.get("tags")

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
                "end_time":self.actual_end,
                "members":self.members,
                "channel_id":CFG.YT_USER_ID
            }
        except Exception as e:
            LOG.logger.error(f"Video file {self.id} not initialized:\n{e}")
            raise e

    def Get_Thumbnail(self,bucket:H3Bucket):
        """Will download the video thumbnail. Checks if there's an updated one and renames the old one and downloads a new one."""

        if self.thumbnail is not None and self.id is not None:
            temp_thumb = f"{bucket.local_root}/{CFG.THUMBNAIL_TAG}/{self.id}_TEMP.jpg"
            # Download a fresh thumbnail
            with open(temp_thumb,'wb') as handle:
                img_response = requests.get(self.thumbnail,stream=True)
                if not img_response.ok:
                    LOG.logger.info(img_response)
                for block in img_response.iter_content(1024):
                    if not block:
                        break
                    handle.write(block)

            _check_and_upload_file(bucket,temp_thumb,self.id,"jpg",CFG.THUMBNAIL_TAG)

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
                    if CFG.GET_MEMBERS_ONLY is False:
                        e_entry["channel_id"] = CFG.YT_USER_ID
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
            if CFG.GET_MEMBERS_ONLY is False:
                self.entry["channel_id"] = CFG.YT_USER_ID
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

_RETRYABLE_HTTP_STATUSES = {429,500,503}

def _execute_with_retry(request,max_attempts:int=5):
    """
    Executes a googleapiclient request, retrying with exponential backoff on transient errors
    (429 rate limit, 500/503 backend errors) -- YouTube's API intermittently returns these under
    normal load, not just when something's actually broken (e.g. a bare "503 The service is
    currently unavailable" mid-pagination once killed a whole channel's run). Any other status
    (404, 400, etc.) is raised immediately, same as calling request.execute() directly.

    :param request: A googleapiclient request object (not yet executed).
    :param max_attempts: Number of attempts before giving up and raising.
    :type max_attempts: Integer
    """
    for attempt in range(max_attempts):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status not in _RETRYABLE_HTTP_STATUSES or attempt == max_attempts - 1:
                raise
            wait = 2 ** attempt
            LOG.logger.warning(f"YouTube API returned {e.resp.status}, retrying in {wait}s (attempt {attempt+1}/{max_attempts})")
            time.sleep(wait)

class SubtitleThrottled(Exception):
    """Raised when yt-dlp subtitle downloads keep failing in a way that looks like YouTube
    throttling/blocking this IP, after exhausting CFG.SUBTITLE_MAX_ATTEMPTS retries."""
    pass

_THROTTLE_SIGNATURES = (r"\b429\b","too many requests","sign in to confirm you're not a bot")

def _is_throttled(exc:Exception) -> bool:
    """Best-effort classification of a yt-dlp DownloadError as YouTube throttling/bot-detection
    rather than an unrelated failure (no captions, video unavailable, etc.) -- yt-dlp doesn't
    expose a distinct exception type for this, so this matches on known message signatures.
    Deliberately the full "sign in to confirm you're not a bot" phrase, not just "sign in to
    confirm" -- that shorter form also matches yt-dlp's age-restriction error ("Sign in to
    confirm your age"), which is a permanent, never-retryable failure for this video (see
    _is_age_restricted), not a transient throttle worth backing off and retrying. "429" is
    matched as a whole number (\\b429\\b), not a bare substring -- yt-dlp's error text can embed
    unrelated numbers (URLs, itags, byte offsets) that happen to contain "429"."""
    msg = str(exc).lower()
    return any(re.search(sig,msg) if sig.startswith(r"\b") else sig in msg for sig in _THROTTLE_SIGNATURES)

_AGE_RESTRICTED_SIGNATURES = ("confirm your age","age-restricted","age restricted")

def _is_age_restricted(exc:Exception) -> bool:
    """Detects yt-dlp's age-restriction error (no authenticated cookies available to bypass
    it). Permanent for this video -- never worth retrying or waiting on, unlike a throttle."""
    msg = str(exc).lower()
    return any(sig in msg for sig in _AGE_RESTRICTED_SIGNATURES)

class _YtdlpSilentLogger:
    """Swallows all of yt-dlp's own log output. Get_Subtitles already logs the outcomes it
    cares about (throttled/age-restricted/no captions/etc.) through modules.logconfig, which
    coordinates properly with an in-progress tqdm bar -- yt-dlp's own error/warning messages
    print straight to stdout/stderr regardless of the quiet/no_warnings options (those only
    suppress its info-level noise), colliding mid-line with the bar's carriage-return updates
    and mangling the console. The exception raised on failure still carries the full message
    text for _is_throttled/_is_age_restricted to classify -- only the raw printing is muted."""
    def debug(self,msg): pass
    def info(self,msg): pass
    def warning(self,msg): pass
    def error(self,msg): pass

class AdaptiveRateLimiter:
    """
    Enforces a minimum gap between subtitle download attempts that grows automatically
    whenever YouTube throttles a request, instead of relying on a fixed guessed delay -- a
    flat few-second gap was still drawing HTTP 429s on a large fraction of videos during a
    real backfill run, even with per-video retry/backoff, and most of those never recovered
    within CFG.SUBTITLE_MAX_ATTEMPTS. Thread-safe and meant to be a single instance shared
    across every video/worker thread for the whole run (see Subtitles.py),
    passed into Get_Subtitles's rate_limiter parameter, so it learns whatever pace YouTube is
    actually enforcing right now rather than guessing a constant up front.

    Starts at `floor` seconds. Every reported throttle multiplies the current delay by 1.5x
    (capped at `ceiling`). After enough consecutive clean fetches, the delay decays back down
    slightly toward the floor, so a temporarily-strict period doesn't permanently slow down
    the rest of a multi-hour run.
    """
    def __init__(self,floor:float,ceiling:float):
        self._lock = threading.Lock()
        self._floor = floor
        self._ceiling = ceiling
        self._delay = floor
        self._last = 0.0
        self._consecutive_clean = 0

    @property
    def current(self) -> float:
        with self._lock:
            return self._delay

    def wait(self):
        with self._lock:
            delay = self._delay
            elapsed = time.monotonic() - self._last
            if elapsed < delay:
                time.sleep(delay - elapsed)
            self._last = time.monotonic()

    def report_throttled(self):
        with self._lock:
            self._consecutive_clean = 0
            new_delay = min(self._delay * 1.5,self._ceiling)
            if new_delay > self._delay:
                LOG.logger.info(f"Subtitle request delay increased to {new_delay:.1f}s after a throttle.")
            self._delay = new_delay

    def report_success(self):
        with self._lock:
            self._consecutive_clean += 1
            if self._consecutive_clean >= 50 and self._delay > self._floor:
                self._delay = max(self._delay * 0.95,self._floor)
                self._consecutive_clean = 0

class SubtitleCircuitBreaker:
    """Trips after N consecutive throttle-exhausted videos (within one run), so a blocked
    IP doesn't get hammered by every remaining video once yt-dlp starts failing."""
    def __init__(self,threshold:int):
        self._lock = threading.Lock()
        self._consecutive = 0
        self._threshold = threshold
    def record_throttle(self):
        with self._lock:
            self._consecutive += 1
    def record_success(self):
        with self._lock:
            self._consecutive = 0
    @property
    def tripped(self) -> bool:
        return self._consecutive >= self._threshold

_VTT_TIMESTAMP = re.compile(r'(\d{2,}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2,}):(\d{2}):(\d{2})\.(\d{3})')
_VTT_TAG = re.compile(r'<[^>]+>')

def _vtt_timestamp_to_ms(hours:str,minutes:str,seconds:str,millis:str) -> int:
    return ((int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000) + int(millis)

def _parse_vtt_cues(vtt_path:str) -> list[dict[str,Any]]:
    """
    Parses a WebVTT file into a list of {seq, start_ms, end_ms, text} cues, in file order.
    Skips the WEBVTT header and any NOTE/STYLE blocks -- only cue timing lines and the text
    that follows them (until the next blank line) are collected.

    YouTube's auto-generated VTT is heavily duplicated by construction, in two different ways:

    1. It renders as a "rolling" 2-line caption window: each cue typically repeats most of
       the previous cue's text plus a little new content (e.g. "[Music]" then "[Music]\ndo"
       then "do" then "do\n[Music]" ...).
    2. Each spoken phrase appears twice: once as a "growing" cue with inline per-word
       karaoke-style timing tags (`<00:12:47.680><c> word</c>...`), then again immediately
       after as a plain "settled" cue containing the exact same words with the tags stripped.

    Both are collapsed here: tags are stripped from each cue's text first (so the tagged
    "growing" and plain "settled" versions of the same phrase become identical strings), then
    each cue is deduped against the immediately preceding one -- any line it repeats verbatim
    from the previous cue is dropped, and a cue contributing nothing new is skipped entirely.
    What's left is the genuinely new text at each cue's own timestamp. Repeats of the same
    word/phrase further apart (i.e. not in adjacent cues) are left alone -- those are real
    recurrences in the video, not parsing artifacts.

    :param vtt_path: Path to a .vtt file written by yt-dlp.
    :type vtt_path: String
    :return: Deduped cues in file order.
    :rtype: list[dict]
    """
    raw_cues:list[tuple[int,int,str]] = []
    with open(vtt_path,'r',encoding='utf-8') as file:
        lines = file.read().splitlines()

    i = 0
    while i < len(lines):
        match = _VTT_TIMESTAMP.search(lines[i])
        if match:
            start_ms = _vtt_timestamp_to_ms(*match.groups()[0:4])
            end_ms = _vtt_timestamp_to_ms(*match.groups()[4:8])
            i += 1
            text_lines:list[str] = []
            while i < len(lines) and lines[i].strip() != "":
                text_lines.append(lines[i])
                i += 1
            text = "\n".join(text_lines).strip()
            if text:
                raw_cues.append((start_ms,end_ms,text))
        else:
            i += 1

    cues:list[dict[str,Any]] = []
    prev_lines:list[str] = []
    seq = 0
    for start_ms,end_ms,raw_text in raw_cues:
        text = _VTT_TAG.sub("",raw_text).strip()
        if not text:
            continue
        cue_lines = text.split("\n")
        new_lines = [line for line in cue_lines if line not in prev_lines]
        prev_lines = cue_lines
        if not new_lines:
            continue
        cues.append({"seq":seq,"start_ms":start_ms,"end_ms":end_ms,"text":" ".join(new_lines)})
        seq += 1
    return cues

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

        response = _execute_with_retry(request)

        video_count = response["contentDetails"]["itemCount"]

        LOG.logger.info(f"{video_count} video(s) found!")
        return video_count

    def Get_Video_Info(self,id:str,bucket:H3Bucket):
        request = self.api.videos().list(part="contentDetails,id,snippet,status,liveStreamingDetails",id=id)

        response = _execute_with_retry(request)

        videos:list[dict] = response["items"]

        if len(videos) > 0:

            video = videos[0]

            del video["kind"]
            del video["etag"]

            temp_path = f"{bucket.local_root}/{CFG.DETAIL_TAG}/{id}_TEMP.json"

            # Write Video data to file
            with open(temp_path,'w') as file:
                file.write(json.dumps(video,indent=4))

            video_detail_status = _check_and_upload_file(bucket,temp_path,id,"json",CFG.DETAIL_TAG)

            vid_obj = VideoClass(video,video_detail_status)

            return vid_obj
        else:
            return None

    def Get_All_Videos(self,bucket:H3Bucket):
        """
        Retrieves all YT videos from a playlist, and returns a list of video_ids

        Also writes a singular JSON formatted file containing basic information of ALL videos in a single file. File is named "__Video_Playlist.json"

        :return: video_ids of all videos from the playlist
        :rtype: List of Strings
        """
        with LOG.TQDM_Logging():
            with tqdm(desc='Video Data Downloaded',bar_format='{desc}: {n_fmt}',ncols=80,position=0,leave=False) as dl_vidbar:

                request = self.api.playlistItems().list(part="contentDetails,id,snippet,status",playlistId=CFG.PLAYLIST,maxResults=50)
                try:
                    response = _execute_with_retry(request)
                except HttpError as e:
                    if e.resp.status == 404:
                        # Channel has no Members-Only content yet, so YouTube hasn't generated this hidden playlist.
                        LOG.logger.warning(f"Playlist {CFG.PLAYLIST} not found (404) - assuming no videos exist yet.")
                        return []
                    raise
                next_page = response.get("nextPageToken")
                video_list:list[dict[str,Any]] = response["items"]
                dl_vidbar.update(len(video_list))

                while True:
                    if next_page == None:
                        break
                    else:
                        next_request = self.api.playlistItems().list(part="contentDetails,id,snippet,status",playlistId=CFG.PLAYLIST,maxResults=50,pageToken=next_page)
                        next_response = _execute_with_retry(next_request)
                        next_page = next_response.get("nextPageToken")
                        response_items:list[dict] = next_response["items"]
                        dl_vidbar.update(len(response_items))
                        video_list:list[dict[str,Any]] = video_list + response_items

        playlist_name = "Video_Playlist" if CFG.GET_MEMBERS_ONLY is False else f"Video_Playlist{CFG.MEMBERS_ONLY_FLAG}"
        playlist_path = f"{CFG.LOCAL_DATA_PATH}/{playlist_name}_TEMP.json"
        with open(playlist_path,'w') as file:
            file.write(json.dumps(video_list,indent=4))

        _check_and_upload_file(bucket,playlist_path,playlist_name,"json")

        ids:list[str] = []

        for video in video_list:
            vid_id:str = video["contentDetails"]["videoId"]
            ids.append(vid_id)

        return ids

    def Get_Videos_Info_Batch(self,ids:list[str],bucket:H3Bucket) -> dict[str,'VideoClass']:
        """Fetches info for up to 50 video IDs in a single API call instead of one call per video."""
        result:dict[str,VideoClass] = {}
        if not ids:
            return result
        request = self.api.videos().list(part="contentDetails,id,snippet,status,liveStreamingDetails",id=",".join(ids))
        response = _execute_with_retry(request)
        for video in response.get("items",[]):
            vid_id = video["id"]
            del video["kind"]
            del video["etag"]
            temp_path = f"{bucket.local_root}/{CFG.DETAIL_TAG}/{vid_id}_TEMP.json"
            with open(temp_path,'w') as file:
                file.write(json.dumps(video,indent=4))
            video_detail_status = _check_and_upload_file(bucket,temp_path,vid_id,"json",CFG.DETAIL_TAG)
            result[vid_id] = VideoClass(video,video_detail_status)
        return result

    def Get_Messages(self,video:VideoClass,bucket:H3Bucket,known_user_ids:set,sorted_nicknames:list[str],db=None,user_id_lock=None,bar_position:int=1,skip_download=False):
        """
        Retrieves all chat messages from a given video, saves them to JSON files, and enters them into the database.

        Writes a JSON formatted file for each video. File is named "[YT URL]_Messages.json"

        :param video: The video that is used to get the chats from
        :type video: Video Class Object
        :return: Stats about the messages that were parsed, and the users who sent them
        :rtype: ChatStats object
        """
        def _WriteFile(object_name:str,object_path:str,existing_messages:list[dict[str,Any]],collected_messages:list[dict[str,Any]]):
            """
            Writes the downloaded chat data to a file with the name of the video ID.
            If the file already exists, load up all existsing chat meessages and
            add any new ones to the file.
            """

            e_ids = set()
            messages_to_save:list[dict[str,Any]] = []

            if existing_messages:
                for ex_message in existing_messages:
                    em_id = ex_message["message_id"]
                    e_ids.add(em_id)
                    messages_to_save.append(ex_message)

            for message in collected_messages:
                nm_id = message["message_id"]
                if nm_id in e_ids:
                    continue
                else:
                    messages_to_save.append(message)

            with open(object_path,"w") as file:
                file.write(json.dumps(messages_to_save,indent=4))

            upload_status = bucket.upload_object(object_path,object_name)
            if isinstance(upload_status,ClientError):
                raise upload_status

        v = video
        _db = db if db is not None else self.db
        _lock = user_id_lock if user_id_lock is not None else threading.Lock()

        with Stage_Bar("Checking existing messages",bar_position):
            message_object = f"{CFG.MESSAGES_TAG}/{v.id}.json"
            existing_messages_local = f'{bucket.local_root}/{message_object}'
            messages_exist = bucket.check_object_exists(message_object)
            if isinstance(messages_exist,ClientError):
                raise messages_exist
            elif messages_exist is True:
                downloaded_object = bucket.download_object(message_object,existing_messages_local)
                if isinstance(downloaded_object,ClientError):
                    raise downloaded_object
                elif os.path.isfile(existing_messages_local):
                    with open(existing_messages_local,'r') as file:
                        messages_on_file:list[dict[str,Any]] = json.load(file) #type: list[dict]
                else:
                    messages_on_file = []
            else:
                messages_on_file = []

            # Pre-load existing message IDs for this video to avoid per-message DB lookups
            existing_msg_rows = DB.GetEntries(_db.cursor,CFG.DB_TABLES["messages"],"message_id",{"video_id":v.id})
            known_message_ids:set = set(row["message_id"] for row in existing_msg_rows)

        #-----------------------#
        #-- GET ALL CHAT DATA --#
        #-----------------------#

        message_list:list[dict[str,Any]] = []
        chat_stats = ChatStats()

        if skip_download == False:
            # chat_downloader's built-in inactivity_timeout calls _thread.interrupt_main(),
            # which only raises KeyboardInterrupt in the main thread. In a worker thread the
            # timer fires but the interrupt lands in the wrong thread and the worker stays
            # blocked on the network call forever. We implement the timeout ourselves via a
            # queue so it works correctly from any thread.
            with Stage_Bar("Connecting to chat",bar_position):
                _raw_chat = ChatDownloader(cookies=CFG.COOKIES).get_chat(url=v.id, message_types=['text_message', 'membership_item', 'paid_message', 'paid_sticker'])
            if CFG.TIMEOUT:
                _msg_queue:queue.Queue = queue.Queue()
                def _feed_queue():
                    try:
                        for _msg in _raw_chat:
                            _msg_queue.put(_msg)
                    except Exception as _e:
                        _msg_queue.put(_e)
                    _msg_queue.put(None)
                threading.Thread(target=_feed_queue, daemon=True).start()
                def _timed_chat_iter():
                    while True:
                        try:
                            _item = _msg_queue.get(timeout=20)
                        except queue.Empty:
                            return
                        if _item is None:
                            return
                        if isinstance(_item, Exception):
                            raise _item
                        yield _item
                chat_list = _timed_chat_iter()
            else:
                chat_list = _raw_chat
        else:
            chat_list = messages_on_file

        if chat_list is None:
            return chat_stats

        def Update_Postfix_Messages():
            return f"New Messages: {chat_stats.new_messages:,} | Existing Messages: {chat_stats.existing_messages:,} | New Users: {len(chat_stats.new_user_ids):,} | Existing Users: {len(chat_stats.exist_user_ids - chat_stats.new_user_ids):,}"

        # record_message_stats() calls are buffered here instead of being executed immediately,
        # and flushed (sorted by user_id) right before every commit -- see _flush_pending_stats().
        pending_stats:list[tuple] = []

        def _flush_pending_stats():
            # Sorting before executing means every commit's worth of record_message_stats calls
            # locks user_first_message/user_channel_activity rows in the same ascending
            # user_id order, no matter which video or worker thread is running them. Two
            # transactions can then never hold those row locks in opposite relative order, which
            # is what a deadlock cycle requires -- see the "deadlock detected ...
            # user_first_message" incidents this replaced (processing messages in chronological
            # arrival order let two videos that share chatters lock overlapping users in
            # whatever order each happened to encounter them, which could cross).
            #
            # Sent as one batched call, not one call per row -- see RecordMessageStatsBatch's
            # docstring. A worker blocked on a contended user was waiting through up to 500
            # sequential round trips to a remote DB host before the lock-holder's batch
            # committed (multiple seconds of real stall, observed directly); one call removes
            # nearly all of that round-trip latency from the wait.
            pending_stats.sort(key=lambda s: s[2])
            DB.RecordMessageStatsBatch(_db.cursor, pending_stats)
            pending_stats.clear()

        with tqdm(desc='Messages Processed',bar_format='{desc}: {n_fmt} {postfix}',ncols=80, postfix=Update_Postfix_Messages() ,position=bar_position, leave=False) as messbar:
            try:
                unique_user_ids = set()
                uncommitted = 0
                # Process all chats collected by Chat_Downloader
                for message in chat_list:
                    chat_stats.total_messages += 1
                    try:
                        msg = MessageClass(message,v)

                        #----------------------#
                        #-- USER ID DATABASE --#
                        #----------------------#

                        # Add Unique UserIDs if they don't already exist in DB (User's names may change over time, but not the UniqueID)
                        # Lock covers check+insert+commit atomically. The commit MUST happen inside the lock so the
                        # user row is visible to all connections before any thread inserts a message referencing it.
                        # Without the commit here, a concurrent thread can see the user in known_user_ids (set already
                        # updated) but fail the FK check because the insert hasn't been flushed to the DB yet.
                        with _lock:
                            _is_new_user = msg.usr_id not in known_user_ids
                            if _is_new_user:
                                DB.InsertEntries(_db.cursor,CFG.DB_TABLES["user_ids"],[{"id":msg.usr_id}])
                                # Flush first -- otherwise this commit durably records every message
                                # inserted so far in this batch while their still-buffered
                                # record_message_stats() calls stay unexecuted, and a crash before the
                                # next flush would lose that message's stats contribution for good
                                # (known_message_ids skips re-inserting it, and the stats call, on retry).
                                _flush_pending_stats()
                                _db.database.commit()  # flush immediately — FK must be satisfied for all connections
                                uncommitted = 0
                                known_user_ids.add(msg.usr_id)
                        if _is_new_user:
                            chat_stats.new_user_ids.add(msg.usr_id)
                            unique_user_ids.add(msg.usr_id)
                        else:
                            unique_user_ids.add(msg.usr_id)
                            chat_stats.exist_user_ids.add(msg.usr_id)

                        #--------------------#
                        #-- EMOTE DATABASE --#
                        #--------------------#

                        # Add Unique Emotes if they don't already exist in DB
                        if len(msg.e_emote_entries) > 0:
                            emote_conflict = "channel_id,id" if CFG.GET_MEMBERS_ONLY is False else "id"
                            DB.InsertEntries(_db.cursor,CFG.DB_TABLES["emotes"],msg.e_emote_entries,emote_conflict)

                        #----------------------#
                        #-- MESSAGE DATABASE --#
                        #----------------------#

                        # Add message to DB if it doesn't already exist
                        if msg.id not in known_message_ids:
                            DB.InsertEntries(cursor=_db.cursor,table=CFG.DB_TABLES["messages"],data_list=[msg.entry])
                            known_message_ids.add(msg.id)

                            # Keep video_message_stats and user_channel_activity up to date incrementally
                            # rather than recomputing them later over the whole (200M+ row) messages table.
                            # Buffered, not called immediately -- see _flush_pending_stats().
                            pending_stats.append((
                                CFG.YT_USER_ID,
                                msg.video_id,
                                msg.usr_id,
                                msg.entry["timestamp"],
                                msg.entry["user_member_status"] is not None and msg.entry["user_member_status"] > -1,
                                msg.entry["user_member_status"],
                                msg.id,
                                len(msg.e_emote_entries),
                                msg.entry["ismoderator"],
                                msg.entry["isverified"],
                                msg.entry["isowner"],
                                msg.entry["amount"],
                                msg.entry["currency"],
                            ))

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
                                                if CFG.GET_MEMBERS_ONLY is False:
                                                    entry["channel_id"] = CFG.YT_USER_ID
                                                used_positions.update(range(start, end))
                                                entries.append(entry)

                                nickname_match_conflict = "channel_id,message_id,index_start,index_end" if CFG.GET_MEMBERS_ONLY is False else "message_id,index_start,index_end"
                                DB.InsertEntries(_db.cursor,CFG.DB_TABLES["nickname_matches"],entries,nickname_match_conflict)

                            chat_stats.new_messages += 1
                            messbar.set_postfix_str(Update_Postfix_Messages())
                            messbar.update(1)
                        else:
                            chat_stats.existing_messages += 1
                            messbar.set_postfix_str(Update_Postfix_Messages())
                            messbar.update(1)

                        message_list.append(message)

                        uncommitted += 1
                        if uncommitted >= 500:
                            _flush_pending_stats()
                            _db.database.commit()
                            uncommitted = 0

                    except Exception as e:
                        messbar.update(1)
                        raise e

            except Exception as r:
                # The failing statement aborts the whole transaction, including any earlier
                # uncommitted inserts in this batch -- they can't be salvaged by committing,
                # only rolled back. Must roll back (not commit) so the connection -- reused
                # for the next video on this worker thread -- isn't left in an aborted state.
                # pending_stats is discarded, not flushed -- those calls were never sent to the
                # DB, and the batch they belong to is being rolled back anyway.
                pending_stats.clear()
                _db.database.rollback()
                _WriteFile(message_object,existing_messages_local,messages_on_file,message_list)
                raise r

            if uncommitted > 0:
                _flush_pending_stats()
                _db.database.commit()
            _WriteFile(message_object,existing_messages_local,messages_on_file,message_list)

        return chat_stats

    def Get_Subtitles(self,video_id:str,bucket:H3Bucket,db=None,rate_limiter:'AdaptiveRateLimiter|None'=None,bar_position:int=1) -> SubtitleStats:
        """
        Downloads YouTube's auto-generated (ASR) subtitles for a video via yt-dlp, parses them
        into cues, uploads the parsed cue list to S3 as subtitles/{id}.json (same
        whole-file-replace/versioning behavior as details/{id}.json via _check_and_upload_file),
        and writes one row per cue into CFG.DB_TABLES["subtitles"].

        Retries on failures that look like YouTube throttling (see _is_throttled), with
        exponential backoff (CFG.SUBTITLE_BACKOFF_BASE ** attempt). Any other failure (no
        captions available, video unavailable, etc.) is not retried -- it just means this
        video/language has no subtitles to store.

        :param video_id: ID of the video to fetch subtitles for.
        :param bucket: The channel's S3 bucket.
        :type bucket: H3Bucket
        :param db: Database connection to use for the insert. Falls back to self.db.
        :param rate_limiter: Shared AdaptiveRateLimiter to pace requests against and report
            throttles/successes to. If omitted, no inter-request pacing happens here -- the
            caller is responsible for spacing calls out itself.
        :param bar_position: This worker's tqdm bar slot, used to show what stage (rate-limit
            wait vs. active download) is currently blocking this thread.
        :return: Stats about what happened for this video.
        :rtype: SubtitleStats
        """
        stats = SubtitleStats()
        _db = db if db is not None else self.db

        subtitle_object = f"{CFG.SUBTITLE_TAG}/{video_id}.json"
        object_exists = bucket.check_object_exists(subtitle_object)
        if isinstance(object_exists,ClientError):
            raise object_exists
        if object_exists is True:
            return stats

        url = f"https://www.youtube.com/watch?v={video_id}"
        all_cues:list[dict[str,Any]] = []

        for lang in CFG.SUBTITLE_LANGUAGES:
            temp_dir = f"{bucket.local_root}/{CFG.SUBTITLE_TAG}"
            current_delay = rate_limiter.current if rate_limiter is not None else CFG.SUBTITLE_REQUEST_DELAY
            ydl_opts:dict[str,Any] = {
                "writeautomaticsub": True,
                "subtitleslangs": [lang],
                "subtitlesformat": "vtt",
                "skip_download": True,
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
                "logger": _YtdlpSilentLogger(),
                "outtmpl": f"{temp_dir}/%(id)s_TEMP.%(ext)s",
                "sleep_interval_subtitles": current_delay,
                "sleep_interval_requests": current_delay,
                "extractor_retries": CFG.SUBTITLE_MAX_ATTEMPTS,
                "cookiefile": CFG.COOKIES,
                "impersonate": _SUBTITLE_IMPERSONATE_TARGET,
            }

            vtt_path = f"{temp_dir}/{video_id}_TEMP.{lang}.vtt"

            with Stage_Bar(f"Subtitles ({lang}): starting",bar_position) as sub_bar:
                for attempt in range(1,CFG.SUBTITLE_MAX_ATTEMPTS + 1):
                    if rate_limiter is not None:
                        sub_bar.set_description_str(f"Subtitles ({lang}): rate-limit wait (~{rate_limiter.current:.1f}s)")
                        rate_limiter.wait()
                    try:
                        sub_bar.set_description_str(f"Subtitles ({lang}): downloading")
                        ydl = yt_dlp.YoutubeDL(ydl_opts) #type:ignore
                        ydl.extract_info(url,download=True)
                        if rate_limiter is not None and attempt == 1:
                            rate_limiter.report_success()
                        break
                    except DownloadError as e:
                        if _is_age_restricted(e):
                            # Permanent for this video -- skip immediately rather than burning
                            # through the throttle retry/backoff loop for something that will
                            # never succeed no matter how many times it's attempted.
                            LOG.logger.info(f"{video_id}: Age-restricted, skipping.")
                            stats.no_captions += 1
                            vtt_path = None
                            break
                        elif _is_throttled(e):
                            if rate_limiter is not None:
                                rate_limiter.report_throttled()
                            if attempt == CFG.SUBTITLE_MAX_ATTEMPTS:
                                stats.throttled += 1
                                raise SubtitleThrottled(f"{video_id}: Subtitle download throttled after {attempt} attempt(s): {e}")
                            wait = CFG.SUBTITLE_BACKOFF_BASE ** attempt
                            LOG.logger.warning(f"{video_id}: Subtitle download throttled, retrying in {wait}s (attempt {attempt}/{CFG.SUBTITLE_MAX_ATTEMPTS}): {e}")
                            sub_bar.set_description_str(f"Subtitles ({lang}): throttled, retrying in {wait:.0f}s")
                            time.sleep(wait)
                            continue
                        else:
                            # Not a throttle signature -- treat as "no captions available" for this
                            # video/language rather than retrying.
                            stats.no_captions += 1
                            vtt_path = None
                            break

            if vtt_path is None or not os.path.isfile(vtt_path):
                stats.no_captions += 1
                continue

            cues = _parse_vtt_cues(vtt_path)
            os.remove(vtt_path)

            for cue in cues:
                cue["video_id"] = video_id
                cue["language"] = lang
                if CFG.GET_MEMBERS_ONLY is False:
                    cue["channel_id"] = CFG.YT_USER_ID
            all_cues.extend(cues)

            DB.DeleteEntries(_db.cursor,CFG.DB_TABLES["subtitles"],{"video_id":video_id,"language":lang})
            if cues:
                DB.InsertEntries(_db.cursor,CFG.DB_TABLES["subtitles"],cues)
                total_words = sum(len(cue["text"].split()) for cue in cues)
                speech_duration_ms = sum(cue["end_ms"] - cue["start_ms"] for cue in cues)
                DB.RecordSubtitleStats(_db.cursor,video_id,CFG.YT_USER_ID,lang,len(cues),total_words,speech_duration_ms)

        if not all_cues:
            return stats

        temp_path = f"{bucket.local_root}/{CFG.SUBTITLE_TAG}/{video_id}_TEMP.json"
        with open(temp_path,'w') as file:
            file.write(json.dumps(all_cues,indent=4))

        _check_and_upload_file(bucket,temp_path,video_id,"json",CFG.SUBTITLE_TAG)

        stats.fetched += 1
        return stats

    def Get_User_Batch(self,users:list[str],bucket:H3Bucket):
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

        # One API call per batch of 50 — rate unchanged vs. the original sequential loop.
        request = self.api.channels().list(part="id,snippet,statistics,status,brandingSettings",id=users)
        response = _execute_with_retry(request)
        user_list:list[dict] = response.get("items",[])

        # IDs returned by the API (present but perhaps file-processing failed)
        # vs. IDs the API didn't return at all (banned / deleted accounts).
        api_returned_ids:set = {u.get("id") for u in user_list if u.get("id")}
        valid_ids:set = set()  # successfully processed

        def _process_user_files(user:dict) -> 'UserClass|None':
            """Handles all file I/O for one user: JSON write, S3 upload, PFP download + upload.
            Returns the UserClass on success, None if the user has no id."""
            del user["kind"]
            del user["etag"]
            u = UserClass(user)
            if u.id is None:
                return None

            #-----------------------------#
            #-- WRITE USER DATA TO DISK --#
            #-----------------------------#

            temp_path = f"{bucket.local_root}/{CFG.DETAIL_TAG}/{u.id}_TEMP.json"
            with open(temp_path,'w') as file:
                file.write(json.dumps(user,indent=4))
            _check_and_upload_file(bucket,temp_path,u.id,"json",CFG.DETAIL_TAG)

            #------------------------------#
            #-- PROFILE PICTURE DOWNLOAD --#
            #------------------------------#

            if u.pfp is not None:
                temp_pfp = f"{bucket.local_root}/{CFG.PFP_TAG}/{u.id}_TEMP.jpg"
                with open(temp_pfp,'wb') as handle:
                    img_response = requests.get(u.pfp,stream=True)
                    if not img_response.ok:
                        LOG.logger.warning(f"PFP download failed for {u.id}: HTTP {img_response.status_code}")
                    for block in img_response.iter_content(1024):
                        if not block:
                            break
                        handle.write(block)
                _check_and_upload_file(bucket,temp_pfp,u.id,"jpg",CFG.PFP_TAG)

            return u

        # Parallelize per-user file I/O within the batch (PFP downloads + S3 uploads).
        # The API call above is already complete — no extra API concurrency is added.
        completed_users:list = []
        with ThreadPoolExecutor(max_workers=CFG.USER_WORKER_COUNT) as pool:
            futures = {pool.submit(_process_user_files,user): user.get("id") for user in user_list}
            for future in as_completed(futures):
                uid = futures[future]
                try:
                    u = future.result()
                    if u is not None:
                        completed_users.append(u)
                        valid_ids.add(u.id)
                except Exception as e:
                    LOG.logger.error(f"Error processing user {uid}: {e}")
                    invalid += 1

        #------------------------------#
        #-- USER DATABASE OPERATIONS --#
        #------------------------------#

        # Write all successful users, then all invalid users, with a single commit per batch.
        for u in completed_users:
            DB.UpdateEntries(self.db.cursor,CFG.DB_TABLES["user_ids"],{**u.entry,"processed":True},"id",u.id)

        for user in users:
            if user not in api_returned_ids:
                # Not in the API response at all — banned or deleted account.
                DB.UpdateEntry(self.db.cursor,CFG.DB_TABLES["user_ids"],"exists",False,"id",user)
                DB.UpdateEntry(self.db.cursor,CFG.DB_TABLES["user_ids"],"processed",True,"id",user)
                invalid += 1

        self.db.database.commit()

        return invalid

########################
### HELPER FUNCTIONS ###
########################

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

def _check_and_upload_file(bucket:H3Bucket,temp_path:str,file_name:str,file_extension:str,file_tag:str|None=None):

    def _build_paths(basepath:str|None=None,counter:str|int|None=None):
        if counter:
            full_name = f"{file_name}_{counter}.{file_extension}"
        else:
            full_name = f"{file_name}.{file_extension}"

        if file_tag:
            key_name = f"{file_tag}/{full_name}"
        else:
            key_name = f"{full_name}"

        if basepath:
            local_path = f"{basepath}/{key_name}"
        else:
            local_path = f"{key_name}"
        return key_name,local_path

    # Hash the fresh file
    with open(temp_path,"rb") as image:
        new_hash = xxhash.xxh128_hexdigest(image.read())

    #------------------------------#
    #-- CHECK FOR EXISTING FILES --#
    #------------------------------#

    hash_set:set[str] = set()
    file_list:list[str] = []
    file_key,file_path = _build_paths(bucket.local_root)

    try:
        # See if file already exists
        object_exists = bucket.check_object_exists(file_key)
        if isinstance(object_exists,ClientError):
            raise object_exists

            #-----------------------------------------------#
            #-- UPLOAD NEWEST FILE: WHEN FILE DON'T EXIST --#
            #-----------------------------------------------#

        elif object_exists is False:
            os.replace(temp_path,file_path)
            file_list.append(file_path)
            file_uploaded = bucket.upload_object(file_path,file_key)
            if isinstance(file_uploaded,ClientError):
                raise file_uploaded
            status = "New"

            #----------------------------#
            #-- GET ALL EXISTING FILES --#
            #----------------------------#

        else:
            # Attempt to download existing file
            file_downloaded = bucket.download_object(file_key,file_path)
            if isinstance(file_downloaded,ClientError):
                raise file_downloaded
            # Hash the file and store it for cross referencing
            with open(file_path,"rb") as image:
                hash_set.add(xxhash.xxh128_hexdigest(image.read()))
            # Add path to list for deletion later
            file_list.append(file_path)

            counter = 1
            next_name,next_path = _build_paths(bucket.local_root,counter)

            # Loop and download files until the next_path doesn't exist
            next_file_exists = bucket.check_object_exists(next_path)
            if isinstance(next_file_exists,ClientError):
                raise next_file_exists
            while next_file_exists is True:
                file_downloaded = bucket.download_object(next_name,next_path)
                if isinstance(file_downloaded,ClientError):
                    raise file_downloaded
                # Hash the file and store it for cross referencing
                with open(next_path,"rb") as image:
                    hash_set.add(xxhash.xxh128_hexdigest(image.read()))
                # Add path to list for deletion later
                file_list.append(next_path)

                counter += 1
                next_name,next_path = _build_paths(bucket.local_root,counter)
                next_file_exists = bucket.check_object_exists(next_path)
                if isinstance(next_file_exists,ClientError):
                    raise next_file_exists

            #-------------------------------------------------#
            #-- UPLOAD NEWEST FILE: WHEN FILE ALREADY EXIST --#
            #-------------------------------------------------#

            # If the new file doesn't match any existing ones:
            # Rename the last exiting file, upload it, replace it with the new one
            if new_hash not in hash_set:
                os.replace(file_path,next_path)
                file_list.append(next_path)
                file_uploaded = bucket.upload_object(next_path,next_name)
                if isinstance(file_uploaded,ClientError):
                    raise file_uploaded
                os.replace(temp_path,file_path)
                file_uploaded = bucket.upload_object(file_path,file_key)
                if isinstance(file_uploaded,ClientError):
                    raise file_uploaded
                status = "Update"
            else:
                status = "Existing"

            # Cleanup local files
            if len(file_list) > 0:
                for file in file_list:
                    if os.path.isfile(file):
                        os.remove(file)
    except ClientError as e:
        LOG.logger.error(f"Error processing file for {file_name}:\n{e}")
        status = "ERROR"
    return status