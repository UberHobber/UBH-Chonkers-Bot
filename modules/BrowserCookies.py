"""
Pulls YouTube login cookies straight out of a browser profile (via yt-dlp's cookie extraction)
and writes them to a temporary Netscape cookies file, the format chat_downloader and yt-dlp
both take -- replaces exporting cookies.txt from the browser by hand. See COOKIES_FROM_BROWSER
in modules/Settings.py.

Kept free of project imports (Settings imports this at load time, before logging is set up).

Use a browser profile that only the bot uses: log into YouTube in it once, then close it and
leave it alone. YouTube rotates session cookies when a browser actively uses them, which is
what kept breaking hand-exported cookies.txt files -- an idle profile isn't rotated, and it's
re-read every run, so a new login there is picked up automatically.

Firefox is the browser to use on Windows: Chrome/Edge (127+) encrypt cookies with app-bound
encryption that only the browser itself can decrypt, so extraction from them fails.
"""

# Native Stuff
import atexit,glob,os,tempfile

# Cookies that only exist on a signed-in Google/YouTube session. Any one of them is enough.
_LOGIN_COOKIES = {"SAPISID","__Secure-3PAPISID","__Secure-1PSID","__Secure-3PSID","LOGIN_INFO"}
_KEEP_DOMAINS = ("youtube.com","google.com")

def _firefox_profile_dirs() -> list[str]:
    """Folders Firefox keeps its profiles in (Windows, then Linux/macOS)."""
    roots = [
        os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles"),
        os.path.expanduser("~/.mozilla/firefox"),
        os.path.expanduser("~/snap/firefox/common/.mozilla/firefox"),
        os.path.expanduser("~/Library/Application Support/Firefox/Profiles"),
    ]
    return [r for r in roots if os.path.isdir(r)]

def _resolve_profile(browser:str,profile:str|None) -> str|None:
    """
    Firefox profile folders are named "<random>.<profile name>" (e.g. "x8k2l1q.ChonkersBot"), and
    yt-dlp only matches the full folder name -- so a plain profile name is turned into that
    folder's path here. A full path, or any non-Firefox browser, is passed through unchanged.
    """
    if browser != "firefox" or profile is None or os.path.isdir(os.path.expanduser(profile)):
        return profile
    for root in _firefox_profile_dirs():
        for candidate in (os.path.join(root,profile),*glob.glob(os.path.join(root,f"*.{glob.escape(profile)}"))):
            if os.path.isdir(candidate):
                return candidate
    raise FileNotFoundError(f'no Firefox profile named "{profile}" (looked in: {", ".join(_firefox_profile_dirs()) or "no Firefox profile folders found"})')

def export_browser_cookies(browser:str,profile:str|None=None) -> str:
    """
    Reads the YouTube/Google cookies from a browser profile and writes them to a temporary
    cookies file, deleted when the program exits.

    :param browser: Browser name as yt-dlp knows it ("firefox" recommended -- see module docstring).
    :param profile: Profile name or full path. None uses the browser's most recently used profile.
    :return: Path to the cookies file.
    :raises RuntimeError: If the cookies can't be read or the profile isn't signed into YouTube.
    """
    # Imported here so scripts that never need cookies don't pay for importing yt-dlp.
    from yt_dlp.cookies import YoutubeDLCookieJar,extract_cookies_from_browser

    try:
        jar = extract_cookies_from_browser(browser,_resolve_profile(browser,profile))
    except Exception as e:
        raise RuntimeError(f"Couldn't read cookies from {browser} profile {profile!r}: {e}") from e

    kept = YoutubeDLCookieJar()
    for cookie in jar:
        if cookie.domain.lstrip(".").endswith(_KEEP_DOMAINS):
            kept.set_cookie(cookie)

    if not any(c.name in _LOGIN_COOKIES for c in kept):
        raise RuntimeError(f"The {browser} profile {profile!r} isn't signed into YouTube -- open it, sign in, then close it and run again.")

    fd,path = tempfile.mkstemp(prefix="yt_cookies_",suffix=".txt")
    os.close(fd)
    kept.save(path,ignore_discard=True,ignore_expires=True)
    atexit.register(lambda: os.path.exists(path) and os.remove(path))
    return path
