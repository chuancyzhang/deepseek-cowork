# Cowork UI SDK 2

界面插件组合现有业务能力。执行扩展仍使用 Tool、Skill 或 MCP；界面插件不直接调用模型、访问数据库或维护第二套发送队列。

## 从示例开始

在“能力 → 工作台插件 → 添加本地插件”中选择以下目录，预览后应用：

- [完整工作台](../examples/workbench)：对话选择、输入、发送与停止，复用宿主草稿。
- [任务笔记](../examples/panel)：追加任务面板、替换文本编辑器并注册菜单命令。

示例只使用包内资源，可直接安装。修改后重新选择目录生成新候选，预览和应用固定为同一份内容快照。包内 `client.js` 来自本目录，更新 SDK 时同时更新示例。内置工作台的 React 源码在 `src/plugins/BuiltinWorkbench.tsx`；其清单是宿主注册描述，不是可单独导出的完整网页。

## 清单与包

```json
{
  "id": "example.focus",
  "name": "专注工作台",
  "version": "2.0.0",
  "hostApi": 2,
  "stateVersion": 1,
  "entry": "index.html",
  "capabilities": ["conversations.read", "drafts.read", "drafts.write", "runs.control"],
  "contributes": [{"id": "main", "kind": "workbench", "target": "main"}]
}
```

`id` 和贡献点 `id` 必须稳定，`stateVersion` 隔离不兼容的插件视图状态；修改状态结构时明确升版。每个贡献点可指定自己的 `entry`，缺省使用包入口。包最多 512 个文件、总计 16 MiB、单文件 4 MiB；拒绝符号链接、路径越界及不允许的资源类型。整个包内容进入版本指纹。

| kind | target | 规则 |
| --- | --- | --- |
| workbench | main | 一个整体布局 |
| page | chat、settings、capabilities、library、favorites、team、plugins | 每个页面一个实现 |
| panel | workspace、files、tasks、browser、terminal | 默认替换；`mode: "append"` 追加 |
| editor | text、html、Office 文件类型或 `*` | 按类型选择，通用实现使用 `*` |
| command | workspace 等命令区域 | 声明全局唯一 `command`、显示 `title` |
| menu | workspace 等菜单区域 | 引用已注册 `command` |

同一命令 ID 冲突会拒绝安装。清单的能力决定可调用操作以及快照里包含哪些数据。

## 使用 SDK

```js
import {createCowork} from './client.js';
const cowork = createCowork();
await cowork.ready();
const render = snapshot => {
  input.value = snapshot.draft?.text || '';
};
render(await cowork.snapshot());
const unsubscribe = cowork.subscribe(render);
input.addEventListener('input', () => {
  cowork.drafts.change({text: input.value, attachments: []}).catch(showError);
});
send.addEventListener('click', () => cowork.runs.send().catch(showError));
// 销毁时 unsubscribe() 和 cowork.dispose()。
```

实际表单应保留输入光标，不用每个快照重设正在输入的 DOM。调用失败显示具体原因，保留输入；不要换用其他后端方法或重新生成提交 ID 自动重发。

完整方法和类型见 [client.d.ts](client.d.ts)，实现见 [client.js](client.js)。

| 领域 | 能力 | 保存与归属 |
| --- | --- | --- |
| conversations、projects、history | conversations.read / conversations.write | 读取、选择、重命名、归档、分支、带版本守卫的历史修订 |
| runs、tasks | runs.control，读取任务需 conversations.read | 发送宿主草稿、停止指定运行、回复交互、查看子任务 |
| drafts | drafts.read / drafts.write | 修改宿主草稿；save 等待版本回执；send 共用原发送队列 |
| editors | document.read / document.edit；打开文件需 files.read | 编辑会话属于宿主；change 只改草稿，save 才写入文件 |
| files | files.read / files.write | 限定工作目录与现有文件能力 |
| management | management.read / management.write | `ManagementRead/Write` 明确枚举操作，不接受任意 RPC 名称 |
| navigation、state、commands | navigation | 路由、版本化插件展示状态及已注册命令 |
| theme | theme | 设置 light / dark / system；读取快照中的实际主题 |
| dialogs | dialogs；附件需 files.read | 使用宿主确认、文件与目录选择 |
| desktop | desktop | 宿主管理的网页、终端、控制权、剪贴板和系统定位 |

优先显式传入 `conversationId` 与文件路径，异步操作保持其原始归属。省略时采用贡献点绑定的会话／文件，整体工作台采用当前会话。终端输入还需当前 `control_revision`；插件不能绕过接管与交还规则。

## 实例、预览与切换

插件页面运行在 `sandbox="allow-scripts"` iframe。`cowork-plugin:` 只提供已校验包内资源，允许本地 JS 模块与 CSS；CSP 禁止联网、Node 和任意本机文件。SDK 校验消息来源、能力和实例租约。

预览只读示例快照。候选加载期间不能修改真实业务数据，只有发出 `ready` 且所有目标区域就绪后才能切换。宿主先等待旧实例操作结束，再保存草稿、文件编辑恢复副本及表单恢复状态；失败保留原界面。失效实例的迟到结果不能覆盖当前编辑状态。

切换、重载、退出和更新共用保存协调。恢复副本与正式文件／设置保存边界不同；插件不应把保留恢复副本显示成“文件已保存”。`state.change` 仅更新视图状态，不能存放第二套会话历史、运行状态或发送队列。

停用与恢复内置界面由独立宿主入口提供。恢复上一版只恢复代码，不回滚用户数据。`hostApi: 1` 仅适配内置 `cowork.writing` 的 `document.read/change/save/render`，新插件使用版本 2。

## 验证

```powershell
npm --prefix desktop test
npm --prefix desktop run build
```

测试覆盖包快照、整包指纹、命令冲突、停用再启用、候选失败保留状态、迟到异步回调隔离，以及真实 Electron 中的本地模块和 CSS 加载。交付前还需用真实窗口检查输入、未保存文件、主题、小窗口、恢复入口和后台任务连续性。
