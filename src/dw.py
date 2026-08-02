#!/usr/bin/env python3
"""dw - interactive yt-dlp download assistant for Debian and Windows.

The application intentionally delegates extraction/downloading to the official
yt-dlp executable and media muxing/validation to FFmpeg.  It never evaluates
data returned by a website as shell code.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import errno
import hashlib
import html
import http.client
import http.cookiejar
import json
import os
import platform
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - allows unit tests to import on Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - unavailable on POSIX
    msvcrt = None


APP_VERSION = "1.4.0"
IS_WINDOWS = os.name == "nt"


def disable_windows_quick_edit() -> None:
    """Prevent classic conhost text selection from pausing active downloads."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL
        input_handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        invalid_handle = ctypes.c_void_p(-1).value
        mode = wintypes.DWORD()
        if input_handle in (None, invalid_handle) or not kernel32.GetConsoleMode(
            input_handle, ctypes.byref(mode)
        ):
            return
        enable_extended_flags = 0x0080
        enable_quick_edit_mode = 0x0040
        updated = (mode.value | enable_extended_flags) & ~enable_quick_edit_mode
        kernel32.SetConsoleMode(input_handle, updated)
    except (AttributeError, OSError, TypeError, ValueError):
        pass


def configure_console() -> None:
    """Use UTF-8 for interactive output, including when imported on Windows."""
    if not IS_WINDOWS:
        return
    disable_windows_quick_edit()
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with contextlib.suppress(OSError, ValueError):
                reconfigure(encoding="utf-8", errors="replace")


configure_console()


def windows_downloads_directory() -> Path:
    """Resolve the current user's redirected Windows Downloads known folder."""
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    if not IS_WINDOWS:
        return home / "Downloads"
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        value_name = "{374DE290-123F-4565-9164-39C4925E467B}"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _value_type = winreg.QueryValueEx(key, value_name)
        resolved = Path(os.path.expandvars(str(value)))
        if resolved.is_absolute():
            return resolved
    except (ImportError, OSError, TypeError, ValueError):
        pass
    return home / "Downloads"


if IS_WINDOWS:
    _USER_HOME = Path(os.environ.get("USERPROFILE") or Path.home())
    _LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA") or (_USER_HOME / "AppData" / "Local"))
    APP_DIR = Path(os.environ.get("DW_APP_DIR", str(_LOCAL_APP_DATA / "yt-dlp-dw")))
    STATE_DIR = Path(os.environ.get("DW_STATE_DIR", str(_LOCAL_APP_DATA / "yt-dlp-dw-data")))
    OUTPUT_DIR = Path(os.environ.get("DW_OUTPUT_DIR", str(windows_downloads_directory())))
    COOKIE_FILE = Path(os.environ.get("DW_COOKIE_FILE", str(_USER_HOME / "cookies.txt")))
    COMMAND_DIR = Path(os.environ.get("DW_COMMAND_DIR", str(APP_DIR / "command")))
    LAUNCHER_PATH = Path(os.environ.get("DW_LAUNCHER_PATH", str(COMMAND_DIR / "dw.cmd")))
    WINDOWS_ALIAS_LAUNCHER = Path(
        os.environ.get(
            "DW_ALIAS_LAUNCHER",
            str(_LOCAL_APP_DATA / "Microsoft" / "WindowsApps" / "dw.cmd"),
        )
    )
else:
    APP_DIR = Path(os.environ.get("DW_APP_DIR", "/opt/dw"))
    STATE_DIR = Path(os.environ.get("DW_STATE_DIR", "/var/lib/dw"))
    OUTPUT_DIR = Path(os.environ.get("DW_OUTPUT_DIR", "/root"))
    COOKIE_FILE = Path(os.environ.get("DW_COOKIE_FILE", "/root/cookies.txt"))
    LAUNCHER_PATH = Path(os.environ.get("DW_LAUNCHER_PATH", "/usr/local/bin/dw"))
    COMMAND_DIR = LAUNCHER_PATH.parent
    WINDOWS_ALIAS_LAUNCHER = LAUNCHER_PATH

BIN_DIR = APP_DIR / "bin"
_EXECUTABLE_SUFFIX = ".exe" if IS_WINDOWS else ""
YT_DLP = BIN_DIR / f"yt-dlp{_EXECUTABLE_SUFFIX}"
DENO = BIN_DIR / f"deno{_EXECUTABLE_SUFFIX}"
FFMPEG = BIN_DIR / f"ffmpeg{_EXECUTABLE_SUFFIX}"
FFPROBE = BIN_DIR / f"ffprobe{_EXECUTABLE_SUFFIX}"
ARIA2 = BIN_DIR / "aria2c.exe" if IS_WINDOWS else Path("/usr/bin/aria2c")
CACHE_DIR = STATE_DIR / "cache"
TASKS_DIR = STATE_DIR / "tasks"
COMPONENTS_FILE = STATE_DIR / "components.json"
MANIFEST_FILE = STATE_DIR / "downloads.json"
PACKAGES_FILE = STATE_DIR / "managed-packages.json"
GITHUB_MIRRORS_FILE = STATE_DIR / "github-mirrors.txt"
LOCK_FILE = STATE_DIR / "dw.lock"
APP_MARKER = APP_DIR / ".dw-owned"
STATE_MARKER = STATE_DIR / ".dw-owned"
WINDOWS_UNINSTALL_HELPER = APP_DIR / "uninstall-windows.ps1"
WINDOWS_UPDATE_HELPER = APP_DIR / "update-windows.ps1"
UPDATE_CONFIG_FILE = STATE_DIR / "update-source.json"
UPDATE_DIR = STATE_DIR / "update"

GITHUB_API = "https://api.github.com/repos"
DEFAULT_UPDATE_REPOSITORY = "lucaskevin9510-beep/yt-dlp-dw"
USER_AGENT = f"yt-dlp-dw/{APP_VERSION} (+https://github.com/lucaskevin9510-beep/yt-dlp-dw)"
PAGE_SIZE = 30
UPDATE_INTERVAL_SECONDS = 24 * 60 * 60
DOWNLOAD_CONNECTIONS = 8
ARIA2_VERSION = "1.37.0"
ARIA2_WINDOWS_ASSET = f"aria2-{ARIA2_VERSION}-win-64bit-build1.zip"
ARIA2_WINDOWS_SHA256 = "67d015301eef0b612191212d564c5bb0a14b5b9c4796b76454276a4d28d9b288"
UNINSTALL_CONFIRMATION = "确认卸载并删除全部下载"

BUILTIN_GITHUB_MIRRORS = (
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://ghproxy.net/",
)
DOMESTIC_DOMAIN_SUFFIXES = {
    "163.com",
    "56.com",
    "acfun.cn",
    "amemv.com",
    "b23.tv",
    "baidu.com",
    "bilibili.com",
    "cctv.com",
    "douban.com",
    "douyin.com",
    "douyu.com",
    "fun.tv",
    "huya.com",
    "ifeng.com",
    "iesdouyin.com",
    "iqiyi.com",
    "ixigua.com",
    "kuaishou.com",
    "le.com",
    "lizhi.fm",
    "mgtv.com",
    "netease.com",
    "pearvideo.com",
    "pptv.com",
    "qq.com",
    "qingting.fm",
    "rednote.com",
    "sohu.com",
    "tudou.com",
    "v.douyin.com",
    "weibo.cn",
    "weibo.com",
    "xhslink.com",
    "xiaohongshu.com",
    "ximalaya.com",
    "xinpianchang.com",
    "yizhibo.com",
    "yinyuetai.com",
    "youku.com",
    "zhihu.com",
}
URL_END_CHARACTERS = "\t\r\n <>\"'`()[]{}，。！？；、【】（）《》〈〉「」『』〔〕［］"
_WARNED_CUSTOM_MIRRORS: set[str] = set()

XPC_CHROME_BRIDGE_SOURCE = r"""
const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function readConfig() {
  const text = await new Response(Deno.stdin.readable).text();
  const value = JSON.parse(text);
  if (!value || typeof value.url !== "string" || !Number.isInteger(value.port)) {
    throw new Error("invalid bridge configuration");
  }
  return value;
}

async function waitForTarget(port, deadline) {
  const endpoint = "http://127.0.0.1:" + port + "/json/list";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(endpoint);
      if (response.ok) {
        const targets = await response.json();
        const target = targets.find((item) => item.type === "page" && item.webSocketDebuggerUrl);
        if (target) {
          return target;
        }
      }
    } catch (_) {
      // Chrome may still be starting.
    }
    await delay(250);
  }
  throw new Error("Chrome remote debugging endpoint did not become ready");
}

async function connect(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Chrome connection timed out")), 15000);
    socket.onopen = () => {
      clearTimeout(timer);
      resolve();
    };
    socket.onerror = () => {
      clearTimeout(timer);
      reject(new Error("Chrome connection failed"));
    };
  });
  return socket;
}

async function main() {
  const config = await readConfig();
  const deadline = Date.now() + Math.max(30000, Number(config.timeoutMs) || 300000);
  const target = await waitForTarget(config.port, Math.min(deadline, Date.now() + 20000));
  const socket = await connect(target.webSocketDebuggerUrl);
  let sequence = 0;
  const pending = new Map();

  socket.onmessage = (event) => {
    let message;
    try {
      message = JSON.parse(String(event.data));
    } catch (_) {
      return;
    }
    if (!message.id || !pending.has(message.id)) {
      return;
    }
    const waiter = pending.get(message.id);
    pending.delete(message.id);
    clearTimeout(waiter.timer);
    if (message.error) {
      waiter.reject(new Error(message.error.message || "Chrome command failed"));
    } else {
      waiter.resolve(message.result || {});
    }
  };
  socket.onclose = () => {
    for (const waiter of pending.values()) {
      clearTimeout(waiter.timer);
      waiter.reject(new Error("Chrome window was closed"));
    }
    pending.clear();
  };

  function command(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => {
        if (pending.delete(id)) {
          reject(new Error("Chrome command timed out: " + method));
        }
      }, 15000);
      pending.set(id, {resolve, reject, timer});
      socket.send(JSON.stringify({id, method, params}));
    });
  }

  await command("Network.enable");
  if (Array.isArray(config.cookies) && config.cookies.length) {
    await command("Network.setCookies", {cookies: config.cookies});
  }
  await command("Page.enable");
  await command("Page.navigate", {url: config.url});

  const expression = String.raw`(() => {
    const state = {
      title: document.title || "",
      url: location.href,
      readyState: document.readyState,
      userAgent: navigator.userAgent || ""
    };
    const element = document.querySelector("#__NEXT_DATA__");
    if (!element) {
      return state;
    }
    try {
      const root = JSON.parse(element.textContent || "{}");
      const detail = root && root.props && root.props.pageProps && root.props.pageProps.detail;
      const video = detail && detail.video;
      if (video && video.vid && video.appKey) {
        state.ready = true;
        state.vid = String(video.vid);
        state.appKey = String(video.appKey);
        state.title = String((detail && detail.title) || state.title || "");
      }
    } catch (_) {
      state.parseError = true;
    }
    return state;
  })()`;

  let lastState = {};
  let nextProgress = Date.now() + 15000;
  while (Date.now() < deadline) {
    const evaluated = await command("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: false
    });
    lastState = evaluated && evaluated.result && evaluated.result.value || {};
    if (lastState.ready && lastState.vid && lastState.appKey) {
      let sessionCookies = [];
      try {
        const cookieResult = await command("Network.getCookies", {
          urls: [config.url, "https://mod-api.xinpianchang.com/"]
        });
        sessionCookies = (cookieResult.cookies || [])
          .filter((cookie) => {
            const domain = String(cookie.domain || "").toLowerCase().replace(/^\./, "");
            return domain === "xinpianchang.com" || domain.endsWith(".xinpianchang.com");
          })
          .map((cookie) => ({
            name: String(cookie.name || ""),
            value: String(cookie.value || ""),
            domain: String(cookie.domain || ""),
            path: String(cookie.path || "/"),
            secure: Boolean(cookie.secure),
            httpOnly: Boolean(cookie.httpOnly),
            expires: Number(cookie.expires) || 0
          }));
      } catch (_) {
        // The public media API often needs no session cookie. Continue safely.
      }
      console.log(JSON.stringify({
        ok: true,
        vid: lastState.vid,
        appKey: lastState.appKey,
        title: lastState.title || "",
        userAgent: lastState.userAgent || "",
        cookies: sessionCookies
      }));
      try {
        socket.send(JSON.stringify({id: ++sequence, method: "Browser.close", params: {}}));
        await delay(250);
      } catch (_) {
        // Python still owns and terminates the browser process as a fallback.
      }
      try {
        socket.close();
      } catch (_) {
        // Browser.close may already have closed the DevTools socket.
      }
      return;
    }
    if (Date.now() >= nextProgress) {
      console.error("仍在等待你完成新片场页面验证，请在打开的 Chrome 窗口中操作 …");
      nextProgress = Date.now() + 15000;
    }
    await delay(1000);
  }
  try {
    socket.send(JSON.stringify({id: ++sequence, method: "Browser.close", params: {}}));
    await delay(250);
  } catch (_) {
    // Python still performs process cleanup after the helper exits.
  }
  try {
    socket.close();
  } catch (_) {
    // Browser may already be closed by the user.
  }
  throw new Error(
    "等待页面验证超时" + (lastState.title ? "；当前页面：" + lastState.title : "")
  );
}

try {
  await main();
} catch (error) {
  console.error("Chrome 辅助读取失败：" + (error && error.message ? error.message : String(error)));
  Deno.exit(1);
}
"""

VIDEO_CONTAINER_ORDER = {"mp4": 0, "webm": 1}
VIDEO_CODEC_ORDER = {"h264": 0, "h265": 1, "av1": 2, "vp9": 3, "vp8": 4}
SUPPORTED_REMUX_CONTAINERS = {"avi", "flv", "mkv", "mov", "mp4", "webm"}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "CONIN$",
    "CONOUT$",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class DwError(RuntimeError):
    """Expected, user-facing application error."""

    def __init__(self, message: str, detail: str = "", cookie_related: bool = False):
        super().__init__(message)
        self.message = message
        self.detail = detail.strip()
        self.cookie_related = cookie_related


class DownloadAborted(Exception):
    """The user cancelled the active batch."""


class LiveAbort(DownloadAborted):
    """The user pressed Ctrl+C twice while recording a live stream."""


@dataclasses.dataclass
class ProcessResult:
    returncode: int
    stderr: str
    live_stopped: bool = False


@dataclasses.dataclass(frozen=True)
class RequestPolicy:
    """Network and cookie choices applied to every yt-dlp call for one URL."""

    direct: bool = False
    cookie_mode: str = "none"
    xpc_info: dict[str, Any] | None = dataclasses.field(default=None, compare=False, repr=False)
    xpc_assist_attempted: bool = False


@dataclasses.dataclass
class ItemSelection:
    video: dict[str, Any]
    audios: list[dict[str, Any]]
    use_embedded_audio: bool
    replace_embedded_audio: bool
    container_mode: str
    resolved_container: str
    thumbnails: list[dict[str, Any]]
    missing_languages: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class SelectionProfile:
    video: dict[str, Any]
    audios: list[dict[str, Any]]
    embedded_audio: dict[str, Any] | None
    prefer_embedded_audio: bool
    replace_embedded_audio: bool
    container_mode: str
    thumbnails: list[dict[str, Any]]
    all_thumbnails: bool


@dataclasses.dataclass
class ItemResult:
    title: str
    paths: list[Path]
    elapsed: float
    size: int
    summary: str
    missing_languages: list[str]


def info(message: str = "") -> None:
    print(message, flush=True)


def warn(message: str) -> None:
    print(f"警告：{message}", file=sys.stderr, flush=True)


def error(message: str) -> None:
    print(f"错误：{message}", file=sys.stderr, flush=True)


def ensure_runtime_directories() -> None:
    for path, mode in ((STATE_DIR, 0o700), (CACHE_DIR, 0o700), (TASKS_DIR, 0o700)):
        path.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            path.chmod(mode)
    if not STATE_MARKER.exists():
        STATE_MARKER.write_text("owned by yt-dlp-dw\n", encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json_atomic(path: Path, value: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()


def hostname_matches(hostname: str, suffixes: Iterable[str]) -> bool:
    hostname = hostname.lower().rstrip(".")
    return any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffixes)


def url_hostname(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def normalize_shared_url(url: str) -> str:
    """Normalize site share URLs while preserving required query tokens."""
    url = html.unescape(url.strip())
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname_matches(hostname, {"douyin.com"}):
        modal_ids = urllib.parse.parse_qs(parsed.query).get("modal_id") or []
        if modal_ids and re.fullmatch(r"\d+", modal_ids[0]):
            return f"https://www.douyin.com/video/{modal_ids[0]}"
    return url


def extract_urls_from_text(text: str) -> list[str]:
    """Extract, clean, normalize, and deduplicate HTTP(S) URLs from share text."""
    value = html.unescape(text.strip())
    starts = list(re.finditer(r"https?://", value, flags=re.IGNORECASE))
    urls: list[str] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for position, match in enumerate(starts):
        end = starts[position + 1].start() if position + 1 < len(starts) else len(value)
        segment = value[match.start() : end]
        stop = next(
            (
                index
                for index, character in enumerate(segment)
                if character in URL_END_CHARACTERS
                or (
                    ord(character) > 127
                    and unicodedata.category(character) in {"Sk", "So"}
                )
            ),
            len(segment),
        )
        candidate = segment[:stop].rstrip(".,;!?:")
        if not candidate:
            continue
        candidate = normalize_shared_url(candidate)
        try:
            parsed = urllib.parse.urlsplit(candidate)
        except ValueError:
            continue
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            continue
        key = (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or "/",
            parsed.query,
            parsed.fragment,
        )
        if key in seen:
            continue
        seen.add(key)
        urls.append(candidate)
    return urls


def is_domestic_url(url: str) -> bool:
    hostname = url_hostname(url)
    return bool(hostname) and (hostname.endswith(".cn") or hostname_matches(hostname, DOMESTIC_DOMAIN_SUFFIXES))


def system_proxy_configured() -> bool:
    with contextlib.suppress(OSError, ValueError):
        proxies = urllib.request.getproxies()
        return any(value for key, value in proxies.items() if key.lower() in {"http", "https", "all"})
    return False


def configured_github_mirrors() -> list[str]:
    values: list[str] = []
    environment = os.environ.get("DW_GITHUB_MIRRORS", "")
    if environment:
        values.extend(re.split(r"[;,\r\n]+", environment))
    try:
        values.extend(GITHUB_MIRRORS_FILE.read_text(encoding="utf-8").splitlines())
    except OSError:
        pass

    mirrors: list[str] = []
    for raw in values:
        mirror = raw.strip()
        if not mirror or mirror.startswith("#") or mirror in mirrors:
            continue
        probe = mirror.replace("{url}", "https://github.com/")
        try:
            parsed = urllib.parse.urlsplit(probe)
        except ValueError:
            parsed = urllib.parse.SplitResult("", "", "", "", "")
        if parsed.scheme != "https" or not parsed.hostname:
            if mirror not in _WARNED_CUSTOM_MIRRORS:
                warn(f"你提供的镜像域名不可使用：{mirror}")
                _WARNED_CUSTOM_MIRRORS.add(mirror)
            continue
        mirrors.append(mirror)
    return mirrors


def apply_github_mirror(mirror: str, url: str) -> str:
    if "{url}" in mirror:
        return mirror.replace("{url}", url)
    return f"{mirror.rstrip('/')}/{url}"


def github_request_candidates(url: str) -> list[tuple[str, str, bool]]:
    hostname = url_hostname(url)
    github_hostnames = {"api.github.com", "github.com", "raw.githubusercontent.com"}
    if hostname not in github_hostnames:
        return [(url, "官方源", False)]

    candidates: list[tuple[str, str, bool]] = []
    for mirror in configured_github_mirrors():
        candidates.append((apply_github_mirror(mirror, url), mirror, True))
    candidates.append((url, "GitHub 官方源", False))
    for mirror in BUILTIN_GITHUB_MIRRORS:
        if hostname == "api.github.com" and mirror != "https://gh-proxy.com/":
            continue
        candidates.append((apply_github_mirror(mirror, url), mirror, False))

    deduplicated: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate[0] in seen:
            continue
        seen.add(candidate[0])
        deduplicated.append(candidate)
    return deduplicated


class InstanceLock:
    def __init__(self) -> None:
        self._handle: Any = None

    def __enter__(self) -> "InstanceLock":
        ensure_runtime_directories()
        self._handle = LOCK_FILE.open("a+b")
        self._handle.seek(0, os.SEEK_END)
        if self._handle.tell() == 0:
            self._handle.write(b"\0")
            self._handle.flush()
        self._handle.seek(0)
        if fcntl is not None:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DwError("已有另一个 dw 实例正在运行，请等待它结束。") from exc
        elif msvcrt is not None:
            try:
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                self._handle.close()
                self._handle = None
                raise DwError("已有另一个 dw 实例正在运行，请等待它结束。") from exc
        return self

    def __exit__(self, *_: Any) -> None:
        if self._handle:
            if fcntl is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:
                with contextlib.suppress(OSError):
                    self._handle.seek(0)
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            self._handle.close()


def request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )


def fetch_json(url: str) -> dict[str, Any]:
    last_error: Exception | None = None
    candidates = github_request_candidates(url)
    for candidate_url, source, custom in candidates:
        attempts = 3 if len(candidates) == 1 else 1
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(request(candidate_url), timeout=45) as response:
                    value = json.load(response)
                if not isinstance(value, dict):
                    raise json.JSONDecodeError("expected a JSON object", "", 0)
                if candidate_url != url:
                    info(f"  已通过 GitHub 镜像获取版本信息：{source}")
                return value
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, http.client.HTTPException) as exc:
                last_error = exc
                if attempt < attempts:
                    warn(f"查询依赖版本失败，第 {attempt}/{attempts} 次重试 …")
                    time.sleep(attempt)
        if custom and source not in _WARNED_CUSTOM_MIRRORS:
            warn(f"你提供的镜像域名不可使用：{source}")
            _WARNED_CUSTOM_MIRRORS.add(source)
    raise DwError("无法查询依赖版本。", str(last_error)) from last_error


def response_content_length(response: Any) -> int:
    try:
        value = response.headers.get("Content-Length")
        length = int(value)
    except (AttributeError, TypeError, ValueError):
        return 0
    return length if length > 0 else 0


def copy_response_with_progress(response: Any, output: Any, label: str) -> int:
    """Copy an HTTP response while showing useful progress in terminals and logs."""
    total = response_content_length(response)
    downloaded = 0
    started = time.monotonic()
    last_update = started - 1.0
    last_width = 0
    line_active = False
    interactive = bool(getattr(sys.stdout, "isatty", lambda: False)())

    def report(final: bool = False) -> None:
        nonlocal last_update, last_width, line_active
        now = time.monotonic()
        if not final and now - last_update < (0.25 if interactive else 5.0):
            return
        elapsed = max(now - started, 0.001)
        speed = format_size(downloaded / elapsed) + "/s"
        if total:
            percent = min(downloaded * 100 / total, 100.0)
            message = (
                f"下载进度 {label}：{percent:6.1f}% | "
                f"{format_size(downloaded)} / {format_size(total)} | {speed}"
            )
        else:
            message = f"下载进度 {label}：{format_size(downloaded)} | {speed}"
        if interactive:
            last_width = max(last_width, len(message))
            print(f"\r  {message.ljust(last_width)}", end="\n" if final else "", flush=True)
            line_active = not final
        else:
            info(f"  {message}")
        last_update = now

    try:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            output.write(block)
            downloaded += len(block)
            report()
        if total and downloaded != total:
            raise OSError(f"incomplete download: received {downloaded} of {total} bytes")
        report(final=True)
        return downloaded
    except BaseException:
        if interactive and line_active:
            print(flush=True)
        raise


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    candidates = github_request_candidates(url)
    for candidate_url, source, custom in candidates:
        attempts = 3 if len(candidates) == 1 else 1
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(request(candidate_url), timeout=90) as response, destination.open(
                    "wb"
                ) as out:
                    copy_response_with_progress(response, out, destination.name)
                if destination.stat().st_size <= 0:
                    raise OSError("downloaded file is empty")
                if candidate_url != url:
                    info(f"  已通过 GitHub 镜像下载：{source}")
                return
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
                last_error = exc
                with contextlib.suppress(FileNotFoundError):
                    destination.unlink()
                if attempt < attempts:
                    warn(f"依赖下载失败，第 {attempt}/{attempts} 次重试 …")
                    time.sleep(attempt)
        if custom and source not in _WARNED_CUSTOM_MIRRORS:
            warn(f"你提供的镜像域名不可使用：{source}")
            _WARNED_CUSTOM_MIRRORS.add(source)
    raise DwError(f"下载依赖失败：{url}", str(last_error)) from last_error


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_checksum(checksum_text: str, asset_name: str) -> str:
    algorithm_match = re.search(r"(?im)^\s*Algorithm\s*:\s*([A-Za-z0-9-]+)\s*$", checksum_text)
    hash_match = re.search(r"(?im)^\s*Hash\s*:\s*([0-9a-f]{64})\s*$", checksum_text)
    path_match = re.search(r"(?im)^\s*Path\s*:\s*(.+?)\s*$", checksum_text)
    if algorithm_match or hash_match or path_match:
        if not algorithm_match or algorithm_match.group(1).replace("-", "").upper() != "SHA256":
            raise DwError(f"{asset_name} 的校验文件算法不是 SHA-256。")
        if not hash_match:
            raise DwError(f"{asset_name} 的校验文件缺少 SHA-256。")
        if path_match:
            named = re.split(r"[\\/]", path_match.group(1).strip())[-1]
            if named.lower() != asset_name.lower():
                raise DwError(f"校验文件对应 {named}，不是预期的 {asset_name}。")
        return hash_match.group(1).lower()
    for line in checksum_text.splitlines():
        fields = line.strip().split()
        if not fields:
            continue
        candidate = fields[0].lower()
        named = fields[-1].lstrip("*") if len(fields) > 1 else ""
        if re.fullmatch(r"[0-9a-f]{64}", candidate) and (not named or named == asset_name):
            return candidate
    raise DwError(f"校验文件中找不到 {asset_name} 的 SHA-256。")


def release_info(repository: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    release = fetch_json(f"{GITHUB_API}/{repository}/releases/latest")
    assets = {asset.get("name", ""): asset for asset in release.get("assets", [])}
    return release, assets


def verified_asset(
    assets: dict[str, dict[str, Any]], asset_name: str, checksum_name: str, workdir: Path
) -> Path:
    try:
        asset_url = assets[asset_name]["browser_download_url"]
        checksum_url = assets[checksum_name]["browser_download_url"]
    except KeyError as exc:
        raise DwError(f"官方发布中缺少依赖文件：{exc.args[0]}") from exc
    asset_path = workdir / asset_name
    checksum_path = workdir / checksum_name
    info(f"  下载 {asset_name} …")
    download_file(asset_url, asset_path)
    download_file(checksum_url, checksum_path)
    checksum = expected_checksum(checksum_path.read_text(encoding="utf-8"), asset_name)
    release_digest = str(assets[asset_name].get("digest") or "").lower()
    if release_digest.startswith("sha256:") and release_digest.removeprefix("sha256:") != checksum:
        raise DwError(f"{asset_name} 的发布元数据与校验文件不一致，已拒绝安装。")
    actual = sha256_file(asset_path)
    if actual != checksum:
        raise DwError(f"{asset_name} 的 SHA-256 校验失败，已拒绝安装。")
    return asset_path


def atomic_install_binary(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    shutil.copyfile(source, temp)
    temp.chmod(0o755)
    os.replace(temp, destination)


def ytdlp_asset_name() -> str:
    return "yt-dlp.exe" if IS_WINDOWS else "yt-dlp_linux"


def deno_asset_layout() -> tuple[str, str]:
    if IS_WINDOWS:
        return "deno-x86_64-pc-windows-msvc.zip", "deno.exe"
    return "deno-x86_64-unknown-linux-gnu.zip", "deno"


def ffmpeg_asset_layout() -> tuple[str, set[str]]:
    if IS_WINDOWS:
        return "ffmpeg-master-latest-win64-gpl.zip", {"ffmpeg.exe", "ffprobe.exe"}
    return "ffmpeg-master-latest-linux64-gpl.tar.xz", {"ffmpeg", "ffprobe"}


def install_ytdlp(metadata: dict[str, Any], workdir: Path, force: bool) -> None:
    release, assets = release_info("yt-dlp/yt-dlp-nightly-builds")
    version = str(release.get("tag_name") or release.get("name") or "unknown")
    if not force and metadata.get("yt-dlp") == version and YT_DLP.is_file():
        return
    asset_name = ytdlp_asset_name()
    asset = verified_asset(assets, asset_name, "SHA2-256SUMS", workdir)
    atomic_install_binary(asset, YT_DLP)
    metadata["yt-dlp"] = version
    info(f"  yt-dlp 已更新至 nightly {version}")


def install_deno(metadata: dict[str, Any], workdir: Path, force: bool) -> None:
    release, assets = release_info("denoland/deno")
    version = str(release.get("tag_name") or "unknown")
    if not force and metadata.get("deno") == version and DENO.is_file():
        return
    name, executable_name = deno_asset_layout()
    archive = verified_asset(assets, name, f"{name}.sha256sum", workdir)
    extracted = workdir / "deno-extracted"
    with zipfile.ZipFile(archive) as bundle:
        member = next((item for item in bundle.infolist() if Path(item.filename).name == executable_name), None)
        if member is None or member.is_dir():
            raise DwError("Deno 压缩包中找不到 deno 可执行文件。")
        with bundle.open(member) as source, extracted.open("wb") as target:
            shutil.copyfileobj(source, target)
    atomic_install_binary(extracted, DENO)
    metadata["deno"] = version
    info(f"  Deno 已更新至 {version}")


def install_ffmpeg(metadata: dict[str, Any], workdir: Path, force: bool) -> None:
    release, assets = release_info("yt-dlp/FFmpeg-Builds")
    name, expected_names = ffmpeg_asset_layout()
    asset_meta = assets.get(name, {})
    version = str(asset_meta.get("updated_at") or release.get("published_at") or "unknown")
    if not force and metadata.get("ffmpeg") == version and FFMPEG.is_file() and FFPROBE.is_file():
        return
    archive = verified_asset(assets, name, "checksums.sha256", workdir)
    extracted: dict[str, Path] = {}
    if IS_WINDOWS:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                basename = Path(member.filename).name
                normalized = member.filename.replace("\\", "/")
                if basename not in expected_names or member.is_dir() or "/bin/" not in normalized:
                    continue
                target = workdir / f"{basename}-extracted"
                with bundle.open(member) as source, target.open("wb") as out:
                    shutil.copyfileobj(source, out)
                extracted[basename] = target
    else:
        with tarfile.open(archive, "r:xz") as bundle:
            for member in bundle.getmembers():
                basename = Path(member.name).name
                if basename not in expected_names or not member.isfile() or "/bin/" not in member.name:
                    continue
                source = bundle.extractfile(member)
                if source is None:
                    continue
                target = workdir / f"{basename}-extracted"
                with source, target.open("wb") as out:
                    shutil.copyfileobj(source, out)
                extracted[basename] = target
    if set(extracted) != expected_names:
        raise DwError("FFmpeg 压缩包结构异常，未安装任何不完整组件。")
    atomic_install_binary(extracted[FFMPEG.name], FFMPEG)
    atomic_install_binary(extracted[FFPROBE.name], FFPROBE)
    metadata["ffmpeg"] = version
    info("  FFmpeg/FFprobe 已更新")


def install_aria2(metadata: dict[str, Any], workdir: Path, force: bool) -> None:
    if not IS_WINDOWS:
        if not ARIA2.is_file() or not os.access(ARIA2, os.X_OK):
            raise DwError("未检测到 aria2c，请重新运行 Debian 安装器。")
        return
    if not force and metadata.get("aria2") == ARIA2_VERSION and ARIA2.is_file():
        return
    url = (
        f"https://github.com/aria2/aria2/releases/download/release-{ARIA2_VERSION}/"
        f"{ARIA2_WINDOWS_ASSET}"
    )
    archive = workdir / ARIA2_WINDOWS_ASSET
    info(f"  下载 {ARIA2_WINDOWS_ASSET} …")
    download_file(url, archive)
    if sha256_file(archive) != ARIA2_WINDOWS_SHA256:
        raise DwError("aria2 Windows x64 压缩包的 SHA-256 校验失败，已拒绝安装。")
    extracted = workdir / "aria2c-extracted.exe"
    with zipfile.ZipFile(archive) as bundle:
        matches = [
            member
            for member in bundle.infolist()
            if not member.is_dir() and Path(member.filename).name.lower() == "aria2c.exe"
        ]
        if len(matches) != 1:
            raise DwError("aria2 压缩包结构异常，找不到唯一的 aria2c.exe。")
        with bundle.open(matches[0]) as source, extracted.open("wb") as target:
            shutil.copyfileobj(source, target)
    atomic_install_binary(extracted, ARIA2)
    metadata["aria2"] = ARIA2_VERSION
    info(f"  aria2c 已安装至 {ARIA2_VERSION}")


def dependencies_present() -> bool:
    return all(
        path.is_file() and (IS_WINDOWS or os.access(path, os.X_OK))
        for path in (YT_DLP, DENO, FFMPEG, FFPROBE, ARIA2)
    )


def install_or_update_dependencies(force: bool = False) -> None:
    ensure_runtime_directories()
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    metadata = read_json(COMPONENTS_FILE, {})
    last_check = float(metadata.get("last_check", 0) or 0)
    if not force and dependencies_present() and time.time() - last_check < UPDATE_INTERVAL_SECONDS:
        info("依赖在最近 24 小时内已检查，跳过更新。")
        return

    info("正在检查 yt-dlp、Deno、FFmpeg 和 aria2 …")
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="dependency-", dir=STATE_DIR) as temp_name:
        workdir = Path(temp_name)
        installers = [
            ("yt-dlp", install_ytdlp),
            ("Deno", install_deno),
            ("FFmpeg", install_ffmpeg),
        ]
        if IS_WINDOWS:
            installers.append(("aria2", install_aria2))
        else:
            install_aria2(metadata, workdir / "aria2", force)
        for label, installer in installers:
            component_dir = workdir / label.lower()
            component_dir.mkdir()
            try:
                installer(metadata, component_dir, force)
            except DwError as exc:
                if {"yt-dlp": YT_DLP, "Deno": DENO, "FFmpeg": FFMPEG, "aria2": ARIA2}[label].exists():
                    failures.append(f"{label} 更新失败，继续使用现有版本：{exc.message}")
                else:
                    raise

    if failures:
        for message in failures:
            warn(message)
    else:
        metadata["last_check"] = int(time.time())
    write_json_atomic(COMPONENTS_FILE, metadata)
    if not dependencies_present():
        raise DwError("必要依赖不完整，请重新运行安装脚本。")


def ytdlp_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["DENO_DIR"] = str(CACHE_DIR / "deno")
    env["PATH"] = str(BIN_DIR) + os.pathsep + env.get("PATH", "")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if not IS_WINDOWS:
        env.setdefault("LC_ALL", "C.UTF-8")
        env.setdefault("LANG", "C.UTF-8")
    return env


def cookie_file_available() -> bool:
    """Return whether the configured cookies file can safely be read."""
    try:
        return COOKIE_FILE.is_file() and os.access(COOKIE_FILE, os.R_OK)
    except OSError:
        return False


def cookie_path_present() -> bool:
    """Return whether the cookie path exists without leaking filesystem errors."""
    try:
        return COOKIE_FILE.exists() or COOKIE_FILE.is_symlink()
    except OSError:
        return False


def ytdlp_base_args(policy: RequestPolicy | None = None, url: str = "") -> list[str]:
    policy = policy or RequestPolicy()
    args = [
        str(YT_DLP),
        "--ignore-config",
        "--cache-dir",
        str(CACHE_DIR / "yt-dlp"),
        "--ffmpeg-location",
        str(BIN_DIR),
        "--js-runtimes",
        f"deno:{DENO}",
        "--no-write-comments",
        "--no-write-playlist-metafiles",
    ]
    if policy.direct:
        args.extend(("--proxy", ""))
    if policy.cookie_mode == "file" and cookie_file_available():
        args.extend(("--cookies", str(COOKIE_FILE)))
    if IS_WINDOWS and hostname_matches(url_hostname(url), {"xinpianchang.com"}):
        args.extend(("--impersonate", "chrome:windows-10"))
    return args


def is_xinpianchang_url(url: str) -> bool:
    return hostname_matches(url_hostname(url), {"xinpianchang.com"})


def chrome_executable_path() -> Path | None:
    """Locate stable Chrome without reading its profile or cookie database."""
    if not IS_WINDOWS:
        return None
    candidates: list[Path] = []
    configured = os.environ.get("DW_CHROME_PATH")
    if configured:
        candidates.append(Path(configured))
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    discovered = shutil.which("chrome.exe") or shutil.which("chrome")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        with contextlib.suppress(OSError):
            if candidate.is_file():
                return candidate.resolve()
    return None


def allocate_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def request_opener(policy: RequestPolicy | None = None) -> urllib.request.OpenerDirector:
    policy = policy or RequestPolicy()
    handlers: list[Any] = []
    if policy.direct:
        handlers.append(urllib.request.ProxyHandler({}))
    if policy.cookie_mode == "file" and cookie_file_available():
        jar = http.cookiejar.MozillaCookieJar(str(COOKIE_FILE))
        try:
            jar.load(ignore_discard=True, ignore_expires=False)
            handlers.append(urllib.request.HTTPCookieProcessor(jar))
        except (OSError, http.cookiejar.LoadError):
            pass
    return urllib.request.build_opener(*handlers)


def xinpianchang_cookie_params(policy: RequestPolicy) -> list[dict[str, Any]]:
    """Load only xinpianchang.com cookies for the isolated, task-scoped browser."""
    if policy.cookie_mode != "file" or not cookie_file_available():
        return []
    jar = http.cookiejar.MozillaCookieJar(str(COOKIE_FILE))
    try:
        jar.load(ignore_discard=True, ignore_expires=False)
    except (OSError, http.cookiejar.LoadError) as exc:
        raise DwError("无法读取手动 cookies.txt。", str(exc), cookie_related=True) from exc

    values: list[dict[str, Any]] = []
    for cookie in jar:
        domain = str(cookie.domain or "").lower().lstrip(".")
        if not hostname_matches(domain, {"xinpianchang.com"}):
            continue
        rest = cookie._rest or {}
        item: dict[str, Any] = {
            "name": str(cookie.name),
            "value": str(cookie.value),
            "domain": str(cookie.domain),
            "path": str(cookie.path or "/"),
            "secure": bool(cookie.secure),
            "httpOnly": "HTTPOnly" in rest or "HttpOnly" in rest,
        }
        if cookie.expires and cookie.expires > time.time():
            item["expires"] = float(cookie.expires)
        values.append(item)
    return values


def stop_xinpianchang_browser(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    with contextlib.suppress(OSError):
        process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)
    if process.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        with contextlib.suppress(OSError):
            process.kill()


def cleanup_xinpianchang_session(path: Path) -> None:
    for attempt in range(5):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt < 4:
                time.sleep(0.25 * (attempt + 1))
    warn(f"Chrome 临时配置未能删除，请手动删除残留路径：{path}")


def run_xinpianchang_chrome_bridge(url: str, policy: RequestPolicy) -> dict[str, Any]:
    if not IS_WINDOWS:
        raise DwError("新片场 Chrome 辅助模式目前仅支持 Windows 10/11。")
    chrome = chrome_executable_path()
    if chrome is None:
        raise DwError("未检测到 Google Chrome，无法启动新片场辅助模式。")
    if not DENO.is_file():
        raise DwError("未检测到 dw 管理的 Deno，无法启动新片场辅助模式。")

    ensure_runtime_directories()
    # Chrome 136+ ignores remote-debugging switches for the default profile.
    # A unique task directory also prevents any access to the user's daily profile.
    session_dir = Path(tempfile.mkdtemp(prefix="xpc-browser-", dir=TASKS_DIR))
    profile_dir = session_dir / "chrome-profile"
    bridge_file = session_dir / "xpc-chrome-bridge.js"
    bridge_file.write_text(XPC_CHROME_BRIDGE_SOURCE, encoding="utf-8", newline="\n")
    with contextlib.suppress(OSError):
        bridge_file.chmod(0o600)
    port = allocate_loopback_port()
    chrome_args = [
        str(chrome),
        f"--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        f"--remote-allow-origins=http://127.0.0.1:{port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-background-mode",
        "--new-window",
        "about:blank",
    ]
    if policy.direct:
        chrome_args.insert(-2, "--no-proxy-server")

    info("\n将启动一个仅供本次任务使用的独立 Chrome 窗口。")
    info("如果页面出现勾选、验证码或登录，请由你本人在该窗口中完成；dw 会自动继续。")
    info("该窗口不会读取日常 Chrome 配置，关闭后会自动删除临时配置和其中的 Cookies。")
    browser_process: subprocess.Popen[Any] | None = None
    helper_process: subprocess.Popen[Any] | None = None
    stderr_lines: list[str] = []
    reader: threading.Thread | None = None
    try:
        browser_process = subprocess.Popen(
            chrome_args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        helper_process = subprocess.Popen(
            [
                str(DENO),
                "run",
                "--no-config",
                "--quiet",
                f"--allow-net=127.0.0.1:{port}",
                str(bridge_file),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=ytdlp_environment(),
        )
        assert helper_process.stdin is not None
        assert helper_process.stdout is not None
        assert helper_process.stderr is not None
        reader = threading.Thread(
            target=process_stderr,
            args=(helper_process.stderr, stderr_lines),
            daemon=True,
        )
        reader.start()
        config = {
            "port": port,
            "url": url,
            "timeoutMs": 5 * 60 * 1000,
            "cookies": xinpianchang_cookie_params(policy),
        }
        helper_process.stdin.write(json.dumps(config, ensure_ascii=False))
        helper_process.stdin.close()
        output = helper_process.stdout.read()
        returncode = helper_process.wait()
        if reader is not None:
            reader.join(timeout=2)
        if returncode != 0:
            raise DwError("新片场 Chrome 辅助模式未能读取作品页。", "".join(stderr_lines))
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            raise DwError("新片场 Chrome 辅助模式没有返回媒体参数。")
        try:
            result = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise DwError("新片场 Chrome 辅助模式返回了无效数据。", str(exc)) from exc
        if not isinstance(result, dict) or not result.get("vid") or not result.get("appKey"):
            raise DwError("新片场 Chrome 辅助模式未找到媒体参数。")
        info("Chrome 页面验证已通过，正在读取新片场媒体版本 …")
        return result
    except KeyboardInterrupt as exc:
        if helper_process is not None and helper_process.poll() is None:
            with contextlib.suppress(OSError):
                helper_process.terminate()
        raise DownloadAborted from exc
    except OSError as exc:
        raise DwError("无法启动新片场 Chrome 辅助模式。", str(exc)) from exc
    finally:
        if helper_process is not None and helper_process.poll() is None:
            with contextlib.suppress(OSError):
                helper_process.terminate()
        if reader is not None:
            reader.join(timeout=1)
        if browser_process is not None:
            stop_xinpianchang_browser(browser_process)
        cleanup_xinpianchang_session(session_dir)


def xinpianchang_http_url(value: Any) -> str | None:
    text = str(value or "").strip()
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return None
    return text if parsed.scheme.lower() in {"http", "https"} and parsed.hostname else None


def xinpianchang_fps(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            result = float(numerator) / float(denominator)
        else:
            result = float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return result if result > 0 else None


def xinpianchang_progressive_formats(items: Any, webpage_url: str) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    formats: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        media_url = xinpianchang_http_url(item.get("url"))
        if not media_url:
            continue
        codec_parts = [part.strip() for part in str(item.get("codecName") or "").split("/")]
        vcodec = codec_parts[0] if codec_parts and codec_parts[0] else "unknown"
        acodec = codec_parts[1] if len(codec_parts) > 1 and codec_parts[1] else "unknown"
        bitrate = numeric(item.get("bitrate"))
        mime = str(item.get("mime") or "").lower()
        ext = "mp4" if "mp4" in mime else Path(urllib.parse.urlsplit(media_url).path).suffix.lstrip(".")
        height = int(numeric(item.get("height"))) or None
        profile = str(item.get("profile") or item.get("quality") or "").strip()
        label = f"{height}p" if height else str(index)
        formats.append(
            {
                "format_id": f"xpc-progressive-{label}-{index}",
                "format_note": profile,
                "url": media_url,
                "protocol": urllib.parse.urlsplit(media_url).scheme.lower(),
                "ext": ext or "mp4",
                "width": int(numeric(item.get("width"))) or None,
                "height": height,
                "fps": xinpianchang_fps(item.get("fps")),
                "vcodec": vcodec,
                "acodec": acodec,
                "tbr": bitrate / 1000 if bitrate else None,
                "filesize": int(numeric(item.get("filesize"))) or None,
                "http_headers": {"Range": "bytes=0-", "Referer": webpage_url},
            }
        )
    return formats


def xinpianchang_manifest_formats(
    resource: dict[str, Any],
    webpage_url: str,
    policy: RequestPolicy,
) -> list[dict[str, Any]]:
    formats: list[dict[str, Any]] = []
    extraction_policy = dataclasses.replace(policy, xpc_info=None, xpc_assist_attempted=True)
    for kind in ("dash", "hls"):
        item = resource.get(kind)
        manifest_url = xinpianchang_http_url(item.get("url")) if isinstance(item, dict) else None
        if not manifest_url:
            continue
        try:
            manifest_info = extract_single(manifest_url, extraction_policy)
        except DwError as exc:
            warn(f"新片场 {kind.upper()} 清单解析失败，继续列出其他真实视频流：{concise_error_reason(exc)}")
            continue
        manifest_formats = manifest_info.get("formats")
        if not isinstance(manifest_formats, list):
            manifest_formats = [manifest_info]
        for index, fmt in enumerate(manifest_formats, 1):
            if not isinstance(fmt, dict) or not xinpianchang_http_url(fmt.get("url")):
                continue
            prepared = dict(fmt)
            prepared["format_id"] = f"xpc-{kind}-{prepared.get('format_id') or index}"
            note = str(prepared.get("format_note") or "").strip()
            prepared["format_note"] = f"{kind.upper()} {note}".strip()
            headers = dict(prepared.get("http_headers") or {})
            headers.setdefault("Referer", webpage_url)
            prepared["http_headers"] = headers
            formats.append(prepared)
    return formats


def xinpianchang_thumbnails(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[tuple[str, Any]] = [("cover", data.get("cover"))]
    resource = data.get("resource") if isinstance(data.get("resource"), dict) else {}
    for name in ("snapshot", "sprite", "remainSprite"):
        candidates.append((name, resource.get(name)))

    thumbnails: list[dict[str, Any]] = []

    def collect(label: str, value: Any, width: Any = None, height: Any = None) -> None:
        if isinstance(value, str):
            image_url = xinpianchang_http_url(value)
            if image_url:
                thumbnails.append(
                    {
                        "id": f"{label}-{len(thumbnails) + 1}",
                        "url": image_url,
                        "width": int(numeric(width)) or None,
                        "height": int(numeric(height)) or None,
                    }
                )
            return
        if isinstance(value, list):
            for child in value:
                collect(label, child, width, height)
            return
        if not isinstance(value, dict):
            return
        item_width = value.get("width") or value.get("subImageWidth") or width
        item_height = value.get("height") or value.get("subImageHeight") or height
        if value.get("url"):
            collect(label, value.get("url"), item_width, item_height)
        for key in ("images", "originImages"):
            if key in value:
                collect(f"{label}-{key}", value.get(key), item_width, item_height)

    for label, value in candidates:
        collect(label, value)
    return thumbnails


def deduplicate_xinpianchang_formats(formats: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    used_ids: set[str] = set()
    for fmt in formats:
        key = (
            fmt.get("url"),
            fmt.get("width"),
            fmt.get("height"),
            fmt.get("fps"),
            fmt.get("vcodec"),
            fmt.get("acodec"),
            fmt.get("tbr"),
            fmt.get("filesize") or fmt.get("filesize_approx"),
        )
        if key in seen:
            continue
        seen.add(key)
        prepared = dict(fmt)
        base_id = str(prepared.get("format_id") or f"xpc-{len(result) + 1}")
        format_id = base_id
        suffix = 2
        while format_id in used_ids:
            format_id = f"{base_id}-{suffix}"
            suffix += 1
        prepared["format_id"] = format_id
        used_ids.add(format_id)
        result.append(prepared)
    return result


def xinpianchang_api_cookie_header(bridge: dict[str, Any], api_url: str) -> str:
    parsed = urllib.parse.urlsplit(api_url)
    api_hostname = (parsed.hostname or "").lower()
    api_path = parsed.path or "/"
    cookie_pairs: list[str] = []
    for cookie in bridge.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        raw_domain = str(cookie.get("domain") or "").lower()
        domain = raw_domain.lstrip(".")
        cookie_path = str(cookie.get("path") or "/")
        domain_applies = api_hostname == domain or (
            raw_domain.startswith(".") and api_hostname.endswith(f".{domain}")
        )
        path_prefix = cookie_path.rstrip("/") + "/"
        path_applies = api_path == cookie_path or api_path.startswith(path_prefix)
        expires = numeric(cookie.get("expires"))
        if (
            re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", name)
            and "\r" not in value
            and "\n" not in value
            and ";" not in value
            and domain_applies
            and cookie_path.startswith("/")
            and path_applies
            and not (expires > 0 and expires <= time.time())
        ):
            cookie_pairs.append(f"{name}={value}")
    return "; ".join(cookie_pairs)


def fetch_xinpianchang_media(
    webpage_url: str,
    bridge: dict[str, Any],
    policy: RequestPolicy,
) -> dict[str, Any]:
    vid = str(bridge.get("vid") or "")
    app_key = str(bridge.get("appKey") or "")
    api_url = (
        "https://mod-api.xinpianchang.com/mod/api/v2/media/"
        + urllib.parse.quote(vid, safe="")
        + "?"
        + urllib.parse.urlencode({"appKey": app_key})
    )
    browser_user_agent = str(bridge.get("userAgent") or "")
    if not browser_user_agent or len(browser_user_agent) > 512 or any(
        character in browser_user_agent for character in "\r\n"
    ):
        browser_user_agent = USER_AGENT
    headers = {
        "Accept": "application/json",
        "Referer": webpage_url,
        "User-Agent": browser_user_agent,
    }
    cookie_header = xinpianchang_api_cookie_header(bridge, api_url)
    if cookie_header:
        headers["Cookie"] = cookie_header
    request_value = urllib.request.Request(
        api_url,
        headers=headers,
    )
    try:
        with request_opener(policy).open(request_value, timeout=45) as response:
            payload = json.load(response)
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        http.client.HTTPException,
    ) as exc:
        raise DwError("无法读取新片场媒体接口。", str(exc)) from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise DwError("新片场媒体接口没有返回有效作品数据。")
    resource = data.get("resource") if isinstance(data.get("resource"), dict) else {}
    formats = xinpianchang_progressive_formats(resource.get("progressive"), webpage_url)
    formats.extend(xinpianchang_manifest_formats(resource, webpage_url, policy))
    formats = deduplicate_xinpianchang_formats(formats)
    for fmt in formats:
        format_headers = dict(fmt.get("http_headers") or {})
        format_headers.setdefault("Referer", webpage_url)
        format_headers.setdefault("User-Agent", browser_user_agent)
        fmt["http_headers"] = format_headers
    if not formats:
        raise DwError("新片场媒体接口没有返回可下载的真实视频流。")

    match = re.search(r"/(a\d+)", urllib.parse.urlsplit(webpage_url).path, flags=re.IGNORECASE)
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    thumbnails = xinpianchang_thumbnails(data)
    cover = xinpianchang_http_url(data.get("cover"))
    return {
        "id": match.group(1) if match else vid,
        "title": str(data.get("title") or bridge.get("title") or vid),
        "description": data.get("description"),
        "duration": numeric(data.get("duration")) or None,
        "categories": data.get("categories"),
        "tags": data.get("keywords"),
        "thumbnail": cover,
        "thumbnails": thumbnails,
        "uploader": owner.get("username"),
        "uploader_id": str(owner.get("id")) if owner.get("id") is not None else None,
        "formats": formats,
        "webpage_url": webpage_url,
        "original_url": webpage_url,
        "extractor": "XinpianchangBrowserAssist",
        "extractor_key": "XinpianchangBrowserAssist",
        "http_headers": {"Referer": webpage_url, "User-Agent": browser_user_agent},
        "_dw_xpc_browser_assisted": True,
    }


def xinpianchang_browser_assist(url: str, policy: RequestPolicy) -> dict[str, Any]:
    bridge = run_xinpianchang_chrome_bridge(url, policy)
    return fetch_xinpianchang_media(url, bridge, policy)


def looks_cookie_related(text: str) -> bool:
    lowered = text.lower()
    strong_patterns = (
        "fresh cookies",
        "cookies are no longer valid",
        "cookie file is not valid",
        "cookie has expired",
        "cookies have expired",
        "sign in to confirm",
        "login required",
        "authentication required",
        "not a bot",
    )
    return any(pattern in lowered for pattern in strong_patterns)


def run_capture(args: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            list(args),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=ytdlp_environment(),
            check=False,
        )
    except OSError as exc:
        raise DwError("无法启动 yt-dlp。", str(exc)) from exc
    if result.returncode != 0:
        detail = result.stderr or result.stdout
        raise DwError("yt-dlp 无法读取该链接。", detail, looks_cookie_related(detail))
    return result.stdout


def parse_json_output(output: str) -> dict[str, Any]:
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise DwError("yt-dlp 返回了无法解析的数据。", str(exc)) from exc
    if not isinstance(value, dict):
        raise DwError("yt-dlp 返回的数据类型异常。")
    return value


def extract_url(url: str, flat_playlist: bool, policy: RequestPolicy | None = None) -> dict[str, Any]:
    if policy is not None and policy.xpc_info is not None and is_xinpianchang_url(url):
        return dict(policy.xpc_info)
    args = ytdlp_base_args(policy, url)
    args.extend(("--dump-single-json", "--flat-playlist" if flat_playlist else "--no-flat-playlist", url))
    return parse_json_output(run_capture(args))


def extract_single(url: str, policy: RequestPolicy | None = None) -> dict[str, Any]:
    if policy is not None and policy.xpc_info is not None and is_xinpianchang_url(url):
        return dict(policy.xpc_info)
    args = ytdlp_base_args(policy, url)
    args.extend(("--dump-single-json", "--no-playlist", url))
    return parse_json_output(run_capture(args))


def extract_playlist_item(
    playlist_url: str, playlist_index: int, policy: RequestPolicy | None = None
) -> dict[str, Any]:
    args = ytdlp_base_args(policy, playlist_url)
    args.extend(
        (
            "--dump-single-json",
            "--no-flat-playlist",
            "--yes-playlist",
            "--playlist-items",
            str(playlist_index),
            playlist_url,
        )
    )
    root = parse_json_output(run_capture(args))
    entries = [entry for entry in root.get("entries") or [] if isinstance(entry, dict)]
    if not entries:
        raise DwError(f"播放列表第 {playlist_index} 项不可用。")
    return entries[0]


def is_playlist(info_dict: dict[str, Any]) -> bool:
    return isinstance(info_dict.get("entries"), list)


def printable_title(item: dict[str, Any], fallback: str) -> str:
    value = item.get("title") or item.get("id") or fallback
    return str(value).replace("\n", " ").replace("\r", " ")


def display_playlist(entries: list[dict[str, Any]]) -> None:
    total = len(entries)
    for page_start in range(0, total, PAGE_SIZE):
        page = entries[page_start : page_start + PAGE_SIZE]
        info(f"\n播放列表项目 {page_start + 1}-{page_start + len(page)} / {total}")
        info("编号   时长       标题")
        info("-" * 76)
        for offset, entry in enumerate(page, page_start + 1):
            duration = format_duration(entry.get("duration"))
            availability = " [不可用]" if entry.get("availability") in {"private", "premium_only", "subscriber_only"} else ""
            info(f"{offset:<6} {duration:<10} {printable_title(entry, '未知标题')}{availability}")
        if page_start + PAGE_SIZE < total:
            answer = input("按回车查看下一页，输入 q 停止浏览并进入选择：").strip().lower()
            if answer == "q":
                break


def parse_number_selection(text: str, maximum: int, allow_all: bool = True) -> list[int]:
    cleaned = text.strip().lower().replace("，", ",")
    if allow_all and cleaned in {"a", "all", "全部"}:
        return list(range(1, maximum + 1))
    if not cleaned:
        raise ValueError("不能为空")
    selected: list[int] = []
    for part in cleaned.split(","):
        part = part.strip()
        if not part:
            raise ValueError("存在空编号")
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if match:
            start, end = (int(match.group(1)), int(match.group(2)))
            if start > end:
                raise ValueError("范围起点不能大于终点")
            values = range(start, end + 1)
        elif part.isdigit():
            values = (int(part),)
        else:
            raise ValueError(f"无法识别：{part}")
        for value in values:
            if not 1 <= value <= maximum:
                raise ValueError(f"编号 {value} 超出范围 1-{maximum}")
            if value not in selected:
                selected.append(value)
    return selected


def ask_number_selection(prompt: str, maximum: int, allow_all: bool = True, multiple: bool = True) -> list[int]:
    while True:
        raw = input(prompt).strip()
        try:
            values = parse_number_selection(raw, maximum, allow_all=allow_all)
            if not multiple and len(values) != 1:
                raise ValueError("这里只能选择一个编号")
            return values
        except ValueError as exc:
            warn(f"输入无效：{exc}")


def ask_yes_no(prompt: str, default: bool | None = None) -> bool:
    suffix = " [Y/n]：" if default is True else " [y/N]：" if default is False else " [y/n]："
    while True:
        answer = input(prompt + suffix).strip().lower()
        if not answer and default is not None:
            return default
        if answer in {"y", "yes", "是", "1"}:
            return True
        if answer in {"n", "no", "否", "0"}:
            return False
        warn("请输入 y 或 n。")


def numeric(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def format_size(value: Any) -> str:
    size = numeric(value)
    if size <= 0:
        return "未知"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{size:.1f} {units[index]}"


def format_duration(value: Any) -> str:
    seconds = int(numeric(value))
    if seconds <= 0:
        return "未知"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def media_size(item: dict[str, Any]) -> Any:
    return item.get("filesize") or item.get("filesize_approx")


def has_video(fmt: dict[str, Any]) -> bool:
    codec = str(fmt.get("vcodec") or "none").lower()
    protocol = str(fmt.get("protocol") or "").lower()
    note = str(fmt.get("format_note") or "").lower()
    ext = str(fmt.get("ext") or "").lower()
    return (
        codec not in {"", "none"}
        and protocol != "mhtml"
        and ext not in {"mhtml", "none"}
        and "storyboard" not in note
        and bool(fmt.get("url") or fmt.get("manifest_url"))
    )


def has_audio(fmt: dict[str, Any]) -> bool:
    return str(fmt.get("acodec") or "none").lower() not in {"", "none"}


def video_codec_family(value: Any) -> str:
    codec = normalized_codec(value)
    if codec.startswith(("avc", "h264")):
        return "h264"
    if codec.startswith(("hev", "hvc", "h265", "bytevc1")):
        return "h265"
    if codec.startswith(("av01", "av1")):
        return "av1"
    if codec.startswith(("vp09", "vp9")):
        return "vp9"
    if codec.startswith(("vp08", "vp8")):
        return "vp8"
    return codec or "other"


def format_is_marked_watermarked(fmt: dict[str, Any]) -> bool:
    """Identify extractor-provided streams explicitly marked as watermarked."""
    description = " ".join(
        str(fmt.get(field) or "")
        for field in ("format_note", "format", "resolution")
    ).lower()
    return "watermark" in description or "水印" in description


def video_formats(info_dict: dict[str, Any]) -> list[dict[str, Any]]:
    formats = [dict(fmt) for fmt in info_dict.get("formats") or [] if isinstance(fmt, dict) and has_video(fmt)]
    # yt-dlp labels known watermarked variants (for example TikTok/Douyin's
    # download_addr) and gives clean playback streams a better preference.  Our
    # interactive sort must preserve that safety property instead of moving a
    # larger watermarked variant above a clean one.
    clean_formats = [fmt for fmt in formats if not format_is_marked_watermarked(fmt)]
    if clean_formats:
        formats = clean_formats

    def sort_key(fmt: dict[str, Any]) -> tuple[Any, ...]:
        ext = str(fmt.get("ext") or "other").lower()
        group = VIDEO_CONTAINER_ORDER.get(ext, 2)
        other = "" if group < 2 else ext
        codec_family = video_codec_family(fmt.get("vcodec"))
        codec_group = VIDEO_CODEC_ORDER.get(codec_family, len(VIDEO_CODEC_ORDER))
        return (
            group,
            other,
            codec_group,
            "" if codec_group < len(VIDEO_CODEC_ORDER) else codec_family,
            -numeric(media_size(fmt)),
            -numeric(fmt.get("height")),
            -numeric(fmt.get("width")),
            -numeric(fmt.get("fps")),
            -numeric(fmt.get("tbr") or fmt.get("vbr")),
            str(fmt.get("format_id") or ""),
        )

    return sorted(formats, key=sort_key)


def audio_formats(info_dict: dict[str, Any]) -> list[dict[str, Any]]:
    formats = [
        dict(fmt)
        for fmt in info_dict.get("formats") or []
        if isinstance(fmt, dict) and has_audio(fmt) and not has_video(fmt)
    ]
    return sorted(
        formats,
        key=lambda fmt: (
            str(fmt.get("language") or "未知").lower(),
            -numeric(fmt.get("abr") or fmt.get("tbr")),
            -numeric(fmt.get("asr")),
            -numeric(media_size(fmt)),
            str(fmt.get("format_id") or ""),
        ),
    )


def resolution(fmt: dict[str, Any]) -> str:
    width, height = fmt.get("width"), fmt.get("height")
    if width and height:
        return f"{width}x{height}"
    return str(fmt.get("resolution") or fmt.get("format_note") or "未知")


def codec_short(value: Any) -> str:
    text = str(value or "未知")
    return text if len(text) <= 18 else text[:17] + "…"


def display_video_formats(formats: list[dict[str, Any]]) -> None:
    info("\n可用视频版本（MP4、WebM、其他；组内 H.264 优先，并按编码和预计大小降序）：")
    if formats and all(format_is_marked_watermarked(fmt) for fmt in formats):
        warn("该网站只返回了明确标记为带画面水印的视频流；无损封装无法删除已烧进画面的水印。")
    last_ext = None
    for index, fmt in enumerate(formats, 1):
        ext = str(fmt.get("ext") or "其他").upper()
        if ext != last_ext:
            info(f"\n[{ext}]")
            info("编号  类型        分辨率       FPS   视频编码            HDR        码率       预计大小    格式ID")
            info("-" * 108)
            last_ext = ext
        media_type = "视频+音频" if has_audio(fmt) else "纯视频"
        fps = f"{numeric(fmt.get('fps')):g}" if fmt.get("fps") else "未知"
        bitrate_value = fmt.get("tbr") or fmt.get("vbr")
        bitrate = f"{numeric(bitrate_value):g}k" if bitrate_value else "未知"
        hdr = str(fmt.get("dynamic_range") or "SDR")
        info(
            f"{index:<5} {media_type:<11} {resolution(fmt):<12} {fps:<5} "
            f"{codec_short(fmt.get('vcodec')):<19} {hdr:<10} {bitrate:<10} "
            f"{format_size(media_size(fmt)):<11} {fmt.get('format_id', '未知')}"
        )


def display_audio_formats(formats: list[dict[str, Any]]) -> None:
    languages = sorted({str(fmt.get("language") or "未知") for fmt in formats})
    info("\n可用独立音频版本：")
    if len(languages) > 1:
        info("检测到多语言音轨，可输入多个编号（例如 1,3,5）下载多音轨。")
        info("语言：" + "、".join(languages))
    info("编号  语言          编码               码率       声道    采样率      预计大小    格式ID")
    info("-" * 100)
    for index, fmt in enumerate(formats, 1):
        abr = fmt.get("abr") or fmt.get("tbr")
        bitrate = f"{numeric(abr):g}k" if abr else "未知"
        channels = fmt.get("audio_channels") or "未知"
        asr = f"{numeric(fmt.get('asr')):g}Hz" if fmt.get("asr") else "未知"
        info(
            f"{index:<5} {str(fmt.get('language') or '未知'):<13} "
            f"{codec_short(fmt.get('acodec')):<18} {bitrate:<10} {str(channels):<7} "
            f"{asr:<11} {format_size(media_size(fmt)):<11} {fmt.get('format_id', '未知')}"
        )


def deduplicated_thumbnails(info_dict: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for thumbnail in info_dict.get("thumbnails") or []:
        if not isinstance(thumbnail, dict) or not thumbnail.get("url"):
            continue
        key = (
            thumbnail.get("url"),
            thumbnail.get("width"),
            thumbnail.get("height"),
            thumbnail.get("filesize") or thumbnail.get("filesize_approx"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(thumbnail))
    result.sort(
        key=lambda thumb: (
            -numeric(thumb.get("width")) * numeric(thumb.get("height")),
            -numeric(thumb.get("filesize") or thumb.get("filesize_approx")),
            str(thumb.get("id") or ""),
        )
    )
    for index, thumbnail in enumerate(result, 1):
        thumbnail["_dw_index"] = index
    return result


def display_thumbnails(thumbnails: list[dict[str, Any]]) -> None:
    info("\n可下载图片（完全重复项已去除）：")
    info("编号  分辨率       格式       预计大小    图片ID")
    info("-" * 66)
    for index, thumbnail in enumerate(thumbnails, 1):
        ext = thumbnail.get("ext") or Path(urllib.parse.urlparse(str(thumbnail.get("url"))).path).suffix.lstrip(".") or "未知"
        info(
            f"{index:<5} {resolution(thumbnail):<12} {str(ext):<10} "
            f"{format_size(thumbnail.get('filesize') or thumbnail.get('filesize_approx')):<11} "
            f"{thumbnail.get('id', '未知')}"
        )


def normalized_codec(codec: Any) -> str:
    return str(codec or "none").lower().replace("-", "")


def codec_compatible(container: str, video_codec: Any, audio_codecs: Iterable[Any]) -> tuple[bool, str]:
    container = container.lower()
    video = normalized_codec(video_codec)
    audios = [normalized_codec(codec) for codec in audio_codecs if normalized_codec(codec) != "none"]
    if container == "mkv":
        return True, ""
    if container == "mp4":
        video_ok = video.startswith(("avc1", "h264", "hev1", "hvc1", "hevc", "av01", "mpeg4"))
        allowed_audio = ("mp4a", "aac", "alac", "mp3", "ac3", "eac3", "ec3")
        audio_ok = all(codec.startswith(allowed_audio) for codec in audios)
    elif container == "webm":
        video_ok = video.startswith(("vp8", "vp9", "vp09", "av01"))
        audio_ok = all(codec.startswith(("opus", "vorbis")) for codec in audios)
    else:
        return True, ""
    reasons = []
    if not video_ok:
        reasons.append(f"视频编码 {video_codec or '未知'}")
    if not audio_ok:
        incompatible = ", ".join(str(codec) for codec in audio_codecs if normalized_codec(codec) != "none")
        reasons.append(f"音频编码 {incompatible or '未知'}")
    return not reasons, "、".join(reasons) + (f" 不适合 {container.upper()}" if reasons else "")


def selected_audio_codecs(selection_video: dict[str, Any], audios: list[dict[str, Any]], use_embedded: bool) -> list[Any]:
    if use_embedded:
        return [selection_video.get("acodec")]
    return [audio.get("acodec") for audio in audios]


def resolve_auto_container(video: dict[str, Any], audios: list[dict[str, Any]], use_embedded: bool) -> str:
    ext = str(video.get("ext") or "").lower()
    if use_embedded and not audios and ext in SUPPORTED_REMUX_CONTAINERS:
        return ext
    audio_codecs = selected_audio_codecs(video, audios, use_embedded)
    if ext in {"mp4", "webm"} and codec_compatible(ext, video.get("vcodec"), audio_codecs)[0]:
        return ext
    return "mkv"


def recommended_container(video: dict[str, Any], audios: list[dict[str, Any]], use_embedded: bool) -> str:
    if len(audios) > 1:
        return "mkv"
    return resolve_auto_container(video, audios, use_embedded)


def ask_container(video: dict[str, Any], audios: list[dict[str, Any]], use_embedded: bool) -> tuple[str, str]:
    recommendation = recommended_container(video, audios, use_embedded)
    choices = {"1": "auto", "2": "mp4", "3": "mkv", "4": "webm"}
    while True:
        info("\n最终文件容器：")
        info(f"1. 保持原格式/自动无损封装（当前将使用 {resolve_auto_container(video, audios, use_embedded).upper()}）")
        info("2. MP4")
        info("3. MKV")
        info("4. WebM")
        info(f"推荐：{recommendation.upper()}")
        choice = input("请选择 [1-4，直接回车默认 1]：").strip() or "1"
        mode = choices.get(choice)
        if mode is None:
            warn("请输入 1、2、3 或 4。")
            continue
        resolved = resolve_auto_container(video, audios, use_embedded) if mode == "auto" else mode
        if mode != "auto":
            compatible, reason = codec_compatible(
                resolved,
                video.get("vcodec"),
                selected_audio_codecs(video, audios, use_embedded),
            )
            if not compatible:
                warn(f"{reason}；不会进行转码。建议改选 MKV。")
                continue
        return mode, resolved


def format_signature(fmt: dict[str, Any], kind: str) -> dict[str, Any]:
    if kind == "video":
        return {
            "ext": str(fmt.get("ext") or "").lower(),
            "width": fmt.get("width"),
            "height": fmt.get("height"),
            "fps": fmt.get("fps"),
            "vcodec": fmt.get("vcodec"),
            "dynamic_range": fmt.get("dynamic_range"),
            "tbr": fmt.get("tbr") or fmt.get("vbr"),
            "language": fmt.get("language"),
            "acodec": fmt.get("acodec"),
            "abr": fmt.get("abr"),
        }
    return {
        "ext": str(fmt.get("ext") or "").lower(),
        "language": fmt.get("language") or "未知",
        "acodec": fmt.get("acodec"),
        "abr": fmt.get("abr") or fmt.get("tbr"),
        "asr": fmt.get("asr"),
        "audio_channels": fmt.get("audio_channels"),
    }


def thumbnail_signature(thumbnail: dict[str, Any]) -> dict[str, Any]:
    return {
        "width": thumbnail.get("width"),
        "height": thumbnail.get("height"),
        "filesize": thumbnail.get("filesize") or thumbnail.get("filesize_approx"),
        "rank": thumbnail.get("_dw_index"),
    }


def choose_initial_selection(info_dict: dict[str, Any]) -> tuple[ItemSelection, SelectionProfile]:
    videos = video_formats(info_dict)
    if not videos:
        raise DwError("没有找到可播放的视频格式。")
    display_video_formats(videos)
    chosen_index = ask_number_selection("请选择一个视频编号：", len(videos), allow_all=False, multiple=False)[0]
    video = videos[chosen_index - 1]

    audios: list[dict[str, Any]] = []
    use_embedded = False
    replace_embedded = False
    embedded_template: dict[str, Any] | None = None
    if has_audio(video):
        embedded_template = format_signature(video, "audio")
        use_embedded = ask_yes_no("所选视频自带音频，是否使用它", default=True)
        replace_embedded = not use_embedded

    if not use_embedded:
        available_audio = audio_formats(info_dict)
        if not available_audio:
            raise DwError("没有找到可单独选择的音频格式。")
        display_audio_formats(available_audio)
        indexes = ask_number_selection(
            "请选择音频编号（多个用逗号，全部输入 a）：",
            len(available_audio),
            allow_all=True,
            multiple=True,
        )
        audios = [available_audio[index - 1] for index in indexes]

    container_mode, resolved = ask_container(video, audios, use_embedded)

    thumbnails = deduplicated_thumbnails(info_dict)
    chosen_thumbnails: list[dict[str, Any]] = []
    all_thumbnails = False
    if ask_yes_no("是否下载图片", default=False):
        if not thumbnails:
            warn("该视频没有可下载图片。")
        else:
            display_thumbnails(thumbnails)
            while True:
                raw = input("请选择图片编号（多个用逗号，全部输入 a）：").strip()
                try:
                    indexes = parse_number_selection(raw, len(thumbnails), allow_all=True)
                    break
                except ValueError as exc:
                    warn(f"输入无效：{exc}")
            all_thumbnails = raw.lower() in {"a", "all", "全部"}
            chosen_thumbnails = [thumbnails[index - 1] for index in indexes]

    selection = ItemSelection(
        video=video,
        audios=audios,
        use_embedded_audio=use_embedded,
        replace_embedded_audio=replace_embedded,
        container_mode=container_mode,
        resolved_container=resolved,
        thumbnails=chosen_thumbnails,
    )
    profile = SelectionProfile(
        video=format_signature(video, "video"),
        audios=[format_signature(audio, "audio") for audio in audios],
        embedded_audio=embedded_template,
        prefer_embedded_audio=use_embedded,
        replace_embedded_audio=replace_embedded,
        container_mode=container_mode,
        thumbnails=[thumbnail_signature(thumbnail) for thumbnail in chosen_thumbnails],
        all_thumbnails=all_thumbnails,
    )
    return selection, profile


def near(value: Any, target: Any, tolerance: float = 0.01) -> bool:
    if value is None and target is None:
        return True
    if value is None or target is None:
        return False
    return abs(numeric(value) - numeric(target)) <= max(1.0, abs(numeric(target)) * tolerance)


def exact_video_match(fmt: dict[str, Any], target: dict[str, Any]) -> bool:
    return (
        str(fmt.get("ext") or "").lower() == target.get("ext")
        and fmt.get("width") == target.get("width")
        and fmt.get("height") == target.get("height")
        and near(fmt.get("fps"), target.get("fps"))
        and normalized_codec(fmt.get("vcodec")) == normalized_codec(target.get("vcodec"))
        and str(fmt.get("dynamic_range") or "SDR") == str(target.get("dynamic_range") or "SDR")
    )


def match_video_format(formats: list[dict[str, Any]], target: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    exact = [fmt for fmt in formats if exact_video_match(fmt, target)]
    if exact:
        exact.sort(key=lambda fmt: abs(numeric(fmt.get("tbr") or fmt.get("vbr")) - numeric(target.get("tbr"))))
        return exact[0], "完全匹配"

    target_height = numeric(target.get("height"))
    candidates = [fmt for fmt in formats if not target_height or numeric(fmt.get("height")) <= target_height]
    if not candidates:
        return None, "没有不超过目标清晰度的格式"

    def score(fmt: dict[str, Any]) -> tuple[Any, ...]:
        ext_penalty = 0 if str(fmt.get("ext") or "").lower() == target.get("ext") else 1
        codec_penalty = 0 if normalized_codec(fmt.get("vcodec")) == normalized_codec(target.get("vcodec")) else 1
        hdr_penalty = 0 if str(fmt.get("dynamic_range") or "SDR") == str(target.get("dynamic_range") or "SDR") else 1
        return (
            ext_penalty,
            codec_penalty,
            target_height - numeric(fmt.get("height")) if target_height else 0,
            abs(numeric(fmt.get("fps")) - numeric(target.get("fps"))),
            hdr_penalty,
            abs(numeric(fmt.get("tbr") or fmt.get("vbr")) - numeric(target.get("tbr"))),
        )

    return min(candidates, key=score), "最接近匹配"


def language_key(value: Any) -> str:
    return str(value or "未知").strip().lower().replace("_", "-")


def base_language(value: Any) -> str:
    return language_key(value).split("-", 1)[0]


def match_audio_formats(
    formats: list[dict[str, Any]], targets: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    chosen: list[dict[str, Any]] = []
    missing: list[str] = []
    used_ids: set[str] = set()
    for target in targets:
        target_language = language_key(target.get("language"))
        if target_language == "未知":
            candidates = formats
        else:
            exact_language = [fmt for fmt in formats if language_key(fmt.get("language")) == target_language]
            candidates = exact_language or [
                fmt for fmt in formats if base_language(fmt.get("language")) == base_language(target_language)
            ]
        candidates = [fmt for fmt in candidates if str(fmt.get("format_id")) not in used_ids]
        if not candidates:
            missing.append(str(target.get("language") or "未知"))
            continue

        def score(fmt: dict[str, Any]) -> tuple[Any, ...]:
            return (
                0 if normalized_codec(fmt.get("acodec")) == normalized_codec(target.get("acodec")) else 1,
                abs(numeric(fmt.get("abr") or fmt.get("tbr")) - numeric(target.get("abr"))),
                abs(numeric(fmt.get("asr")) - numeric(target.get("asr"))),
                abs(numeric(fmt.get("audio_channels")) - numeric(target.get("audio_channels"))),
            )

        selected = min(candidates, key=score)
        chosen.append(selected)
        used_ids.add(str(selected.get("format_id")))
    return chosen, missing


def match_thumbnails(
    thumbnails: list[dict[str, Any]], targets: list[dict[str, Any]], all_thumbnails: bool
) -> list[dict[str, Any]]:
    if all_thumbnails:
        return thumbnails
    chosen: list[dict[str, Any]] = []
    used: set[int] = set()
    for target in targets:
        available = [(index, thumb) for index, thumb in enumerate(thumbnails) if index not in used]
        if not available:
            break
        target_area = numeric(target.get("width")) * numeric(target.get("height"))
        target_size = numeric(target.get("filesize"))
        target_rank = numeric(target.get("rank"))

        def score(pair: tuple[int, dict[str, Any]]) -> tuple[Any, ...]:
            index, thumb = pair
            area = numeric(thumb.get("width")) * numeric(thumb.get("height"))
            size = numeric(thumb.get("filesize") or thumb.get("filesize_approx"))
            return (
                abs(area - target_area) if target_area else abs((index + 1) - target_rank),
                abs(size - target_size) if target_size else 0,
                abs((index + 1) - target_rank),
            )

        index, selected = min(available, key=score)
        used.add(index)
        chosen.append(selected)
    return chosen


def choose_matched_selection(info_dict: dict[str, Any], profile: SelectionProfile) -> ItemSelection:
    videos = video_formats(info_dict)
    if not videos:
        raise DwError("没有找到可播放的视频格式。")
    video, match_note = match_video_format(videos, profile.video)
    if video is None:
        warn("无法自动匹配视频：" + match_note)
        display_video_formats(videos)
        index = ask_number_selection("请为当前视频选择一个视频编号：", len(videos), allow_all=False, multiple=False)[0]
        video = videos[index - 1]
        match_note = "手动选择"
    info(f"视频格式：{match_note} -> {video.get('format_id')} / {resolution(video)} / {video.get('ext')}")

    use_embedded = profile.prefer_embedded_audio and has_audio(video)
    replace_embedded = has_audio(video) and not use_embedded
    audio_targets = profile.audios or ([profile.embedded_audio] if profile.embedded_audio else [])
    audios: list[dict[str, Any]] = []
    missing: list[str] = []
    if not use_embedded:
        audios, missing = match_audio_formats(audio_formats(info_dict), [target for target in audio_targets if target])
        if missing:
            warn("缺少音频语言：" + "、".join(missing) + "；将下载当前可用语言。")
        if not audios:
            raise DwError("统一音频规则在当前视频中没有任何可用匹配。")

    if profile.container_mode == "auto":
        container_mode = "auto"
        resolved = resolve_auto_container(video, audios, use_embedded)
    else:
        container_mode = profile.container_mode
        resolved = profile.container_mode
        compatible, reason = codec_compatible(
            resolved, video.get("vcodec"), selected_audio_codecs(video, audios, use_embedded)
        )
        if not compatible:
            warn(f"统一容器规则无法用于当前视频：{reason}")
            container_mode, resolved = ask_container(video, audios, use_embedded)

    available_thumbnails = deduplicated_thumbnails(info_dict)
    thumbs = match_thumbnails(available_thumbnails, profile.thumbnails, profile.all_thumbnails)
    expected_thumbnails = len(available_thumbnails) if profile.all_thumbnails else len(profile.thumbnails)
    if len(thumbs) < expected_thumbnails:
        warn(f"当前视频只能匹配 {len(thumbs)}/{expected_thumbnails} 张所选图片。")
    return ItemSelection(
        video=video,
        audios=audios,
        use_embedded_audio=use_embedded,
        replace_embedded_audio=replace_embedded,
        container_mode=container_mode,
        resolved_container=resolved,
        thumbnails=thumbs,
        missing_languages=missing,
    )


def sanitize_filename(title: str, max_bytes: int = 180) -> str:
    title = unicodedata.normalize("NFC", str(title))
    title = re.sub(r"[\x00-\x1f\x7f]", "_", title)
    title = re.sub(r"[<>:\"/\\|?*]", "_", title)
    title = re.sub(r"\s+", " ", title).strip(" .")
    if title in {"", ".", ".."}:
        title = "未命名视频"
    if title.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        title = f"_{title}"
    encoded = title.encode("utf-8")
    if len(encoded) <= max_bytes:
        return title
    output: list[str] = []
    used = 0
    for character in title:
        length = len(character.encode("utf-8"))
        if used + length > max_bytes:
            break
        output.append(character)
        used += length
    return "".join(output).rstrip(" .") or "未命名视频"


def unique_output_path(base_title: str, extension: str, reserved: set[Path]) -> Path:
    safe_title = sanitize_filename(base_title)
    extension = extension.lstrip(".").lower() or "bin"
    candidate = OUTPUT_DIR / f"{safe_title}.{extension}"
    counter = 1
    while candidate.exists() or candidate in reserved:
        candidate = OUTPUT_DIR / f"{safe_title} ({counter}).{extension}"
        counter += 1
    reserved.add(candidate)
    return candidate


def move_file_exclusive(source: Path, destination: Path) -> None:
    """Move a file without ever replacing an existing destination."""
    try:
        os.link(source, destination)
        source.unlink()
        return
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise FileExistsError(destination) from exc

    try:
        with source.open("rb") as input_file, destination.open("xb") as output_file:
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            output_file.flush()
            os.fsync(output_file.fileno())
        shutil.copystat(source, destination, follow_symlinks=False)
        source.unlink()
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            destination.unlink()
        raise


def process_stderr(stream: Any, buffer: list[str]) -> None:
    for line in iter(stream.readline, ""):
        buffer.append(line)
        if len(buffer) > 200:
            del buffer[:50]
        print(line, end="", file=sys.stderr, flush=True)
    stream.close()


def process_group_options(live: bool) -> dict[str, Any]:
    if not live:
        return {}
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def signal_download_process(process: subprocess.Popen[Any], grouped: bool, force: bool) -> None:
    """Stop yt-dlp and its FFmpeg child without invoking a shell."""
    if process.poll() is not None:
        return
    if IS_WINDOWS:
        if force:
            try:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                with contextlib.suppress(OSError):
                    process.kill()
            return
        if grouped and hasattr(signal, "CTRL_BREAK_EVENT"):
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                return
            except OSError:
                pass
        with contextlib.suppress(OSError):
            process.terminate()
        return
    if grouped:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGINT)
    elif force:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    else:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()


def run_download_process(args: list[str], live: bool) -> ProcessResult:
    stderr_lines: list[str] = []
    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=ytdlp_environment(),
            **process_group_options(live),
        )
    except OSError as exc:
        raise DwError("无法启动下载进程。", str(exc)) from exc
    assert process.stderr is not None
    reader = threading.Thread(target=process_stderr, args=(process.stderr, stderr_lines), daemon=True)
    reader.start()

    if not live:
        try:
            returncode = process.wait()
        except KeyboardInterrupt as exc:
            signal_download_process(process, grouped=False, force=False)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
            if process.poll() is None:
                signal_download_process(process, grouped=False, force=True)
            raise DownloadAborted from exc
        finally:
            reader.join(timeout=2)
        return ProcessResult(returncode, "".join(stderr_lines))

    first_interrupt = 0.0
    abort_requested = False
    old_handler = signal.getsignal(signal.SIGINT)

    def live_interrupt(_signum: int, _frame: Any) -> None:
        nonlocal first_interrupt, abort_requested
        now = time.monotonic()
        if not first_interrupt:
            first_interrupt = now
            info("\n正在停止直播录制并封装文件；5 秒内再次按 Ctrl+C 将删除本次任务 …")
            signal_download_process(process, grouped=True, force=False)
            return
        if now - first_interrupt <= 5:
            abort_requested = True
            signal_download_process(process, grouped=True, force=True)

    signal.signal(signal.SIGINT, live_interrupt)
    try:
        while process.poll() is None:
            if abort_requested:
                raise LiveAbort
            time.sleep(0.2)
        if abort_requested:
            raise LiveAbort
        return ProcessResult(process.returncode or 0, "".join(stderr_lines), live_stopped=bool(first_interrupt))
    finally:
        signal.signal(signal.SIGINT, old_handler)
        if process.poll() is None:
            signal_download_process(process, grouped=True, force=True)
        reader.join(timeout=2)


def media_candidates(task_dir: Path) -> list[Path]:
    excluded_suffixes = {".part", ".ytdl", ".txt", ".json", ".description"}
    candidates = []
    for path in task_dir.iterdir():
        if not path.is_file() or path.name.startswith("result-path"):
            continue
        if path.suffix.lower() in excluded_suffixes or ".part" in path.name:
            continue
        candidates.append(path)
    return sorted(candidates, key=lambda path: path.stat().st_size, reverse=True)


def strip_dw_private_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_dw_private_fields(child)
            for key, child in value.items()
            if not str(key).startswith("_dw_")
        }
    if isinstance(value, list):
        return [strip_dw_private_fields(child) for child in value]
    return value


def build_download_args(
    url: str,
    selection: ItemSelection,
    task_dir: Path,
    result_file: Path,
    live: bool,
    policy: RequestPolicy | None = None,
    info_dict: dict[str, Any] | None = None,
) -> list[str]:
    selector_parts = [str(selection.video.get("format_id"))]
    if not selection.use_embedded_audio:
        selector_parts.extend(str(audio.get("format_id")) for audio in selection.audios)
    selector = "+".join(selector_parts)
    # A combined source selected for video may carry an audio codec that the
    # requested final container cannot accept.  When replacing that embedded
    # track, merge into MKV first, then map only the requested tracks into the
    # final container in normalize_external_audio().
    download_container = "mkv" if selection.replace_embedded_audio else selection.resolved_container
    args = ytdlp_base_args(policy, url)
    args.extend(
        (
            "--no-playlist",
            "--newline",
            "--no-write-subs",
            "--no-write-auto-subs",
            "--no-write-thumbnail",
            "--no-write-info-json",
            "--no-write-description",
            "--no-embed-metadata",
            "--no-embed-chapters",
            "--no-embed-info-json",
            "--no-embed-thumbnail",
            "--no-embed-subs",
            "--no-keep-video",
            "--audio-multistreams",
            "--retries",
            "10",
            "--fragment-retries",
            "10",
            "--concurrent-fragments",
            str(DOWNLOAD_CONNECTIONS),
            "--paths",
            f"home:{task_dir}",
            "--paths",
            f"temp:{task_dir / 'temp'}",
            "--output",
            "media.%(ext)s",
            "--print-to-file",
            "after_move:filepath",
            str(result_file),
            "--format",
            selector,
            "--merge-output-format",
            download_container,
            "--remux-video",
            download_container,
        )
    )
    if not live:
        # aria2c can split ordinary HTTP(S) files across eight connections.
        # Segmented DASH/HLS media stays on yt-dlp's native downloader, where
        # --concurrent-fragments provides the corresponding eight-way download.
        args.extend(
            (
                "--downloader",
                "aria2c",
                "--downloader",
                "dash,m3u8:native",
                "--downloader-args",
                (
                    f"aria2c:-x {DOWNLOAD_CONNECTIONS} -s {DOWNLOAD_CONNECTIONS} "
                    f"-j {DOWNLOAD_CONNECTIONS} -k 1M --file-allocation=none "
                    "--summary-interval=1"
                ),
            )
        )
    if live:
        args.append("--no-live-from-start")
    if info_dict is not None and info_dict.get("_dw_xpc_browser_assisted"):
        info_file = task_dir / "xinpianchang.info.json"
        write_json_atomic(info_file, strip_dw_private_fields(info_dict))
        args.extend(("--load-info-json", str(info_file)))
    else:
        args.append(url)
    return args


def download_media(
    url: str,
    selection: ItemSelection,
    task_dir: Path,
    live: bool,
    policy: RequestPolicy | None = None,
    info_dict: dict[str, Any] | None = None,
) -> Path:
    result_file = task_dir / "result-path.txt"
    args = build_download_args(url, selection, task_dir, result_file, live, policy, info_dict)

    result = run_download_process(args, live=live)
    candidates: list[Path] = []
    if result_file.exists():
        for line in result_file.read_text(encoding="utf-8", errors="replace").splitlines():
            path = Path(line.strip())
            if path.is_file() and task_dir in path.parents:
                candidates.append(path)
    if not candidates:
        candidates = media_candidates(task_dir)
    if result.returncode != 0 and not (result.live_stopped and candidates):
        raise DwError("下载或合并失败。", result.stderr, looks_cookie_related(result.stderr))
    if not candidates:
        raise DwError("下载结束，但没有找到最终媒体文件。", result.stderr)
    return candidates[0]


def ffprobe_audio_count(path: Path) -> int:
    result = subprocess.run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "json",
            str(path),
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise DwError("FFprobe 无法验证下载文件。", result.stderr)
    try:
        return len(json.loads(result.stdout).get("streams") or [])
    except json.JSONDecodeError as exc:
        raise DwError("FFprobe 返回了无法解析的数据。", str(exc)) from exc


def validate_media(path: Path) -> None:
    result = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise DwError("最终媒体文件校验失败。", result.stderr)


def normalize_external_audio(path: Path, selection: ItemSelection) -> Path:
    if not selection.audios:
        return path
    count = ffprobe_audio_count(path)
    wanted = len(selection.audios)
    if count < wanted:
        raise DwError(f"合并后的文件只有 {count} 条音轨，少于所选的 {wanted} 条。")
    first = count - wanted
    temp = path.with_name(f"normalized-{uuid.uuid4().hex}.{selection.resolved_container}")
    command = [str(FFMPEG), "-y", "-v", "error", "-i", str(path), "-map", "0:v:0"]
    for index, audio in enumerate(selection.audios):
        command.extend(("-map", f"0:a:{first + index}"))
    command.extend(("-map_metadata", "-1", "-map_chapters", "-1", "-c", "copy"))
    for index, audio in enumerate(selection.audios):
        language = str(audio.get("language") or "und").split("-", 1)[0][:3]
        command.extend((f"-metadata:s:a:{index}", f"language={language}"))
    command.append(str(temp))
    result = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    if result.returncode != 0:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()
        raise DwError("替换或整理音轨失败。", result.stderr)
    path.unlink()
    return temp


def ensure_final_container(path: Path, selection: ItemSelection) -> Path:
    # Always perform one stream-copy remux, even when the extension already
    # matches.  This strips container metadata, chapters, cover attachments and
    # platform tags without re-encoding the audio or video streams.
    target = path.with_name(f"remuxed-{uuid.uuid4().hex}.{selection.resolved_container}")
    result = subprocess.run(
        [
            str(FFMPEG),
            "-y",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c",
            "copy",
            str(target),
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        with contextlib.suppress(FileNotFoundError):
            target.unlink()
        raise DwError("无法无损整理并封装为所选容器。", result.stderr)
    path.unlink()
    return target


def thumbnail_opener(policy: RequestPolicy | None = None) -> urllib.request.OpenerDirector:
    return request_opener(policy)


def download_thumbnails(
    selected: list[dict[str, Any]],
    info_dict: dict[str, Any],
    task_dir: Path,
    policy: RequestPolicy | None = None,
) -> list[tuple[Path, dict[str, Any]]]:
    if not selected:
        return []
    opener = thumbnail_opener(policy)
    common_headers = {
        str(key): str(value)
        for key, value in (info_dict.get("http_headers") or {}).items()
        if isinstance(key, str) and isinstance(value, (str, int, float))
    }
    common_headers["User-Agent"] = common_headers.get("User-Agent", USER_AGENT)
    outputs: list[tuple[Path, dict[str, Any]]] = []
    for ordinal, thumbnail in enumerate(selected, 1):
        url = str(thumbnail.get("url"))
        source = task_dir / f"thumbnail-source-{ordinal}.bin"
        output = task_dir / f"thumbnail-{ordinal}.png"
        try:
            with opener.open(urllib.request.Request(url, headers=common_headers), timeout=60) as response, source.open("wb") as handle:
                shutil.copyfileobj(response, handle, length=1024 * 1024)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DwError(f"图片 {thumbnail.get('_dw_index', ordinal)} 下载失败。", str(exc)) from exc
        result = subprocess.run(
            [str(FFMPEG), "-y", "-v", "error", "-i", str(source), "-frames:v", "1", str(output)],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        with contextlib.suppress(FileNotFoundError):
            source.unlink()
        if result.returncode != 0 or not output.is_file():
            raise DwError(f"图片 {thumbnail.get('_dw_index', ordinal)} 转换 PNG 失败。", result.stderr)
        outputs.append((output, thumbnail))
    return outputs


def record_for_path(path: Path, task_id: str) -> dict[str, Any]:
    stat_result = path.stat()
    return {
        "path": str(path),
        "task_id": task_id,
        "created_at": int(time.time()),
        "size": stat_result.st_size,
        "device": stat_result.st_dev,
        "inode": stat_result.st_ino,
    }


def append_manifest(paths: list[Path], task_id: str) -> None:
    manifest = read_json(MANIFEST_FILE, {"version": 1, "files": []})
    files = manifest.setdefault("files", [])
    files.extend(record_for_path(path, task_id) for path in paths if path.exists())
    write_json_atomic(MANIFEST_FILE, manifest)


def remove_manifest_paths(paths: Iterable[Path]) -> None:
    path_strings = {str(path) for path in paths}
    manifest = read_json(MANIFEST_FILE, {"version": 1, "files": []})
    manifest["files"] = [entry for entry in manifest.get("files", []) if entry.get("path") not in path_strings]
    write_json_atomic(MANIFEST_FILE, manifest)


def finalize_item(
    title: str,
    media: Path,
    thumbnails: list[tuple[Path, dict[str, Any]]],
    reserved: set[Path],
    task_id: str,
) -> list[Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    destinations: list[tuple[Path, Path]] = []
    video_destination = unique_output_path(title, media.suffix, reserved)
    destinations.append((media, video_destination))
    safe_title = sanitize_filename(title)
    for ordinal, (thumbnail_path, thumbnail) in enumerate(thumbnails, 1):
        index = thumbnail.get("_dw_index") or ordinal
        width = thumbnail.get("width") or "未知"
        height = thumbnail.get("height") or "未知"
        image_title = f"{safe_title}_thumbnail_{index}_{width}x{height}"
        destinations.append((thumbnail_path, unique_output_path(image_title, "png", reserved)))

    moved: list[Path] = []
    try:
        for source, destination in destinations:
            move_file_exclusive(source, destination)
            moved.append(destination)
    except Exception:
        for path in moved:
            with contextlib.suppress(OSError):
                path.unlink()
        raise
    return moved


def selection_summary(selection: ItemSelection) -> str:
    video = selection.video
    audio_description = "使用视频自带音频" if selection.use_embedded_audio else ", ".join(
        f"{audio.get('language') or '未知语言'}/{audio.get('acodec') or '未知编码'}" for audio in selection.audios
    )
    return (
        f"视频 {resolution(video)} {video.get('vcodec') or '未知编码'} ({video.get('format_id')}); "
        f"音频 {audio_description}; 容器 {selection.resolved_container.upper()}"
    )


def process_item(
    url: str,
    info_dict: dict[str, Any],
    selection: ItemSelection,
    live: bool,
    reserved: set[Path],
    batch_id: str,
    policy: RequestPolicy | None = None,
) -> ItemResult:
    title = printable_title(info_dict, str(info_dict.get("id") or "未命名视频"))
    task_id = f"{batch_id}-{uuid.uuid4().hex[:10]}"
    task_dir = TASKS_DIR / task_id
    task_dir.mkdir(parents=True, mode=0o700)
    start = time.monotonic()
    finalized: list[Path] = []
    try:
        info(f"\n开始处理：{title}")
        media = download_media(url, selection, task_dir, live, policy, info_dict)
        media = normalize_external_audio(media, selection)
        media = ensure_final_container(media, selection)
        validate_media(media)
        images = download_thumbnails(selection.thumbnails, info_dict, task_dir, policy)
        finalized = finalize_item(title, media, images, reserved, task_id)
        # Record ownership immediately after the atomic move instead of waiting
        # for the whole playlist.  A later batch cancellation removes both the
        # files and these entries.
        append_manifest(finalized, task_id)
        elapsed = time.monotonic() - start
        total_size = sum(path.stat().st_size for path in finalized)
        return ItemResult(
            title=title,
            paths=finalized,
            elapsed=elapsed,
            size=total_size,
            summary=selection_summary(selection),
            missing_languages=selection.missing_languages,
        )
    except (DownloadAborted, KeyboardInterrupt):
        for path in finalized:
            with contextlib.suppress(OSError):
                path.unlink()
        remove_manifest_paths(finalized)
        raise DownloadAborted
    except Exception:
        for path in finalized:
            with contextlib.suppress(OSError):
                path.unlink()
        remove_manifest_paths(finalized)
        raise
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


def is_live_info(info_dict: dict[str, Any]) -> bool:
    return bool(info_dict.get("is_live")) or info_dict.get("live_status") == "is_live"


def is_upcoming_info(info_dict: dict[str, Any]) -> bool:
    return info_dict.get("live_status") == "is_upcoming"


def confirm_live(info_dict: dict[str, Any]) -> bool:
    if is_upcoming_info(info_dict):
        info("检测到即将开始的直播。选择录制后，dw 会等待直播开始。")
    else:
        info("检测到正在进行的直播或持续直播。")
    return ask_yes_no("是否开始录制直播", default=False)


def wait_for_upcoming(url: str, policy: RequestPolicy | None = None) -> dict[str, Any]:
    info("正在等待直播开始；等待期间按 Ctrl+C 将取消任务 …")
    args = ytdlp_base_args(policy, url)
    args.extend(("--dump-single-json", "--no-playlist", "--wait-for-video", "30-60", url))
    try:
        return parse_json_output(run_capture(args))
    except KeyboardInterrupt as exc:
        raise DownloadAborted from exc


def choose_cookie_mode(urls: Sequence[str] = ()) -> str:
    info("\nCookies 使用方式：")
    hostnames = list(dict.fromkeys(url_hostname(url) for url in urls if url_hostname(url)))
    if hostnames:
        info("本次已选网站：" + "、".join(hostnames))
    file_note = "已检测到文件" if cookie_file_available() else "未检测到文件"
    info(f"1. 使用我手动上传的 cookies.txt（默认；{file_note}：{COOKIE_FILE}）")
    info("2. 不使用 Cookies")
    while True:
        choice = input("请选择 Cookies 使用方式 [直接回车默认 1]：").strip() or "1"
        if choice == "1":
            if not cookie_file_available():
                warn(f"未检测到 cookies：{COOKIE_FILE}，请上传后重新选择。")
                continue
            info(f"已发现并启用 cookies：{COOKIE_FILE}")
            return "file"
        if choice == "2":
            info("本次任务不使用 Cookies。")
            return "none"
        warn("请输入 1 或 2。")


def initial_request_policy(url: str, cookie_mode: str) -> RequestPolicy:
    direct = is_domestic_url(url)
    if direct:
        info("检测到国内网站，本任务优先强制直连。")
        if system_proxy_configured():
            warn("已检测到系统代理；若代理软件启用了 TUN 模式，请同时将该网站设置为 DIRECT。")
    return RequestPolicy(direct=direct, cookie_mode=cookie_mode)


def network_retry_recommended(detail: str) -> bool:
    lowered = detail.lower()
    return any(
        marker in lowered
        for marker in (
            "http error",
            "unable to download",
            "connection",
            "network",
            "proxy",
            "timed out",
            "timeout",
            "temporary failure",
        )
    )


def xinpianchang_verification_failure(url: str, detail: str) -> bool:
    lowered = detail.lower()
    return hostname_matches(url_hostname(url), {"xinpianchang.com"}) and any(
        marker in lowered
        for marker in (
            "http error 403",
            "403: forbidden",
            "http error 406",
            "406: not acceptable",
            "security verification",
            "_jsc_ch_conf",
        )
    )


def next_request_policy(url: str, policy: RequestPolicy, exc: DwError) -> RequestPolicy | None:
    detail = exc.detail or exc.message
    xinpianchang_verification = xinpianchang_verification_failure(url, detail)
    if xinpianchang_verification and not policy.xpc_assist_attempted:
        warn("新片场作品页拒绝了普通下载请求，需要真实 Chrome 完成页面验证。")
        if policy.cookie_mode == "file":
            warn(f"已上传的 {COOKIE_FILE} 会仅导入本次临时 Chrome，不会写入日常浏览器配置。")
        if IS_WINDOWS:
            if ask_yes_no("是否启动新片场 Chrome 辅助模式（推荐）", default=False):
                try:
                    assisted = xinpianchang_browser_assist(url, policy)
                except DwError as assist_error:
                    warn(f"新片场 Chrome 辅助模式失败：{concise_error_reason(assist_error)}")
                else:
                    return dataclasses.replace(
                        policy,
                        xpc_info=assisted,
                        xpc_assist_attempted=True,
                    )
        else:
            warn("当前系统暂不支持自动 Chrome 辅助模式；可在 Windows 版 dw 中处理该链接。")
    if policy.direct and network_retry_recommended(detail):
        warn("国内网站直连请求失败。")
        if ask_yes_no("是否使用系统代理重试", default=False):
            return dataclasses.replace(policy, direct=False)
    return None


def run_with_policy_retries(url: str, policy: RequestPolicy, operation: Any) -> tuple[Any, RequestPolicy]:
    current = policy
    while True:
        try:
            return operation(current), current
        except DwError as exc:
            replacement = next_request_policy(url, current, exc)
            if replacement is None:
                raise
            current = replacement
            if not current.direct:
                info("正在使用系统代理重试 …")


def show_dw_error(exc: DwError) -> None:
    error(exc.message)
    if exc.cookie_related:
        error(f"Cookies 可能已失效或与当前网站不匹配；请重新导出并更新 {COOKIE_FILE}。")
    if exc.detail:
        detail_lines = exc.detail.strip().splitlines()
        info("失败原因：")
        for line in detail_lines[-20:]:
            print(f"  {line}", file=sys.stderr)


def concise_error_reason(exc: DwError) -> str:
    if not exc.detail:
        return exc.message
    lines = [line.strip() for line in exc.detail.splitlines() if line.strip()]
    return f"{exc.message} {lines[-1]}" if lines else exc.message


def print_batch_summary(results: list[ItemResult], failures: list[tuple[str, str]]) -> None:
    info("\n========== 下载结果 ==========")
    if results:
        for index, result in enumerate(results, 1):
            info(f"\n成功 {index}：{result.title}")
            for path in result.paths:
                info(f"  路径：{path}")
            info(f"  文件总大小：{format_size(result.size)}")
            info(f"  下载与处理耗时：{result.elapsed:.1f} 秒")
            info(f"  格式摘要：{result.summary}")
            if result.missing_languages:
                info(
                    "  缺少语言："
                    + "、".join(result.missing_languages)
                    + "（当前视频没有提供与统一规则匹配的音轨）"
                )
    if failures:
        info("\n失败汇总：")
        for title, reason in failures:
            info(f"  - {title}：{reason}")
    info(f"\n成功 {len(results)} 项，失败 {len(failures)} 项。")


def cleanup_paths(paths: Iterable[Path]) -> list[Path]:
    failed: list[Path] = []
    for path in reversed(list(paths)):
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
        except OSError:
            failed.append(path)
    return failed


def choose_urls_from_share_text(text: str) -> list[str]:
    urls = extract_urls_from_text(text)
    if not urls:
        raise DwError("没有在输入内容中找到有效的 http:// 或 https:// 链接。")
    if len(urls) == 1:
        info(f"已自动提取链接：{urls[0]}")
        return urls

    info("\n检测到多个不同链接：")
    for index, url in enumerate(urls, 1):
        info(f"{index}. {url}")
    selected = ask_number_selection(
        "请选择要处理的链接（例如 1,3,5-9；全部输入 a）：",
        len(urls),
        allow_all=True,
        multiple=True,
    )
    return [urls[index - 1] for index in selected]


def process_top_level_url(
    url: str,
    cookie_mode: str,
    batch_id: str,
    results: list[ItemResult],
    failures: list[tuple[str, str]],
    created_paths: list[Path],
    reserved: set[Path],
) -> str:
    policy = initial_request_policy(url, cookie_mode)
    info("正在读取链接信息 …")
    root, policy = run_with_policy_retries(
        url,
        policy,
        lambda current: extract_url(url, flat_playlist=True, policy=current),
    )

    if is_playlist(root):
        entries = [entry for entry in root.get("entries") or [] if isinstance(entry, dict)]
        if not entries:
            raise DwError("播放列表为空或没有可访问项目。")
        display_playlist(entries)
        selected = ask_number_selection(
            "请选择播放列表项目（例如 1,3,5-9；全部输入 a）：",
            len(entries),
            allow_all=True,
            multiple=True,
        )
        profile: SelectionProfile | None = None
        for position, playlist_index in enumerate(selected, 1):
            flat_entry = entries[playlist_index - 1]
            fallback_title = printable_title(flat_entry, f"第 {playlist_index} 项")
            info(f"\n[{position}/{len(selected)}] 正在读取：{fallback_title}")
            try:
                item_info, policy = run_with_policy_retries(
                    url,
                    policy,
                    lambda current, index=playlist_index: extract_playlist_item(url, index, current),
                )
                item_url = str(
                    item_info.get("webpage_url")
                    or item_info.get("original_url")
                    or item_info.get("url")
                    or ""
                )
                if not re.match(r"^https?://", item_url, flags=re.IGNORECASE):
                    raise DwError("无法获得当前播放列表项目的完整链接。")
                live = is_live_info(item_info) or is_upcoming_info(item_info)
                if live and not confirm_live(item_info):
                    failures.append((fallback_title, "用户取消直播录制"))
                    continue
                if is_upcoming_info(item_info):
                    item_info, policy = run_with_policy_retries(
                        item_url,
                        policy,
                        lambda current: wait_for_upcoming(item_url, current),
                    )
                    live = True
                if profile is None:
                    selection, profile = choose_initial_selection(item_info)
                else:
                    selection = choose_matched_selection(item_info, profile)
                result, policy = run_with_policy_retries(
                    item_url,
                    policy,
                    lambda current: process_item(
                        item_url,
                        item_info,
                        selection,
                        live,
                        reserved,
                        batch_id,
                        current,
                    ),
                )
                results.append(result)
                created_paths.extend(result.paths)
            except DownloadAborted:
                raise
            except DwError as exc:
                show_dw_error(exc)
                failures.append((fallback_title, concise_error_reason(exc)))
            except Exception as exc:  # defensive boundary for one playlist item
                error(f"当前项目出现意外错误：{exc}")
                failures.append((fallback_title, str(exc)))
        return policy.cookie_mode

    item_info, policy = run_with_policy_retries(
        url,
        policy,
        lambda current: extract_single(url, current),
    )
    live = is_live_info(item_info) or is_upcoming_info(item_info)
    if live and not confirm_live(item_info):
        raise DownloadAborted
    if is_upcoming_info(item_info):
        item_info, policy = run_with_policy_retries(
            url,
            policy,
            lambda current: wait_for_upcoming(url, current),
        )
        live = True
    selection, _profile = choose_initial_selection(item_info)
    result, policy = run_with_policy_retries(
        url,
        policy,
        lambda current: process_item(
            url,
            item_info,
            selection,
            live,
            reserved,
            batch_id,
            current,
        ),
    )
    results.append(result)
    created_paths.extend(result.paths)
    return policy.cookie_mode


def download_task() -> None:
    install_or_update_dependencies(force=False)
    share_text = input("请粘贴下载链接或完整分享文案：").strip()
    urls = choose_urls_from_share_text(share_text)
    cookie_mode = choose_cookie_mode(urls)

    batch_id = uuid.uuid4().hex
    results: list[ItemResult] = []
    failures: list[tuple[str, str]] = []
    created_paths: list[Path] = []
    reserved: set[Path] = set()

    try:
        for position, url in enumerate(urls, 1):
            if len(urls) > 1:
                info(f"\n========== 链接 {position}/{len(urls)} ==========")
            try:
                cookie_mode = process_top_level_url(
                    url,
                    cookie_mode,
                    batch_id,
                    results,
                    failures,
                    created_paths,
                    reserved,
                )
            except DownloadAborted:
                raise
            except DwError as exc:
                show_dw_error(exc)
                failures.append((url, concise_error_reason(exc)))
            except Exception as exc:  # defensive boundary for one shared URL
                error(f"当前链接出现意外错误：{exc}")
                failures.append((url, str(exc)))
    except (DownloadAborted, KeyboardInterrupt):
        info("\n正在取消任务并删除本次任务的全部文件 …")
        failed_cleanup = cleanup_paths(created_paths)
        remove_manifest_paths(created_paths)
        shutil.rmtree(TASKS_DIR, ignore_errors=True)
        TASKS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
        if failed_cleanup:
            error("以下文件未能删除：")
            for path in failed_cleanup:
                error(str(path))
        else:
            info("本次任务文件已全部删除。")
        return

    print_batch_summary(results, failures)


def manifest_owned_file(entry: dict[str, Any]) -> tuple[Path | None, str | None]:
    try:
        path = Path(str(entry["path"]))
    except (KeyError, TypeError):
        return None, "清单条目缺少有效路径"
    if not path.is_absolute() or path.parent != OUTPUT_DIR:
        return None, f"拒绝处理清单范围外路径：{path}"
    if not path.exists() and not path.is_symlink():
        return path, None
    if path.is_symlink() or not path.is_file():
        return None, f"路径类型已变化，未删除：{path}"
    current = path.stat()
    expected_device = entry.get("device")
    expected_inode = entry.get("inode")
    if expected_device is not None and expected_inode is not None:
        if current.st_dev != expected_device or current.st_ino != expected_inode:
            return None, f"文件已被替换，未删除：{path}"
    return path, None


def tree_stats(path: Path) -> tuple[int, int]:
    if not path.exists() and not path.is_symlink():
        return 0, 0
    if path.is_symlink() or path.is_file():
        try:
            return 1, path.lstat().st_size
        except OSError:
            return 1, 0
    count = 0
    size = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        count += len(files)
        for name in files:
            candidate = Path(root) / name
            with contextlib.suppress(OSError):
                size += candidate.lstat().st_size
        for name in list(dirs):
            candidate = Path(root) / name
            if candidate.is_symlink():
                count += 1
                dirs.remove(name)
    return count, size


def safe_owned_tree_remove(path: Path, marker_name: str = ".dw-owned") -> str | None:
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink():
        return f"拒绝递归删除符号链接：{path}"
    if not path.is_dir() or not (path / marker_name).is_file():
        return f"缺少所有权标记，未删除：{path}"
    try:
        shutil.rmtree(path)
    except OSError as exc:
        return f"{path}：{exc}"
    return None


def launcher_is_owned(path: Path = LAUNCHER_PATH) -> bool:
    if path.is_symlink():
        with contextlib.suppress(OSError):
            return path.resolve() == APP_DIR / "dw.py"
        return False
    try:
        return "dw-managed-launcher" in path.read_text(encoding="utf-8", errors="ignore")[:256]
    except OSError:
        return False


def windows_cleanup_helper_is_owned() -> bool:
    try:
        return "dw-managed-windows-cleanup" in WINDOWS_UNINSTALL_HELPER.read_text(
            encoding="utf-8", errors="ignore"
        )[:512]
    except OSError:
        return False


def update_source() -> tuple[str, str]:
    value = read_json(
        UPDATE_CONFIG_FILE,
        {"repository": DEFAULT_UPDATE_REPOSITORY, "ref": "main"},
    )
    if not isinstance(value, dict):
        raise DwError("更新源配置损坏，请重新运行安装命令修复。")
    repository = str(value.get("repository") or DEFAULT_UPDATE_REPOSITORY).strip()
    reference = str(value.get("ref") or "main").strip()
    repository_pattern = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
    reference_pattern = r"[A-Za-z0-9._/-]+"
    unsafe_reference = (
        reference.startswith(("/", "-"))
        or reference.endswith("/")
        or ".." in reference
        or "//" in reference
    )
    if (
        not re.fullmatch(repository_pattern, repository)
        or not re.fullmatch(reference_pattern, reference)
        or unsafe_reference
    ):
        raise DwError("更新源配置无效，请重新运行安装命令修复。")
    return repository, reference


def update_helper_is_owned() -> bool:
    if not IS_WINDOWS:
        return True
    try:
        return "dw-managed-windows-update" in WINDOWS_UPDATE_HELPER.read_text(
            encoding="utf-8", errors="ignore"
        )[:512]
    except OSError:
        return False


def download_update_installer(repository: str, reference: str) -> Path:
    filename = "install-windows.ps1" if IS_WINDOWS else "install.sh"
    quoted_reference = urllib.parse.quote(reference, safe="/")
    official = f"https://raw.githubusercontent.com/{repository}/{quoted_reference}/{filename}"
    jsdelivr = f"https://cdn.jsdelivr.net/gh/{repository}@{quoted_reference}/{filename}"
    identity = "yt-dlp-dw Windows 10/11" if IS_WINDOWS else "yt-dlp-dw"
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        UPDATE_DIR.chmod(0o700)
    destination = UPDATE_DIR / f"{Path(filename).stem}-{uuid.uuid4().hex}{Path(filename).suffix}"
    failures: list[str] = []
    for url in (official, jsdelivr):
        try:
            download_file(url, destination)
            content = destination.read_text(encoding="utf-8", errors="ignore")
            if identity not in content[:65536]:
                raise DwError("下载的更新安装器未通过身份校验。")
            return destination
        except (DwError, OSError) as exc:
            failures.append(str(exc))
            with contextlib.suppress(OSError):
                destination.unlink()
    with contextlib.suppress(OSError):
        UPDATE_DIR.rmdir()
    raise DwError("所有程序更新源均不可用。", "\n".join(failures))


def schedule_windows_update(installer: Path, repository: str, reference: str) -> None:
    if not update_helper_is_owned():
        raise DwError(f"Windows 更新器缺失或不属于 dw：{WINDOWS_UPDATE_HELPER}")
    powershell = shutil.which("powershell.exe") or str(
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    command = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(WINDOWS_UPDATE_HELPER),
        "-ParentPid",
        str(os.getpid()),
        "-Installer",
        str(installer),
        "-StateDir",
        str(STATE_DIR),
        "-Repository",
        repository,
        "-RepositoryRef",
        reference,
    ]
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        raise DwError("无法启动 Windows 程序更新器。", str(exc)) from exc


_DEBIAN_UPDATE_RUNNER = r"""
import os
import subprocess
import sys
import time

parent_pid = int(sys.argv[1])
installer, repository, reference = sys.argv[2:]
while True:
    try:
        os.kill(parent_pid, 0)
    except ProcessLookupError:
        break
    except PermissionError:
        break
    time.sleep(0.2)
environment = os.environ.copy()
environment["DW_REPO_REF"] = reference
environment["DW_UPDATE_REF"] = reference
try:
    result = subprocess.run(["/bin/bash", installer], env=environment, check=False)
    if result.returncode == 0:
        print("dw 程序更新完成。下次输入 dw 即为新版本。", flush=True)
    else:
        print(f"错误：dw 程序更新失败，安装器退出码 {result.returncode}。", file=sys.stderr, flush=True)
finally:
    try:
        os.unlink(installer)
    except OSError:
        pass
    try:
        os.rmdir(os.path.dirname(installer))
    except OSError:
        pass
"""


def schedule_debian_update(installer: Path, repository: str, reference: str) -> None:
    try:
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _DEBIAN_UPDATE_RUNNER,
                str(os.getpid()),
                str(installer),
                repository,
                reference,
            ],
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            start_new_session=True,
        )
    except OSError as exc:
        raise DwError("无法启动 Debian 程序更新器。", str(exc)) from exc


def prepare_self_update() -> None:
    repository, reference = update_source()
    if IS_WINDOWS and not update_helper_is_owned():
        raise DwError("当前安装缺少菜单更新组件，请先重新运行一次安装命令完成升级。")
    info(f"正在获取 dw 程序更新：{repository}@{reference} …")
    installer = download_update_installer(repository, reference)
    try:
        if IS_WINDOWS:
            schedule_windows_update(installer, repository, reference)
        else:
            schedule_debian_update(installer, repository, reference)
    except DwError:
        with contextlib.suppress(OSError):
            installer.unlink()
        with contextlib.suppress(OSError):
            UPDATE_DIR.rmdir()
        raise
    info("更新已准备完成；dw 退出后会自动安装。请暂时不要关闭当前终端。")


def schedule_windows_cleanup() -> str | None:
    if not windows_cleanup_helper_is_owned():
        return f"Windows 卸载清理器缺失或不属于 dw：{WINDOWS_UNINSTALL_HELPER}"
    powershell = shutil.which("powershell.exe") or str(
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    command = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(WINDOWS_UNINSTALL_HELPER),
        "-ParentPid",
        str(os.getpid()),
        "-AppDir",
        str(APP_DIR),
        "-StateDir",
        str(STATE_DIR),
        "-CommandDir",
        str(COMMAND_DIR),
        "-AliasLauncher",
        str(WINDOWS_ALIAS_LAUNCHER),
    ]
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        return f"无法启动 Windows 卸载清理器：{exc}"
    return None


def uninstall() -> None:
    manifest = read_json(MANIFEST_FILE, {"files": []})
    entries = [entry for entry in manifest.get("files", []) if isinstance(entry, dict)]
    deletable: list[Path] = []
    preflight_warnings: list[str] = []
    for entry in entries:
        path, warning_message = manifest_owned_file(entry)
        if warning_message:
            preflight_warnings.append(warning_message)
        elif path is not None and (path.exists() or path.is_symlink()):
            deletable.append(path)

    download_size = sum(path.lstat().st_size for path in deletable)
    app_count, app_size = tree_stats(APP_DIR)
    state_count, state_size = tree_stats(STATE_DIR)
    packages = [] if IS_WINDOWS else read_json(PACKAGES_FILE, [])
    packages = [str(package) for package in packages if isinstance(package, str)]

    info("\n卸载将处理以下内容：")
    info(f"- dw 下载文件：{len(deletable)} 个，{format_size(download_size)}")
    for path in deletable:
        info(f"  {path}")
    info(f"- 应用目录：{APP_DIR}（{app_count} 个文件，{format_size(app_size)}）")
    info(f"- 状态与缓存：{STATE_DIR}（{state_count} 个文件，{format_size(state_size)}）")
    info(f"- 命令入口：{LAUNCHER_PATH}")
    if IS_WINDOWS and WINDOWS_ALIAS_LAUNCHER != LAUNCHER_PATH:
        info(f"- 即时命令别名：{WINDOWS_ALIAS_LAUNCHER}")
    if IS_WINDOWS:
        info("- Windows 版使用隔离的便携依赖，不修改系统软件包")
    elif packages:
        info("- 由 dw 安装的 Debian 软件包：" + "、".join(packages))
    else:
        info("- 没有记录需要移除的 Debian 软件包")
    for message in preflight_warnings:
        warn(message)

    confirmation = input(f"\n请输入“{UNINSTALL_CONFIRMATION}”继续：").strip()
    if confirmation != UNINSTALL_CONFIRMATION:
        info("已取消卸载。")
        return
    delete_cookie = ask_yes_no(f"是否同时永久删除 {COOKIE_FILE}", default=False)

    failures: list[str] = list(preflight_warnings)
    for path in deletable:
        try:
            path.unlink()
        except OSError as exc:
            failures.append(f"{path}：{exc}")
    if delete_cookie and cookie_path_present():
        try:
            COOKIE_FILE.unlink()
        except OSError as exc:
            failures.append(f"{COOKIE_FILE}：{exc}")

    if packages and not IS_WINDOWS:
        result = subprocess.run(
            ["apt-get", "purge", "-y", *packages],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode != 0:
            failures.append("Debian 软件包未能完全移除：\n" + result.stdout[-4000:])

    if failures:
        residual = {"failed_at": int(time.time()), "items": failures}
        with contextlib.suppress(OSError):
            write_json_atomic(STATE_DIR / "uninstall-residuals.json", residual)
        error("卸载未完全完成。以下内容需要处理后再次选择卸载：")
        for failure in failures:
            error(failure)
        return

    if IS_WINDOWS:
        cleanup_failure = schedule_windows_cleanup()
        if cleanup_failure:
            failures.append(cleanup_failure)
            residual = {"failed_at": int(time.time()), "items": failures}
            with contextlib.suppress(OSError):
                write_json_atomic(STATE_DIR / "uninstall-residuals.json", residual)
            error("Windows 卸载清理器未能启动，以下内容仍然保留：")
            for failure in failures:
                error(failure)
            return
        info("下载文件已删除；dw 退出后将继续删除命令、便携依赖、缓存和状态目录。")
        if not delete_cookie and cookie_path_present():
            info(f"已按你的选择保留：{COOKIE_FILE}")
        return

    if LAUNCHER_PATH.exists() or LAUNCHER_PATH.is_symlink():
        if launcher_is_owned():
            try:
                LAUNCHER_PATH.unlink()
            except OSError as exc:
                failures.append(f"{LAUNCHER_PATH}：{exc}")
        else:
            failures.append(f"命令入口不再属于 dw，未删除：{LAUNCHER_PATH}")

    app_failure = safe_owned_tree_remove(APP_DIR)
    if app_failure:
        failures.append(app_failure)
    if not failures:
        state_failure = safe_owned_tree_remove(STATE_DIR)
        if state_failure:
            failures.append(state_failure)

    if failures:
        error("卸载未完全完成，残留如下：")
        for failure in failures:
            error(failure)
    else:
        info("dw、专用依赖、缓存、状态和清单内下载文件均已删除。")
        if not delete_cookie and cookie_path_present():
            info(f"已按你的选择保留：{COOKIE_FILE}")


def recover_abandoned_tasks() -> None:
    if not TASKS_DIR.exists():
        return
    for child in TASKS_DIR.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        elif child.is_file() or child.is_symlink():
            with contextlib.suppress(OSError):
                child.unlink()


def require_root_and_platform() -> None:
    if IS_WINDOWS:
        version = sys.getwindowsversion()
        if version.major != 10:
            raise DwError(
                f"不支持的 Windows 版本：{platform.platform()}；当前仅支持 Windows 10/11。"
            )
        machine = (
            os.environ.get("PROCESSOR_ARCHITEW6432")
            or os.environ.get("PROCESSOR_ARCHITECTURE")
            or platform.machine()
        ).lower()
        if machine not in {"amd64", "x86_64"}:
            raise DwError(f"不支持的 CPU 架构：{machine}；Windows 版当前仅支持 x64/amd64。")
        return
    if os.name != "posix" or not hasattr(os, "geteuid"):
        raise DwError("dw 当前仅支持 Debian 12/13 或 Windows 10/11。")
    if os.geteuid() != 0:
        raise DwError("dw 仅供 root 用户运行，请切换到 root 后重试。")
    machine = os.uname().machine.lower()
    if machine not in {"x86_64", "amd64"}:
        raise DwError(f"不支持的 CPU 架构：{machine}；当前仅支持 x86_64/amd64。")
    os_release = read_os_release()
    if os_release.get("ID") != "debian" or os_release.get("VERSION_ID") not in {"12", "13"}:
        raise DwError(
            f"不支持的系统：{os_release.get('PRETTY_NAME', '未知')}；当前仅支持 Debian 12/13。"
        )


def read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    except OSError:
        pass
    return values


def main_menu() -> None:
    with InstanceLock():
        recover_abandoned_tasks()
        while True:
            info("\n========== dw 下载助手 ==========")
            info("1. 开始下载")
            info("2. 卸载 dw")
            info("3. 更新 dw")
            info("0. 退出")
            choice = input("请选择：").strip()
            if choice == "0":
                return
            if choice == "2":
                uninstall()
                return
            if choice == "3":
                prepare_self_update()
                return
            if choice != "1":
                warn("请输入 0、1、2 或 3。")
                continue
            while True:
                try:
                    download_task()
                except DwError as exc:
                    show_dw_error(exc)
                except KeyboardInterrupt:
                    info("\n已取消当前操作。")
                if not ask_yes_no("是否继续输入下一个链接", default=False):
                    break


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="交互式 yt-dlp 下载助手")
    parser.add_argument("--install-dependencies", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="store_true", help="显示版本")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        require_root_and_platform()
        ensure_runtime_directories()
        if args.version:
            info(f"dw {APP_VERSION}")
            return 0
        if args.install_dependencies:
            install_or_update_dependencies(force=args.force)
            return 0
        main_menu()
        return 0
    except DwError as exc:
        show_dw_error(exc)
        return 1
    except KeyboardInterrupt:
        info("\n已取消。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
