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
   | `cookies.txt` | *Optional.* Only needed for members-only or age-restricted chat, and only if you don't set `COOKIES_FROM_BROWSER` (see the note in `modules/Settings.py`). |

6. **Edit the top of [`modules/Settings.py`](modules/Settings.py):**
   - set `QUICK_SETTINGS_DATA` (a local scratch folder) and `QUICK_SETTINGS_SECRET` (the folder from step 5);
   - set `PROCESS_ALL` (every channel) or `CHANNEL_SELECTION` (just one);
   - check the other flags (see [Settings](#settings) below).
7. **Register channels.** Insert rows into `branches`, then add each channel to `channel_directory` with its `messages_<db_suffix>` partition. `modules.Database.AddChannel()` does both in one step. Only channels with `process = true` are downloaded.

### Routine run

Each step is safe to re-run, and each picks up where it left off if it's interrupted.

1. **Download new videos and chat:** `Launch_With_Monitor.bat` runs `Main.py` in one window and a live status view (`Monitor.py`) in another. `Launch.bat` runs `Main.py` alone. For each channel the bot:
   - lists every upload;
   - saves metadata (including tags) and thumbnails for new videos;
   - downloads chat for every unprocessed video into Postgres, keeping the raw chat as `messages/<video>.json.gz` in the channel's bucket;
   - if the channel finished with no errors, hands it to a background thread that prepares and archives its finished chat (steps 3 and 4 below) while the bot moves on to the next channel. The thread shows as the **Archive** slot in the monitor. Turn this off with `AUTO_ARCHIVE = False`.

   Shorts are marked done without downloading chat. Live and upcoming streams are left for a later run.
2. *(Optional)* **Download auto-captions:** `Launch.bat Subtitles.py`. This can run on its own or at the same time as `Main.py`. Captions are always English, so they can all be searched in English. For a video not spoken in English, they're YouTube's machine translation, and `videos.caption_source_language` records which language they were translated from (`en` means native English).

Steps 3 and 4 run automatically for every channel that finishes cleanly. Run them by hand for channels that had errors, to catch up after a run with `AUTO_ARCHIVE` off, or for a channel the bot isn't processing. Both scripts accept `--channel "<name>"` to limit them to one channel, and `--dry-run` to report what they'd do without changing anything.

3. **Prepare finished chat for archiving:** `Launch.bat compress_raw_chat.py --workers 4`. For each processed video it compresses any raw chat still in the old format and corrects badge flags from it. Videos the bot downloaded in one go are already marked done, so this only has real work for older videos. It's safe to run while the bot is running.
4. **Archive finished chat:** `Launch.bat archive_chat.py`. It moves processed videos' chat out of Postgres into Parquet files under `chat_archive/` in each channel's bucket, verifies every file, then empties the moved rows from the database. Don't run it for a channel the bot is processing at that moment. Other channels are fine.

### Watching a run

`Monitor.py` is a live status view that runs in its own window. `Launch_With_Monitor.bat` opens it alongside the bot, or start it on its own with `Launch.bat Monitor.py`. It reads the `status_<script>.json` files that `Main.py`, `Subtitles.py`, `compress_raw_chat.py` and `archive_chat.py` write every few seconds. It shows:

- each running script, with its channel and phase;
- what each worker is doing (video, stage, message count and rate), plus the **Archive** slot and how many channels are queued behind it;
- the last few warnings and errors.

Opening, closing or resizing the monitor never affects the bot.

| Status | Meaning |
|---|---|
| **NOT RESPONDING** | The process is still alive but has stopped updating. |
| **TERMINATED** | The process was killed, for example its window was closed. |
| **FINISHED**, **CRASHED** or **STOPPED** | The run ended. CRASHED shows the error; STOPPED means Ctrl+C. |

Ended runs stay on screen for 5 minutes (`--keep-ended <minutes>`), then disappear until that script starts again. If `STATUS_DIRECTORY` is set, pass the same folder with `--dir`.

### Querying chat

- **Totals, leaderboards and per-user summaries** live in Postgres. The rollup tables (`video_message_stats`, `user_channel_activity`, and the pre-generated audience rollups such as `channel_audience` and `channel_overlap`) work in any SQL client and cover all chat, archived or not.
- **Individual messages** are split between Parquet (archived videos) and Postgres (unprocessed videos). Read them through DuckDB with [`modules/Archive.py`](modules/Archive.py), which sees both:
  ```python
  import modules.Archive as A
  con = A.connect()                                # or A.connect(channels=["Miko"])
  A.Messages(con, "video_id = 'abc123'").show()   # filter applied in Postgres and Parquet
  con.sql("SELECT user_id, count(*) FROM archived_messages GROUP BY 1 ORDER BY 2 DESC LIMIT 10").show()
  ```
  Plain Postgres SQL only sees chat that hasn't been archived yet.

### Audience statistics

Users and Community statistics are **pre-generated while the bot runs**:
- audience size, engagement buckets and membership badges
- arrivals and departures per month
- top 100 chatters per channel
- distinct chatters per group/branch, and chatters shared between channels
- **regulars**: chatted in each of 4 consecutive weeks, from the weekly `user_channel_activity.active_weeks` bitmap
- **retention**: cohorts by first month, per channel and distinct per group/branch/company, plus each month's new, returning and back-after-a-break chatters

Each batch of messages records what changed in staging tables. After each channel, the bot folds those changes into the rollup tables (all in `sql/audience_rollups.sql`): `channel_audience`, `channel_audience_monthly`, `channel_audience_yearly`, `channel_top_chatters`, `scope_audience`, `scope_audience_monthly`, `channel_overlap`, `regular_overlap`, `scope_regulars`, `channel_retention` and `scope_retention`. Read those tables. Don't aggregate `user_channel_activity` (~28M rows) directly: that reads the whole table.

Distinct-per-scope retention makes the fold read each changed chatter's other channels, roughly 5–6× the users the Community fold reads. If that turns out too heavy, set `SCOPE_RETENTION = False` in `modules/Database.py`. Per-channel retention keeps working, `scope_retention` goes stale, and a rebuild refreshes it before you turn it back on.

`build_audience_baseline.py` rebuilds them from scratch, or checks them:
```
Launch.bat build_audience_baseline.py --verify                  # full drift check, writes nothing
Launch.bat build_audience_baseline.py --verify --channel Anya   # quick check of one small channel
Launch.bat build_audience_baseline.py                           # full rebuild, with the bot stopped
```
DuckDB and numpy on this PC do the heavy work; the database only streams the table out once (~8 min, mostly the weekly bitmaps). A full run takes about 15 minutes and needs about 1 GB of RAM. Unit tests for the delta logic: `.venv\Scripts\python.exe -m unittest discover -s Tests`.

It's crash-safe: staged changes commit together with their messages, and every run starts by folding whatever a killed run left behind. Until that next start, `--verify` reports drift, and warns that staged rows are waiting. This was tested on 2026-11-08 by hard-killing the bot partway through a stream (21,500 of 71,016 messages stored) and restarting it. The restart folded the leftovers, skipped the stored messages, and a full `--verify` showed 0 drift.

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
| Video details, thumbnails and subtitles | S3: `<channel bucket>/details/`, `thumbnails/`, `subtitles/` (members-only videos: `details_members/`, `thumbnails_members/`) |
| User details and profile pictures | S3: `<user bucket>/details/`, `details_members/`, `pfp/` |

The S3 store holds the only copy of archived chat, so it needs to be in your backups.

The raw JSON keeps fields the database doesn't, such as creator hearts, membership event text, full badge titles and emotes per message, so it's worth keeping for future features.

### How much space it takes

Measured on 2026-10-25, with 1.13B chat messages from 110 channels and 4.6M users. Postgres was 74 GB and S3 was 633 GB.

| Item | Size | Notes |
|---|---|---|
| Raw chat, `messages/*.json.gz` | ~150 B per message | About 85% of the chat bytes on S3 |
| Archived chat, Parquet | ~27 B per message | A full 10M-message file is about 270 MB |
| A user, in the user bucket | ~83 KB | The profile picture is ~80 KB of that, so pictures are about 95% of the bucket |
| A user, in Postgres | ~1.15 KB | Spread over `user_ids`, `user_first_message` and `user_channel_activity` |
| Chat in Postgres before it's archived | ~370 B per message | Freed when the channel is archived |
| `video_user_seen` | ~12 B per message | The largest Postgres table that never shrinks (16 GB at 1.13B messages) |
| Subtitles | ~0.23 GB per 1,000 stream hours | English captions only |

How much chat a channel produces depends mostly on its branch. Finished channels averaged about 14k messages per stream hour for JP, 8.6k for EN, 3.3k for ID, 2.7k for Stars EN and 0.9k for Stars.

With the current channel list, the remaining JP channels have about 120k stream hours to go. That projects to about 1.3B more messages and 1.5M new users. A finished dataset would be about **1 TB on S3** and **70 GB in Postgres** once archived. While a channel is being processed, its chat stays in Postgres until that channel is archived. A large channel can need about 40 GB extra for this.

Most of the S3 space is raw chat (about 360 GB at the end) and profile pictures (about 500 GB). Without them the S3 total would be closer to 200 GB.

To check current bucket sizes on Garage, use `garage bucket info <bucket>`. It's much quicker than listing objects, since the user bucket holds about 9M of them.

---

## Settings

The main flags at the top of [`modules/Settings.py`](modules/Settings.py):

| Setting | What it does |
|---|---|
| `PROCESS_ALL` / `CHANNEL_SELECTION` | Process every `process = true` channel, or just the named one |
| `WORKER_COUNT` | Videos downloaded in parallel |
| `REQUEST_DELAY` | Minimum seconds between chat download starts |
| `SKIP_SUBTITLE_DOWNLOAD` | Leave captions to `Subtitles.py` instead of the end of `Main.py` |
| `USE_COOKIES` | Use cookies for public runs (for age-restricted videos) |
| `COOKIES_FROM_BROWSER` | Read cookies straight from a browser profile at startup, e.g. `("firefox", "ChonkersBot")`, instead of a hand-exported `cookies.txt` (see below) |
| `AUTO_ARCHIVE` | Compress and archive each channel that finishes with no errors, in the background while the next channel runs |
| `PROGRESS_BARS` | Also draw progress bars in the bot's own console (`Monitor.py` shows the same progress without the line spam) |
| `STATUS_INTERVAL` / `STATUS_DIRECTORY` | How often the status files for `Monitor.py` are written, and where (blank = the launch folder) |
| `LOG_VERBOSE` | Also log debug lines, including the per-file and per-50-video archive progress |
| `GET_MEMBERS_CHAT` | Run a members-only chat pass instead of the normal run (see below) |
| `QUICK_SETTINGS` | `True` uses the folders and flags in the file. `False` prompts with dialogs at launch, including the members chat pass, chat timeout and livestream skipping. |

Every normal run reads both the uploads and the members-only playlists. It stores members-only video details and thumbnails (`videos.members = true`) but not their chat. Those videos stay unprocessed, and are skipped on later normal runs once their details are final.

Members-only chat comes from a separate pass with `GET_MEMBERS_CHAT = True` and signed-in YouTube cookies. It covers only channels with `channel_directory.members_chat = true` (ones your cookies hold a membership for), and only their members-only videos. Chat goes to the per-channel `*_<db_suffix>_members` tables, which are created the first time the pass reaches a channel. `archive_chat.py` doesn't archive them.

To skip exporting `cookies.txt` by hand, set `COOKIES_FROM_BROWSER`. The bot then reads the YouTube cookies from that browser profile each time it starts, keeps them in a temporary file, and deletes the file when it exits. It stops with an error if the profile isn't signed in. Setup:

1. Create a Firefox profile just for the bot: open `about:profiles` and choose **Create a New Profile**.
2. Launch it, sign into YouTube with the account that holds the memberships, then close it.
3. Set `COOKIES_FROM_BROWSER = ("firefox", "<profile name>")`.

Don't browse with that profile. YouTube rotates the cookies of a browser that's actively using them, and that's what used to break exported files. If the login ever expires, sign in to that profile again. Chrome and Edge can't be used on Windows, because their cookie encryption can only be decrypted by the browser itself.

---

## Credits

xenova created the amazing tool that near effortlessly downloads all the messages from a chat. I never made it far enough to figure out how and judging by how infinitely better that program is, I probably would've given up.

https://github.com/xenova/chat-downloader

All the other bits and bobs were written by me and a LOT of heavy lifting by Googling...
