"""Independent Windows installer, generated outside the frozen application bundle."""
import json
import os
import uuid


def write_installer(install_dir, staged_dir, plan_path, root, wait_pids, exe_name, theme=None, version=""):
    transaction = os.path.join(root, "transactions", uuid.uuid4().hex)
    os.makedirs(transaction)
    config = {"install_dir": install_dir, "source_dir": staged_dir, "plan_path": plan_path,
              "wait_pids": wait_pids, "exe_name": exe_name, "version": version,
              "theme": theme or {}, "id": os.path.basename(transaction)}
    with open(os.path.join(transaction, "config.json"), "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False)
    script = os.path.join(transaction, "install.ps1")
    with open(script, "w", encoding="utf-8-sig", newline="\r\n") as handle:
        handle.write(INSTALLER_SCRIPT)
    with open(os.path.join(transaction, "恢复说明.txt"), "w", encoding="utf-8") as handle:
        handle.write('如果安装中断，请先关闭应用，再在 PowerShell 中执行以下命令恢复本次已修改的文件。\n'
                     '恢复前请保留本目录、backup 目录和更新清单。\n'
                     'powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "' + script + '" -Recover\n')
    return script


INSTALLER_SCRIPT = r'''param([switch]$Headless, [switch]$Recover, [string]$PreviewImage, [double]$PreviewScale=1)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
# A delayed security approval must not revive a cancelled transaction or take
# the installation mutex away from the user's subsequent attempt.
if(-not $Recover -and (Test-Path -LiteralPath (Join-Path $root 'abort.json'))) { exit 0 }
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$root = $PSScriptRoot
$cfg = Get-Content -LiteralPath (Join-Path $root 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$install = [IO.Path]::GetFullPath($cfg.install_dir).TrimEnd('\')
$source = [IO.Path]::GetFullPath($cfg.source_dir).TrimEnd('\')
$backup = Join-Path $root 'backup'
$logPath = Join-Path $root 'update.log'
$script:busy = $false
$script:committed = $false
$script:mutated = $false
$script:written = New-Object 'System.Collections.Generic.List[string]'
$script:removed = New-Object 'System.Collections.Generic.List[string]'
$script:owned = @{}
$script:outcome = 'waiting'
$keyHash=[Security.Cryptography.SHA256]::Create()
$key=[BitConverter]::ToString($keyHash.ComputeHash([Text.Encoding]::UTF8.GetBytes($install.ToLowerInvariant()))).Replace('-','')
$keyHash.Dispose()
if(-not $Recover -and (Test-Path -LiteralPath (Join-Path $root 'abort.json'))) { exit 0 }
$mutex=New-Object Threading.Mutex($false, ('Local\CoworkUpdate'+$key))
try { $acquired=$mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $acquired=$true }
if(-not $acquired) { throw '另一个安装程序正在处理此应用，请等待其完成。' }

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Collections.Generic;
public static class CoworkLocks {
 [StructLayout(LayoutKind.Sequential)] public struct UniqueProcess { public int pid; public System.Runtime.InteropServices.ComTypes.FILETIME start; }
 [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] public struct ProcessInfo {
  public UniqueProcess process;
  [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)] public string name;
  [MarshalAs(UnmanagedType.ByValTStr, SizeConst=64)] public string service;
  public uint type; public uint status; public uint session; [MarshalAs(UnmanagedType.Bool)] public bool restartable;
 }
 [DllImport("rstrtmgr.dll", CharSet=CharSet.Unicode)] static extern int RmStartSession(out uint session, int flags, string key);
 [DllImport("rstrtmgr.dll", CharSet=CharSet.Unicode)] static extern int RmRegisterResources(uint session, uint count, string[] files, uint apps, UniqueProcess[] processes, uint services, string[] names);
 [DllImport("rstrtmgr.dll")] static extern int RmGetList(uint session, out uint needed, ref uint count, [In, Out] ProcessInfo[] info, ref uint reasons);
 [DllImport("rstrtmgr.dll")] static extern int RmEndSession(uint session);
 public static ProcessInfo[] Find(string[] paths) {
  if(paths.Length == 0) return new ProcessInfo[0];
  uint session; int rc = RmStartSession(out session, 0, Guid.NewGuid().ToString("N"));
  if(rc != 0) throw new Exception("RmStartSession: " + rc);
  try {
   rc = RmRegisterResources(session, (uint)paths.Length, paths, 0, null, 0, null);
   if(rc != 0) throw new Exception("RmRegisterResources: " + rc);
   for(int i=0;i<4;i++) {
    uint count=0, needed=0, reasons=0;
    rc=RmGetList(session,out needed,ref count,null,ref reasons);
    if(rc==0) return new ProcessInfo[0];
    if(rc!=234) throw new Exception("RmGetList: " + rc);
    ProcessInfo[] info = new ProcessInfo[needed]; count=needed;
    rc=RmGetList(session,out needed,ref count,info,ref reasons);
    if(rc==234) continue;
    if(rc!=0) throw new Exception("RmGetList: " + rc);
    Array.Resize(ref info,(int)count); return info;
   }
   throw new Exception("Resource owners are changing; retry update.");
  } finally { RmEndSession(session); }
 }
}
'@

function Save-Json($name, $value) {
 $path = Join-Path $root $name
 $temp = $path + '.tmp'
 $value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temp -Encoding UTF8
 Move-Item -LiteralPath $temp -Destination $path -Force
}
function Log($message) {
 $line = ('[{0}] {1}' -f (Get-Date -Format 'o'), $message)
 Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
 if($script:logBox) { $script:logBox.AppendText($line + [Environment]::NewLine) }
}
function Status($message, $percent=-1) {
 Log $message
 if(-not $Headless) {
  $status.Text = $message
  if($percent -lt 0) { $progress.Style='Marquee' } else { $progress.Style='Continuous'; $progress.Value=[Math]::Max(0,[Math]::Min(100,$percent)) }
  [Windows.Forms.Application]::DoEvents()
 }
}
function Within($path, $directory) {
 if(-not $path) { return $false }
 return [IO.Path]::GetFullPath($path).StartsWith($directory.TrimEnd('\')+'\', [StringComparison]::OrdinalIgnoreCase)
}
function App-Path($path) {
 return (Within $path $install) -and -not (Within $path (Join-Path $install 'user_data'))
}
function Safe-Path($directory, $relative) {
 if([string]::IsNullOrWhiteSpace($relative) -or [IO.Path]::IsPathRooted($relative)) { throw "Unsafe update path: $relative" }
 $path=[IO.Path]::GetFullPath((Join-Path $directory $relative.Replace('/', '\')))
 if(-not (Within $path $directory) -or $relative -match '^(?i:user_data)(/|\\|$)') { throw "Unsafe update path: $relative" }
 return $path
}
function Copy-File($from, $to) {
 if(Test-Path -LiteralPath $to -PathType Container) { throw ('安装目标变成了目录：'+$to) }
 [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($to)) | Out-Null
 Copy-Item -LiteralPath $from -Destination $to -Force
}
function File-Hash($path) {
 $stream=[IO.File]::OpenRead($path); $hash=[Security.Cryptography.SHA256]::Create()
 try { return [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
 finally { $stream.Dispose(); $hash.Dispose() }
}
function Identity($process) {
 return $process.StartTime.ToUniversalTime().Ticks.ToString()
}
function Discover-Owned {
 $all=@(Get-CimInstance Win32_Process)
 $exclude=@{}; $exclude[[int]$PID]=$true
 # Never classify the independent updater or its descendants as application children.
 do {
  $changed=$false
  foreach($p in $all) { if($exclude.ContainsKey([int]$p.ParentProcessId) -and -not $exclude.ContainsKey([int]$p.ProcessId)) { $exclude[[int]$p.ProcessId]=$true; $changed=$true } }
 } while($changed)
 foreach($p in $all) {
  $id=[int]$p.ProcessId
  if($exclude.ContainsKey($id)) { continue }
  $process=Get-Process -Id $id -ErrorAction SilentlyContinue
  if(-not $process) { continue }
  try {
   $identity=Identity $process
   $known=$script:owned.ContainsKey($id) -and $script:owned[$id] -eq $identity
   $ownedPath=App-Path $p.ExecutablePath
   $parentKnown=$script:owned.ContainsKey([int]$p.ParentProcessId)
   if($parentKnown) {
    $parent=Get-Process -Id ([int]$p.ParentProcessId) -ErrorAction SilentlyContinue
    $parentKnown=$parent -and (Identity $parent) -eq $script:owned[[int]$p.ParentProcessId]
   }
   if($known -or $ownedPath -or $parentKnown) { $script:owned[$id]=$identity }
  } catch { Log ('process identity unavailable PID '+$id) }
 }
}
function Live-Owned {
 $live=@()
 foreach($id in @($script:owned.Keys)) {
  $p=Get-Process -Id $id -ErrorAction SilentlyContinue
  if($p) { try { if((Identity $p) -eq $script:owned[$id]) { $live += $p } } catch { throw "Cannot verify application process $id" } }
 }
 return $live
}
function Release-Files($paths) {
 Status '正在关闭应用及后台任务…'
 $deadline=[DateTime]::UtcNow.AddSeconds(30)
 do {
  $live=@(Live-Owned)
  if($live.Count -eq 0) { break }
  Start-Sleep -Milliseconds 250
  if(-not $Headless) { [Windows.Forms.Application]::DoEvents() }
 } while([DateTime]::UtcNow -lt $deadline)
 Discover-Owned
 foreach($p in @(Live-Owned)) {
  Log ('ending application process PID '+$p.Id+' identity '+(Identity $p))
  Stop-Process -Id $p.Id -Force -ErrorAction Stop
 }
 $deadline=[DateTime]::UtcNow.AddSeconds(10)
 do {
  $blockers=@([CoworkLocks]::Find([string[]]$paths))
  $unknown=@()
  foreach($b in $blockers) {
   $id=[int]$b.process.pid
   $p=Get-Process -Id $id -ErrorAction SilentlyContinue
   if(-not $p) { continue }
   if(-not $script:owned.ContainsKey($id) -and $id -ne $PID) {
    # Inspect loaded modules only for actual blockers, not every process on Windows.
    try { foreach($module in $p.Modules) { if(App-Path $module.FileName) { $script:owned[$id]=Identity $p; break } } } catch { Log ('Cannot inspect blocker PID '+$id) }
   }
   $fileTime=([long]$b.process.start.dwHighDateTime -shl 32) -bor ([long]$b.process.start.dwLowDateTime -band 0xffffffffL)
   $rmIdentity=[DateTime]::FromFileTimeUtc($fileTime).Ticks.ToString()
   if($script:owned.ContainsKey($id) -and $script:owned[$id] -eq (Identity $p) -and $rmIdentity -eq (Identity $p) -and $id -ne $PID) {
    Stop-Process -Id $id -Force -ErrorAction Stop
   } else { $unknown += ($b.name+' (PID '+$id+')') }
  }
  if($unknown.Count) { throw ('请关闭占用安装文件的程序后重试：'+($unknown -join '、')) }
  if($blockers.Count -eq 0 -and @(Live-Owned).Count -eq 0) { return }
  Start-Sleep -Milliseconds 250
 } while([DateTime]::UtcNow -lt $deadline)
 throw '应用文件仍被占用，尚未替换文件。请关闭相关程序后重试。'
}
function Record-Transaction($phase) {
 Save-Json 'transaction.json' @{ id=$cfg.id; phase=$phase; written=@($script:written.ToArray()); removed=@($script:removed.ToArray()); updated=(Get-Date -Format 'o') }
}
function Open-App {
 $exe=Join-Path $install $cfg.exe_name
 $env:COWORK_UPDATE_RECEIPT=Join-Path $root 'opened.json'
 $env:COWORK_UPDATE_ID=$cfg.id
 $p=Start-Process -FilePath $exe -WorkingDirectory $install -PassThru
 Remove-Item Env:COWORK_UPDATE_RECEIPT -ErrorAction SilentlyContinue
 Remove-Item Env:COWORK_UPDATE_ID -ErrorAction SilentlyContinue
 if(-not $p) { throw '无法启动应用。' }
 return $p
}
function Rollback($plan) {
 Status '正在恢复原版本…'
 # Verify every required backup before touching the installation.
 foreach($item in @($plan.modified)+@($plan.deleted)) {
  if($item.path -in $script:written -or $item.path -in $script:removed) {
   if((File-Hash (Safe-Path $backup $item.path)) -ne $item.before_sha256) { throw ('备份文件校验失败：'+$item.path) }
  }
 }
 foreach($relative in @($script:written.ToArray())) {
  $dest=Safe-Path $install $relative
  $old=Safe-Path $backup $relative
  if(Test-Path -LiteralPath $old -PathType Leaf) { Copy-File $old $dest }
  elseif(@($plan.added | Where-Object { $_.path -eq $relative }).Count) { if(Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Force } }
 }
 foreach($relative in @($script:removed.ToArray())) { Copy-File (Safe-Path $backup $relative) (Safe-Path $install $relative) }
 foreach($item in @($plan.modified)+@($plan.deleted)) {
  if($item.path -in $script:written -or $item.path -in $script:removed) {
   if((File-Hash (Safe-Path $install $item.path)) -ne $item.before_sha256) { throw ('原版本恢复校验失败：'+$item.path) }
  }
 }
 Record-Transaction 'rolled_back'
}
function Recover-Update {
 $script:busy=$true
 try {
  Discover-Owned
  if(@(Live-Owned).Count) { throw '请先关闭应用及其后台任务，再恢复原版本。' }
  $record=Get-Content -LiteralPath (Join-Path $root 'transaction.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  if($record.id -ne $cfg.id -or $record.phase -notin @('writing','rollback_failed')) { throw '本次事务没有需要恢复的中断写入。' }
  $plan=Get-Content -LiteralPath $cfg.plan_path -Raw -Encoding UTF8 | ConvertFrom-Json
  $script:written.Clear(); $script:removed.Clear()
  foreach($relative in $record.written) { $script:written.Add([string]$relative) }
  foreach($relative in $record.removed) { $script:removed.Add([string]$relative) }
  Rollback $plan
  $script:outcome='rolled_back'; Status '已恢复原版本，可以重新打开应用。' 100
  if(-not $Headless) { $retry.Text='打开原版本'; $retry.Visible=$true }
 } catch {
  $script:outcome='rollback_failed'
  Status ('恢复未完成：'+$_.Exception.Message+' 备份已保留在：'+$backup)
  if(-not $Headless) { $retry.Text='重试恢复'; $retry.Visible=$true }
 } finally { $script:busy=$false }
}
function Apply-Update {
 $script:busy=$true
 $script:written.Clear(); $script:removed.Clear(); $script:mutated=$false
 $plan=$null
 try {
  if(-not (Test-Path -LiteralPath (Join-Path $root 'commit.json'))) { throw '应用尚未完成保存，未执行安装。' }
  $commit=Get-Content -LiteralPath (Join-Path $root 'commit.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  if($commit.id -ne $cfg.id) { throw '安装许可不匹配。' }
  $plan=Get-Content -LiteralPath $cfg.plan_path -Raw -Encoding UTF8 | ConvertFrom-Json
  if([int]$plan.format_version -ne 1) { throw '更新文件清单格式无效。' }
  # Validate every path and all source hashes before making any mutation.
  $files=@($plan.added)+@($plan.modified)
  $paths=@()
  foreach($item in @($files)+@($plan.deleted)) {
   $dest=Safe-Path $install ([string]$item.path)
   if(Test-Path -LiteralPath $dest -PathType Leaf) { $paths += $dest }
  }
  foreach($item in $files) {
   $from=Safe-Path $source ([string]$item.path)
   if((File-Hash $from) -ne $item.sha256) { throw ('安装包文件校验失败：'+$item.path) }
  }
  Release-Files $paths
  foreach($item in @($files)+@($plan.deleted)) {
   $dest=Safe-Path $install $item.path
   if($item.before_sha256) {
    if(-not (Test-Path -LiteralPath $dest -PathType Leaf) -or (File-Hash $dest) -ne $item.before_sha256) { throw '安装目录在准备期间发生了变化，请重新打开应用后重试更新。' }
   } elseif(Test-Path -LiteralPath $dest) { throw '安装目录在准备期间出现了新文件，请重新检查更新。' }
  }
  Status '正在备份原版本…' 10
  foreach($item in @($plan.modified)+@($plan.deleted)) {
   $from=Safe-Path $install $item.path
   if(Test-Path -LiteralPath $from -PathType Leaf) { Copy-File $from (Safe-Path $backup $item.path) }
  }
  Record-Transaction 'backed_up'
  $total=[Math]::Max(1,$files.Count+@($plan.deleted).Count); $index=0
  foreach($item in $files) {
   # Journal intent before each operation, so interruption/partial copy is recoverable.
   $script:written.Add([string]$item.path); Record-Transaction 'writing'; $script:mutated=$true
   Copy-File (Safe-Path $source $item.path) (Safe-Path $install $item.path)
   $index++; Status '正在安装更新…' (15+[int](65*$index/$total))
  }
  foreach($item in @($plan.deleted)) {
   $dest=Safe-Path $install $item.path
   if(Test-Path -LiteralPath $dest -PathType Leaf) {
    $script:removed.Add([string]$item.path); Record-Transaction 'writing'; $script:mutated=$true
    Remove-Item -LiteralPath $dest -Force
   }
   $index++; Status '正在安装更新…' (15+[int](65*$index/$total))
  }
  Status '正在检查安装结果…' 85
  foreach($item in $files) {
   if((File-Hash (Safe-Path $install $item.path)) -ne $item.sha256) { throw ('安装结果校验失败：'+$item.path) }
  }
  Record-Transaction 'installed'; $script:outcome='installed'
  try {
   Status '更新已安装，正在重新打开应用…' 95
   $opened=Open-App
   $deadline=[DateTime]::UtcNow.AddSeconds(30)
   while(-not (Test-Path -LiteralPath (Join-Path $root 'opened.json'))) {
    if($opened.HasExited -or [DateTime]::UtcNow -ge $deadline) { throw '应用未确认启动完成。' }
    Start-Sleep -Milliseconds 250
    if(-not $Headless) { [Windows.Forms.Application]::DoEvents() }
   }
   $receipt=Get-Content -LiteralPath (Join-Path $root 'opened.json') -Raw -Encoding UTF8 | ConvertFrom-Json
   if($receipt.id -ne $cfg.id -or $receipt.version -ne $cfg.version.TrimStart('v','V')) { throw '应用启动确认不匹配。' }
   Record-Transaction 'complete'; $script:outcome='complete'; Status '更新完成' 100
   $script:busy=$false
   if(-not $Headless) { $form.Close() }
  } catch {
   Log $_.Exception.Message
   $script:outcome='installed_not_opened'; Record-Transaction $script:outcome
   Status '更新已安装，未能确认应用打开。可以重新打开应用。'
   if(-not $Headless) { $retry.Text='重新打开应用'; $retry.Visible=$true }
  }
 } catch {
  $reason=$_.Exception.Message; Log $reason
  $script:outcome='failed'
  if($script:mutated -and $plan) {
   try { Rollback $plan; $script:outcome='rolled_back'; $reason='更新未完成，已恢复原版本。'+$reason; try { $null=Open-App } catch { Log ('原版本未能自动打开：'+$_.Exception.Message) } }
   catch { $script:outcome='rollback_failed'; Record-Transaction 'rollback_failed'; $reason='更新未完成，原版本尚未恢复。请关闭占用程序后重试恢复；备份保留在：'+$backup+'。'+$_.Exception.Message }
  }
  Save-Json 'result.json' @{id=$cfg.id; phase=$script:outcome; error=$reason}
  Status $reason
  if(-not $Headless) {
   $retry.Visible=$true
   $retry.Text=if($script:outcome -eq 'rolled_back') {'打开原版本'} elseif($script:outcome -eq 'rollback_failed') {'重试恢复'} else {'重试'}
  }
 } finally { $script:busy=$false }
}

if(-not $Headless) {
 $form=New-Object Windows.Forms.Form
 $form.Text='DeepSeek Cowork · 应用更新'; $form.StartPosition='CenterScreen'
 $fontFamily=if($cfg.theme.font){$cfg.theme.font}else{'Microsoft YaHei UI'}
 $fontSize=if($cfg.theme.font_size){[float]$cfg.theme.font_size * 0.75}else{10.5}
 $spacing=if($cfg.theme.spacing){[int]$cfg.theme.spacing}else{24}
 $form.AutoScaleMode='Dpi'; $form.Font=New-Object Drawing.Font($fontFamily,$fontSize)
 $form.ClientSize=New-Object Drawing.Size(520,250); $form.MinimumSize=New-Object Drawing.Size(460,280)
 $form.MaximizeBox=$false; $form.MinimizeBox=$true
 function Color($key,$default) { $value=$cfg.theme.$key; if(-not $value) {$value=$default}; return [Drawing.ColorTranslator]::FromHtml($value) }
 $form.BackColor=Color 'bg' '#f7f7f8'; $form.ForeColor=Color 'text' '#242426'
 $layout=New-Object Windows.Forms.TableLayoutPanel
 $layout.Dock='Fill'; $layout.Padding=New-Object Windows.Forms.Padding($spacing); $layout.ColumnCount=1; $layout.RowCount=6; $layout.AutoScroll=$true
 $form.Controls.Add($layout)
 $title=New-Object Windows.Forms.Label; $title.Text='正在安装更新'; $title.AutoSize=$true; $title.Font=New-Object Drawing.Font($fontFamily,14,[Drawing.FontStyle]::Bold)
 $subtitle=New-Object Windows.Forms.Label; $subtitle.Text='完成后会自动重新打开应用，请稍候。'; $subtitle.AutoSize=$true; $subtitle.Dock='Fill'
 $status=New-Object Windows.Forms.Label; $status.Text='正在准备…'; $status.AutoSize=$true; $status.Dock='Fill'; $status.MaximumSize=New-Object Drawing.Size(1000,0)
 $progress=New-Object Windows.Forms.ProgressBar; $progress.Dock='Top'; $progress.Height=12; $progress.Style='Marquee'; $progress.ForeColor=Color 'primary' '#6467ce'
 $buttons=New-Object Windows.Forms.FlowLayoutPanel; $buttons.AutoSize=$true; $buttons.Dock='Fill'
 $details=New-Object Windows.Forms.Button; $details.Text='查看详情'; $details.AutoSize=$true; $details.FlatStyle='Flat'; $details.FlatAppearance.BorderSize=0
 $retry=New-Object Windows.Forms.Button; $retry.Text='重试'; $retry.AutoSize=$true; $retry.Visible=$false
 $buttons.Controls.Add($details); $buttons.Controls.Add($retry)
 $script:logBox=New-Object Windows.Forms.TextBox; $script:logBox.Multiline=$true; $script:logBox.ReadOnly=$true; $script:logBox.ScrollBars='Vertical'; $script:logBox.Dock='Fill'; $script:logBox.Visible=$false; $script:logBox.Height=180
 $script:logBox.BackColor=$form.BackColor; $script:logBox.ForeColor=$form.ForeColor
 foreach($button in @($details,$retry)) { $button.BackColor=$form.BackColor; $button.ForeColor=$form.ForeColor; $button.FlatStyle='Flat' }
 $layout.Controls.Add($title); $layout.Controls.Add($subtitle); $layout.Controls.Add($status); $layout.Controls.Add($progress); $layout.Controls.Add($buttons); $layout.Controls.Add($script:logBox)
 foreach($label in @($title,$subtitle,$status)) { $label.Margin=New-Object Windows.Forms.Padding(0,0,0,10) }
 $form.Add_Resize({ foreach($label in @($subtitle,$status)) { $label.MaximumSize=New-Object Drawing.Size([Math]::Max(200,$form.ClientSize.Width-2*$spacing),0) } })
 $details.Add_Click({ $script:logBox.Visible=-not $script:logBox.Visible; $form.Height=if($script:logBox.Visible){490}else{290}; $details.Text=if($script:logBox.Visible){'收起详情'}else{'查看详情'} })
 $retry.Add_Click({
  $retry.Visible=$false
  if($script:outcome -in @('installed_not_opened','rolled_back')) { try { $null=Open-App; $form.Close() } catch { Status $_.Exception.Message; $retry.Visible=$true } }
  elseif($script:outcome -eq 'rollback_failed') { Recover-Update }
  else { Apply-Update }
 })
 $form.Add_FormClosing({ param($sender,$event) if($script:busy -or (-not $script:committed -and (Test-Path -LiteralPath (Join-Path $root 'commit.json')))){$event.Cancel=$true} elseif(-not $script:committed){Save-Json 'abort.json' @{id=$cfg.id}} })
}

if($PreviewImage) {
 $form.Opacity=0; $form.ShowInTaskbar=$false
 $status.Text='正在检查安装结果…'; $progress.Style='Continuous'; $progress.Value=85
 $form.Scale((New-Object Drawing.SizeF($PreviewScale,$PreviewScale)))
 $form.Font=New-Object Drawing.Font($fontFamily,($fontSize*$PreviewScale))
 $title.Font=New-Object Drawing.Font($fontFamily,(14*$PreviewScale),[Drawing.FontStyle]::Bold)
 $form.Show(); $form.PerformLayout(); [Windows.Forms.Application]::DoEvents()
 $bitmap=New-Object Drawing.Bitmap($form.Width,$form.Height)
 $form.DrawToBitmap($bitmap,(New-Object Drawing.Rectangle(0,0,$form.Width,$form.Height)))
 $bitmap.Save($PreviewImage); $bitmap.Dispose(); $form.Dispose(); exit 0
}

try {
 if(-not $Recover -and (Test-Path -LiteralPath (Join-Path $root 'abort.json'))) { exit 0 }
 # Capture root identities before the application exits. PID reuse cannot expand ownership.
 foreach($id in $cfg.wait_pids) {
  $p=Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
  if($p -and [int]$id -ne $PID -and (App-Path $p.Path)) { $script:owned[[int]$id]=Identity $p }
 }
 Discover-Owned; Discover-Owned
 $launchParent=Get-Process -Id ([int]$cfg.wait_pids[0]) -ErrorAction SilentlyContinue
 $launchParentIdentity=if($launchParent){Identity $launchParent}else{''}
 Save-Json 'ready.json' @{id=$cfg.id; pid=$PID}
} catch { Save-Json 'result.json' @{id=$cfg.id; phase='startup_failed'; error=$_.Exception.Message}; throw }

if($Headless) {
 if($Recover) { Recover-Update }
 elseif(Test-Path -LiteralPath (Join-Path $root 'commit.json')) { $script:committed=$true; Apply-Update }
 exit $(if($script:outcome -eq 'complete'){0}else{1})
}
$context=New-Object Windows.Forms.ApplicationContext
$form.Add_FormClosed({$context.ExitThread()})
$timer=New-Object Windows.Forms.Timer; $timer.Interval=250
$timer.Add_Tick({
 if($Recover) { $timer.Stop(); $script:committed=$true; $form.Show(); Recover-Update; return }
 if(Test-Path -LiteralPath (Join-Path $root 'abort.json')) { $timer.Stop(); $context.ExitThread(); return }
 $parent=Get-Process -Id ([int]$cfg.wait_pids[0]) -ErrorAction SilentlyContinue
 $parentAlive=$parent -and (Identity $parent) -eq $launchParentIdentity
 if(Test-Path -LiteralPath (Join-Path $root 'commit.json')) {
  $script:committed=$true
  if($parentAlive) { return }
  $timer.Stop(); $form.Show(); Log 'installer_window_shown'; Apply-Update; return
 }
 if(-not $parentAlive) {
  $timer.Stop(); Status '应用未完成安装准备，尚未替换任何文件。请重新打开应用后重试。'; $form.Show()
 }
})
# Pump the handshake without showing an installer window over a still-running app.
$timer.Start()
[Windows.Forms.Application]::Run($context)
$timer.Dispose(); $form.Dispose(); $context.Dispose()
'''
