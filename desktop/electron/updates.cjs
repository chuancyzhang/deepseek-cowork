const {randomUUID} = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function prepareInstall(backend, hostPid, {sleep=delay, id=randomUUID().replaceAll('-','')}={}) {
  const transaction = {id};
  try {
    let status = await backend.call('updates.prepare_install', {...transaction, host_pid:hostPid});
    while (['preparing','cancelling'].includes(status.phase)) {
      await sleep(250);
      status = await backend.call('updates.install_status', transaction);
    }
    if (status.phase === 'cancelled') return {cancelled:true};
    if (status.phase !== 'ready') throw new Error(status.error || '安装准备已取消，当前版本继续可用。');
    return transaction;
  } catch (error) {
    try { await backend.call('updates.abort_install', transaction); }
    catch (abortError) { throw new Error(`${error.message}；暂时无法确认取消，后台会在连接等待超时后撤销准备。${abortError.message}`); }
    throw error;
  }
}

async function commitInstall(backend, transaction, dataDir, quit, {sleep=delay}={}) {
  if (!/^[0-9a-f]{32}$/.test(transaction?.id)) throw new Error('更新事务编号无效。');
  try {
    let status = await backend.call('updates.commit_install', transaction);
    while (status.phase === 'committing') {
      await sleep(250);
      status = await backend.call('updates.install_status', transaction);
    }
    if (status.phase !== 'committed') throw new Error(status.error || '安装未提交。');
  } catch (error) {
    // A lost response can follow a successful shutdown. The durable commit is authority.
    let committed = false;
    try {
      const record = JSON.parse(await fs.readFile(path.join(dataDir,'updates/desktop/transactions',transaction.id,'commit.json'),'utf8'));
      committed = record.id === transaction.id;
    } catch (readError) { if (readError.code !== 'ENOENT') throw new Error(`无法核对安装提交结果：${readError.message}`); }
    if (!committed) {
      let status;
      try { status = await backend.call('updates.install_status', transaction); }
      catch { throw new Error(`安装提交结果尚未确认，请查看更新窗口或诊断日志。${error.message}`); }
      if (['committing','committed'].includes(status.phase)) throw new Error('安装已进入提交阶段，请等待更新窗口处理，勿重复安装。');
      await backend.call('updates.abort_install', transaction);
      throw error;
    }
  }
  await quit({serviceStopped:true});
  return {accepted:true};
}

function startupRequest(env, installDir) {
  if (!env.COWORK_UPDATE_ID && !env.COWORK_UPDATE_RECEIPT) return null;
  return {id:env.COWORK_UPDATE_ID, receipt:env.COWORK_UPDATE_RECEIPT, install_dir:installDir};
}

class UpdateStartup {
  constructor({installDir, version, call, diagnostic=()=>{}}) {
    this.installDir=path.resolve(installDir);this.version=version;this.call=call;this.diagnostic=diagnostic;
    this.pending=new Map();this.ready=false;this.flushing=null;
  }
  accept(request) {
    if (!request) return;
    if (!/^[0-9a-f]{32}$/.test(request.id) || typeof request.receipt !== 'string' ||
        typeof request.install_dir !== 'string' || path.resolve(request.install_dir).toLowerCase() !== this.installDir.toLowerCase()) {
      this.diagnostic('update_startup_rejected',{reason:'installation_or_transaction_mismatch'});return;
    }
    this.pending.set(request.id, request);
  }
  async flush() {
    if (!this.ready) return {acknowledged:false};
    if (this.flushing) return this.flushing;
    this.flushing=(async()=>{
      let acknowledged=false;
      while (this.ready && this.pending.size) {
        const [id,request]=this.pending.entries().next().value;
        const result=await this.call('updates.acknowledge',{request,host_version:this.version});
        if (!result.acknowledged) throw new Error('更新启动确认未通过，请查看更新窗口或诊断日志。');
        this.pending.delete(id);acknowledged=true;
        this.diagnostic('update_startup_forwarded',{transaction:id});
      }
      return {acknowledged};
    })();
    try {return await this.flushing;}
    catch(error) {this.diagnostic('update_startup_forward_failed',{error:error.message});throw error;}
    finally {this.flushing=null;}
  }
}
module.exports={prepareInstall,commitInstall,startupRequest,UpdateStartup};
