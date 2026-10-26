# Plan: native-language auto-subtitles

Status: **shelved** (2026-10-25). Kept as research for a possible separate native-language subtitles section later.

**Decision:** `subtitles` stays English for every video. Subtitles exist to be searched, and searching in English is what's needed for now. So `CFG.SUBTITLE_LANGUAGES = ["en"]` is kept on purpose. For videos not transcribed in English, that means YouTube's machine translation of its native captions (finding 1 below), and that's accepted. JP and Stars channels can be turned on as they are.

A native-language section, if it's built later, would sit alongside the English rows rather than replace them. The findings and phases below still apply to that.

**Done instead (2026-10-25):** `videos.caption_source_language` records the language YouTube transcribed each video in, read from the English track's URL as it's downloaded (no extra request). That's the per-video language list Phase 0 was meant to produce, collected over time as `Subtitles.py` runs. It doesn't need a separate survey. Videos whose subtitles were fetched before this date stay NULL unless they're re-fetched.

Written 2026-10-25.

## Goal

Store the auto-generated captions YouTube makes from what was actually said in each video, in whatever language YouTube transcribed it in. Today the bot only ever asks for English, which is wrong for most JP videos and for many ID and some EN ones.

Talents switch languages: EN talents sometimes stream partly or entirely in Japanese, ID talents use Indonesian, English, Japanese (and some Korean), and some JP talents now use English too. So before any long run, check which language YouTube transcribed for each video, so we know what we're getting.

## What we found

All of this was tested on 2026-10-25 with yt-dlp 2026.03.17.

### 1. Asking for `en` can return a machine translation

YouTube offers one real auto-caption track per video, plus about 150 machine translations of it. yt-dlp lists the real one as `<lang>-orig`. `Get_Subtitles` requests the language codes in `CFG.SUBTITLE_LANGUAGES` (`["en"]`). If the video wasn't transcribed in English, YouTube quietly returns the translation.

On a Pekora stream:

| Track | Source |
|---|---|
| `ja-orig` | the Japanese captions (`lang=ja`) |
| `ja` | the same captions (`lang=ja`) |
| `en` | **translated from Japanese** (`lang=ja&tlang=en`) |

### 2. Some subtitles already stored as English are translations

We sampled 2 videos each from 10 channels that already have `en` subtitles:

| Channel | Real track(s) in the sample | What the stored `en` is |
|---|---|---|
| Calli, Reine, Zeta | `en-orig` (both videos) | real English captions |
| Anya, Kaela, Moona | `id-orig` + `en-orig` (one each) | 1 of 2 translated from Indonesian |
| Kobo | `id-orig` (both) | both translated from Indonesian |
| Iofi, Ollie | `ko-orig` + `en-orig` (one each) | 1 of 2 translated from Korean |
| hololive (official) | `ja-orig` (both) | both translated from Japanese |

So 9 of the 20 were translations. The two Korean tracks (Iofi `005lxZsjbcU`, an Ao Oni stream; Ollie `00x0EZW6QxU`, a Usada Kensetsu summer festival collab) may be genuine Korean speech or a wrong guess by YouTube. Their titles don't settle it, so they need checking by reading the captions (Phase 0).

### 3. The Data API's language setting doesn't tell us the caption language

`snippet.defaultAudioLanguage` (also in the cached `details/{id}.json`) is a single setting per video, usually left at the creator's default. It didn't match the transcribed track on many of the sampled videos. Calli's two English-transcribed streams say `ja`, which may reflect her Japanese-language streams but not these two. Zeta's and Ollie's say `en-US`, and Iofi's say `id`. It can't describe a stream that's partly in another language, and it isn't updated per stream. **The only way to know a video's caption language is to read its `-orig` track from the watch page**, which takes one request per video and is paced like any other subtitle download.

### 4. One caption language per video

YouTube transcribes each audio track with a single language, which it detects or the creator sets. It does not follow switches between languages within a video. When a talent mixes languages, the less-used one gets transcribed as if it were the main one: English in a Japanese stream comes out as katakana-like approximations, and Japanese in an English stream comes out as garbled English. A video that's mostly one language and partly another therefore gets one track, with the minority language transcribed poorly.

The sample agrees: every video had exactly one `-orig` language. One Moona video had both `en-orig` and `en-US-orig`, but those are the same language.

Because the whole video gets one language, an unexpected language (Korean on an ID channel, Japanese on an EN channel) is either a stream genuinely in that language or YouTube guessing wrong. Only the caption text can tell them apart, so those videos get checked by reading a short excerpt, not filtered out automatically.

### 5. Japanese text in Postgres

- **Trigram search (`ILIKE '%…%'`, `idx_subtitles_text_trgm`) works for Japanese.** The database uses `en_US.utf8`, so `show_trgm('ぺこらです')` gives proper trigrams.
- **Full-text search (`idx_subtitles_text_fts`, the 'simple' configuration) doesn't.** It splits on spaces, so an unspaced Japanese sentence becomes a single "word". Exact word or phrase search won't work for `ja`; use trigram search.
- **Word counts don't apply.** `video_subtitle_stats.total_words` and `words_per_min` count space-separated words, which means nothing for Japanese.

## Plan

### Phase 0: survey (check before committing to a long run)

A **temporary** script, `survey_caption_languages.py`, to be deleted once the survey is finished. For a sample of public streams from every channel in `channel_directory` (not only `process = true` ones), it makes one metadata-only yt-dlp request per video and records:

- the video's `-orig` track(s) and yt-dlp's `language` field
- `defaultAudioLanguage`, for comparison only
- whether the video already has `en` subtitles stored, and whether that `en` track is a translation
- **for videos whose language is unusual for their branch** (anything other than `en` for EN/Stars_EN, `id`/`en`/`ja` for ID, `ja`/`en` for JP/Stars), the first ~20 cues of the native track, so we can read whether it's real speech in that language or YouTube guessing wrong. This costs one extra small request for those videos only.

Results go to a CSV, plus a summary for each channel: the share of videos per language, which videos are unusual (with their excerpts), and the share with no auto-captions at all.

The survey uses the same `AdaptiveRateLimiter` and cookies as `Subtitles.py`. Running it at the same time as the bot is fine.

- **Sample size:** 20 per channel, spread across each channel's history (oldest to newest), is about 2,200 requests. At the current ~22 s pace that's about 13 hours. 10 per channel would halve that.
- **What we learn:** how mixed each channel and branch really is, whether wrong guesses happen at all (and how often), and which channels need their existing subtitles re-fetched.

### Phase 1: native mode in `Get_Subtitles`

1. **Choose the native track.** Call `extract_info(download=False)`, pick the `-orig` key (if there's more than one, take the one matching `info["language"]`, otherwise the first), then download only that track from the same info. That's the same number of requests as today.
2. **Store whatever YouTube transcribed**, labelled with its language. No language filter by default. If Phase 0 shows wrong guesses are common, add a review step for unusual languages; until then, a Korean track on an ID channel is kept, because it may well be real.
3. **Record the language on the video.** A new column, `videos.caption_language`, holds the `-orig` language code, `'none'` when YouTube has no auto-captions, and NULL when not yet checked. It's useful by itself, since it says roughly which language each stream was mainly in, and it lets later re-fetches skip videos already known to have no captions.
4. **Store `subtitles.language`** as the code without `-orig` (`ja`, `id`, `en`, `ko`, …). The table, `video_subtitle_stats` and the S3 `subtitles/{id}.json` layout stay the same.
5. **Statistics:** add `total_chars` and `chars_per_min` to `video_subtitle_stats`, so speaking pace means something for Japanese. Leave `total_words` as it is for languages with spaces.
6. **Settings:** replace `SUBTITLE_LANGUAGES` with a native-only setting. Translated tracks are no longer downloaded.

Schema changes go through a temporary migration script, deleted once it has run, and `update_schema.py` (update the `subtitles`, `videos` and `video_subtitle_stats` NARRATIVE entries). Also update the README and the chat-database skill.

### Phase 2: replace translated subtitles

For each affected video: re-fetch it in native mode, delete its old `en` rows (and stats row) if the native language isn't English, and insert the native rows. `Get_Subtitles` already deletes and reinserts per video and language, and overwrites `subtitles/{id}.json` on S3.

Order, which can be reset per channel with `subtitles_processed = false`:

1. ID channels and the official channels: most likely to have translations (~31k stream hours, about 5 days)
2. EN talents: mostly English, but every Japanese-language stream currently holds a translation (~43k stream hours, about 12 days). Phase 0 shows how many.

### Phase 3: JP channels (and Stars)

These have no subtitles yet. Turn on `Subtitles.py` (`SKIP_SUBTITLE_DOWNLOAD`) once Phase 1 is in place. JP is ~156k stream hours: about 35 GB in Postgres at the current ~0.23 GB per 1,000 hours, and about two weeks of `Subtitles.py` time at the current pace. Japanese streams with English segments get one Japanese track (see finding 4).

## Decisions needed

- [ ] Survey sample size (20 or 10 per channel)
- [ ] Which languages count as "usual" per branch for the survey's excerpt check (draft above)
- [ ] After Phase 0: whether unusual-language tracks need a review step, or are stored as they are
- [ ] After Phase 0: whether EN talents get re-fetched
- [ ] Whether `total_chars`/`chars_per_min` are worth adding, or Japanese stats can do without

## Out of scope: following language switches

YouTube's captions can't follow a talent switching between languages within a video. Doing that would mean downloading the audio and running our own speech recognition on a GPU. That's written up, with estimates, in [local_transcription_plan.md](local_transcription_plan.md): about 7 TB to download and 3–8 months of GPU time for all ~309k stream hours. It's deferred as not viable for now.
