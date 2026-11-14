"""
Pre-made file for initializing logging functionality.
"""
# Import of main package files
import logging,time,atexit,datetime,os,signal,sys,threading
from logging.handlers import TimedRotatingFileHandler

# Import specific items from a package
from typing import List
from contextlib import contextmanager
from tqdm import tqdm

# Import custom Python stuff
import modules.Settings as CFG


class ConsoleFormatter(logging.Formatter):
    '''Formats the console outputs based on what type of log object they are'''
    grey_light = "\x1b[28;30m"
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    # format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)"
    crit_fmt = 'CRITICAL: %(message)s (%(filename)s:%(lineno)d)'
    err_fmt = 'ERROR: %(message)s (%(filename)s:%(lineno)d)'
    warn_fmt = 'WARN: %(message)s (%(filename)s:%(lineno)d)'
    dbg_fmt = 'DEBUG: %(message)s (%(filename)s:%(lineno)d)'
    info_fmt = '%(message)s'

    FORMATS = {
        logging.DEBUG: grey_light + dbg_fmt + reset,
        logging.INFO: grey + info_fmt + reset,
        logging.WARNING: yellow + warn_fmt + reset,
        logging.ERROR: red + err_fmt + reset,
        logging.CRITICAL: bold_red + crit_fmt + reset
    }
    PLAIN_FORMATS = {
        logging.DEBUG: dbg_fmt,
        logging.INFO: info_fmt,
        logging.WARNING: warn_fmt,
        logging.ERROR: err_fmt,
        logging.CRITICAL: crit_fmt
    }
    # No colour codes when the console is redirected (e.g. into systemd's journal), where they'd show as junk
    COLOR = sys.stderr is not None and sys.stderr.isatty()

    def format(self, record):
        log_fmt = (self.FORMATS if self.COLOR else self.PLAIN_FORMATS).get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

class LogFormatter(logging.Formatter):
    '''
    Log file lines: date, time, level and thread on every line, so an unattended run's log can be
    read back later. Blank lines a message starts with (spacers for the console) go before the
    prefix instead of after it.
    '''
    def __init__(self):
        super().__init__('%(asctime)s %(levelname)-8s [%(threadName)s] %(message)s','%Y-%m-%d %H:%M:%S')

    def formatMessage(self, record):
        message = record.message
        stripped = message.lstrip('\n')
        record.message = stripped
        try:
            return message[:len(message) - len(stripped)] + super().formatMessage(record)
        finally:
            record.message = message

class Summary:
    """
    Object to store summary information to place all at once in a log file later.
    """
    def __init__(self):
        self.lines: List[str] = []
        self.lines.append('') # Append blank line to create a spacer from the data above

    def add(self,line: str):
        """
        Add an entry to the Summary Object

        :param line: Given line to store
        :type line: String
        """
        self.lines.append(line)

class TQDMHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            tqdm.write(msg)
        except Exception:
            self.handleError(record)

logger = logging.getLogger('Larry')
logger.setLevel(logging.DEBUG)

def TimeCurrent():
    """Get the current time at moment of function call

    :return: Hours:Minutes:Seconds
    :rtype: String
    """
    return f'{time.strftime('%H:%M:%S')}'

def TimeDurration():
    """Gets the start time of the program and calculates how long it's been at moment of function call.

    :return: Time elapsed since start of program, in seconds.
    :rtype: String"""
    time_current = time.time()
    time_duration = time_current-time_start
    time_duration = int(round(time_duration,0))
    time_duration = str(datetime.timedelta(seconds=time_duration))
    return time_duration

def FullTimestamp():
    """Formatted combination of both Current Time and Time Elapsed.

    :return: {Hours:Minutes:Seconds}, {Seconds} elapsed
    :rtype: String"""
    return f'{TimeCurrent()}, {TimeDurration()} elapsed'

def ProgramComplete():
    """
    Executes at the end of the program, on exit.
    """
    logger.info(f'\n*--------------------*\nPROGRAM COMPLETE AT {TimeCurrent()}\nEXECUTION DURATION: {TimeDurration()}\n*--------------------*\n')

def StreamLog():
    """
    The Log Handler for the console output. Control log level with LOG_VERBOSE
    """
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO) if CFG.LOG_VERBOSE == False else ch.setLevel(logging.DEBUG)
    ch.setFormatter(ConsoleFormatter())
    logger.addHandler(ch)

def FileLog(single_filename='log',log_filename='log'):
    """
    The Log Handler for the file output, in CFG.LOG_DIRECTORY. Control log level with LOG_VERBOSE.
    CONTINUOUS_LOG writes one file that starts afresh each midnight, keeping CFG.LOG_RETENTION_DAYS
    old ones (named <single_filename>.log.YYYY-MM-DD).

    :param single_filename: Name of the debug log file
    :type single_filename: String
    :param log_filename: Prefix of the main log file
    :type log_filename: String
    """
    directory = CFG.LOG_DIRECTORY or os.getcwd()
    os.makedirs(directory,exist_ok=True)
    if CFG.CONTINUOUS_LOG == False:
        fh = logging.FileHandler(os.path.join(directory,f'{log_filename} ({time.strftime('%Y.%m.%d-%H.%M.%S')}).log'),encoding='utf-8')
    else:
        fh = TimedRotatingFileHandler(os.path.join(directory,f'{single_filename}.log'),when='midnight',backupCount=CFG.LOG_RETENTION_DAYS,encoding='utf-8')
    fh.setLevel(logging.INFO) if CFG.LOG_VERBOSE == False else fh.setLevel(logging.DEBUG)
    fh.setFormatter(LogFormatter())
    logger.addHandler(fh)

@contextmanager
def TQDM_Logging():
    original_handlers = logger.handlers.copy()

    stream_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
    for handler in stream_handlers:
        logger.removeHandler(handler)

    tqdm_handler = TQDMHandler()
    tqdm_handler.setLevel(logging.INFO) if CFG.LOG_VERBOSE == False else tqdm_handler.setLevel(logging.DEBUG)
    if stream_handlers:
        tqdm_handler.setFormatter(stream_handlers[0].formatter)

    logger.addHandler(tqdm_handler)

    try:
        yield logger
    finally:
        # Restore original handlers
        logger.removeHandler(tqdm_handler)
        for handler in stream_handlers:
            logger.addHandler(handler)

StreamLog()

# chat-downloader adds a StreamHandler to both 'chat_downloader.metadata' and
# 'urllib3' at import time (debugging.py loop-variable bug means log() calls
# actually go through urllib3). Replace both with TQDMHandler so their output
# goes through tqdm.write() and doesn't break progress bars.
for _cd_name in ('chat_downloader.metadata', 'urllib3'):
    _cd_logger = logging.getLogger(_cd_name)
    for _h in [h for h in list(_cd_logger.handlers) if isinstance(h, logging.StreamHandler)]:
        _cd_logger.removeHandler(_h)
    _cd_logger.setLevel(logging.ERROR)

if CFG.LOG == True:
    FileLog(CFG.DEBUG_LOG_FILE,CFG.LOG_NAME)

def _log_uncaught(exc_type,exc,tb):
    """Uncaught exceptions go to the log (and so the log file and any alert webhook), not just stderr."""
    if issubclass(exc_type,KeyboardInterrupt):
        logger.warning('Stopped before finishing (interrupted). Anything unfinished is picked up by the next run.')
        return
    logger.critical(f'Uncaught {exc_type.__name__}, stopping: {exc}',exc_info=(exc_type,exc,tb))

def _log_uncaught_thread(args):
    if issubclass(args.exc_type,SystemExit):
        return
    thread_name = args.thread.name if args.thread is not None else '?'
    logger.critical(f'Uncaught {args.exc_type.__name__} in thread {thread_name}: {args.exc_value}',exc_info=(args.exc_type,args.exc_value,args.exc_traceback))

sys.excepthook = _log_uncaught
threading.excepthook = _log_uncaught_thread

def _on_stop_signal(signum,frame):
    """
    systemctl stop (or kill) sends SIGTERM, which would otherwise end Python without logging anything
    or running its exit handlers. Treat it like Ctrl+C instead: the run stops the same way, and the
    log and status file say so.
    """
    logger.warning(f'Received {signal.Signals(signum).name}, stopping. Videos in progress are left unprocessed for the next run.')
    raise KeyboardInterrupt

if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGTERM,_on_stop_signal)

time_start = time.time()
time_epoch = int(round(time_start,0))

logger.info(f'CURRENT TIME OF EXECUTION:\n{TimeCurrent()}\n----------------------')

# Will run TimeDuration function when the program finishes
atexit.register(ProgramComplete)