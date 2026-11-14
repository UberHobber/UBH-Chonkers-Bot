"""
Posts a failed systemd unit's state and last log lines to the bot's alert webhook.

Run by chonkers-bot-alert@.service (OnFailure= in chonkers-bot.service) for failures the bot can't
report itself: an out-of-memory kill, a SIGKILL after a stop timeout, or a crash before logging
was set up. Standard library only, so it runs on the system Python without the bot's venv.

The webhook is the same "alert_webhook" key the bot uses, in $CHONKERS_SECRETS_DIR/Settings.json.

Usage: python3 notify_failure.py <unit name>
"""
import json,os,socket,subprocess,sys,urllib.request

LOG_LINES = 25

def main():
    unit = sys.argv[1] if len(sys.argv) > 1 else "chonkers-bot.service"
    secrets = os.environ.get("CHONKERS_SECRETS_DIR","")
    try:
        with open(os.path.join(secrets,"Settings.json"),encoding="utf-8") as file:
            webhook = json.load(file).get("alert_webhook") or ""
    except (OSError,ValueError) as e:
        sys.exit(f"Can't read alert_webhook from {secrets}/Settings.json: {e}")
    if not webhook:
        return

    def run(*args:str) -> str:
        try:
            return subprocess.run(args,capture_output=True,text=True,timeout=30).stdout.strip()
        except Exception as e:
            return f"({' '.join(args[:2])} failed: {e})"

    state = run("systemctl","show",unit,"--property=Result,ExecMainCode,ExecMainStatus","--no-pager").replace("\n",", ")
    log = run("journalctl","-u",unit,"-n",str(LOG_LINES),"--no-pager","-o","short-iso").replace("```","'''")
    header = f"🚨 **{unit} failed** on `{socket.gethostname()}` ({state})\n```\n"
    log = log[-(1990 - len(header) - 4):] # Discord's 2000-character limit; the end of the log matters most
    body = json.dumps({"content":f"{header}{log}\n```"}).encode("utf-8")

    request = urllib.request.Request(webhook,data=body,headers={"Content-Type":"application/json","User-Agent":"chonkers-bot-alert"})
    with urllib.request.urlopen(request,timeout=15):
        pass

if __name__ == "__main__":
    main()
