---
name: yt-dlp-wrapper
description: Universal video downloader wrapper for yt-dlp. Supports YouTube and many other sites.
description_cn: 通用视频下载工具 (yt-dlp 封装)，支持 YouTube、B站等多种视频网站。
license: MIT
type: bundled_plugin
source_type: bundled_plugin
default_enabled: false
created_by: ai-refactor
dependencies: ["yt-dlp"]
experience: ["Learned to install 'yt-dlp' via pip when missing."]
allowed-tools: download_video
---
# yt-dlp Wrapper

This skill wraps the powerful `yt-dlp` library to provide video downloading capabilities.

## Key Features
- **Auto Dependency Management**: Automatically detects and installs `yt-dlp` if it's missing in the current environment.
- **Universal Support**: Works with thousands of video sites supported by yt-dlp.
- **Smart Defaults**: Downloads the best available single format to avoid strict dependency on FFmpeg (though FFmpeg is recommended for best quality 1080p+ merges).
- **Workspace Output**: Downloads should stay inside the selected workspace unless God Mode or an explicit user-approved path allows otherwise.

## Tools

### download_video
Downloads a video or playlist from a given URL.

**Parameters:**
- `url`: The link to the video or playlist.
- `output_dir` (optional): The folder to save the video. Defaults to a `downloads` folder in the current directory.

**Returns:**
- A status message indicating the filename and location of the downloaded file.

## Current Runtime Notes
- Downloading creates files and may use the network, so it must be called directly and never through `parallel_tools`.
- Respect website terms, copyright, and user authorization before downloading media.
