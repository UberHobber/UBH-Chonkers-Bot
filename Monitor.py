"""
Live view of a running bot, read from the status_<script>.json files modules/Status.py writes.

Run it in its own window while Main.py (and/or a standalone Subtitles.py) is running:
    Launch.bat Monitor.py
    Launch.bat Monitor.py --dir D:/some/folder   (if CFG.STATUS_DIRECTORY is set)

Read-only and independent of the bot -- open, close, or resize it whenever; it never affects the
run. A run that has ended (finished, crashed, or killed -- detected by its pid being gone) stays
on screen for --keep-ended minutes, then is hidden until that script starts again. Redraws the whole screen from the top each tick (no relative cursor movement), so a resize
or scroll can't make it spam lines the way tqdm bars do.

Deliberately doesn't import modules.Settings, which connects to things and may prompt on import.
"""
# Native Stuff
import argparse,ctypes,glob,json,os,shutil,sys,time,unicodedata
from datetime import timedelta

STALE_AFTER = 10 # Seconds past the writer's interval before a "running" status counts as not responding

def _pid_alive(pid:int) -> bool:
    """Whether a process is still running. Never signals it (os.kill on Windows would terminate it)."""
    if os.name != "nt":
        try:
            os.kill(pid,0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000,False,pid) # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value == 259 # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)

def _load(path:str) -> dict|None:
    try:
        with open(path,encoding='utf-8') as file:
            return json.load(file)
    except (OSError,ValueError):
        return None # Mid-write -- next tick will catch it

def _ended(status:dict) -> bool:
    """A file whose process is gone: finished/crashed/stopped, or killed while still saying running."""
    return status["state"] != "running" or not _pid_alive(status["pid"])

def _width(text:str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)

def _fit(text:str,cols:int) -> str:
    """Trim to the console width (CJK characters take two columns) so lines never wrap."""
    if _width(text) <= cols:
        return text
    out,used = [],0
    for c in text:
        w = 2 if unicodedata.east_asian_width(c) in "WF" else 1
        if used + w > cols - 1:
            break
        out.append(c)
        used += w
    return "".join(out) + "…"

def _duration(seconds:float) -> str:
    return str(timedelta(seconds=int(seconds)))

def _progress(row:dict) -> str:
    """'Stage: n/total (pct) postfix (rate/s, elapsed)' for a bar row, skipping parts it doesn't have."""
    parts = [row["stage"] or "..."]
    n,total,elapsed = row["n"],row["total"],row["elapsed"]
    if total:
        parts.append(f"{n:,}/{total:,} ({n/total:.0%})")
    elif n:
        parts.append(f"{n:,}")
    if row["postfix"]:
        parts.append(str(row["postfix"]))
    if elapsed is not None:
        rate = f"{n/elapsed:,.1f}/s, " if n and elapsed > 0 else ""
        parts.append(f"({rate}{_duration(elapsed)})")
    return "  ".join(parts)

def _render(status:dict,now:float) -> list[str]:
    age = now - status["updated"]
    state = status["state"].upper()
    if status["state"] == "running":
        if not _pid_alive(status["pid"]):
            state = "TERMINATED" # Killed (window closed, Task Manager) before it could write a final state
        elif age > status["interval"] + STALE_AFTER:
            state = "NOT RESPONDING"
    header = f"=== {status['script']} (pid {status['pid']}) | {state} | up {_duration(status['updated'] - status['started'])} | updated {_duration(age)} ago ==="
    lines = [header]
    if status.get("error"):
        lines.append(f"Exit reason: {status['error']}")

    where = status.get("channel")
    if where and status.get("channel_progress"):
        where = f"{where} ({status['channel_progress']})"
    lines.append(f"Channel: {where or '-'}    Phase: {status.get('phase') or '-'}")
    if status["overall"]:
        lines.append(_progress(status["overall"]))

    lines.append("")
    for worker in status["workers"]:
        lines.append(f" {worker.get('label') or 'W' + str(worker['position'])}  {worker['task'] or 'idle'}")
        if worker["stage"] is not None:
            lines.append(f"     {_progress(worker)}")

    if status["events"]:
        lines.append("")
        lines.append("Recent warnings/errors:")
        lines.extend(f"  {event}" for event in status["events"])
    return lines

def main():
    parser = argparse.ArgumentParser(description="Live view of the bot's status files.")
    parser.add_argument("--dir",default=os.path.dirname(os.path.abspath(__file__)),help="Folder holding status_*.json (CFG.STATUS_DIRECTORY)")
    parser.add_argument("--interval",type=float,default=1.0,help="Seconds between redraws")
    parser.add_argument("--keep-ended",type=float,default=5.0,help="Minutes to keep showing a run after it ends (finished, crashed or killed)")
    args = parser.parse_args()

    os.system("") # Turns on ANSI escape handling in the classic Windows console
    sys.stdout.write("\x1b[?25l\x1b[2J") # Hide cursor, clear once
    try:
        while True:
            now = time.time()
            cols,rows = shutil.get_terminal_size()
            lines = [f"Chonkers Bot Monitor | {time.strftime('%H:%M:%S')} | Ctrl+C to exit",""]
            # Status files outlive their process (each run overwrites its own when it starts), so
            # leftovers from earlier runs are hidden once they've been over for --keep-ended.
            shown = 0
            for path in sorted(glob.glob(os.path.join(args.dir,"status_*.json"))):
                status = _load(path)
                if status is None or (_ended(status) and now - status["updated"] > args.keep_ended * 60):
                    continue
                lines.extend(_render(status,now))
                lines.append("")
                shown += 1
            if not shown:
                lines.append(f"Waiting for the bot to start... (no active status files in {args.dir})")
            # Home, overwrite each line in place (\x1b[K clears its leftover tail), then clear below.
            screen = "\x1b[H" + "".join(_fit(line,cols) + "\x1b[K\n" for line in lines[:rows - 1]) + "\x1b[J"
            sys.stdout.write(screen)
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h\n") # Show cursor again

if __name__ == "__main__":
    main()
