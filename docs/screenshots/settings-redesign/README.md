# 设置界面与换肤验证

本目录为 2026-10-22 的真实 PySide6 控件截图，使用隔离的临时配置与空历史。未连接模型、企业账号或消息渠道；AI 候选截图使用本地构造的有效主题包。按本轮范围只完成浅色验收，不继续扩展深色主题。

## 交互与保存

- 八个设置分类覆盖原入口，旧标签仍能定位对应子页；搜索可直接定位日志、凭据等子页。
- 切换分类保留草稿；全局保存失败保留主题、阅读和回答偏好输入。快捷换肤更新主题基线，不将其他草稿误标为已保存。
- 阅读设置独立保存；仅修改阅读设置不重写主题包。应用自定义主题时不连带保存未确认的阅读草稿。
- 快捷换肤可撤销，不重建历史列表、不清空聊天输入。后台 AI 主题任务使用独立会话，沿用原任务管线。
- AI 候选只更新示例区域，迟到的旧版本不发布；应用和弃用前检查候选版本。取消设置草稿不会清除后来生成的其他 AI 候选。
- 模型默认值、实验功能、数据安全与主题工具继续使用既有保存、校验和授权规则。系统提示词、工具定义及顺序未改，运行时 prompt prefix 无变化。

## 检查结果

以下主批次通过 **96 项测试及 3 个子用例**；按范围排除 1 项专门的深色截图检查：

```powershell
.venv/Scripts/python.exe -m pytest test/test_settings_redesign.py test/test_default_model_settings.py test/test_long_context_ui.py test/test_theme_settings_ui.py test/test_theme.py test/test_theme_service.py test/test_theme_package.py test/test_theme_tools.py test/test_settings_ui_styles.py test/test_data_security_ui.py -q -k 'not dark_switch'
```

最后补充候选弃用版本校验与设置草稿归属后，重新运行候选/预览用例，**8 项通过**；其中包含 1 项新增用例，与上述批次合计去重 97 项。上一批主题运行时覆盖单独运行 `test/test_theme_runtime_coverage.py`，18 项通过。语法编译与 `git diff --check` 通过。

```powershell
.venv/Scripts/python.exe -m pytest test/test_settings_redesign.py test/test_theme_service.py test/test_theme_settings_ui.py -q -k 'preview or candidate'
.venv/Scripts/python.exe -m py_compile main.py core/theme.py core/theme_service.py core/theme_presets.py ui/theme_settings.py ui/theme_preview.py ui/settings_sections.py ui/theme_workspace.py scripts/render_settings_redesign.py
git diff --check
```

测试环境使用 `QT_QPA_PLATFORM=offscreen`、独立 `APPDATA` 和 `COWORK_EXTERNAL_BACKGROUND_SERVICES=0`；截图脚本自行创建临时数据，预置空配置避免迁移真实配置，并关闭不需要的启动后台服务。

## 截图与缩放

```powershell
.venv/Scripts/python.exe scripts/render_settings_redesign.py
$env:QT_SCALE_FACTOR='1.25' # 第二次设为 1.5
$env:COWORK_SETTINGS_SCALE_ONLY='1'
$env:COWORK_SETTINGS_SCREENSHOTS='D:/code/cowork/docs/screenshots/settings-redesign/scale-125'
.venv/Scripts/python.exe -X faulthandler scripts/render_settings_redesign.py
```

100% 覆盖全部分类和子页；125%/150% 检查外观、窄窗口与候选确认区域。窄窗口切换为下拉导航与两列风格卡片；页面滚动可到达下方操作。代表截图：

- [设置全貌](appearance.png)、[模型与服务](models.png)、[消息连接](connections.png)、[隐私与数据](privacy.png)、[高级与维护](advanced.png)
- [候选比较与应用](candidate.png)、[窄窗口](narrow.png)
- [125%](scale-125/appearance.png)、[150% 窄窗口](scale-150/narrow.png)、[150% 候选](scale-150/candidate.png)

## 换肤耗时

同机、隔离数据、空历史、设置页面已构造的场景下，早期实现的四次换肤约为 2.05–2.18 秒；减少全局 QSS 重设、只刷新分隔条把手并保留历史控件后，100% 四次测得 **375.6、384.2、367.8、365.8 ms**。计时覆盖同步切换及一次 Qt 事件处理。原始结果见 [timing.json](timing.json)，125%/150% 各目录保存独立测量结果。

`COWORK_PROFILE_THEME=1` 可输出 `profile.txt`。性能采样放在打开日志等异步页面之前，避免把其他回调耗时误归因于换肤。该结果不代表有大量历史、自定义图片/布局或发行包的性能，也不包含模型生成时间。

## 未覆盖与已知问题

- 没有运行完整仓库测试、真实 AI 生成/停止联调、外部账号和渠道联调，也未打包或执行发行包验收。
- 本轮既有主题静态检查仍报告二维码/打印模板的四处固定黑白色；已与修改前 `HEAD` 对照确认，未修改这些无关颜色。
- 150% 离屏截图首次进程以 Windows 原生错误 `0xC0000005` 退出，没有 Python 堆栈；同命令启用 `faulthandler` 后完成截图和正常退出，125% 也正常完成。该次原生退出根因尚未确认，不将后续成功等同于已修复此问题。
