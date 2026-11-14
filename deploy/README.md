# Running the bot headless on Linux

These files run `Main.py` once a day as a systemd service, with no screen or console. It logs to a file and can post errors to a Discord webhook.

| File | What it is |
|---|---|
| `chonkers-bot.service` | One run of `Main.py` |
| `chonkers-bot.timer` | Starts that run every day at 04:00 |
| `chonkers-bot-alert@.service` + `notify_failure.py` | Posts the last log lines to the webhook if the run fails in a way the bot can't report itself (out-of-memory kill, killed after a stop timeout) |

The paths below match the examples in the unit files. Change both together if you use different ones.

| Path | Holds |
|---|---|
| `/opt/chonkers-bot` | This repo and its `.venv` |
| `/srv/chonkers/secrets` | The secrets folder (see the main README). Must be writable by the bot: it rewrites `token.pickle` when it refreshes the YouTube token. |
| `/srv/chonkers/data` | Local scratch data (deleted after each channel) |
| `/srv/chonkers/tmp` | The archiver's temporary files |
| `/srv/chonkers/status` | `status_*.json` for `Monitor.py` |
| `/var/log/chonkers-bot` | Log files, one per day, kept for `LOG_RETENTION_DAYS` (30) |

## Setup

**In a Proxmox LXC:** a Debian 13 container works, unprivileged is fine. Turn on `nesting=1` (Options → Features), which the systemd sandboxing settings in the service file need; without it the run fails at start with `226/NAMESPACE`. Set the container's timezone (`timedatectl set-timezone ...`), since the timer's 04:00 is local time.

1. **Python 3.12 or newer.** The code doesn't run on 3.11 (Debian 12's). Debian 13 or Ubuntu 24.04 work. `psycopg2` builds from source, so install the build tools too:
   ```
   sudo apt install python3 python3-venv python3-dev build-essential libpq-dev git
   ```
2. **A user and folders:**
   ```
   sudo useradd --system --create-home --shell /usr/sbin/nologin chonkers
   sudo mkdir -p /srv/chonkers/{secrets,data,tmp,status}
   sudo chown -R chonkers:chonkers /srv/chonkers
   sudo chmod 700 /srv/chonkers/secrets
   ```
3. **The code:**
   ```
   sudo git clone <repo url> /opt/chonkers-bot
   sudo chown -R chonkers:chonkers /opt/chonkers-bot
   cd /opt/chonkers-bot
   sudo -u chonkers python3 -m venv .venv
   sudo -u chonkers .venv/bin/pip install -r requirements.txt
   sudo -u chonkers .venv/bin/python -m modules.Patches
   ```
   The last line applies the chat-downloader fix in [`patches/`](../patches/chat_downloader-0.2.8/README.md). `Main.py` also applies it by itself at startup, so it's only there to check the install straight away. Repeat it after any `pip install` that reinstalls chat-downloader.
4. **Secrets.** Copy the whole secrets folder from the machine the bot ran on before, **including `token.pickle`**. A headless run can't open a browser for the YouTube sign-in. Without a working token it stops straight away with an error (and an alert) instead of waiting.
   - If the saved token ever stops refreshing, the run stops with a "YouTube sign-in needed" error (and alert). Run the bot once on a machine with a browser, sign in, and copy the new `token.pickle` over. Google documents a 7-day refresh-token lifetime for OAuth apps whose publishing status is **Testing**. Switching the app to **In production** (free, no Workspace account needed) avoids that if it turns out to apply.
   - Members-only chat (`GET_MEMBERS_CHAT`) needs cookies. `COOKIES_FROM_BROWSER` needs a desktop browser, so on a server use an exported `cookies.txt` with `COOKIES_FROM_BROWSER = None`.
5. **Alerts (optional).** Create a Discord webhook (channel settings → Integrations → Webhooks) and add it to the secrets folder's `Settings.json`:
   ```json
   "alert_webhook": "https://discord.com/api/webhooks/..."
   ```
   Leave it out or blank for no alerts.
6. **deno (only for subtitles).** yt-dlp needs a JavaScript runtime, deno 2.3 or newer, to solve YouTube's challenge. Install it from the [deno releases](https://github.com/denoland/deno/releases) (`deno-x86_64-unknown-linux-gnu.zip`, check its `.sha256sum`) to `/usr/local/bin/deno`, which is on the units' default PATH. It isn't an apt package, so update it by hand along with yt-dlp.
7. **Install the units:**
   ```
   sudo cp deploy/chonkers-bot.service deploy/chonkers-bot.timer deploy/chonkers-bot-alert@.service /etc/systemd/system/
   sudo systemctl daemon-reload
   ```
8. **Try one run by hand** before turning the timer on. The command returns when the run finishes, so start it with `--no-block` and watch the log:
   ```
   sudo systemctl start --no-block chonkers-bot
   tail -f /var/log/chonkers-bot/Chat_Process_Log.log
   ```
9. **Turn on the daily timer:**
   ```
   sudo systemctl enable --now chonkers-bot.timer
   systemctl list-timers chonkers-bot.timer
   ```

## Day to day

| To... | Run |
|---|---|
| Read the log | `less /var/log/chonkers-bot/Chat_Process_Log.log` (older days: `Chat_Process_Log.log.YYYY-MM-DD`) |
| Watch a run live | `sudo -u chonkers /opt/chonkers-bot/.venv/bin/python /opt/chonkers-bot/Monitor.py --dir /srv/chonkers/status` |
| Check the last run's result | `systemctl status chonkers-bot` |
| Console output and crashes | `journalctl -u chonkers-bot` |
| Start a run now | `sudo systemctl start --no-block chonkers-bot` |
| Stop a run | `sudo systemctl stop chonkers-bot`. Videos in progress are left for the next run, the same as Ctrl+C. |
| Update the code | `cd /opt/chonkers-bot && sudo -u chonkers git pull`. Takes effect on the next run. |
| Test the failure alert | `sudo systemctl start chonkers-bot-alert@chonkers-bot.service` |

## What alerts look like

With a webhook set:

- **Errors during a run:** every ERROR or CRITICAL log line, gathered for a minute and posted as one message (`ALERT_LEVEL`, `ALERT_BATCH_SECONDS` in `modules/Settings.py`). A crash posts straight away, with the error details.
- **End of run:** a short summary, with ✅ if the run was clean or ⚠️ if any channel had errors, an archive failed, or errors were posted (`ALERT_RUN_SUMMARY`).
- **The process died without reporting it:** 🚨 from `notify_failure.py`, with the last 25 lines of the journal.

If no ✅/⚠️ summary arrives on a given day, the run either didn't start, is still going, or was stopped by hand.

## Environment variables

The unit files set these. All are optional, and each falls back to the value in `modules/Settings.py`.

| Variable | Overrides |
|---|---|
| `CHONKERS_DATA_DIR` | `QUICK_SETTINGS_DATA` |
| `CHONKERS_SECRETS_DIR` | `QUICK_SETTINGS_SECRET` |
| `CHONKERS_LOG_DIR` | `LOG_DIRECTORY`, and turns file logging on |
| `CHONKERS_STATUS_DIR` | `STATUS_DIRECTORY` |
| `CHONKERS_HEADLESS` | `1` = never show dialogs or open a browser. Already assumed on Linux when there's no display. |
