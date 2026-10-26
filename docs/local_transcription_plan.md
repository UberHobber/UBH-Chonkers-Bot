# Plan: transcribe stream audio locally (Whisper)

Status: **deferred**. Not viable at this time because of the time and hardware it needs. Written 2026-10-25 as a record of the estimate, in case it's worth revisiting (for example with a dedicated GPU machine, or for a small set of channels).

The current subtitle approach is YouTube's own native-language captions; see [native_subtitles_plan.md](native_subtitles_plan.md).

## Idea

Download each stream's audio one at a time, transcribe it locally with Whisper (faster-whisper), store the transcript in `subtitles` the same way as YouTube's captions, then delete the audio.

The draw is that YouTube's captions use one language per video. Whisper can detect the language for each segment, so streams where talents switch between Japanese, English and Indonesian would be transcribed in the right language throughout.

## How much work it is

As of 2026-10-25, the public streams of every channel in `channel_directory` come to:

| Branch | Stream hours | Streams | Share with speech* |
|---|---|---|---|
| JP | 157,900 | 57,800 | ~38% (small sample) |
| Stars | 49,600 | 16,400 | — |
| EN | 43,700 | 13,600 | 68% |
| ID | 32,800 | 11,100 | 58% |
| Stars_EN | 25,200 | 7,400 | — |
| **Total** | **~309,000 h** | **~106,000** | |

*Average `video_subtitle_stats.speech_coverage_pct` for streams. A voice-activity filter would skip roughly the rest (music, gameplay, silence), so about 55–65% of the audio would go through the model.

### Data

- **Download:** about 7 TB using YouTube's lowest audio format (249, ~50 kbps opus, ~22 MB/hour). Whisper converts everything to 16 kHz mono, so that quality is enough. The 130 kbps format (251) would be about 18 TB.
- **Local disk:** a few GB at a time, since each file is deleted once transcribed.
- **Postgres:** about 70 GB for all streams, at the measured ~0.23 GB of subtitles per 1,000 stream hours.

### Time

The only suitable GPU available is the RTX PRO 5000 Blackwell laptop GPU (24 GB) in the development laptop. The homelab servers only have Intel integrated graphics.

| Bottleneck | Estimate |
|---|---|
| GPU, faster-whisper `large-v3-turbo`, batched | ~70–100× real time, so **~2.5–4 months** running non-stop |
| GPU, `large-v3` (most accurate) | ~30–50× real time, so **~5–8 months** |
| Downloads | Bandwidth isn't the limit (7 TB at 100 Mbps is about a week). YouTube's anti-bot measures are: 106k downloads at about one a minute is **~2.5 months**, from the same IP as the chat bot, which risks getting the chat downloads throttled too. |

Downloading and transcribing can overlap, so in total it's **roughly 3–8 months**, with a laptop GPU at full load around the clock. The speeds are estimates for this class of GPU, not measurements.

## Would it beat YouTube's captions?

Probably for most of what matters here, but not everywhere. This hasn't been tested on these streams.

**Likely better:**
- **Language switches:** `multilingual=True` detects the language for each ~30-second window, so a stream that switches to Japanese partway through gets that part in Japanese. YouTube uses a single language for the whole video.
- **Names and in-jokes:** you can supply hint words (faster-whisper `hotwords` / `initial_prompt`). The `nicknames` table already has each talent's names.
- **Punctuation and Japanese script:** Whisper adds punctuation and writes Japanese kanji/kana properly. YouTube's captions often have no punctuation.

**Likely worse or no better:**
- **Hallucinations:** during music, silence or singing, Whisper sometimes outputs things nobody said, or repeats itself in a loop. A known Japanese example is "ご視聴ありがとうございました" ("thanks for watching") appearing over background music. Karaoke streams are the worst case. A voice-activity filter and `condition_on_previous_text=False` reduce it but don't stop it.
- **Collabs and game audio:** overlapping voices and in-game speech are hard for both. Neither says who's speaking without a separate speaker-identification step (e.g. pyannote), which costs more GPU time.
- **Mixing within a sentence:** Japanese with English words dropped in is still weak. Per-segment detection only helps with longer stretches.

## If it's revisited

1. **Pilot first:** take ~10 streams, including a Japanese-language Calli stream, a trilingual ID stream and a JP stream with English. Transcribe a 15-minute slice of each with `large-v3-turbo` and `large-v3`, and compare them side by side with YouTube's native track. That measures quality and the real speed on the available GPU in an afternoon.
2. **Hybrid:** keep YouTube's native captions for everything, and transcribe locally only the channels or streams the native-subtitles survey (Phase 0 of [native_subtitles_plan.md](native_subtitles_plan.md)) shows switch language a lot. That cuts months to weeks.
3. **Storage:** either replace the YouTube rows, or store Whisper output under its own `language`/source marker so both can be compared. Decide before writing any rows.
