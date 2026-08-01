#!/usr/bin/env bash
set -Eeuo pipefail

umask 022

readonly APP_DIR="/opt/dw"
readonly STATE_DIR="/var/lib/dw"
readonly LAUNCHER="/usr/local/bin/dw"
readonly REPOSITORY="lucaskevin9510-beep/yt-dlp-dw"
readonly REPOSITORY_REF="${DW_REPO_REF:-main}"
readonly RAW_BASE="https://raw.githubusercontent.com/${REPOSITORY}/${REPOSITORY_REF}"
readonly JSDELIVR_BASE="https://cdn.jsdelivr.net/gh/${REPOSITORY}@${REPOSITORY_REF}"

TEMP_DIR=""

cleanup() {
    if [[ -n "${TEMP_DIR}" && -d "${TEMP_DIR}" && "${TEMP_DIR}" == /tmp/dw-install.* ]]; then
        rm -rf -- "${TEMP_DIR}"
    fi
}
trap cleanup EXIT

fail() {
    printf '安装失败：%s\n' "$1" >&2
    exit 1
}

if [[ "$(id -u)" -ne 0 ]]; then
    fail "请切换到 root 用户后运行安装脚本。"
fi

if [[ ! -r /etc/os-release ]]; then
    fail "无法识别操作系统；当前仅支持 Debian 12/13。"
fi

ID=""
VERSION_ID=""
PRETTY_NAME=""
# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "debian" || ("${VERSION_ID:-}" != "12" && "${VERSION_ID:-}" != "13") ]]; then
    fail "当前仅支持 Debian 12/13，检测到：${PRETTY_NAME:-未知系统}。"
fi

case "$(uname -m)" in
    x86_64|amd64) ;;
    *) fail "当前仅支持 x86_64/amd64，检测到：$(uname -m)。" ;;
esac

if [[ -e "${APP_DIR}" && ! -f "${APP_DIR}/.dw-owned" ]]; then
    fail "${APP_DIR} 已存在但不属于 dw，为避免覆盖已停止安装。"
fi
if [[ -e "${STATE_DIR}" && ! -f "${STATE_DIR}/.dw-owned" ]]; then
    fail "${STATE_DIR} 已存在但不属于 dw，为避免覆盖已停止安装。"
fi
if [[ -L "${LAUNCHER}" ]]; then
    if [[ "$(readlink -f -- "${LAUNCHER}" 2>/dev/null || true)" != "${APP_DIR}/dw.py" ]]; then
        fail "${LAUNCHER} 是指向其他程序的符号链接；请先处理命名冲突。"
    fi
    rm -f -- "${LAUNCHER}"
elif [[ -e "${LAUNCHER}" ]]; then
    if ! head -c 256 "${LAUNCHER}" 2>/dev/null | grep -q 'dw-managed-launcher'; then
        fail "${LAUNCHER} 已存在且不属于本程序；请先处理命名冲突。"
    fi
fi

TEMP_DIR="$(mktemp -d /tmp/dw-install.XXXXXX)"
before_packages="${TEMP_DIR}/packages-before.txt"
after_packages="${TEMP_DIR}/packages-after.txt"
new_packages="${TEMP_DIR}/packages-new.txt"

dpkg-query -W -f='${binary:Package}\n' 2>/dev/null | LC_ALL=C sort -u > "${before_packages}" || true

required_packages=()
for package in python3 ca-certificates; do
    if ! dpkg-query -W -f='${Status}' "${package}" 2>/dev/null | grep -q '^install ok installed$'; then
        required_packages+=("${package}")
    fi
done

if [[ "${#required_packages[@]}" -gt 0 ]]; then
    printf '正在安装系统引导依赖：%s\n' "${required_packages[*]}"
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${required_packages[@]}"
fi

command -v python3 >/dev/null 2>&1 || fail "Python 3 安装失败。"

dpkg-query -W -f='${binary:Package}\n' 2>/dev/null | LC_ALL=C sort -u > "${after_packages}" || true
comm -13 "${before_packages}" "${after_packages}" > "${new_packages}" || true

install -d -m 0755 "${APP_DIR}" "${APP_DIR}/bin"
install -d -m 0700 "${STATE_DIR}" "${STATE_DIR}/cache" "${STATE_DIR}/tasks"
printf 'owned by yt-dlp-dw\n' > "${APP_DIR}/.dw-owned"
printf 'owned by yt-dlp-dw\n' > "${STATE_DIR}/.dw-owned"
chmod 0600 "${STATE_DIR}/.dw-owned"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source_file="${script_dir}/src/dw.py"
downloaded_source="${TEMP_DIR}/dw.py"

if [[ -f "${source_file}" ]]; then
    install -m 0755 "${source_file}" "${APP_DIR}/dw.py"
else
    printf '正在获取 dw 主程序 …\n'
    python3 - \
        "${RAW_BASE}/src/dw.py" \
        "${JSDELIVR_BASE}/src/dw.py" \
        "${downloaded_source}" \
        "${STATE_DIR}/github-mirrors.txt" <<'PY'
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request

official_url, jsdelivr_url, destination, mirror_file = sys.argv[1:]


def configured_mirrors():
    values = re.split(r"[;,\r\n]+", os.environ.get("DW_GITHUB_MIRRORS", ""))
    try:
        with open(mirror_file, encoding="utf-8") as handle:
            values.extend(handle)
    except OSError:
        pass
    mirrors = []
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
            print(f"警告：你提供的镜像域名不可使用：{mirror}", file=sys.stderr)
            continue
        mirrors.append(mirror)
    return mirrors


def mirrored(mirror):
    if "{url}" in mirror:
        return mirror.replace("{url}", official_url)
    return f"{mirror.rstrip('/')}/{official_url}"


custom = configured_mirrors()
candidates = [(mirrored(mirror), mirror, True) for mirror in custom]
candidates.extend([
    (official_url, "GitHub 官方源", False),
    (jsdelivr_url, "https://cdn.jsdelivr.net/", False),
    (f"https://gh-proxy.com/{official_url}", "https://gh-proxy.com/", False),
    (f"https://ghfast.top/{official_url}", "https://ghfast.top/", False),
    (f"https://ghproxy.net/{official_url}", "https://ghproxy.net/", False),
])
last_error = None
for url, source, is_custom in candidates:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "yt-dlp-dw-installer/1.2"})
        with urllib.request.urlopen(request, timeout=60) as response, open(destination, "wb") as output:
            shutil.copyfileobj(response, output)
        with open(destination, "rb") as handle:
            if b"yt-dlp-dw" not in handle.read(65536):
                raise ValueError("下载内容身份校验失败")
        if url != official_url:
            print(f"已使用 GitHub 镜像：{source}")
        if is_custom:
            with open(mirror_file, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(source + "\n")
            os.chmod(mirror_file, 0o600)
        break
    except Exception as exc:
        last_error = exc
        try:
            os.unlink(destination)
        except FileNotFoundError:
            pass
        if is_custom:
            print(f"警告：你提供的镜像域名不可使用：{source}", file=sys.stderr)
else:
    raise SystemExit(f"所有 GitHub 下载源均不可用：{last_error}")
PY
    install -m 0755 "${downloaded_source}" "${APP_DIR}/dw.py"
fi

python3 - "${STATE_DIR}/managed-packages.json" "${new_packages}" <<'PY'
import json
import os
import sys
import tempfile

destination, additions_file = sys.argv[1:]
try:
    with open(destination, encoding="utf-8") as handle:
        existing = json.load(handle)
except (FileNotFoundError, json.JSONDecodeError):
    existing = []
with open(additions_file, encoding="utf-8") as handle:
    additions = [line.strip() for line in handle if line.strip()]
packages = sorted({item for item in existing + additions if isinstance(item, str)})
fd, temporary = tempfile.mkstemp(prefix=".managed-packages.", dir=os.path.dirname(destination))
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(packages, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY

printf '正在安装并校验 yt-dlp、Deno 和 FFmpeg …\n'
python3 "${APP_DIR}/dw.py" --install-dependencies --force

launcher_temp="${TEMP_DIR}/dw-launcher"
cat > "${launcher_temp}" <<'EOF'
#!/bin/sh
# dw-managed-launcher
exec /usr/bin/python3 /opt/dw/dw.py "$@"
EOF
install -m 0755 "${launcher_temp}" "${LAUNCHER}"

printf '\n安装完成。以后在任意目录输入以下命令：\n\n  dw\n\n'
printf '程序目录：%s\n状态目录：%s\n下载目录：/root\n' "${APP_DIR}" "${STATE_DIR}"
