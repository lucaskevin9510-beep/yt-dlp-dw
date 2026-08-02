# Windows 10/11 使用说明

本页说明如何在 Windows 10/11 x64 上安装、运行、更新和卸载 `yt-dlp-dw`。Windows 版与 Debian 版使用同一套交互逻辑，支持视频与音频版本选择、多语言音轨、播放列表、完整分享文案抽链、手动 cookies.txt、国内站点直连、PNG 图片和直播录制。

## 系统要求

- Windows 10 或 Windows 11；
- x64/amd64 处理器，不支持 x86、ARM64；
- Windows PowerShell 5.1 或更高版本；
- 能访问至少一个 GitHub 官方/回退源、Python.org 和目标视频网站；
- 不需要管理员权限，也不需要预先安装 Python、yt-dlp、Deno、FFmpeg 或 aria2c。

程序按当前 Windows 用户独立安装，不会安装系统级 Python，也不会调用 Winget、Chocolatey 或修改机器级 PATH；只管理当前用户 PATH 和自己的命令别名。

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
3. 安装 Windows x64 版 yt-dlp nightly、Deno、FFmpeg、FFprobe 和经过固定 SHA-256 校验的 aria2c；
4. 使用上游提供的 SHA-256 校验文件验证所有媒体依赖；
5. 创建 `dw.cmd`，把专用命令目录加入当前用户 PATH；
6. 在已有的 `%LOCALAPPDATA%\Microsoft\WindowsApps` 中创建受管理的即时别名，让当前窗口及 Windows Terminal 新标签页直接识别 `dw`。

大文件下载会实时显示百分比、已下载/总大小和当前平均速度，例如：

```text
Progress ffmpeg.zip: 45.2% | 38.6 MiB / 85.4 MiB | 5.1 MiB/s
```

如果镜像没有返回文件总大小，安装器仍会持续显示已下载大小和速度。

正常情况下，安装结束后当前窗口和新 PowerShell 标签页都能直接输入 `dw`，不需要再执行 `$env:Path += ...`。如果 WindowsApps 中已有不属于本程序的同名 `dw.cmd`，安装器会保护原文件并给出警告；此时可直接运行：

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
3. 更新 dw
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
1. 使用我手动上传的 cookies.txt（默认）
2. 不使用 Cookies
请选择 Cookies 使用方式 [直接回车默认 1]：
```

- 直接按回车或选 `1`：读取 `%USERPROFILE%\cookies.txt`，文件不存在时要求先上传；
- 选 `2`：本次任务不使用 Cookies。

yt-dlp 在 Windows 上读取 Chrome Cookie 数据库可能长期报 `Could not copy Chrome cookie database`，并非关闭浏览器就一定能解决。因此 `dw` 不再显示这个不可靠选项，统一使用用户主动导出的 Netscape `cookies.txt`。

### 4. 选择视频

视频列表按 MP4、WebM、其他格式分组。每组内 H.264 优先，再按编码和预计大小从高到低排列，大小未知时使用分辨率、帧率和码率继续排序。输入一条视频前面的脚本编号：

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

程序会显示推荐值。直接按回车默认选择 `1`，让程序自动决定；也可输入 `2`、`3`、`4` 指定。程序不会转码；编码与 MP4/WebM 不兼容时会拒绝该选择并推荐 MKV。

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

## 下载速度与“按回车才继续”

普通 HTTP/HTTPS 媒体文件默认由 aria2c 使用 8 个连接分片下载；HLS/DASH 分段流使用 yt-dlp 原生的 8 片段并发。直播保持原生下载，避免外部下载器干扰连续录制。8 路指网络连接/片段并发，并不保证跑满千兆带宽；网站限速、CDN、HTTP Range 支持、磁盘和本地线路都会影响结果。

程序启动时会关闭经典 Windows 控制台的“快速编辑”暂停，并让 yt-dlp/aria2c 的标准输入保持关闭，因此下载不应再依靠反复敲回车推进；`Ctrl+C` 取消仍可正常使用。如果当前窗口已经处于文字选择状态，先按 `Esc` 退出选择，再更新并重新启动 `dw`。

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

将 Netscape 格式的 cookies 文件放在下面的位置，运行时在 Cookies 菜单直接按回车即可使用：

```text
%USERPROFILE%\cookies.txt
```

PowerShell 示例：

```powershell
Copy-Item 'D:\你的路径\cookies.txt' "$env:USERPROFILE\cookies.txt"
dw
```

只有看到“已发现并启用 cookies”才表示手动文件已使用。Cookies 等同登录凭据，请勿上传到 GitHub、网盘或发送给其他人。

新片场首次访问如果需要页面勾选/验证，先在 Chrome 打开原链接并完成操作，然后重新导出并覆盖上述 `cookies.txt`。若直连仍失败，`dw` 会显示实际原因，再询问是否使用系统代理。

## 平台标签与水印

最终文件会经过一次 `-c copy` 无损重封装，容器元数据、章节、封面附件和平台标签会被删除，音视频流不会重新编码。yt-dlp 明确把某个候选流标为 `watermarked` 且还存在干净流时，`dw` 会自动隐藏带水印候选；抖音/TikTok 的水印 `download_addr` 就按此规则处理。

画面本身已经包含的文字或图案无法靠删标签无损擦除。Bilibili、小红书或上传者烧进每帧画面的水印，需要裁剪、模糊或修复并重新编码，可能损失画质；当前脚本遵守“不转码”原则，不会声称已经删除这类内容。需要处理时，请先提供一张截图确认水印位置和是否移动。

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

每次下载前，程序每 24 小时最多检查一次 yt-dlp nightly、Deno、FFmpeg 和 aria2c。需要更新 `dw` 主程序时，运行 `dw` 并在主菜单选择：

```text
3. 更新 dw
```

程序会显示安装器下载进度，当前 `dw` 退出后自动完成安装；更新结束后重新输入 `dw`。无需再次粘贴一键安装命令，下载清单、状态、成品和 `%USERPROFILE%\cookies.txt` 都会保留。只有从缺少菜单更新组件的旧版本第一次升级时，才需要重新运行一次本文开头的一键安装命令。

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

卸载器会单独询问是否删除 `%USERPROFILE%\cookies.txt`，默认保留。Python 主进程退出后，PowerShell 清理器会继续删除应用、便携依赖、状态、缓存、两个受管理的命令入口和用户 PATH 项，并明确列出任何未能删除的路径。成功卸载不保留日志。

卸载后应重新打开终端以刷新 PATH。手动移动或改名的成品不再属于清单原路径，程序不会搜索或猜测其新位置。

## 安装路径

| 路径 | 用途 |
|---|---|
| `%LOCALAPPDATA%\yt-dlp-dw\dw.py` | 主程序 |
| `%LOCALAPPDATA%\yt-dlp-dw\python\` | 官方嵌入式 Python |
| `%LOCALAPPDATA%\yt-dlp-dw\bin\` | yt-dlp、Deno、FFmpeg、FFprobe、aria2c |
| `%LOCALAPPDATA%\yt-dlp-dw\update-windows.ps1` | 菜单更新使用的受管理更新器 |
| `%LOCALAPPDATA%\yt-dlp-dw\command\dw.cmd` | `dw` 命令入口 |
| `%LOCALAPPDATA%\Microsoft\WindowsApps\dw.cmd` | 立即生效的受管理别名，卸载时删除 |
| `%LOCALAPPDATA%\yt-dlp-dw-data\cache\` | 专用缓存 |
| `%LOCALAPPDATA%\yt-dlp-dw-data\tasks\` | 下载事务临时目录 |
| `%LOCALAPPDATA%\yt-dlp-dw-data\downloads.json` | 安全卸载清单 |
| `%LOCALAPPDATA%\yt-dlp-dw-data\github-mirrors.txt` | 用户可选的持久 GitHub 镜像列表 |
| `%LOCALAPPDATA%\yt-dlp-dw-data\update-source.json` | 菜单更新使用的仓库和版本标识 |
| `%USERPROFILE%\Downloads\` | 默认成品位置，可能被 Windows 重定向 |
| `%USERPROFILE%\cookies.txt` | 用户可选 Cookies，不属于程序生成文件 |

## 常见问题

### 下载会停住，必须反复按回车

先按 `Esc` 检查是否退出了控制台文字选择状态，再通过主菜单 `3` 更新到新版。新版会关闭经典控制台的快速编辑暂停，并禁止下载子进程等待终端输入。如果仍复现，请保留卡住前最后 20 行输出，并说明使用的是 Windows PowerShell、CMD 还是 Windows Terminal。

### 已启用 8 路为什么仍然只有几 MiB/s

8 路只能加速允许并发分片或 HTTP Range 的资源，不能绕过网站针对 IP、账号、视频或 CDN 节点的限速。直播也不会交给 aria2c。请以终端中 yt-dlp/aria2c 的实时速度为准，并用不同网站的资源交叉测试。

### PowerShell 显示脚本执行被禁止

使用文档中的完整安装命令。`-ExecutionPolicy Bypass` 只作用于该次 PowerShell 进程，不会永久修改系统策略。

### 安装成功但找不到 `dw`

新版安装器会在 WindowsApps 命令目录创建即时别名，安装后无需手动追加 PATH。先重新运行最新版安装命令；若 WindowsApps 已有不属于 `dw` 的同名文件，安装器会保护它并报警，此时直接运行：

```powershell
& "$env:LOCALAPPDATA\yt-dlp-dw\command\dw.cmd"
```

### 安全软件拦截 yt-dlp 或 FFmpeg

依赖来自上游官方 GitHub Releases，并经过上游 SHA-256 校验。请先核对本仓库和下载来源，再根据你所使用的安全软件决定是否允许；不要从不明网站替换这些文件。

### 文件没有出现在普通 Downloads 目录

程序读取 Windows Downloads 已知文件夹。打开资源管理器，在地址栏输入 `shell:Downloads` 即可查看系统实际使用的位置。

### 登录内容下载失败

优先确认 Chrome 中可以正常播放，再重新导出 Cookies。确认文件名严格为 `%USERPROFILE%\cookies.txt`、格式为 Netscape cookies；在 `dw` 的 Cookies 菜单直接按回车。程序会保留 yt-dlp 的实际失败原因；只有错误明确指向 Cookies/登录会话时才提示更新。

### 抖音精选链接提示 Unsupported URL

可直接粘贴 `https://www.douyin.com/jingxuan?modal_id=...` 或带 `v.douyin.com` 短链接的整段分享口令。`dw` 会自动提取 URL，并将数字 `modal_id` 转换为 `/video/<id>`。若提示 `Fresh cookies needed`，请在 Chrome 登录后重新导出 `%USERPROFILE%\cookies.txt`。

### 新片场返回 403

先在 Chrome 打开同一新片场链接，完成首次页面勾选/验证并确认能播放，然后重新导出 `%USERPROFILE%\cookies.txt`。再次运行后如果直连仍失败，程序会询问是否使用系统代理。

### 下载后仍能看到 Bilibili 或小红书水印

文件属性里的平台标签会被最终无损重封装删除；如果水印已经出现在画面像素中，它不能在“不转码”前提下被无损去掉。请提供一张视频截图并标出位置，再评估是否值得增加会重新编码的裁剪、模糊或修复模式。

### Clash/v2rayN 开启时国内站点仍走代理

这通常是 TUN 模式在系统网卡层接管流量。请在代理软件中将目标站点域名设为 `DIRECT`；单靠 yt-dlp 的空代理参数无法绕过所有 TUN 配置。
