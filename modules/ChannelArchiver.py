"""
Background compress + archive of finished channels, so Main.py doesn't have to stop for them.

Main.py submit()s each channel that finished with no errors (CFG.AUTO_ARCHIVE). One daemon
thread works through them in order: compress_raw_chat.compress_channel, then
archive_chat.archive_channel -- one channel at a time, to bound the extra load on the DB server.

Safe to run beside the bot because:
    - The bot reads and writes each channel's messages partition directly (never the parent
      table), so the archive's exclusive lock on a finished channel's partition can't block it.
    - user_channel_activity rows are per channel, so compress's badge recompute doesn't collide
      with the bot's stats writes for the next channel.
    - Both steps look their channel up with CFG.channel_targets, never CFG.select_channel (which
      rewrites globals the main thread is using).
    - Everything here has its own DB connection, S3 client and DuckDB connection.

The thread is a daemon: Ctrl+C doesn't wait for it. Both steps are idempotent and transactional
(an interrupted archive leaves Postgres untouched; its files are cleaned up as orphans next run).
"""
# Native Stuff
import queue,tempfile,threading,time

# Other Project Files
import modules.Settings as CFG
import modules.logconfig as LOG
import modules.Database as DB
import modules.Classes as C
import modules.Archive as A
import modules.Status as ST
import compress_raw_chat
import archive_chat

LOG_PREFIX = "[Archive] "

class ChannelArchiver:
    """Queue of finished channels to compress and archive on one background thread."""
    def __init__(self,position:int):
        """
        :param position: Status/bar slot for this thread's progress (one past the video workers).
        """
        self.position = position
        self._queue:queue.Queue[str|None] = queue.Queue()
        self.done:list[str] = []
        self.failed:list[str] = []
        self.rows = 0
        self._thread = threading.Thread(target=self._run,name="channel-archiver",daemon=True)
        self._thread.start()

    def submit(self,channel_name:str):
        """Queue a channel the bot has finished with."""
        self._queue.put(channel_name)

    def finish(self):
        """Wait for every queued channel, then log a summary."""
        if self._thread.is_alive():
            pending = self._queue.qsize()
            if pending:
                LOG.logger.info(f"{LOG_PREFIX}Waiting for {pending:,} queued channel(s) to finish compressing/archiving...")
            self._queue.put(None)
            self._thread.join()
        summary = f"{LOG_PREFIX}{len(self.done):,} channel(s) compressed/archived, {self.rows:,} message(s) moved to Parquet."
        if self.failed:
            summary += f" Failed: {', '.join(self.failed)} -- see errors above; a later run retries them."
        LOG.logger.info(summary)

    def _run(self):
        db = client = duck = None
        with tempfile.TemporaryDirectory() as tmp_dir:
            while True:
                channel_name = self._queue.get()
                if channel_name is None:
                    break
                ST.set_task(self.position,channel_name,label="Archive")
                start = time.time()
                try:
                    if db is None:
                        db = DB.PostgresClass()
                        client = C.H3Client().client
                        duck = A.Base_Connection()
                    compress_raw_chat.compress_channel(db,client,channel_name,tmp_dir,CFG.ARCHIVE_COMPRESS_WORKERS,
                                                       bar_position=self.position,log_prefix=LOG_PREFIX)
                    rows,videos = archive_chat.archive_channel(db,client,duck,channel_name,CFG.ARCHIVE_MAX_ROWS,
                                                               bar_position=self.position,log_prefix=LOG_PREFIX)
                    self.rows += rows
                    self.done.append(channel_name)
                    LOG.logger.info(f"{LOG_PREFIX}{channel_name}: done in {time.time()-start:,.0f}s ({videos:,} video(s) archived).")
                except Exception as e:
                    self.failed.append(channel_name)
                    LOG.logger.error(f"{LOG_PREFIX}{channel_name}: {e}")
                    if db is not None:
                        try:
                            db.database.rollback()
                        except Exception:
                            db = None # Connection is gone -- reconnect for the next channel
                finally:
                    ST.set_task(self.position,None)
        if db is not None:
            db.Close()
