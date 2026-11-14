# chat-downloader 0.2.8: YouTube chat fix

[chat-downloader](https://github.com/xenova/chat-downloader) 0.2.8 is its last release. Unpatched, it fails on every YouTube video with `ParsingError: Unable to parse initial video data`.

The fix adds one block to `chat_downloader/sites/youtube.py`, shown in [`youtube.py.diff`](youtube.py.diff). It reads the chat's continuation token from the watch page, loads the `live_chat` (or `live_chat_replay`) page, and takes the Top chat and Live chat continuations from there.

- `youtube.py` is the full patched file.
- `LICENSE` is chat-downloader's MIT license, which covers it.

You don't need to apply it by hand. `Main.py` calls `modules/Patches.py` before it imports chat-downloader, and that module installs this file when it finds the stock 0.2.8 `youtube.py`. The files are told apart by SHA-256, ignoring line endings. To check or apply it yourself after a `pip install`:

```
python -m modules.Patches
```

If chat-downloader is ever upgraded, `Patches.py` skips the file and logs a warning. Check whether the new release still needs the fix before deleting this folder.
