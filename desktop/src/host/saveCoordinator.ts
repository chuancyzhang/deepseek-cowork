export type SaveReason='plugin'|'reload'|'quit'|'update'|'hide';
/** Persist recovery work without changing the explicit file/config save boundary. */
export class SaveCoordinator {
  participants=new Map<string,(reason:SaveReason)=>Promise<void>>();
  pending:Promise<void>|null=null;
  register(id:string,save:(reason:SaveReason)=>Promise<void>) {this.participants.set(id,save);return ()=>{this.participants.delete(id);};}
  async preserve(reason:SaveReason) {
    if (this.pending) await this.pending;
    const task=(async()=>{for (const save of this.participants.values()) await save(reason);})();
    this.pending=task;
    try {await task;} finally {if (this.pending===task) this.pending=null;}
  }
}
