// The title bar and native accelerators share these menus. Editing stays with
// Electron's focused webContents, including native browser and editor surfaces.
function applicationMenu({command, exit, logs, guide, recover, reload}) {
  const item = (id, label, accelerator) => ({id, label, ...(accelerator ? {accelerator} : {}), click: () => command(id)});
  const separator = {type:'separator'};
  return [
    {id:'file', label:'文件', submenu:[
      item('new-chat','新建聊天','CmdOrCtrl+N'), item('add-project','新建项目…','CmdOrCtrl+O'),
      {...item('export-chat','导出当前聊天…'), enabled:false}, separator,
      {label:'退出应用', click:exit},
    ]},
    {id:'edit', label:'编辑', submenu:[
      {label:'撤销',role:'undo'}, {label:'重做',role:'redo'}, separator,
      {label:'剪切',role:'cut'}, {label:'复制',role:'copy'}, {label:'粘贴',role:'paste'},
      {label:'全选',role:'selectAll'}, separator, item('settings','设置…','CmdOrCtrl+,'),
    ]},
    {id:'view', label:'视图', submenu:[
      item('sidebar','显示 / 隐藏侧边栏','CmdOrCtrl+Shift+S'), item('search','搜索聊天与项目','CmdOrCtrl+K'),
      item('tasks','运行任务'), item('workspace','工作区'), item('appearance','切换浅色 / 深色'), separator,
      {label:'放大',role:'zoomIn'}, {label:'缩小',role:'zoomOut'}, {label:'重置缩放',role:'resetZoom'},
      {label:'全屏',role:'togglefullscreen'}, separator,
      {label:'重新载入界面',accelerator:'CmdOrCtrl+R',click:reload},
    ]},
    {id:'help', label:'帮助', submenu:[
      {label:'使用指南',click:guide}, item('shortcuts','键盘快捷键'), separator,
      {label:'打开诊断日志',click:logs}, {label:'恢复内置界面',click:recover}, separator,
      item('updates','更新与关于'),
    ]},
  ];
}
module.exports={applicationMenu};
