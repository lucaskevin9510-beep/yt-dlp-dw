# yt-dlp-dw

`dw` 是面向 **Windows 10/11 x64** 和 **Debian 12/13 x86_64** 的交互式下载助手。它基于 [yt-dlp](https://github.com/yt-dlp/yt-dlp)，会先完整列出可播放的视频与独立音频版本，让用户明确选择后再下载、无损封装。Windows 成品保存到当前用户的 Downloads 已知文件夹，Debian 成品保存到 `/root`。

> 请只下载你有权访问和保存的内容，并遵守网站条款及当地法律。`dw` 不绕过 DRM，也不能保证所有网站始终可用；实际站点支持能力由当前 yt-dlp 版本决定。

## 功能总览

| 功能 | 说明 |
|---|---|
| 支持网站 | 面向当前 yt-dlp 支持的网站；普通视频、播放列表和直播使用同一个入口 |
| 分享文案 | 可直接粘贴小红书、抖音等平台的完整分享文案；自动提取、规范化和去重链接，多链接可多选 |
| 视频选择 | 完整列出真实、可播放的视频流，区分纯视频与自带音频的视频；按容器、编码和预计大小排序 |
| 音频选择 | 列出语言、编码、码率、声道、采样率和大小；支持选择一条或多条语言音轨 |
| 文件容器 | 每次选择自动、MP4、MKV 或 WebM；不转码，不兼容时要求重新选择并推荐 MKV |
| 播放列表 | 分页显示项目，支持单选、多选、范围和全部；第一项的规则可自动应用到后续项目 |
| 图片 | 列出并去重所有可下载缩略图，支持多选或全部下载，最终统一保存为 PNG |
| Cookies | 每次任务可选“手动 `cookies.txt`”或“不使用”；直接回车默认使用固定位置的手动文件；Windows 新片场验证失败时可征得同意后启动隔离 Chrome 辅助模式 |
| 标签与水印源 | 最终无损重封装会删除容器元数据/平台标签；存在干净流时自动排除 yt-dlp 明确标记的带水印流 |
| 网络策略 | 国内网站优先强制直连；直连失败后才询问是否使用系统代理，不静默切换 |
| 下载速度 | 普通 HTTP/HTTPS 文件使用 aria2c 进行 8 连接分片；HLS/DASH 使用 yt-dlp 原生 8 片段并发 |
| GitHub 容错 | 官方源不可用时可回退至 jsDelivr 和多个第三方加速源；支持用户自定义 HTTPS 镜像 |
| 直播 | 可录制正在直播、即将开始或持续直播的内容；支持安全停止与强制取消 |
| 输出与清理 | Windows 保存到 Downloads，Debian 保存到 `/root`；同名自动改名，失败或取消时按任务清理 |
| 更新与卸载 | 主菜单可一键更新；自动安装或更新 yt-dlp nightly、Deno、FFmpeg、aria2c，依赖下载实时显示进度；提供带清单保护的完整卸载功能 |

## 快速安装

### Windows 10/11 x64

无需管理员权限，也不需要预装 Python、yt-dlp、Deno、FFmpeg 或 aria2c。打开 **PowerShell** 或 Windows Terminal，运行下面的自动回退安装命令。它会按顺序尝试 GitHub 官方源、jsDelivr、gh-proxy.com、ghfast.top 和 ghproxy.net：

```powershell
$sources = @(
    'https://raw.githubusercontent.com/lucaskevin9510-beep/yt-dlp-dw/main/install-windows.ps1'
    'https://cdn.jsdelivr.net/gh/lucaskevin9510-beep/yt-dlp-dw@main/install-windows.ps1'
    'https://gh-proxy.com/https://raw.githubusercontent.com/lucaskevin9510-beep/yt-dlp-dw/main/install-windows.ps1'
    'https://ghfast.top/https://raw.githubusercontent.com/lucaskevin9510-beep/yt-dlp-dw/main/install-windows.ps1'
    'https://ghproxy.net/https://raw.githubusercontent.com/lucaskevin9510-beep/yt-dlp-dw/main/install-windows.ps1'
)
$installer = $null
foreach ($source in $sources) {
    try {
        $candidate = (Invoke-WebRequest -UseBasicParsing -Uri $source -TimeoutSec 30).Content
        if ($candidate -match 'yt-dlp-dw Windows 10/11') { $installer = $candidate; break }
    } catch {}
}
if (-not $installer) { throw '所有 dw 安装源均不可用' }
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command $installer
```

GitHub 官方源可直连时，也可使用较短的官方源命令：

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "Invoke-RestMethod 'https://raw.githubusercontent.com/lucaskevin9510-beep/yt-dlp-dw/main/install-windows.ps1' | Invoke-Expression"
```

安装完成后输入：

```powershell
dw
```

安装 Python、yt-dlp、Deno、FFmpeg 和 aria2c 时会实时显示类似 `45.2% | 38.6 MiB / 85.4 MiB | 5.1 MiB/s` 的进度。服务器不提供总大小时，仍会显示已下载大小和速度。

安装器还会在 Windows 已有的 `%LOCALAPPDATA%\Microsoft\WindowsApps` 中创建一个受管理的 `dw.cmd` 即时别名，因此已经打开的 Windows Terminal 新标签页也不需要手动执行 `$env:Path += ...`。如果该位置已有不属于 `dw` 的同名文件，安装器不会覆盖，并会显示可直接运行的完整入口。Windows 的安装、使用、Cookies、更新、卸载和故障处理详见 [Windows 10/11 完整说明](docs/WINDOWS.md)。

### Debian 12/13 x86_64

Debian 版只能由 `root` 运行。普通用户先进入 root shell：

```bash
sudo -i
```

如果系统没有 `sudo`，也可以使用 `su -`。执行下面的命令应当输出 `0`：

```bash
id -u
```

确认是 root 后安装。下面的命令会按顺序尝试 GitHub 官方源、jsDelivr 和三个加速源：

```bash
urls=(
  'https://raw.githubusercontent.com/lucaskevin9510-beep/yt-dlp-dw/main/install.sh'
  'https://cdn.jsdelivr.net/gh/lucaskevin9510-beep/yt-dlp-dw@main/install.sh'
  'https://gh-proxy.com/https://raw.githubusercontent.com/lucaskevin9510-beep/yt-dlp-dw/main/install.sh'
  'https://ghfast.top/https://raw.githubusercontent.com/lucaskevin9510-beep/yt-dlp-dw/main/install.sh'
  'https://ghproxy.net/https://raw.githubusercontent.com/lucaskevin9510-beep/yt-dlp-dw/main/install.sh'
)
installer='/tmp/install-dw.sh'
rm -f "$installer"
for url in "${urls[@]}"; do
  if curl -fL --connect-timeout 15 --max-time 90 "$url" -o "$installer" && grep -q 'yt-dlp-dw' "$installer"; then
    break
  fi
  rm -f "$installer"
done
test -s "$installer" || { echo '所有 dw 安装源均不可用' >&2; exit 1; }
bash "$installer"
rm -f "$installer"
```

GitHub 官方源可直连时的短命令：

```bash
curl -fsSL https://raw.githubusercontent.com/lucaskevin9510-beep/yt-dlp-dw/main/install.sh | bash
```

安装完成后输入：

```bash
dw
```

如果希望先审阅 Debian 安装器再执行：

```bash
curl -fsSL https://raw.githubusercontent.com/lucaskevin9510-beep/yt-dlp-dw/main/install.sh -o /tmp/install-dw.sh
less /tmp/install-dw.sh
bash /tmp/install-dw.sh
rm -f /tmp/install-dw.sh
```

Debian 安装命令需要系统已有 `curl`。如果没有：

```bash
apt-get update && apt-get install -y curl
```

## 最重要：如何使用 `dw`

下面是完成一次下载时的实际操作顺序。终端显示的编号会因网站和视频而不同，请根据当次列表选择，不要照抄示例编号。

### 第一步：启动并粘贴链接或分享文案

Windows 在普通 PowerShell、CMD 或 Windows Terminal 中运行；Debian 在 root shell 中运行：

```bash
dw
```

主菜单输入 `1`，然后粘贴单个链接，或直接粘贴平台生成的完整分享文案：

```text
请选择：1
请粘贴下载链接或完整分享文案：8.74 nqr:/ #恋爱脑 https://v.douyin.com/3qaI6648IrI/ 复制此链接……
```

程序会自动找出文案中的 `http://`/`https://` 链接，处理抖音精选页的 `modal_id`，并去除完全重复的链接。如果找到多个不同链接，会先列表，可输入 `1,3`、`1-4` 或 `a` 选择要处理的链接。

### 第二步：选择 Cookies 方式

链接提取后，程序会显示已选网站并询问：

```text
Cookies 使用方式：
1. 使用我手动上传的 cookies.txt（默认）
2. 不使用 Cookies
请选择 Cookies 使用方式 [直接回车默认 1]：
```

- 直接按回车或选 `1`：使用 Windows `%USERPROFILE%\cookies.txt` 或 Debian `/root/cookies.txt`；文件不存在时会要求先上传；
- 选 `2`：本次任务完全不使用 Cookies。

Windows 上 yt-dlp 的 Chrome 数据库复制功能存在长期未解决的权限/锁定问题；即使关闭浏览器也可能出现 `Could not copy Chrome cookie database`。因此普通 Cookies 菜单不直接读取日常 Chrome 数据库，统一使用可检查、可替换的 Netscape `cookies.txt`。新片场遇到浏览器验证页时有单独的、需再次征得同意的隔离 Chrome 辅助流程，详见下方说明。

选择完成后才会读取链接。普通单视频直接进入格式选择；播放列表先选项目；直播先询问是否录制。

### 第三步：选择视频版本

视频表格会按 MP4、WebM、其他容器分组。每组内先按编码排列（H.264 优先），同一编码再按预计大小从大到小排列，未知大小排在该编码的后面，并用分辨率、帧率和码率继续排序。每一行都会显示脚本编号、`纯视频`/`视频+音频`、分辨率、帧率、编码、HDR、码率、大小和 yt-dlp 格式 ID。

这里只能输入一个脚本编号，例如：

```text
请选择一个视频编号：3
```

如果这一行标为 `视频+音频`，程序会继续询问：

```text
所选视频自带音频，是否使用它 [Y/n]：
```

- 直接按回车或输入 `y`：保留视频自带音频，不再选择独立音频；
- 输入 `n`：删除原音轨，随后从独立音频列表选择新音轨。

### 第四步：选择音频版本

选择纯视频，或者决定不使用视频自带音频时，程序会列出所有独立音频。可以选择一条或多条：

```text
请选择音频编号（多个用逗号，全部输入 a）：1
请选择音频编号（多个用逗号，全部输入 a）：1,3
```

多音轨通常应选择 MKV。播放列表后续项目缺少某种已选语言时，程序会下载仍然存在的音轨，并在最终汇总中说明缺少的语言。

### 第五步：选择最终文件容器

程序每次都会列出：

```text
1. 保持原格式/自动无损封装
2. MP4
3. MKV
4. WebM
```

同时会显示推荐容器。直接按回车默认选 `1`，让程序按所选编码自动决定；希望指定容器时输入 `2`、`3` 或 `4`。`dw` 不进行转码，如果编码不能直接放进 MP4 或 WebM，会说明原因并重新显示选择菜单；此时通常选择 `3`（MKV）。

### 第六步：决定是否下载图片

```text
是否下载图片 [y/N]：
```

- 直接按回车或输入 `n`：不下载图片，开始处理视频；
- 输入 `y`：显示图片列表，然后输入一个编号、多个编号或 `a` 下载全部。

例如：

```text
请选择图片编号（多个用逗号，全部输入 a）：1,3
```

选中的图片会转换为 PNG，并与视频一起作为独立文件保存到当前平台的成品目录。

### 第七步：等待完成并查看结果

下载、合并、容器整理和文件校验成功后，程序会显示：

- 最终文件的完整路径；
- 文件大小和处理耗时；
- 视频、音频和容器摘要；
- 播放列表失败项目及缺失语言（如果有）。

典型输出位置：

```text
# Windows
C:\Users\你的用户名\Downloads\视频标题.mkv
C:\Users\你的用户名\Downloads\视频标题_thumbnail_1_1920x1080.png

# Debian
/root/视频标题.mkv
/root/视频标题_thumbnail_1_1920x1080.png
```

最后出现 `是否继续输入下一个链接 [y/N]：`：输入 `y` 继续，直接按回车或输入 `n` 返回主菜单。

### 输入规则速查

| 操作 | 可以输入 |
|---|---|
| 主菜单 | `1` 下载、`2` 卸载、`3` 更新、`0` 退出 |
| 是/否问题 | `y`/`n`、`yes`/`no`、`是`/`否`、`1`/`0` |
| `[Y/n]` | 大写的 `Y` 表示默认“是”，直接按回车等于 `y` |
| `[y/N]` | 大写的 `N` 表示默认“否”，直接按回车等于 `n` |
| 单个编号 | `3` |
| 多个编号 | `1,3,8`，也接受中文逗号 `1，3，8` |
| 连续范围 | `2-10` |
| 混合选择 | `1,3,5-9` |
| 全部选择 | `a`、`all` 或 `全部` |
| 分享文案中的多链接 | 同样支持 `1,3`、`1-4`、`a` |
| 播放列表翻页 | 按回车看下一页；输入 `q` 停止浏览并开始选择 |

### 一次典型操作示例

下面仅演示输入顺序：选择第 2 个视频、不使用自带音频、选择第 1 和第 3 条音轨、保存为 MKV、下载全部图片，完成后不继续下一个链接。

```text
dw
请选择：1
请粘贴下载链接或完整分享文案：https://example.com/video
请选择 Cookies 使用方式 [直接回车默认 1]：
请选择一个视频编号：2
所选视频自带音频，是否使用它 [Y/n]：n
请选择音频编号（多个用逗号，全部输入 a）：1,3
请选择 [1-4]：3
是否下载图片 [y/N]：y
请选择图片编号（多个用逗号，全部输入 a）：a
是否继续输入下一个链接 [y/N]：n
```

如果选择的视频是 `纯视频`，不会出现“是否使用自带音频”的问题，而是直接显示独立音频列表。如果选择不下载图片，也不会出现图片编号选择。

### 播放列表的使用顺序

播放列表链接会先分页显示视频标题，每页 30 项。选择格式前先输入要下载的项目：

```text
请选择播放列表项目（例如 1,3,5-9；全部输入 a）：1,3,5-9
```

程序会在第一项上询问完整的视频、音频、容器和图片规则，然后尽量自动匹配后续项目。某项失败不会中止其余项目，最终会统一汇总。如果主动取消整个播放列表，本批次已经完成的成品也会删除。

### 使用 Cookies 下载登录内容

运行 `dw` 前，将 Netscape 格式的 `cookies.txt` 保存到固定路径。Cookies 菜单直接按回车即可使用该文件。

Windows PowerShell：

```powershell
Copy-Item 'D:\你的路径\cookies.txt' "$env:USERPROFILE\cookies.txt"
dw
```

Debian：

```bash
cp /你的路径/cookies.txt /root/cookies.txt
chmod 600 /root/cookies.txt
dw
```

程序显示 `已发现并启用 cookies` 才表示手动文件已被使用。Cookies 等同于登录凭据：不要上传到 GitHub、网盘或发送给他人。

### 更新程序与进入卸载

运行 `dw` 并在主菜单选择 `3. 更新 dw`，程序会显示安装器下载进度，并在当前 `dw` 退出后自动安装新版本，无需再次输入一长串安装命令。更新会保留下载清单、设置、成品和手动 `cookies.txt`。只有从不含菜单更新组件的旧版本首次升级时，才需要重新运行一次“快速安装”命令。需要卸载时在主菜单选择 `2`；卸载会进一步要求输入完整确认文字，详细删除范围见下方“安全卸载”。

> **卸载警告：** 完整确认卸载后，程序会删除清单中由 `dw` 下载且仍在原路径的全部视频和图片，而不只是删除脚本本身。需要保留的文件请先手动移动或改名，并仔细阅读下方“安全卸载”。

## 运行条件

| 平台 | 系统与权限 | CPU | 引导环境 |
|---|---|---|---|
| Windows | Windows 10/11，当前用户安装，无需管理员 | x64/amd64 | Windows PowerShell 5.1+ |
| Debian | Debian 12/13，仅限 `root` | x86_64/amd64 | Bash、APT，安装命令需要 `curl` |

两个平台都需要能访问至少一个 GitHub 官方/回退源、Python.org（Windows 首次安装）和目标视频网站。

安装器会自动完成以下工作：

- 检查系统版本和 CPU 架构；Debian 额外检查 `root` 权限；
- Windows 安装经过固定 SHA-256 校验的官方嵌入式 Python，不使用系统 Python；
- Debian 在缺失时通过 APT 安装最小引导依赖（Python 3、CA 证书、aria2）；
- 安装并校验 yt-dlp nightly、Deno、FFmpeg、FFprobe 和 aria2c；
- 将应用、便携依赖、缓存、任务和清单放在平台专用目录；
- 创建 `dw` 命令；Windows 写入当前用户 PATH，并在既有 WindowsApps 命令目录创建受管理的即时别名；Debian 创建 `/usr/local/bin/dw`。

下载的发布文件都要通过发布方提供的 SHA-256 校验后才会替换现有组件。更新失败但旧组件仍可使用时，`dw` 会明确警告并继续使用旧版本。

## 主菜单

```text
========== dw 下载助手 ==========
1. 开始下载
2. 卸载 dw
3. 更新 dw
0. 退出
请选择：
```

选择下载后会显示：

```text
请粘贴下载链接或完整分享文案：
```

完成一次任务后，程序会询问是否继续输入下一个链接。

## 8 路并发与 Windows 卡顿处理

普通 HTTP/HTTPS 媒体文件默认由 aria2c 使用 8 个连接分片下载；HLS/DASH 分段流仍由 yt-dlp 原生下载器处理，并同时下载 8 个片段。直播只使用 yt-dlp 原生下载器，避免外部下载器影响持续录制。这里的“8 路”是网络连接/片段并发，不是视频转码线程；实际速度仍受网站限速、CDN、是否支持 Range、磁盘和线路质量限制，不能保证所有链接都跑满宽带。

Windows 启动时会关闭经典控制台的“快速编辑”暂停，并让 yt-dlp/aria2c 不再读取交互输入，避免下载进程等待回车。`Ctrl+C` 取消逻辑仍然保留。如果旧窗口已经进入文字选择状态，先按 `Esc` 退出选择，再通过主菜单更新并重新打开 `dw`。

## 视频格式选择

`dw` 读取 yt-dlp 返回的全部真实视频流，并隐藏故事板、MHTML 图片序列等不能作为普通视频播放的项目。

列表包含：

- 脚本编号与 yt-dlp 原始格式 ID；
- `纯视频` 或 `视频+音频` 标记；
- 容器、分辨率、帧率；
- 视频编码、HDR/SDR、码率；
- 预计大小，网站未提供时显示 `未知`。

排序规则固定为：

1. MP4 格式组；
2. WebM 格式组；
3. 其他容器格式组；
4. 每组内部 H.264 优先，其后为 H.265、AV1、VP9、VP8 和其他编码；
5. 同一编码内按预计大小从高到低，大小未知时再按分辨率、帧率和码率排序。

相同分辨率但编码、帧率、HDR 或码率不同的格式不会被合并或隐藏。

如果选择的视频已经带有音频，`dw` 会询问是否保留。选择不保留时，最终文件会删除原音轨，只留下随后选择的独立音轨。

## 平台标签与画面水印

下载完成后，`dw` 会用 FFmpeg 做一次 `-c copy` 无损重封装，删除容器元数据、章节、封面附件和平台标签，不重新编码音视频。yt-dlp 如果把某个候选流明确标记为 `watermarked`，且同一链接还提供干净流，`dw` 会隐藏带水印候选并只让用户选择干净流；这能避免抖音/TikTok 的 `download_addr` 水印版本因文件更大而排到前面。

如果文字或图案已经烧进每一帧画面，它就是视频内容的一部分，无法通过删标签或无损封装去除。小红书、Bilibili 或上传者本身烧入的可见水印只有裁剪、模糊、修复等重新编码方案，位置还可能移动；当前版本遵守“不转码”规则，不会假装已经清除这类画面水印。如果遇到这种情况，请提供一张能看清水印位置的截图，再决定是否单独增加有损处理模式。

## 音频与多语言

独立音频列表包含：

- 语言；
- 编码与码率；
- 声道与采样率；
- 预计大小；
- yt-dlp 原始格式 ID。

支持输入多个编号，例如：

```text
1,3,5
```

选择多条音轨时会优先推荐 MKV。`dw` 不进行音视频转码；如果所选编码与 MP4 或 WebM 不兼容，会说明原因并要求重新选择容器。

## 最终容器

每个下载任务提供四个选项：

1. 保持原格式/自动无损封装；
2. MP4；
3. MKV；
4. WebM。

程序会根据所选视频和音频编码显示推荐方案。“保持原格式”在独立音视频流需要合并时，表示自动选择可以无损容纳全部所选流的容器。

## 图片下载

媒体格式选择完成后，`dw` 会询问是否下载图片。选择“是”后：

- 列出网站返回的所有缩略图；
- 去除网址、尺寸和大小均完全相同的重复项；
- 支持单选、多选或输入 `a` 下载全部；
- 使用 FFmpeg 转换成独立 PNG；
- PNG 转换成功后删除临时原图。

图片命名格式：

```text
视频标题_thumbnail_编号_宽x高.png
```

## 播放列表

播放列表每页显示 30 项。选择语法如下：

```text
3          # 单项
1,3,8      # 多项
2-10       # 范围
1,3,5-9    # 混合
a          # 全部
```

第一次成功读取的所选视频用于建立统一规则，之后自动应用：

- 优先匹配相同容器、分辨率、编码、帧率和 HDR；
- 没有完全匹配时，选择不超过目标清晰度的最接近格式；
- 没有合适格式时暂停并让用户手动选择；
- 音轨优先按语言、编码、码率、采样率和声道匹配；
- 缺少某种语言时下载当前可用语言，并在最终汇总中说明；
- 图片按分辨率、大小和列表顺序匹配；
- 某一项失败时记录实际原因，继续处理其余项目。

如果用户主动取消整个播放列表，本次列表任务已经完成的成品也会全部删除；以前其他任务下载的文件不受影响。

## Cookies

每次开始下载时提供两种策略，不会直接访问浏览器数据库：

1. **手动 `cookies.txt`（默认）**：从下表的固定路径读取 Netscape 格式文件，菜单直接回车即选中。
2. **不使用 Cookies**：适合公开且无验证的内容。

手动文件路径：

| 平台 | 固定路径 |
|---|---|
| Windows | `%USERPROFILE%\cookies.txt` |
| Debian | `/root/cookies.txt` |

Debian 建议限制权限：

```bash
chmod 600 /root/cookies.txt
```

手动文件存在时会显示“已发现并启用 cookies”；不存在时会显示“未检测到 cookies”并要求重新选择。错误明确指向登录会话或 Cookies 时，程序会建议重新导出并更新手动文件；无法确定时保留 yt-dlp 的实际失败原因，不会武断归因。

Windows 版处理新片场时，如果普通 yt-dlp 请求收到 `403 Forbidden` 或 `406 Not Acceptable`，会询问 `是否启动新片场 Chrome 辅助模式（推荐）`。输入 `y` 后：

1. `dw` 启动一个只供当前任务使用的独立 Chrome 配置目录，不读取或修改日常 Chrome 配置；
2. 如果已选择手动 `cookies.txt`，只把其中属于 `xinpianchang.com` 的 Cookie 通过本机回环接口导入该临时 Chrome；
3. 页面若出现勾选、验证码或登录，由用户本人在打开的窗口中完成；无需在 PowerShell 敲回车，程序会自动检测；
4. 页面通过后读取作品的 `vid/appKey`；临时会话中新产生且仅属于新片场的 Cookie 只由本次任务使用，`dw` 不另行导出或覆盖手动 `cookies.txt`；
5. `dw` 直接请求新片场官方媒体接口，并照常列出全部真实可下载版本；
6. Chrome 窗口、临时配置和其中的 Cookie 随即删除。清理失败时会明确列出残留路径。

辅助 Chrome 同样遵循国内站点直连策略。辅助失败后仍会保留真实原因，并询问是否使用系统代理重试。该自动辅助目前仅用于 Windows 10/11 的新片场链接；Debian 版仍使用手动 `cookies.txt` 和 yt-dlp 原生提取流程。

`cookies.txt` 含有敏感登录凭据，不要上传到本仓库或发送给他人。

## 国内网站、系统代理与 GitHub 镜像

### 目标视频网站的连接策略

- 抖音、小红书、新片场、哔哩哔等国内站点默认向 yt-dlp 传入空代理，优先使用直连/真实 IP；
- 如果直连请求失败，`dw` 会显示真实错误，再询问 `是否使用系统代理重试`；只有输入 `y` 才切换；
- 如果 v2rayN、Clash 等启用了 TUN/虚拟网卡模式，应用程序内的空代理不一定能绕过 TUN。`dw` 会警告，但你仍需在代理软件规则中将目标域名设为 `DIRECT`。

### GitHub 安装与更新回退

安装器和依赖更新会尝试官方 GitHub，失败后回退至内置源：`cdn.jsdelivr.net`、`gh-proxy.com`、`ghfast.top`、`ghproxy.net`。镜像的可用性会变化，不保证每个地区和每个时刻都可用。

允许自定义一个或多个 HTTPS 镜像，多个值用分号、逗号或换行分隔：

```text
# 前缀型：将完整 GitHub URL 追加在后面
https://mirror.example/

# 模板型：用完整 GitHub URL 替换 {url}
https://mirror.example/proxy?target={url}
```

Windows PowerShell 当前会话：

```powershell
$env:DW_GITHUB_MIRRORS = 'https://mirror.example/{url};https://backup.example/'
```

Debian 当前 root shell：

```bash
export DW_GITHUB_MIRRORS='https://mirror.example/{url};https://backup.example/'
```

也可把每个镜像单独写一行，持久保存到 Windows `%LOCALAPPDATA%\yt-dlp-dw-data\github-mirrors.txt` 或 Debian `/var/lib/dw/github-mirrors.txt`。自定义镜像始终最先尝试；格式无效或请求失败时会明确显示 `你提供的镜像域名不可使用`，然后继续尝试官方与内置回退源。

> 内置加速站和用户自定义镜像都是第三方服务。它们可以看到请求的 GitHub URL，也是下载信任边界的一部分。依赖发布文件仍会执行 SHA-256/发布摘要校验，但请只配置你信任的镜像。

## 直播

遇到正在直播、即将开始或持续直播的链接时，`dw` 会询问是录制还是取消。即将开始的直播选择录制后，程序会等待直播可用。

- 第一次按 `Ctrl+C`：停止录制，等待 yt-dlp/FFmpeg 完成封装并保留文件；
- 5 秒内再次按 `Ctrl+C`：强制取消并删除本次任务的全部文件。

默认从开始执行 `dw` 时的直播位置录制。yt-dlp 的“从直播起点下载”能力只支持部分网站且仍属实验功能，因此本程序没有全局强制启用。

## 文件名与输出位置

成品不创建视频子目录。Windows 使用当前用户的 Downloads 已知文件夹（包括 OneDrive 等重定向位置），Debian 使用 `/root`。

```text
# Windows
C:\Users\你的用户名\Downloads\视频标题.mp4
C:\Users\你的用户名\Downloads\视频标题 (1).mp4
C:\Users\你的用户名\Downloads\视频标题_thumbnail_1_1920x1080.png

# Debian
/root/视频标题.mp4
/root/视频标题 (1).mp4
/root/视频标题_thumbnail_1_1920x1080.png
```

- 保留中文、日文及其他 Unicode 字符；
- 替换路径分隔符、控制字符和不安全字符；
- Windows 自动处理 `CON`、`NUL`、`COM1`、`LPT1` 等保留设备名；
- 自动缩短过长文件名；
- 同名文件使用 `(1)`、`(2)` 依次改名，从不覆盖已有文件。

程序不下载或嵌入字幕、弹幕、简介、章节、封面、评论或 info JSON 等元数据。

## 失败、取消与事务清理

每个项目先在专用任务目录完成下载、合并、音轨整理、媒体校验和 PNG 转换，全部成功后才移动到成品目录。Windows 任务目录位于 `%LOCALAPPDATA%\yt-dlp-dw-data\tasks`，Debian 位于 `/var/lib/dw/tasks`。

- 单视频失败：清理该任务的全部临时文件并显示原因；
- 播放列表单项失败：清理该项，记录原因并继续；
- 普通任务主动取消：删除本次任务产生的全部文件；
- 播放列表主动取消：删除本批次已完成的全部成品；
- 启动时：自动清理由异常断电或进程崩溃留下的专用任务目录。

下载完成后显示最终路径、文件大小、处理耗时、视频/音频/容器摘要，以及播放列表失败和缺失语言汇总。

## 自动更新

执行下载前，`dw` 每 24 小时最多检查一次：

- yt-dlp nightly；
- Deno；
- yt-dlp 官方 FFmpeg/FFprobe 构建；
- Windows 便携 aria2c（Debian 使用 APT 安装的 aria2）。

依赖查询和 GitHub Release 下载同样使用自定义镜像、官方源和内置回退源。应用自身不会静默更新：只有用户在主菜单选择 `3. 更新 dw` 后，才会下载并运行程序安装器。更新完成后重新输入 `dw`；下载清单、设置、成品和手动 Cookies 均保留。

## 安全卸载

输入 `dw`，选择 `2. 卸载 dw`。卸载前会显示下载文件数量、总大小、应用目录和状态目录；Debian 还会显示确认由 `dw` 新安装的软件包。

必须完整输入：

```text
确认卸载并删除全部下载
```

卸载行为：

- 删除清单中由 `dw` 创建且仍位于原路径的全部视频和图片；
- 使用设备号和 inode 检查文件是否已被替换，避免误删同名新文件；
- 不搜索或删除用户手动移动、改名的文件；
- Windows 删除 `%LOCALAPPDATA%\yt-dlp-dw`、`%LOCALAPPDATA%\yt-dlp-dw-data`、受管理的 WindowsApps 即时别名和当前用户 PATH 项；
- Debian 删除 `/opt/dw`、`/var/lib/dw`、`/usr/local/bin/dw`，并移除安装器确认新增的软件包；
- Debian 不执行全局 `apt autoremove`，不清理公共 APT 索引、缓存或系统日志；
- 单独询问是否删除当前平台固定位置的 `cookies.txt`，默认保留；
- 任何项目删除失败时保留程序和清单以便重试，并逐项显示残留，不会虚报成功；
- 成功卸载后不留下卸载日志。

手动移动或改名的下载文件无法被安全追踪，需要用户自行删除。断电、磁盘故障或强制杀死卸载进程也可能导致部分清理未完成。

## 安装目录

### Windows

| 路径 | 用途 |
|---|---|
| `%LOCALAPPDATA%\yt-dlp-dw\dw.py` | 主程序 |
| `%LOCALAPPDATA%\yt-dlp-dw\python\` | 隔离的官方嵌入式 Python |
| `%LOCALAPPDATA%\yt-dlp-dw\bin\` | yt-dlp、Deno、FFmpeg、FFprobe、aria2c |
| `%LOCALAPPDATA%\yt-dlp-dw\update-windows.ps1` | 主菜单更新使用的受管理更新器 |
| `%LOCALAPPDATA%\yt-dlp-dw\command\dw.cmd` | 命令入口，加入当前用户 PATH |
| `%LOCALAPPDATA%\Microsoft\WindowsApps\dw.cmd` | 受管理的即时命令别名；卸载时一并删除 |
| `%LOCALAPPDATA%\yt-dlp-dw-data\cache\` | 专用缓存 |
| `%LOCALAPPDATA%\yt-dlp-dw-data\tasks\` | 下载事务临时目录 |
| `%LOCALAPPDATA%\yt-dlp-dw-data\downloads.json` | 卸载使用的下载文件清单 |
| `%LOCALAPPDATA%\yt-dlp-dw-data\github-mirrors.txt` | 用户可选的持久 GitHub 镜像列表 |
| `%LOCALAPPDATA%\yt-dlp-dw-data\update-source.json` | 主菜单更新所跟随的仓库和版本标识 |
| `%USERPROFILE%\Downloads\` | 默认成品位置，可能被 Windows 重定向 |
| `%USERPROFILE%\cookies.txt` | 用户可选提供的 Cookies |

### Debian

| 路径 | 用途 |
|---|---|
| `/usr/local/bin/dw` | 命令入口 |
| `/opt/dw/dw.py` | 主程序 |
| `/opt/dw/bin/` | 隔离的 yt-dlp、Deno、FFmpeg、FFprobe（aria2c 位于 `/usr/bin`） |
| `/var/lib/dw/cache/` | yt-dlp 与 Deno 专用缓存 |
| `/var/lib/dw/tasks/` | 下载事务临时目录 |
| `/var/lib/dw/downloads.json` | 卸载使用的下载文件清单 |
| `/var/lib/dw/github-mirrors.txt` | 用户可选的持久 GitHub 镜像列表 |
| `/var/lib/dw/update-source.json` | 主菜单更新所跟随的仓库和版本标识 |
| `/root/` | 最终视频和 PNG 图片 |
| `/root/cookies.txt` | 用户可选提供的 cookies，不属于程序生成文件 |

## 常见问题

### 显示的文件大小为什么是“未知”？

部分网站或流媒体清单不会提前提供文件大小。该格式仍可正常选择和下载。

### 为什么下载时仍然跑不满千兆宽带？

`dw` 已默认启用 8 路连接/片段并发，但网站可能限制单 IP、单视频或单 CDN 节点速度，也可能不支持 HTTP Range。8 路会改善允许分片的资源，不代表能绕过服务端限速；速度显示以 yt-dlp/aria2c 的实时输出为准。

### 为什么下载像卡住，按回车后才继续？

旧版常见原因是经典 Windows 控制台进入“快速编辑/文字选择”状态，整个控制台进程会暂停。新版会在启动时关闭该暂停模式，并切断下载子进程的标准输入，不再依靠敲回车推进。若当前窗口已经选中了文字，先按 `Esc`，选择主菜单 `3` 更新，然后重新启动 `dw`。

### 为什么不能选择 MP4？

所选视频或音频编码可能无法无损封装进 MP4。程序不会偷偷转码，因此会推荐 MKV 并要求重新选择。

### 网站突然不能下载怎么办？

先再次运行 `dw`，依赖检查到期后会更新 yt-dlp nightly。若仍失败，查看程序保留的 yt-dlp 实际错误；登录内容请重新导出并更新 Windows `%USERPROFILE%\cookies.txt` / Debian `/root/cookies.txt`。

### 抖音精选页或完整分享口令怎么输入？

直接粘贴全部文案，不需要手动删除前后文字。`dw` 会保留 `v.douyin.com` 短链接，并将 `https://www.douyin.com/jingxuan?modal_id=<数字>` 转换为 yt-dlp 可识别的 `/video/<数字>` 链接。抖音如提示 `Fresh cookies needed`，请在 Chrome 中登录后重新导出 `%USERPROFILE%\cookies.txt`。

### 新片场链接返回 403 或 406 怎么办？

Windows 版会先询问是否启动新片场 Chrome 辅助模式。输入 `y`，在弹出的独立 Chrome 窗口中完成页面勾选、验证码或登录；完成后不要关闭窗口，也不用敲回车，`dw` 会自动读取媒体版本并关闭窗口。该流程不复制日常 Chrome Cookie 数据库，临时配置会在任务后删除。若辅助直连失败，程序会显示原因，再询问是否使用系统代理。

### 为什么下载后仍能看到 Bilibili、小红书或上传者水印？

先确认它是文件属性中的标签，还是画面里的文字/图案。文件标签会在最终无损重封装时删除；烧进画面的内容不能在“不转码”前提下无损去掉。请截取一帧并标出水印位置，才能评估裁剪、模糊或修复等有损方案。

### 明明关闭了代理参数，国内站点为什么仍走 Clash/v2rayN？

代理软件的 TUN 模式在系统网卡层截获流量，不受 yt-dlp 的空代理参数完全控制。请在 Clash/v2rayN 规则中将目标国内域名设为 `DIRECT`。

### 自定义 GitHub 镜像失效会不会中止安装？

不会立即中止。程序会显示 `你提供的镜像域名不可使用`，然后尝试官方源和内置回退源；只有所有来源都失败才终止。

### 为什么卸载没有删除我移动过的视频？

安全清单只记录原始路径。主动扫描 Downloads、`/root` 或其他目录可能误删用户文件，因此程序不会尝试猜测移动后的文件位置。

## 开发与验证

本项目不依赖第三方 Python 包。运行测试：

```bash
python3 -m py_compile src/dw.py
python3 -m unittest discover -s tests -v
bash -n install.sh
```

GitHub Actions 会分别在 Ubuntu 和 Windows 上使用 Python 3.11、3.13 执行单元测试，并检查 Bash 与 PowerShell 安装器语法。

## 上游项目与许可证

- [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [yt-dlp/FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds)
- [denoland/deno](https://github.com/denoland/deno)
- [aria2/aria2](https://github.com/aria2/aria2)
- [Python](https://www.python.org/)

本仓库代码使用 MIT License。安装器下载的第三方程序分别遵循各自许可证；FFmpeg 构建的具体许可信息以其发布包为准。
