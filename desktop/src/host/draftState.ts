import {useCallback,useSyncExternalStore} from 'react';
import type {Drafts} from '../drafts';
export function useDraft(drafts:Drafts,id:string) {
  const subscribe=useCallback((listener:()=>void)=>drafts.subscribe(id,listener),[drafts,id]);
  const read=useCallback(()=>drafts.versions.get(id)||0,[drafts,id]);
  useSyncExternalStore(subscribe,read,read);
  return drafts.get(id);
}
