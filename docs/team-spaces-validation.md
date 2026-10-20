# 团队空间源码验证记录

日期：2026-10-19。当前源码版本标识仍为 5.3.0，本记录不代表已发布新版客户端。使用隔离账号、临时数据目录和本机测试数据库，没有操作真实企业数据或调用收费模型。

## 已验证

| 范围 | 结果 | 覆盖内容 |
| --- | --- | --- |
| 团队空间专项 | 54 项、3 个子测试通过 | SQLite 18 项、PostgreSQL 18 项、客户端 12 项、原生页面 6 项 |
| 既有运行时聚焦回归 | 166 项、18 个子测试通过 | 普通工作区、侧栏项目、记忆、企业客户端、模型工厂、会话完整性与保存、内部 Skill 模型调用、长上下文 |
| 平台兼容回归 | 33 项、3 个子测试通过 | 原企业平台、数据库账号认证、独立文件夹部署；与当时 16 项 PostgreSQL 空间测试合跑共 49 项 |
| 会话操作 | 7 项、2 个子测试通过 | 分支、引用、消息操作；测试替身显式声明普通会话未绑定空间，并补齐原有运行中补充消息的工作台接口 |
| 桌面原有入口 | 3 个独立用例通过 | 首次发送、本地新项目可见性、项目与聊天行真实点击 |
| 150% 原生界面 | 6 项通过 | 计划、上传、新任务请求、停止状态分享、丢确认恢复、账号草稿隔离、主题预览与取消 |
| Web 浏览器 | 代表性流程通过 | 所有者设置保存后重读、普通成员只读设置、动态来源、计划及成果查看、固定文件修订、桌面交接链接 |
| 最后定向回归 | 32 项、3 个子测试通过 | 客户端与工作区；新增绑定损坏时 Worker 正常终止、模型未调用且已提交输入保留的验证 |
| 静态检查 | 通过 | 26 个 Python 文件编译、两份 Web JavaScript 语法、所有文档本地链接及 `git diff --check` |

空间专项覆盖邀请撤销和重试、角色边界、非成员与跨空间对象拒绝、并发版本冲突、同名文件冲突、上传摘要和额度、不可变修订、成果撤回与保留附件、归档后已有任务策略、成员移除、停用所有者恢复、身份切换、中心离线时不请求供应商、三种模型协议、SDK 隐式重试关闭，以及恢复后文件摘要校验。PostgreSQL 使用 Docker PostgreSQL 17，每个用例独立 schema，测试结束清理。

原生测试使用真实中心 API 和真实 Qt 控件；模型策略响应真实执行，模型服务调用被隔离。新任务验证了持久归属先于发送，未用模拟模型输出冒充完整真实 AI 任务。

![640 逻辑像素窗口，150% 缩放](screenshots/team-spaces/scale-150/desktop-plans-narrow.png)

![隔离主题预览，可取消恢复](screenshots/team-spaces/scale-150/desktop-theme-preview.png)

网页在 770px 可见宽度检查共享成果，页面 `scrollWidth` 与 `clientWidth` 相等，正文和下载入口可达。测试页面只有共享内容，没有发布或计划编辑入口。

## 复现命令

在仓库根目录执行；Windows 可将 `python` 替换为 `.venv/Scripts/python.exe`。

```powershell
python -m pytest -q test/test_team_spaces.py test/test_team_space_client.py test/test_team_space_ui.py
# 必须使用独立的本机 *_qa 测试数据库。每例仅建立/删除自己的临时 schema。
$env:COWORK_TEST_POSTGRES_URL='postgresql+psycopg://测试账号:测试密码@127.0.0.1:测试端口/spaces_qa'
python -m pytest -q test/test_team_spaces_postgres.py
$env:QT_SCALE_FACTOR='1.5'
python -m pytest -q test/test_team_space_ui.py
```

原生截图通过 `COWORK_SPACE_SCREENSHOTS` 指定输出目录。网页夹具为 `python test/team_space_ui_fixture.py`，仅绑定 `127.0.0.1:8791`，提供合成成员 owner/member/admin；`/qa/login/{角色}` 和 `/qa/stop` 只存在于测试夹具，不进入生产应用。

运行时回归使用：

```powershell
python -m pytest -q test/test_chat_workspace.py test/test_sidebar_projects.py test/test_memory_update.py test/test_enterprise_client.py test/test_llm_factory.py test/test_conversation_integrity.py test/test_chat_storage_messages.py test/test_remote_skill_installer.py test/test_long_context.py test/test_long_context_agent.py test/test_long_context_provider.py
python -m pytest -q test/test_enterprise_platform.py test/test_enterprise_database_auth.py test/test_enterprise_deployment.py test/test_conversation_actions.py
```

本次涉及 MainWindow 的运行用临时目录替换 `core.env_utils.get_app_data_dir`，避免读取或改写真实配置。普通模型请求没有空间策略网络访问；空间策略和管理元数据不修改 system/developer/tool 稳定前缀，共享任务输入追加到原消息流程。

## 2026-10-20 登录后页面补充验证

2026-10-20 登录后页面布局补充验证：原生界面 9 项通过，150% 缩放下另跑列表加载、空列表与登录恢复 3 项通过。使用受控延迟停留在数据返回前，检查 1440×960 与 480×640 窗口中的标题、说明实际高度和间距；覆盖连接失败后重试、邀请接受、回车搜索、长名称换行，以及原有计划、上传、任务、成果和草稿流程。仅检查浅色主题及浅色强调色预览取消，没有进行深色主题测试；最终产品验收由用户完成。

![加载中，内容保持顶部紧凑排列](screenshots/team-spaces/list/desktop-list-loading-narrow.png)

上述补充可用 `python -m pytest -q test/test_team_space_ui.py` 复现；设置 `QT_SCALE_FACTOR=1.5` 后用 `-k 'list_loading or empty_list or sign_in_guidance'` 运行缩放检查。截图保存在 `docs/screenshots/team-spaces/list/`。

## 未覆盖与验证限制

未宣称全仓测试通过。`test_main_workspace_linear.py` 的整组离屏运行出现关闭等待超时、首次模型设置弹窗等待，以及一次 Qt 原生 access violation，未形成完整通过报告；相关三个独立入口用例已单独通过，未以跳过失败断言的方式宣称整组通过。原有引用测试缺少工作台方法的失败已在未修改 HEAD 中复现，随后仅补齐测试替身。

`scripts/check_docs.py` 与文档测试发现 11 份原有文档的当前版本仍为 5.2.9，而 `APP_VERSION` 为 5.3.0；这些声明在 HEAD 已存在。本次没有批量改写无关文档版本。所有本地链接检查通过，文档测试中其余 7 项通过；总检查未宣称通过。

尚未执行两台物理 Windows 客户端、真实企业 SSO、真实供应商模型、多客户端长期运行、完整生产备份还原及 Linux 应用层容量压测。PostgreSQL 契约通过、上传竞争测试与文件摘要检查不能替代这些部署验收。未打包、提交、推送或发布新版本。
