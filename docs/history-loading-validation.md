# 历史对话加载与耗时验证

验证日期：2026-11-01。使用隔离配置和示例消息，不请求模型，不修改用户对话。

## 行为与范围

- 历史占位覆盖正文视口，不进入消息布局；分批首屏渲染完成并应用已有滚动位置后移除，无额外等待或动画。
- 读取或首屏渲染失败时退出占位，显示实际原因与重试入口；过期加载回调不揭开新一轮加载，也不改变其他会话的位置。
- 复用实时整轮单调时钟耗时，按已有请求 ID 保存到会话展示元数据 `ui_run_elapsed_v1`。仅精确匹配单一请求且数值有效时显示用时；旧记录、混合请求及非法值不补算。
- 原有思考原文、工具详情、过程延迟加载与折叠方式、最终正文、阅读落点保持现有行为。运行时模型消息、系统提示词、工具定义及 prompt prefix 均未改变。

## 自动验证

```powershell
New-Item -ItemType Directory -Force -Path .tmp/history-loading-ux | Out-Null
$env:TEMP = (Resolve-Path '.tmp/history-loading-ux').Path
$env:TMP = $env:TEMP
$env:APPDATA = Join-Path $env:TEMP 'isolated-roaming'
$env:LOCALAPPDATA = Join-Path $env:TEMP 'isolated-local'
$env:COWORK_EXTERNAL_BACKGROUND_SERVICES = '0'
$env:QT_QPA_PLATFORM = 'offscreen'
.venv/Scripts/python.exe -X utf8 -B -m pytest -q test/test_history_performance.py test/test_history_paging.py test/test_child_window_lifecycle.py --tb=short --disable-warnings -p no:cacheprovider
```

结果：34 项测试与 13 项子测试通过。覆盖真实耗时保存与重读、消息内容不变、重复终态、旧记录与非法耗时、过期回调、后台会话完成、渲染失败与重试入口、思考原文保留、分页顺序、身份标题与子控件生命周期。历史测试数据按用例隔离；只模拟提交/保存的用例保持模拟至退出，避免测试收尾把未持久化的合成历史再次写入数据库。

## Windows 界面验证

```powershell
.venv/Scripts/python.exe -X utf8 -B scripts/render_history_loading.py --native --output docs/images/history-loading
```

实际平台为 Windows Qt 原生窗口，`QT_SCALE_FACTOR=1`，设备像素比为 `1.5`，逻辑 DPI 为 `96`。宽窗口为 `1360×860`、窄窗口为 `980×760` 逻辑像素；同时做过离屏 `1x` 检查。不是将 `QT_SCALE_FACTOR=1.5` 当作系统 150% 缩放。

从主题生效后的控件构造开始记录 `QEvent.Show`，再显示宿主并插入历史控件；未发现非预期顶层窗口。浅深主题、加载层随视口缩放、连续切换、草稿保留、思考原文、失败入口及主题预览取消均通过。取消预览后主题存储文件未改变。机器可读结果见 [原生报告](images/history-loading/report-1x-windows.json)。

| 状态 | 截图 |
| --- | --- |
| 浅色加载 | [加载占位](images/history-loading/loading-light-1x-windows.png) |
| 浅色完成 | [真实用时](images/history-loading/ready-light-1x-windows.png) |
| 过程展开 | [沿用思考过程](images/history-loading/process-light-1x-windows.png) |
| 深色窄窗口 | [完成](images/history-loading/ready-dark-narrow-1x-windows.png)、[加载](images/history-loading/loading-dark-narrow-1x-windows.png)、[失败](images/history-loading/error-dark-narrow-1x-windows.png) |

示例截图中的 119 秒为固定验证数据；真实运行耗时的保存和重读通过自动测试验证。未进行真实模型请求及整库历史兼容性测试。
