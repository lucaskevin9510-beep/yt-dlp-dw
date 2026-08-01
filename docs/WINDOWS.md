# Windows 10/11 使用说明

本页说明如何在 Windows 10/11 x64 上安装、运行、更新和卸载 `yt-dlp-dw`。Windows 版与 Debian 版使用同一套交互逻辑，支持视频与音频版本选择、多语言音轨、播放列表、完整分享文案抽链、智能 Chrome Cookies、国内站点直连、PNG 图片和直播录制。

## 系统要求

- Windows 10 或 Windows 11；
- x64/amd64 处理器，不支持 x86、ARM64；
- Windows PowerShell 5.1 或更高版本；
- 能访问至少一个 GitHub 官方/回退源、Python.org 和目标视频网站；
- 不需要管理员权限，也不需要预先安装 Python、yt-dlp、Deno 或 FFmpeg。

程序按当前 Windows 用户独立安装，不会安装系统级 Python，也不会调用 Winget、Chocolatey 或修改系统 PATH。

## 一键安装

打开 **PowerShell** 或 Windows Terminal，运行下面的自动回退命令。它会按顺序尝试 GitHub 官方源、jsDelivr 和三个加速源：

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

官方 GitHub 直连正常时的短命令：

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "Invoke-RestMethod 'https://raw.githubusercontent.com/lucaskevin9510-beep/yt-dlp-dw/main/install-windows.ps1' | Invoke-Expression"
```

安装器会完成以下工作：

1. 验证 Windows 版本和 CPU 架构；
2. 下载并校验 Python 3.13 x64 官方嵌入式运行时；
3. 安装 Windows x64 版 yt-dlp nightly、Deno、FFmpeg 和 FFprobe；
4. 使用上游提供的 SHA-256 校验文件验证所有媒体依赖；
5. 创建 `dw.cmd` 并把专用命令目录加入当前用户 PATH；
6. 在当前终端中立即启用 `dw` 命令。

如果安装后当前终端找不到 `dw`，关闭终端并重新打开，然后再输入 `dw`。也可以直接运行：

```powershell
& "$env:LOCALAPPDATA\yt-dlp-dw\command\dw.cmd"
```

## 安装前先审阅脚本

不希望直接通过管道执行时，可以先保存并查看：

```powershell
$installer = Join-Path $env:TEMP 'install-dw.ps1'
Invoke-WebRequest 'https://raw.githubusercontent.com/lucaskevin9510-beep/yt-dlp-dw/main/install-windows.ps1' -OutFile $installer
notepad.exe $installer
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $installer
Remove-Item -LiteralPath $installer -Force
```

## 完成一次普通视频下载

### 1. 启动

```powershell
dw
```

主菜单输入 `1`：

```text
========== dw 下载助手 ==========
1. 开始下载
2. 卸载 dw
0. 退出
请选择：1
```

### 2. 粘贴链接或完整分享文案

```text
请粘贴下载链接或完整分享文案：8.74 nqr:/ #恋爱脑 https://v.douyin.com/3qaI6648IrI/ 复制此链接……
```

不需要手动删除小红书、抖音等平台分享口令中的标题、表情或说明文字。程序会自动提取所有 HTTP(S) 链接、去重，并将抖音 `jingxuan?modal_id=...` 规范化为 `/video/...`。文案中有多个不同链接时，可输入 `1,3`、`1-4` 或 `a` 选择。

### 3. 选择 Cookies 方式

```text
Cookies 使用方式：
1. 同意智能读取 Chrome Cookies（推荐，仅本次任务）
2. 使用我手动上传的 cookies.txt
3. 不使用 Cookies
请选择 Cookies 使用方式：
```

- 选 `1`：明确授权 yt-dlp 在本次任务临时读取 Chrome，并按当前网站自动匹配 Cookies。`dw` 不导出、不上传、不长期保存浏览器 Cookies；
- 选 `2`：读取 `%USERPROFILE%\cookies.txt`，文件不存在时要求先上传；
- 选 `3`：本次任务不使用 Cookies。

### 4. 选择视频

视频列表按 MP4、WebM、其他格式分组，每组按分辨率、帧率和码率从高到低排列。输入一条视频前面的脚本编号：

```text
请选择一个视频编号：3
```

如果选择的版本已经带音频：

```text
所选视频自带音频，是否使用它 [Y/n]：
```

- 按回车或输入 `y`：保留自带音频；
- 输入 `n`：移除自带音频，然后选择独立音轨。

### 5. 选择音频

独立音频可以单选或多选：

```text
请选择音频编号（多个用逗号，全部输入 a）：1
请选择音频编号（多个用逗号，全部输入 a）：1,3
```

多语言、多音轨文件通常选择 MKV。播放列表后续项目缺少目标语言时，程序会使用仍存在的音轨并在最终汇总中说明。

### 6. 选择容器

```text
1. 保持原格式/自动无损封装
2. MP4
3. MKV
4. WebM
```

程序会显示推荐值。输入 `1` 让程序自动选择，或者输入 `2`、`3`、`4` 指定。程序不会转码；编码与 MP4/WebM 不兼容时会拒绝该选择并推荐 MKV。

### 7. 选择图片

```text
是否下载图片 [y/N]：
```

- 按回车或输入 `n`：不下载图片；
- 输入 `y`：列出图片，然后选择一个编号、多个编号或输入 `a` 下载全部。

所有选中图片都会转换为独立 PNG 文件。

### 8. 查看结果

成功后会显示路径、大小、耗时和格式摘要。Windows 成品保存在当前用户的 Downloads 已知文件夹；如果 Downloads 被重定向到 OneDrive 或其他位置，程序会使用重定向后的真实路径。

```text
C:\Users\你的用户名\Downloads\视频标题.mkv
C:\Users\你的用户名\Downloads\视频标题_thumbnail_1_1920x1080.png
```

最后输入 `y` 可以继续下一个链接，按回车或输入 `n` 返回主菜单。

## 输入语法

| 用途 | 输入示例 |
|---|---|
| 单个编号 | `3` |
| 多个编号 | `1,3,8` 或 `1，3，8` |
| 连续范围 | `2-10` |
| 混合范围 | `1,3,5-9` |
| 全部 | `a`、`all` 或 `全部` |
| 是 | `y`、`yes`、`是`、`1` |
| 否 | `n`、`no`、`否`、`0` |
| 播放列表下一页 | 按回车 |
| 停止浏览播放列表并进入选择 | `q` |

`[Y/n]` 表示直接按回车默认选择“是”；`[y/N]` 表示直接按回车默认选择“否”。

## 播放列表

播放列表每页显示 30 项：

```text
请选择播放列表项目（例如 1,3,5-9；全部输入 a）：1,3,5-9
```

第一项用于建立统一的视频、音频、容器和图片规则。后续项目优先完全匹配，没有完全匹配时选择不超过目标清晰度的最接近版本；无法安全匹配时会重新询问。单项失败不会阻止其余项目，最终统一汇总。

主动取消整个播放列表会删除本批次已经下载完成的全部成品，不影响以前任务的文件。

## Cookies

推荐在任务中选择“智能读取 Chrome Cookies”。请先确保 Chrome 能正常打开/播放目标页面。选择后 yt-dlp 会读取 Chrome 的本地 Cookie 数据库，再由标准域名和路径规则为当前 URL 选用对应 Cookies。

这项读取只在用户每次明确选择 `1` 后发生。`dw` 不会把浏览器 Cookies 导出到文件、上传网络或写入状态目录。若 Chrome 正在锁定 Cookie 数据库导致读取失败，保存浏览器工作后退出 Chrome，再重试。

如果选择手动方式，将 Netscape 格式的 cookies 文件放在：

```text
%USERPROFILE%\cookies.txt
```

PowerShell 示例：

```powershell
Copy-Item 'D:\你的路径\cookies.txt' "$env:USERPROFILE\cookies.txt"
dw
```

只有看到“已发现并启用 cookies”才表示手动文件已使用。Cookies 等同登录凭据，请勿上传到 GitHub、网盘或发送给其他人。

新片场首次访问如果需要页面勾选/验证，先在 Chrome 打开原链接并完成操作。若 `dw` 收到 403，会询问是否在完成验证后重新读取 Chrome Cookies。

## 国内站点直连与系统代理

抖音、小红书、新片场、哔哩哔等国内站点默认强制 yt-dlp 直连，不使用 Windows 系统代理。直连发生网络失败时，程序才会询问：

```text
是否使用系统代理重试 [y/N]：
```

直接回车或输入 `n` 会保持直连，绝不静默切换。如果 Clash、v2rayN 开启 TUN/虚拟网卡模式，流量可能在系统网卡层被代理，这时还需在代理软件中将目标国内域名设为 `DIRECT`。

## GitHub 加速镜像

安装器及依赖更新支持 GitHub 官方源、jsDelivr、gh-proxy.com、ghfast.top 和 ghproxy.net。这些第三方源的可用性可能随时变化。

用户自定义镜像可以使用“前缀型”或 `{url}` 模板型，必须是 HTTPS：

```powershell
$env:DW_GITHUB_MIRRORS = 'https://mirror.example/{url};https://backup.example/'
```

需要持久保存时，把每个镜像单独写一行到：

```text
%LOCALAPPDATA%\yt-dlp-dw-data\github-mirrors.txt
```

自定义镜像最先尝试。如果无效，会显示 `你提供的镜像域名不可使用`，然后回退到其他来源。依赖发布文件仍会执行 SHA-256/发布摘要校验，但第三方镜像本身仍属于下载信任边界，请只使用你信任的自定义服务。

## 直播录制

遇到正在直播、即将开始或持续直播的链接时，程序会询问是否录制。

- 第一次按 `Ctrl+C`：请求 yt-dlp/FFmpeg 安全停止并完成封装；
- 5 秒内再次按 `Ctrl+C`：强制结束整个下载进程树并删除本次任务文件。

如果直接关闭终端或强制结束 Windows，会在下次启动时清理专用任务目录，但已经来不及写入磁盘的数据无法恢复。

## 文件名和同名处理

- 保留中文、日文和其他 Unicode 标题；
- 替换 Windows 不允许的 `< > : " / \ | ? *` 及控制字符；
- 自动处理 `CON`、`NUL`、`COM1`、`LPT1` 等保留设备名；
- 删除结尾的空格和句点；
- 自动缩短过长名称；
- 同名文件依次保存为 `标题 (1).mp4`、`标题 (2).mp4`，永不覆盖。

## 更新

每次下载前，程序每 24 小时最多检查一次 yt-dlp nightly、Deno 和 FFmpeg。GitHub 查询和 Release 下载会使用自定义镜像、官方源与内置回退源。更新主程序或便携 Python 时，重新运行一键安装命令即可；下载清单和状态会保留。

## 安全卸载

运行：

```powershell
dw
```

主菜单选择 `2`，核对清单，然后完整输入：

```text
确认卸载并删除全部下载
```

> 完整确认后会删除清单中由 `dw` 下载且仍在原路径的全部视频和图片，不只是删除程序。需要保留的文件必须先移动或改名。

卸载器会单独询问是否删除 `%USERPROFILE%\cookies.txt`，默认保留。Python 主进程退出后，PowerShell 清理器会继续删除应用、便携依赖、状态、缓存、命令入口和用户 PATH 项，并明确列出任何未能删除的路径。成功卸载不保留日志。

卸载后应重新打开终端以刷新 PATH。手动移动或改名的成品不再属于清单原路径，程序不会搜索或猜测其新位置。

## 安装路径

| 路径 | 用途 |
|---|---|
| `%LOCALAPPDATA%\yt-dlp-dw\dw.py` | 主程序 |
| `%LOCALAPPDATA%\yt-dlp-dw\python\` | 官方嵌入式 Python |
| `%LOCALAPPDATA%\yt-dlp-dw\bin\` | yt-dlp、Deno、FFmpeg、FFprobe |
| `%LOCALAPPDATA%\yt-dlp-dw\command\dw.cmd` | `dw` 命令入口 |
| `%LOCALAPPDATA%\yt-dlp-dw-data\cache\` | 专用缓存 |
| `%LOCALAPPDATA%\yt-dlp-dw-data\tasks\` | 下载事务临时目录 |
| `%LOCALAPPDATA%\yt-dlp-dw-data\downloads.json` | 安全卸载清单 |
| `%LOCALAPPDATA%\yt-dlp-dw-data\github-mirrors.txt` | 用户可选的持久 GitHub 镜像列表 |
| `%USERPROFILE%\Downloads\` | 默认成品位置，可能被 Windows 重定向 |
| `%USERPROFILE%\cookies.txt` | 用户可选 Cookies，不属于程序生成文件 |

## 常见问题

### PowerShell 显示脚本执行被禁止

使用文档中的完整安装命令。`-ExecutionPolicy Bypass` 只作用于该次 PowerShell 进程，不会永久修改系统策略。

### 安装成功但找不到 `dw`

关闭并重新打开 PowerShell、CMD 或 Windows Terminal。仍然找不到时直接运行：

```powershell
& "$env:LOCALAPPDATA\yt-dlp-dw\command\dw.cmd"
```

### 安全软件拦截 yt-dlp 或 FFmpeg

依赖来自上游官方 GitHub Releases，并经过上游 SHA-256 校验。请先核对本仓库和下载来源，再根据你所使用的安全软件决定是否允许；不要从不明网站替换这些文件。

### 文件没有出现在普通 Downloads 目录

程序读取 Windows Downloads 已知文件夹。打开资源管理器，在地址栏输入 `shell:Downloads` 即可查看系统实际使用的位置。

### 登录内容下载失败

优先确认 Chrome 中可以正常播放，然后选择智能 Chrome Cookies。手动方式则确认文件名严格为 `%USERPROFILE%\cookies.txt`、格式为 Netscape cookies。程序会保留 yt-dlp 的实际失败原因；只有错误明确指向 Cookies/登录会话时才提示更新。

### 抖音精选链接提示 Unsupported URL

可直接粘贴 `https://www.douyin.com/jingxuan?modal_id=...` 或带 `v.douyin.com` 短链接的整段分享口令。`dw` 会自动提取 URL，并将数字 `modal_id` 转换为 `/video/<id>`。若提示 `Fresh cookies needed`，请选择智能 Chrome Cookies。

### 新片场返回 403

先在 Chrome 打开同一新片场链接，完成首次页面勾选/验证并确认能播放，再选择智能 Chrome Cookies。如果直连仍失败，程序会先询问是否重读更新后的 Cookies，再单独询问是否使用系统代理。

### Clash/v2rayN 开启时国内站点仍走代理

这通常是 TUN 模式在系统网卡层接管流量。请在代理软件中将目标站点域名设为 `DIRECT`；单靠 yt-dlp 的空代理参数无法绕过所有 TUN 配置。
