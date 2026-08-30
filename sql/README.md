# Database bootstrap scripts

Each `.sql` file here is a runnable DDL script for one cluster of tables (plus any
materialized views/plain views/functions that belong to it). They're generated directly from the live
`YTDB_Public` schema by [`.claude/skills/chat-database/update_schema.py`](../.claude/skills/chat-database/update_schema.py)
— **do not hand-edit these files**; edit the `NARRATIVE` dict in that script instead (for
rationale comments) or the schema itself (for structural changes), then re-run it:

```
python .claude/skills/chat-database/update_schema.py
```

That same run also regenerates [`SKILL.MD`](../.claude/skills/chat-database/SKILL.MD) from
the same live introspection, so the two can never drift apart.

`modules.Database.EnsureSchema` (called once at startup, see `Main.py`) runs these files
automatically against whatever database it connects to — it checks each file's "anchor"
table/matview/view and only runs a file if that's missing, so this is safe to leave in place
permanently rather than being a one-time setup step. The `*_members` files are only run when
`CFG.GET_MEMBERS_ONLY` is `True`, so a public-only deployment never gets members-only tables
it doesn't need.

## Run order

Later files have foreign keys pointing at tables created by earlier ones:

1. `channel_directory.sql`
2. `user_ids.sql`
3. `videos.sql`
4. `tags.sql`
5. `emotes.sql`
6. `messages.sql` — must come before `nicknames.sql`/`nicknames_members.sql`/`messages_members.sql`
7. `nicknames.sql`
8. `subtitles.sql`
9. `video_subtitle_stats.sql` — its `record_subtitle_stats()` FKs `video_subtitle_stats` to
   `videos` (step 3) and `channel_directory` (step 1); doesn't touch `subtitles` itself, but
   fits here topically since it's `subtitles`' rollup table.
10. `user_channel_activity_stats.sql` — must come before `video_message_stats.sql`:
    `record_message_stats()`/`record_message_stats_batch()` (defined in the latter) write to
    `user_first_message`/`user_channel_activity` (defined in the former, renamed from
    `user_first_channel_message` since it now holds far more than a first-message pointer).
11. `video_message_stats.sql`
12. `user_channel_superchats.sql` — per-user, per-channel, per-currency superchat totals,
    also written to by `record_message_stats()` (defined in step 11).
13. `user_global_message_rankings.sql` — its materialized views select from
    `user_channel_activity`, created in step 10.
14. `user_summary.sql` — a materialized view combining `user_ids` (step 2), `user_first_message`
    and `user_channel_activity` (both step 10), `user_channel_superchats` (step 12), and
    `user_global_message_rankings` (step 13) into one row per user. Refreshed once per bot run,
    after `user_global_message_rankings` (see `refresh_user_summary()`, defined here).

**Members-only files** (only needed/run if you're tracking members-only content — see below):

15. `emotes_members.sql`
16. `messages_members.sql` — must come before `nicknames_members.sql`: its
    `nickname_matches_<talent>_members` tables FK to `messages_<talent>_members(message_id)`.
17. `nicknames_members.sql`
18. `subtitles_members.sql`

After creating the `channel_directory` row(s) for whichever channels you're tracking, also
create their `messages_<db_suffix>` partition of `public.messages` — see
`modules.Database.AddChannel`/`EnsureMessagesPartition`, or add it manually the same way the
existing per-talent partitions in `messages.sql` are declared.

The `*_<talent>_members` tables (`emotes`, `messages`, `nickname_matches`, `subtitles`) are
hardcoded per-talent (one table per name, e.g. `subtitles_kiara_members`) rather than created
dynamically per channel — see the note at the top of `channel_directory.sql` for why these
predate the unified `channel_id` schema. Adding members-only support for a talent that isn't
already one of the five in these files means adding its table to the relevant `_members.sql`
file (and to `FILE_GROUPS` in `update_schema.py`) by hand.
