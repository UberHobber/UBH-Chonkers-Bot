"""
Optional alerts for unattended runs, posted to a Discord webhook.

The webhook URL is the "alert_webhook" key in the secrets folder's Settings.json (CFG.ALERT_WEBHOOK).
Without one, nothing here does anything, so the bot runs the same for anyone who doesn't set it up.

start() hooks a handler onto the bot's logger. Every line at CFG.ALERT_LEVEL or above (ERROR by
default) is gathered for CFG.ALERT_BATCH_SECONDS and posted as one message, so a burst of per-video
errors becomes one post instead of hundreds. A CRITICAL line (an uncaught crash, see logconfig.py)
is posted straight away. Whatever is still gathered when the process exits is posted by
logging.shutdown(), which flushes every handler.

summary() posts a run's closing summary (CFG.ALERT_RUN_SUMMARY).

Only entry-point scripts (Main.py, a standalone Subtitles.py) should call start(), like
Status.start(), so a utility script run by hand doesn't post alerts.

A process that's killed outright (out of memory, SIGKILL) can't post anything. On a server the
systemd unit's OnFailure= hook covers that (see deploy/).
"""
# Import of main package files
import logging,socket,sys,threading

# Import specific items from a package
import requests

# Import custom Python stuff
import modules.Settings as CFG

MESSAGE_LIMIT = 1900 # Discord allows 2000 characters per message, with some room for the header
LINE_LIMIT = 800 # A longer line (a traceback) keeps its start and its end

_handler:'WebhookHandler|None' = None

def send(text:str) -> bool:
    """Posts one message to the webhook. Never raises, and never logs through the bot's logger (that could loop)."""
    if not CFG.ALERT_WEBHOOK:
        return False
    try:
        response = requests.post(CFG.ALERT_WEBHOOK,json={"content":text[:2000]},timeout=15)
        response.raise_for_status()
        return True
    except Exception as e:
        sys.stderr.write(f"Alert webhook post failed: {e}\n")
        return False

def _trim(line:str) -> str:
    line = line.replace("```","'''") # Would end the code block early
    if len(line) <= LINE_LIMIT:
        return line
    return f"{line[:300]}\n...\n{line[-(LINE_LIMIT - 305):]}"

class _Formatter(logging.Formatter):
    """One timestamped line per record, without the blank lines messages often start with for the console."""
    def formatMessage(self,record:logging.LogRecord) -> str:
        message = record.message
        record.message = message.strip()
        try:
            return super().formatMessage(record)
        finally:
            record.message = message

class WebhookHandler(logging.Handler):
    """Gathers log lines and posts them in batches (see module docstring)."""
    def __init__(self,title:str,level:int,batch_seconds:float):
        super().__init__(level)
        self.title = title
        self.batch_seconds = batch_seconds
        self.count = 0 # Lines this run that reached the alert level
        self._lines:list[str] = []
        self._lines_lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = threading.Event()
        self.setFormatter(_Formatter('%(asctime)s %(levelname)s [%(threadName)s] %(message)s','%H:%M:%S'))
        threading.Thread(target=self._loop,name="alert-poster",daemon=True).start()

    def emit(self,record:logging.LogRecord):
        try:
            line = self.format(record)
        except Exception:
            self.handleError(record)
            return
        with self._lines_lock:
            self.count += 1
            self._lines.append(line)
        if record.levelno >= logging.CRITICAL:
            self._wake.set()

    def _loop(self):
        while not self._closed.is_set():
            self._wake.wait(self.batch_seconds)
            self._wake.clear()
            self.flush()

    def flush(self):
        with self._lines_lock:
            lines,self._lines = self._lines,[]
        if not lines:
            return
        header = f"**{self.title}** on `{socket.gethostname()}`: {len(lines):,} alert(s)\n```\n"
        body,shown = "",0
        for line in lines:
            line = _trim(line)
            if len(header) + len(body) + len(line) + 60 > MESSAGE_LIMIT:
                break
            body += line + "\n"
            shown += 1
        if shown < len(lines):
            body += f"... and {len(lines) - shown:,} more (see the log file)\n"
        send(f"{header}{body}```")

    def close(self):
        self._closed.set()
        self.flush()
        super().close()

def start(name:str,logger:logging.Logger):
    """Begin posting this script's alert-level log lines to the webhook. Does nothing without one."""
    global _handler
    if not CFG.ALERT_WEBHOOK or _handler is not None:
        return
    _handler = WebhookHandler(f"Chonkers Bot ({name})",logging.getLevelName(CFG.ALERT_LEVEL),CFG.ALERT_BATCH_SECONDS)
    logger.addHandler(_handler)

def alert_count() -> int:
    """How many lines reached the alert level since start() (0 without a webhook)."""
    return _handler.count if _handler is not None else 0

def summary(text:str,problems:bool):
    """Posts a run's closing summary, after any alert lines still waiting (CFG.ALERT_RUN_SUMMARY)."""
    if _handler is None or not CFG.ALERT_RUN_SUMMARY:
        return
    _handler.flush()
    send(f"{'⚠️' if problems else '✅'} **{_handler.title}** on `{socket.gethostname()}`\n{text}")
