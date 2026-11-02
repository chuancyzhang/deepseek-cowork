# 历史提问跳转验证

验证日期：2026-11-02。

## 问题与引入原因

首次打开长对话，目录显示全部提问，但正文只物化最近 12 个渲染范围。直接点击未物化的旧提问时，界面不移动；再次点击提示“历史分页正在加载，请稍后重试”，向上翻阅也无法继续。

Git 提交 `5f9f7d2c`（2026-08-09 14:46 +0800）新增提问导航。`fb57e46a`（2026-08-18 22:28 +0800，`fix: 统一用户消息投影并补齐模型过载与线程异常收尾`）随后为历史分页加入加载批次校验，但只更新滚动入口，遗漏导航入口。后者没有初始化或捕获 `history_page_token`，回调将缺失值视为 0，与实际历史批次不符而直接返回；启动时设定的加载标记及显示计数无法收尾。先滚动一次会初始化批次，掩盖此问题。

以上是源码引入时间，不代表首次发布或用户首次遇到的时间。

## 修复边界

- 滚动、目录、左侧提问导航及搜索复用同一个分页启动函数，绑定当前历史批次并在定时器中捕获。
- 过期回调保留隔离保护，显式传入的批次 0 不被当前批次覆盖；重载时清理旧分页状态。
- 加载期间再次点击，复用当前队列，仅补充缺失范围；完成后定位最后选择的提问。
- 向上翻阅保留阅读锚点，主动点击最终滚动到目标提问。日志覆盖启动、排队、完成、过期回调和错误。
- 不修改持久消息、模型历史、模型请求或 prompt prefix。

## 回归验证

新增的前三项回归在修复前全部失败：首次打开直接跳转无法完成、导航回调错误使用重载后的新批次、显式批次 0 错误消费新分页。

最终相关测试 **69 项通过、9 个子测试通过**。包含首次保存并重新打开 10 轮对话后，从目录、左侧导航、滚动和搜索访问未显示历史；检查正文目标的实际坐标，覆盖第一问及中间提问。另覆盖连续点击、分页期间选择更早目标、部分失败后重试、消息顺序、旧回调、重载状态清理及滚轮归属。没有模型调用或真实用户数据修改。

```powershell
New-Item -ItemType Directory -Force .tmp/history-navigation | Out-Null
$env:TEMP = "$PWD\.tmp\history-navigation"
$env:TMP = $env:TEMP
$env:APPDATA = "$env:TEMP\appdata"
$env:LOCALAPPDATA = "$env:TEMP\localappdata"
$env:QT_QPA_PLATFORM = 'offscreen'
$env:COWORK_EXTERNAL_BACKGROUND_SERVICES = '0'
.venv/Scripts/python.exe -u -X utf8 -B -m pytest -q -p no:cacheprovider test/test_history_paging.py test/test_conversation_render.py test/test_history_performance.py test/test_file_workbench_question_navigator.py::QuestionNavigatorTest test/test_file_workbench_question_navigator.py::LongQuestionNavigatorTest test/test_chat_scroll_regressions.py --tb=short -o faulthandler_timeout=45
```

扩展检查曾停在旧导航布局测试触发的首次模型配置弹窗；该测试现明确关闭无关引导和后台服务，再运行上述相关集合通过。完整应用测试套件不在本次通过范围内。

## 视觉检查

Windows 上使用 Qt `offscreen`、隔离应用数据及实际 `MainWindow`，加载微软雅黑字体后检查浅色 1280×800、深色 1000×800，分别设 `QT_SCALE_FACTOR=1` / `1.5`，实际 DPR 为 1 / 1.5。确认首条目标位于视口顶部约 18 逻辑像素，中间目标位于约 20 逻辑像素；主题预览取消成功。截图使用隔离示例正文。

这证明上述离屏窗口的布局及定位行为，不替代另一台机器的发行包、Windows 原生窗口和系统 DPI 验收。

| 场景 | Qt 1 倍 | Qt 1.5 倍 |
| --- | --- | --- |
| 直接定位第一问 | [浅色](images/history-navigation/light-jump-100.png) | [浅色](images/history-navigation/light-jump-150.png) |
| 定位第三问 | [深色小窗口](images/history-navigation/dark-jump-100.png) | [深色小窗口](images/history-navigation/dark-jump-150.png) |
