"""
Fixes for third-party packages that have no fixed release, kept in patches/ and applied to the
installed copy.

chat-downloader 0.2.8 (the last release) fails on every video with "ParsingError: Unable to parse
initial video data", because YouTube changed how a watch page links to its chat. The patched
sites/youtube.py in patches/chat_downloader-0.2.8/ (see youtube.py.diff there) reads the chat
continuations from the live_chat / live_chat_replay page instead.

ensure_chat_downloader() compares the installed file with the stock 0.2.8 file and the patched one
(by hash, ignoring line endings), and swaps in the patched file if the stock one is installed. So a
fresh venv fixes itself on the first run. It must run BEFORE chat_downloader is imported, since an
already-imported module keeps its old code. Standard library only, and it doesn't log: it returns
(level, message) pairs for the caller to log once logging/alerts are set up.

By hand (e.g. after `pip install -r requirements.txt`): python -m modules.Patches
"""
# Native Stuff
import hashlib,importlib.metadata,importlib.util,logging,os,sys

PATCH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"patches","chat_downloader-0.2.8")
CHAT_DOWNLOADER_VERSION = "0.2.8"
STOCK_SHA256 = "1306d707cb14874956024d34548f8ffc7cf2e86aacbbe6c6e82a1555c6035a4d"
PATCHED_SHA256 = "1019b0fda88b7c0c9967a80a6310fcd3988442f9bca67da2a25132ce6f03a275"

def _sha256(path:str) -> str:
    """Hash with line endings normalized, so a CRLF copy matches its LF original."""
    with open(path,"rb") as file:
        return hashlib.sha256(file.read().replace(b"\r\n",b"\n")).hexdigest()

def ensure_chat_downloader() -> list[tuple[int,str]]:
    """
    Make sure the installed chat_downloader has the patched sites/youtube.py. Returns what it found
    or did as (logging level, message) pairs. Raises if the stock file is installed but can't be replaced.
    """
    try:
        version = importlib.metadata.version("chat-downloader")
    except importlib.metadata.PackageNotFoundError:
        return [(logging.ERROR,"chat-downloader isn't installed (pip install -r requirements.txt).")]
    if version != CHAT_DOWNLOADER_VERSION:
        return [(logging.WARNING,f"chat-downloader is {version}, not {CHAT_DOWNLOADER_VERSION}: its YouTube fix in patches/ wasn't checked or applied.")]

    spec = importlib.util.find_spec("chat_downloader") # Locates the package without importing it
    if spec is None or not spec.submodule_search_locations:
        return [(logging.ERROR,"chat-downloader is installed but its files couldn't be found.")]
    target = os.path.join(list(spec.submodule_search_locations)[0],"sites","youtube.py")
    patched = os.path.join(PATCH_DIR,"youtube.py")

    current = _sha256(target)
    if current == PATCHED_SHA256:
        return []
    if current != STOCK_SHA256:
        return [(logging.ERROR,f"chat-downloader's sites/youtube.py matches neither the stock {CHAT_DOWNLOADER_VERSION} file nor the patched one, "
                               f"so the fix in patches/ wasn't applied. Chat downloads may fail with 'Unable to parse initial video data'. ({target})")]
    if _sha256(patched) != PATCHED_SHA256:
        raise RuntimeError(f"{patched} doesn't match its expected hash; refusing to install it.")

    with open(patched,"rb") as file:
        content = file.read()
    temp_path = f"{target}.tmp"
    with open(temp_path,"wb") as file:
        file.write(content)
    os.replace(temp_path,target)
    return [(logging.WARNING,f"Applied the chat-downloader {CHAT_DOWNLOADER_VERSION} YouTube fix from patches/ to {target}.")]

if __name__ == "__main__":
    results = ensure_chat_downloader()
    for level,message in results or [(logging.INFO,"chat-downloader already has the patch.")]:
        print(f"{logging.getLevelName(level)}: {message}")
    sys.exit(1 if any(level >= logging.ERROR for level,_ in results) else 0)
