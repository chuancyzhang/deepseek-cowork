# 设置按需加载验证

2026-11-05，主分支源码、Windows 原生 Qt、隔离配置与空历史，禁用外部后台服务。设置只构建当前分类或直达子页；主题编辑器及高级字段、模型服务详情、消息渠道详情首次打开时创建，之后复用。搜索查页面元信息，保存只收集已创建分区的实际修改，未访问配置不被默认值覆盖。

## 性能

修改前后各五次独立进程；不启用 cProfile 或额外事件分析器。时间包含同步入口和六次 Qt 事件处理，系统缩放 150%、`QT_SCALE_FACTOR=1`、实际 DPR=1.5。原始数据见 [measurements.json](measurements.json)。

| 项目 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 首次打开中位数 | 1108.37 ms | 128.26 ms |
| 首次打开范围 | 1083.86–1644.39 ms | 116.78–184.62 ms |
| 重复打开中位数 | 22.00 ms | 20.67 ms |
| 首次创建的 QWidget 后代 | 1212 | 166 |

首开中位数减少 **88.43%**。其他分区首次进入的中位数为 **26.20–64.94 ms**；主题编辑器 **83.03 ms**，高级主题字段 **55.84 ms**。这些测量不含网络、模型请求和登录时间，不代表低性能目标机、真实大库、冷磁盘或发行包表现。

```powershell
.venv/Scripts/python.exe -X utf8 -B scripts/profile_settings_startup.py --native --output .tmp/settings-lazy/recheck
```

脚本使用独立 `APPDATA/LOCALAPPDATA/TEMP/TMP`，退出后清理隔离数据。`--baseline` 可指向修改前保存的 `main.before.py`、`models.before.py`、`theme.before.py`，在同一依赖环境中比较，不切换有未提交修改的工作区。五组样本采集后仅补充了失败提示换行、缓存复用日志和放弃模型草稿后的总览恢复。

## 功能与恢复

相关合并批次 **173 项测试、28 个子用例通过**；收尾补充模型详情放弃后的路由检查，重跑按需加载和模型配置 **28 项、16 个子用例通过**，与前批次有重叠，不累加为独立覆盖数。

- 默认首开只创建外观页；直达组件、日志等子页不构建默认子页。
- 搜索、保存和状态检查不创建未访问编辑器；切换分类保留草稿，新建分区不重置已有草稿基线。
- 覆盖保存失败保留输入、配置回滚、外部配置合并、修改历史目录时迁移必要记忆、放弃编辑、主题预览取消及即时换肤的独立边界。
- 模型列表与默认模型校验不创建服务编辑器；放弃服务编辑后回到总览。消息渠道只创建所选详情，后台连接沿用应用级生命周期。
- 分区创建失败保留其他草稿，提供“重新加载”；创建与缓存复用写入既有导航日志。
- 模型请求、prompt prefix、工具定义与历史协议没有改变。

```powershell
$env:TEMP = 'D:\code\cowork\.tmp\settings-lazy'
$env:TMP = $env:TEMP
$env:QT_QPA_PLATFORM = 'offscreen'
$env:COWORK_EXTERNAL_BACKGROUND_SERVICES = '0'
.venv/Scripts/python.exe -X utf8 -B -m pytest test/test_settings_lazy_loading.py test/test_default_model_settings.py test/test_settings_redesign.py test/test_theme_settings_ui.py test/test_unified_management.py test/test_model_configuration_flow.py test/test_product_experience_fixes.py test/test_child_window_lifecycle.py test/test_chat_experience.py test/test_capability_cards.py test/test_data_security_ui.py test/test_ui_navigation_performance.py -k 'not test_assistant_bubble_renders_image_only_output_and_missing_state and not test_input_height_animation_is_owned_by_editor' -q --tb=short -o cache_dir=.tmp/settings-lazy/pytest-cache
.venv/Scripts/python.exe -X utf8 -B -m pytest test/test_settings_lazy_loading.py test/test_model_configuration_flow.py -q --tb=short -o cache_dir=.tmp/settings-lazy/pytest-cache
```

没有宣称全仓测试通过。两项排除的旧断言均已在修改前源码上复现：图片消息区域可见性、输入框高度动画对象；尚未确认是否对应实际产品缺陷。另 `test_linear_management_navigation.py` 的九项用例因 `setUp` 引用了已不存在的 `SettingsDialog.start_app_update` 而无法进入断言，修改前同样失败；不据此判断侧栏入口有故障，本次未修改这些无关行为。

## 原生窗口与显示

在主题已生效、构建控件前安装 `QEvent.Show` 检查，覆盖主窗口已显示后的逐页构建、模型详情、全部五个消息渠道详情和主题编辑器。允许主窗口及 Qt Popup/ToolTip，其余非预期顶层窗口均记录调用栈。浅色和深色两次检查结果为空，见 [浅色结果](window-audit-light.json)、[深色结果](window-audit-dark.json)；这些带检查器的时间不混入上面的性能样本。

| 场景 | 参数 | 截图 |
| --- | --- | --- |
| 浅色 | 1440×900 逻辑尺寸，系统 150%，Qt scale=1，DPR=1.5 | [设置首页](settings-light.png) |
| 深色小窗口 | 请求 1000×760 逻辑尺寸，系统 150%，Qt scale=1.5，DPR=2.25；实际高度受屏幕可用区限制 | [设置首页](settings-dark.png)、[微信详情](connection-dark.png) |

已检查导航在窄宽时切换为下拉、内容可滚动、底部操作可达、无重叠裁切，并恢复已保存主题后确认设置未产生意外草稿。截图是本地示例，不包含真实账号、渠道或模型联调。

```powershell
.venv/Scripts/python.exe -X utf8 -B scripts/profile_settings_startup.py --native --screenshot --audit-windows --output .tmp/settings-lazy/audit-light
.venv/Scripts/python.exe -X utf8 -B scripts/profile_settings_startup.py --native --dark --scale 1.5 --screenshot --audit-windows --output .tmp/settings-lazy/audit-dark
```
