/** One host-owned state graph. Views subscribe to the keys they actually use. */
export type Update<T> = T | ((previous:T)=>T);
export class HostStore {
  values = new Map<string,unknown>();
  listeners = new Map<string,Set<()=>void>>();
  all = new Set<()=>void>();
  version = 0;
  get<T>(key:string, initial?:T|(()=>T)):T {
    if (!this.values.has(key)) this.values.set(key,typeof initial==='function' ? (initial as ()=>T)() : initial);
    return this.values.get(key) as T;
  }
  set<T>(key:string, update:Update<T>) {
    const previous=this.get<T>(key), value=typeof update==='function' ? (update as (value:T)=>T)(previous) : update;
    if (Object.is(previous,value)) return value;
    this.values.set(key,value); this.version++;
    for (const listener of this.listeners.get(key)||[]) listener();
    for (const listener of this.all) listener();
    return value;
  }
  subscribe(key:string, listener:()=>void) {
    if (!this.listeners.has(key)) this.listeners.set(key,new Set());
    this.listeners.get(key)!.add(listener);
    return ()=>{this.listeners.get(key)?.delete(listener);};
  }
  subscribeAll(listener:()=>void) { this.all.add(listener); return ()=>{this.all.delete(listener);}; }
  sessionKey(field:string, id=this.get<string>('navigation:selected','')) { return `session:${id}:${field}`; }
  ref<T>(key:string|(()=>string), initial?:T) {
    const store=this, resolve=()=>typeof key==='function' ? key() : key;
    store.get(resolve(),initial);
    return {get current():T {return store.get<T>(resolve(),initial);},set current(value:T) {store.set(resolve(),value);}};
  }
}
