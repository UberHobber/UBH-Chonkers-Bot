"""
Live run status, written to a small JSON file that Monitor.py redraws in its own console window.

Multi-position tqdm bars are fragile on Windows consoles -- a window narrower than the bars, a
resize, or a scroll throws off the cursor-up math and every refresh lands on a new line. Every
progress bar in the bot is a Bar (a tqdm subclass) that registers itself here, so a background
thread can snapshot what each worker slot is doing every CFG.STATUS_INTERVAL seconds without the
bars' own update paths doing any extra work. With CFG.PROGRESS_BARS off the bars still count (so
the snapshot is accurate) but draw to os.devnull instead of the console, leaving the bot's
console as a plain log.

Nothing is written until start() is called -- only entry-point scripts (Main.py, standalone
Subtitles.py) should call it, so utility scripts that import Classes don't clobber a running
bot's status file. Each script writes its own status_<name>.json so Main.py and a standalone
subtitle pass can run side by side.
"""
# Import of main package files
import atexit,itertools,json,logging,os,sys,threading,time
from collections import deque
from typing import Any

# Import specific items from a package
from tqdm import tqdm

# Import custom Python stuff
import modules.Settings as CFG

EVENT_COUNT = 12 # Recent WARNING+ log lines kept in the status file

_lock = threading.Lock()
_open_order = itertools.count()
_bars:dict[int,'Bar'] = {} # id(bar) -> open bar
_tasks:dict[int,str] = {} # bar position -> what that worker slot is working on
_labels:dict[int,str] = {} # bar position -> slot name shown instead of "W<n>" (e.g. "Archive")
_info:dict[str,Any] = {}
_events:deque[str] = deque(maxlen=EVENT_COUNT)
_devnull = None
_path:str|None = None
_write_lock = threading.Lock()
_stop = threading.Event()

class Bar(tqdm):
    """
    Drop-in tqdm replacement that the status writer can see. Only reads attributes tqdm already
    maintains (n, total, desc, postfix, start_t) at snapshot time, so per-update cost is unchanged.
    """
    def __init__(self,*args,**kwargs):
        global _devnull
        self._status_position:int = kwargs.get("position") or 0
        self._status_order = next(_open_order)
        if not CFG.PROGRESS_BARS:
            if _devnull is None:
                _devnull = open(os.devnull,'w',encoding='utf-8')
            # Not disable=True -- a disabled tqdm stops counting, which would freeze the snapshot.
            kwargs["file"] = _devnull
        super().__init__(*args,**kwargs)
        with _lock:
            _bars[id(self)] = self

    def display(self,msg=None,pos=None):
        if CFG.PROGRESS_BARS:
            return super().display(msg,pos)
        return True # Skip formatting a line nobody will see

    def close(self):
        with _lock:
            _bars.pop(id(self),None)
        super().close()

def set_task(position:int,text:str|None,label:str|None=None):
    """
    Label what a worker slot is working on (e.g. the video ID/title). None clears it.
    `label` names the slot itself in the monitor (default "W<position>") and sticks until replaced.
    """
    with _lock:
        if label is not None:
            _labels[position] = label
        if text is None:
            _tasks.pop(position,None)
        else:
            _tasks[position] = text

def set_info(**fields):
    """Run-level fields shown in the monitor header (channel, phase, etc.)."""
    with _lock:
        _info.update(fields)

class _EventHandler(logging.Handler):
    """Keeps the last few warnings/errors so the monitor can show them without the log file."""
    def emit(self, record):
        try:
            _events.append(f"{time.strftime('%H:%M:%S',time.localtime(record.created))} {record.levelname}: {record.getMessage()}")
        except Exception:
            self.handleError(record)

def _row(bar:Bar|None,task:str|None,now:float) -> dict[str,Any]:
    if bar is None:
        return {"task":task,"stage":None,"n":None,"total":None,"postfix":None,"elapsed":None}
    return {
        "task":task,
        "stage":(bar.desc or "").rstrip(": "),
        "n":bar.n,
        "total":bar.total,
        "postfix":bar.postfix or None,
        "elapsed":round(now - bar.start_t,1)
    }

def _snapshot(state:str) -> dict[str,Any]:
    now = time.time()
    with _lock:
        bars = list(_bars.values())
        tasks = dict(_tasks)
        labels = dict(_labels)
        info = dict(_info)
        events = list(_events)

    # Several bars can share a position (e.g. a stage bar nested inside the messages bar) --
    # the most recently opened one is what that slot is currently doing.
    current:dict[int,Bar] = {}
    for bar in bars:
        p = bar._status_position
        if p not in current or bar._status_order > current[p]._status_order:
            current[p] = bar

    workers = [{"position":p,"label":labels.get(p),**_row(current.get(p),tasks.get(p),now)} for p in sorted((set(current) | set(tasks)) - {0})]
    return {
        **info,
        "state":state,
        "updated":now,
        "interval":CFG.STATUS_INTERVAL,
        "overall":_row(current.get(0),tasks.get(0),now) if 0 in current else None,
        "workers":workers,
        "events":events
    }

def _write(state:str):
    if _path is None:
        return
    with _write_lock:
        temp_path = f"{_path}.tmp"
        try:
            with open(temp_path,'w',encoding='utf-8') as file:
                json.dump(_snapshot(state),file,ensure_ascii=False)
            os.replace(temp_path,_path)
        except OSError:
            pass # Monitor had the file open mid-replace (Windows) -- next tick will catch up

def _loop():
    while not _stop.wait(CFG.STATUS_INTERVAL):
        _write("running")

def _on_exit():
    _stop.set()
    error = _info.get("error")
    _write("finished" if error is None else ("stopped" if error == "KeyboardInterrupt" else "crashed"))

def start(name:str,logger:logging.Logger):
    """Begin writing status_<name>.json every CFG.STATUS_INTERVAL seconds until the process exits."""
    global _path
    directory = CFG.STATUS_DIRECTORY or os.getcwd()
    os.makedirs(directory,exist_ok=True)
    _path = os.path.join(directory,f"status_{name}.json")
    set_info(script=name,pid=os.getpid(),started=time.time(),error=None)

    handler = _EventHandler()
    handler.setLevel(logging.WARNING)
    logger.addHandler(handler)

    # Record an uncaught exception so the final write says crashed/stopped rather than finished.
    original_hook = sys.excepthook
    def _hook(exc_type,exc,tb):
        set_info(error="KeyboardInterrupt" if issubclass(exc_type,KeyboardInterrupt) else f"{exc_type.__name__}: {exc}")
        original_hook(exc_type,exc,tb)
    sys.excepthook = _hook

    atexit.register(_on_exit)
    _write("running")
    threading.Thread(target=_loop,name="status-writer",daemon=True).start()
