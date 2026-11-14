# Plan: findings from the 2026-11-13 catch-up run

Status: **ready to implement** (written 2026-11-13 from the homelab repo's benchmark session). Nothing in this repo has been changed yet; nothing on the database has been changed either.

The full measurement is in the homelab repo: `c:\code\homelab-network\docs\performance\2026-11-13-pox-storage-db-catchup-run.md`. This file keeps only what matters for the bot.

## Background

On 2026-11-13 the bot ran its first normal catch-up after the backfill:
- **The run:** `Launch_With_Monitor.bat`, 90 channels, video details plus chat, no subtitles. The newest videos were from 2026-11-08.
- **Result:** 4.67 M messages from 75 channels in 3 h 16 min, finishing normally.
- **Monitoring:** Postgres on 102, Pox's NVMe and the bot's own process were sampled the whole time.

**The good news, nothing to do:**
- **Writes are fixed.** The run cost ~3.5 KB of disk writes per message, against ~16 KB on 2026-11-02. Steady state is ~20–25 GB/day, most of it the Proxmox host. Batching, `wal_compression`, the dropped totals index, `fillfactor = 85` and the 2026-11-08 rebuild did the job.
- **HOT held.** `user_channel_activity` stayed at 94–98% HOT all run (395 updates/s). `video_message_stats` was at 98.5%.
- **The 2026-11-08 audience and retention folds** were cheap at this volume. There were no temp files and no forced checkpoints.

**The problem this plan fixes:** the bot re-reads the whole `user_ids` table for every channel. In the run, `user_ids` (5.7 M rows, 708 MB heap, 1.4 GB with indexes) took **360 sequential scans that read 1.05 billion rows**, about 180 full reads. That caused:
- most of Postgres's reads during the run (11 MB/s, in bursts at each channel's start and end) and the NVMe heat that goes with them
- **25.5 GB sent from 102 to the bot**, about half of everything the bot's machine downloaded (the other ~25 GB was YouTube)
- the bot's memory peak (2.6 GB, against ~1.2 GB typical) and its CPU bursts of 3–4.8 cores at channel boundaries

Only ~2,600 users were new in the whole run.

## Where it happens

Both queries are inside `process_channel()` in `Main.py`, which the loop at `for channel_index,channel_name in enumerate(CFG.CHANNELS_TO_PROCESS)` calls once per channel:

1. **`Main.py` ~line 195, the known-user preload:**
   ```python
   known_user_ids:set = set(r["id"] for r in DB.GetEntries(db.cursor,CFG.DB_TABLES["user_ids"],"id"))
   ```
   This does `SELECT id FROM user_ids`, and `GetEntries` `fetchall()`s every row and builds a list of 5.7 M dicts before the set is made. That's the memory spike. It is a full scan, run once per channel.
2. **`Main.py` ~line 469, the unprocessed users:**
   ```python
   unique_users = DB.GetEntries(db.cursor,CFG.DB_TABLES["user_ids"],"id",{"processed":False})
   ```
   No index covers `processed`, so this is another full scan (often a parallel one) per channel. It returns only the few thousand unprocessed rows.

## Changes

### 1. Load `known_user_ids` once per run, not once per channel

The set only grows, so it's safe to keep it for the whole run:
- `Get_Messages()` (`modules/Classes.py`, around "Add Unique UserIDs") adds every newly inserted user to the set under the shared lock.
- The insert uses `ON CONFLICT (id) DO NOTHING`. So if another process (for example `Subtitles.py`, or a second bot run) inserted a user the set doesn't know about, the insert is a harmless no-op. That user would only be counted as "new" in this run's statistics, which is cosmetic.

Suggested shape:
- Build the set once before the channel loop, honouring `CFG.SKIP_CHAT_DOWNLOAD` (empty set in that mode, as today).
- Pass it into `process_channel()`, or keep it at module level next to the other run-wide state, and remove the per-channel load.
- Keep the existing `known_user_ids` log line, once per run.
- The emote IDs and nicknames loaded next to it look per-channel by design (the members-mode tables are per channel), so leave them unless they also show up as costly.

While doing this, load it without building millions of dicts. For example, use a plain cursor:
```python
db.cursor.execute(f"SELECT id FROM {CFG.DB_TABLES['user_ids']}")
known_user_ids = {row[0] for row in db.cursor}
```
Or, better for memory, use a named (server-side) cursor with `itersize = 50000`, so psycopg2 streams the rows instead of holding them all at once. Commit after it as today (see the "same reason as after the preload above" comment) so no transaction stays open.

**Expected effect:** ~90 of the ~180 full reads per run disappear, along with ~12+ GB of LAN traffic per run (most of that 25.5 GB is this query), the per-channel memory spike, and the bursts at channel starts.

### 2. Partial index for the unprocessed users

This needs the user's go-ahead before running it on 102. It's safe with the bot running, and needs no restart:
```sql
CREATE INDEX CONCURRENTLY idx_user_ids_unprocessed ON public.user_ids (id) WHERE NOT processed;
```
- **It stays tiny,** because almost every row has `processed = true`. New users are inserted unprocessed, and `Get_User_Batch` flips them, so the index only holds the backlog of a few thousand rows.
- **Check that Postgres uses it:** `GetEntries` sends `WHERE processed = false`, and Postgres normally simplifies that to `NOT processed`, so the partial index should match. Confirm with:
  ```sql
  EXPLAIN SELECT id FROM user_ids WHERE processed = false;
  ```
  It should show an Index (Only) Scan on `idx_user_ids_unprocessed`, not a Seq Scan or Parallel Seq Scan. If it doesn't match, change the query to `WHERE NOT processed`, or build the index with `WHERE processed = false`.
- **Schema file:** add the index to `sql/user_ids.sql`. If that file is regenerated by the chat-database skill's `update_schema.py` (as `sql/user_channel_activity_stats.sql` was), regenerate it after creating the index live, and keep the explanatory comment in the generator's narrative.

**Expected effect:** the other ~90 full reads per run disappear. Changes 1 and 2 together remove essentially all of the run's sequential reads of `user_ids`.

### 3. Optional: unused `user_ids` indexes

From `pg_stat_user_indexes` on 2026-11-13 (counts since the stats reset in December 2025):

| Index | Scans | Size |
|---|---|---|
| `pk_user_ids_id` | 3.1 billion | 351 MB |
| `idx_user_ids_created` | 2 | 180 MB |
| `idx_user_ids_subscribers` | 3 | 65 MB |
| `idx_user_ids_viewcount` | **0** | 70 MB |

`Get_User_Batch` updates `viewcount` and `subscribers` on every processed user. Because those columns are indexed, those updates can never be HOT (in the run: 2,615 updates, 0% HOT). That's minor at ~2,600 users per run, but the three secondary indexes are ~315 MB that are barely used.
- **First, check** whether anything reads them: Grafana dashboards (`grafana/dashboards/build.py`) and any ad hoc queries.
- **If nothing does:** `DROP INDEX CONCURRENTLY` (with the user's go-ahead), and update `sql/user_ids.sql`.
- **Low priority.** Do it only if it's clearly unused.

### 4. Watch `user_channel_activity` autovacuum (no change yet)

Dead rows reached 390k after this one run, and the last autovacuum was 2026-11-09. HOT stayed at ~96%, so nothing is needed now.
- **If a later run shows HOT under ~85%** (the 2026-11-05 decay to 38% was this), lower the table's vacuum threshold:
  ```sql
  ALTER TABLE user_channel_activity SET (autovacuum_vacuum_scale_factor = 0.01);
  ```
- Record it in `sql/user_channel_activity_stats.sql` next to the `fillfactor = 85` note.

## How to verify

1. **Before deploying the change, note the scan counters** on 102 (read-only):
   ```sql
   select seq_scan, seq_tup_read, idx_scan from pg_stat_user_tables where relname = 'user_ids';
   select indexrelname, idx_scan from pg_stat_user_indexes where relname = 'user_ids';
   ```
2. **Run the bot as usual.** Afterwards:
   - `user_ids` `seq_scan` should have risen by about 1–3 for the whole run (the single preload, and a parallel scan counts once per worker), not ~360
   - `idx_user_ids_unprocessed` `idx_scan` should have risen by about one per channel
3. **Bot memory:** the per-channel spike to ~2.5 GB should be gone. The one-time preload at the start still costs a few hundred MB.
4. **Homelab side, if wanted:** the homelab repo's `scripts/bot-run-bench/` has the snapshot scripts used for this measurement (`snap.sh` before and after, `delta.py` to compare). Ask in the homelab session to repeat the measurement.

## Notes for running the bot elsewhere (for later, not part of this change)

The run also measured what the bot needs, for a possible move off the laptop:
- **Measured:** ~0.4 CPU cores on average (bursts to ~4), ~1.2 GB RAM typical (2.6 GB peak, which change 1 should lower), ~4.5 MB/s in.
- **Enough:** a 2–4 core, 4 GB LXC on Mox would do.
- **What would need porting:**
  - **Cookies:** `modules/BrowserCookies.py` reads YouTube login cookies from a dedicated Firefox profile. It already looks in Linux paths (`~/.mozilla/firefox`), but the profile would have to exist on that machine (logged in once, then left alone).
  - **Launchers:** `Launch.bat` / `Launch_With_Monitor.bat` are Windows-only. A Linux host would need a small shell launcher or a systemd unit.
  - **Settings and paths:** `DB_Settings.json` and the other settings files would need to be copied, and any Windows paths in `Settings.py` checked.
- **Measuring on Windows:** the venv's `.venv\Scripts\python.exe` is only a launcher stub, and the real work happens in its `python3.13.exe` child process. Measure that child.

## Constraints

- **The user commits this repo by hand.** Don't commit or push; list the changed files at the end.
- **Database changes on 102** (`CREATE INDEX`, `DROP INDEX`, `ALTER TABLE`) need the user's explicit go-ahead first. Use `CONCURRENTLY` for indexes so the bot can keep running. Nothing here needs a Postgres restart.
- **Never aggregate the big chat tables live** (`user_channel_activity`, `messages_*`, `video_user_seen`). Heavy one-offs stream the table once into DuckDB on the bot PC, as `build_audience_baseline.py` does.
