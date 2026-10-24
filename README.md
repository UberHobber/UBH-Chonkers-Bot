# UBH Chonkers Bot

Downloads the metadata, thumbnails and full chat history (messages, Super Chats, memberships, Super Stickers) of every public video on a set of YouTube channels into PostgreSQL. Chat from finished videos is then archived as compressed Parquet files on S3 storage.

> This does **not** download the videos themselves. Use something like [yt-dlp](https://github.com/yt-dlp/yt-dlp) for that.

---

## Operation

### First-time setup

1. **Install dependencies** into a virtual environment in the repo folder, which is where `Launch.bat` looks for it:
   ```
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. **Create an empty PostgreSQL database, and a role for the bot that owns it.** You don't need to create any tables: the bot builds the schema on its first run from the scripts in [`sql/`](sql/README.md). The role needs these rights on the database:
   - `CONNECT`;
   - `CREATE` on the `public` schema;
   - **`TEMPORARY`**, which `archive_chat.py` needs: `GRANT TEMPORARY ON DATABASE "<db>" TO <role>;`
3. **Set up an S3-compatible bucket store** (the bot is used with Garage). The bot creates one bucket per channel, plus one for user data.
4. **Create a Google Cloud OAuth 2.0 client** for the YouTube Data API and download its secrets JSON.
5. **Fill in a secrets folder.** Keep it outside the repo. It needs:

   | File | Contents |
   |---|---|
   | `client_secret.json` | The OAuth client from step 4. `token.pickle` is created next to it on the first run. |
   | `Settings.json` | `user_data_name` (the name of the bucket and folder for user data, e.g. `"users"`) |
   | `DB_Settings.json` | `DB_USR`, `DB_PASS`, `DB_HOST`, `DB_PORT`, `db_name` |
   | `S3_Settings.json` | `endpoint_url`, `aws_access_key_id`, `aws_secret_access_key`, `region_name` |
   | `cookies.txt` | *Optional.* Only needed for members-only or age-restricted chat (see the note in `modules/Settings.py`). |

6. **Edit the top of [`modules/Settings.py`](modules/Settings.py):**
   - set `QUICK_SETTINGS_DATA` (a local scratch folder) and `QUICK_SETTINGS_SECRET` (the folder from step 5);
   - set `PROCESS_ALL` (every channel) or `CHANNEL_SELECTION` (just one);
   - check the other flags (see [Settings](#settings) below).
7. **Register channels.** Insert rows into `branches`, then add each channel to `channel_directory` with its `messages_<db_suffix>` partition. `modules.Database.AddChannel()` does both in one step. Only channels with `process = true` are downloaded.

### Routine run

Each step is safe to re-run, and each picks up where it left off if it's interrupted.

1. **Download new videos and chat:** `Launch_With_Monitor.bat` runs `Main.py` in one window and a live status view (`Monitor.py`) in another. `Launch.bat` runs `Main.py` alone. For each channel the bot:
   - lists every upload;
   - saves metadata and thumbnails for new videos;
   - downloads chat for every unprocessed video into Postgres, keeping the raw chat as `messages/<video>.json.gz` in the channel's bucket;
   - if the channel finished with no errors, hands it to a background thread that prepares and archives its finished chat (steps 3 and 4 below) while the bot moves on to the next channel. The thread shows as the **Archive** slot in the monitor. Turn this off with `AUTO_ARCHIVE = False`.

   Shorts are marked done without downloading chat. Live and upcoming streams are left for a later run.
2. *(Optional)* **Download auto-captions:** `Launch.bat Subtitles.py`. This can run on its own or at the same time as `Main.py`.

Steps 3 and 4 run automatically for every channel that finishes cleanly. Run them by hand for channels that had errors, to catch up after a run with `AUTO_ARCHIVE` off, or for a channel the bot isn't processing. Both scripts accept `--channel "<name>"` to limit them to one channel, and `--dry-run` to report what they'd do without changing anything.

3. **Prepare finished chat for archiving:** `Launch.bat compress_raw_chat.py --workers 4`. For each processed video it compresses any raw chat still in the old format and corrects badge flags from it. Videos the bot downloaded in one go are already marked done, so this only has real work for older videos. It's safe to run while the bot is running.
4. **Archive finished chat:** `Launch.bat archive_chat.py`. It moves processed videos' chat out of Postgres into Parquet files under `chat_archive/` in each channel's bucket, verifies every file, then empties the moved rows from the database. Don't run it for a channel the bot is processing at that moment. Other channels are fine.

### Querying chat

- **Totals, leaderboards and per-user summaries** live in Postgres. The rollup tables and views (`video_message_stats`, `user_channel_activity`, `user_summary` and so on) work in any SQL client and cover all chat, archived or not.
- **Individual messages** are split between Parquet (archived videos) and Postgres (unprocessed videos). Read them through DuckDB with [`modules/Archive.py`](modules/Archive.py), which sees both:
  ```python
  import modules.Archive as A
  con = A.connect()                                # or A.connect(channels=["Miko"])
  A.Messages(con, "video_id = 'abc123'").show()   # filter applied in Postgres and Parquet
  con.sql("SELECT user_id, count(*) FROM archived_messages GROUP BY 1 ORDER BY 2 DESC LIMIT 10").show()
  ```
  Plain Postgres SQL only sees chat that hasn't been archived yet.

### After a schema change

Regenerate the `sql/` bootstrap scripts and the schema reference:
```
.venv\Scripts\python.exe .claude/skills/chat-database/update_schema.py
```

---

## Where data lives

| Data | Location |
|---|---|
| Video, user, emote and nickname records; rollups and leaderboards | PostgreSQL |
| Chat for videos not yet archived | PostgreSQL `messages` (partitioned by channel) |
| Archived chat (zstd Parquet, ~10M messages per file) | S3: `<channel bucket>/chat_archive/`, listed in the `chat_archive` table |
| Raw chat as downloaded (minified gzip JSON) | S3: `<channel bucket>/messages/<video>.json.gz` |
| Video details, thumbnails and subtitles | S3: `<channel bucket>/details/`, `thumbnails/`, `subtitles/` |
| User details and profile pictures | S3: `<user bucket>/details/`, `pfp/` |

The S3 store holds the only copy of archived chat, so it needs to be in your backups.

The raw JSON keeps fields the database doesn't, such as creator hearts, membership event text, full badge titles and emotes per message, so it's worth keeping for future features.

---

## Settings

The main flags at the top of [`modules/Settings.py`](modules/Settings.py):

| Setting | What it does |
|---|---|
| `PROCESS_ALL` / `CHANNEL_SELECTION` | Process every `process = true` channel, or just the named one |
| `WORKER_COUNT` | Videos downloaded in parallel |
| `REQUEST_DELAY` | Minimum seconds between chat download starts |
| `SKIP_ROLLUPS` | Skip the end-of-run rebuild of leaderboards and `user_summary` |
| `SKIP_SUBTITLE_DOWNLOAD` | Leave captions to `Subtitles.py` instead of the end of `Main.py` |
| `USE_COOKIES` | Use `cookies.txt` for public runs (for age-restricted videos) |
| `AUTO_ARCHIVE` | Compress and archive each channel that finishes with no errors, in the background while the next channel runs |
| `PROGRESS_BARS` | Also draw progress bars in the bot's own console (`Monitor.py` shows the same progress without the line spam) |
| `QUICK_SETTINGS` | `True` uses the folders and flags in the file. `False` prompts with dialogs at launch, including members-only mode, chat timeout and livestream skipping. |

Members-only mode (`GET_MEMBERS_ONLY`) writes to the legacy `*_members` tables, which `archive_chat.py` doesn't archive.

---

## Credits

xenova created the amazing tool that near effortlessly downloads all the messages from a chat. I never made it far enough to figure out how and judging by how infinitely better that program is, I probably would've given up.

https://github.com/xenova/chat-downloader

All the other bits and bobs were written by me and a LOT of heavy lifting by Googling...
