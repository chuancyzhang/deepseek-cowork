import type {HostStore} from './store';

export type UpdateState = {
  revision:string; phase:string; message:string; error:string; percent:number; busy:boolean;
  current_version:string; version:string; ready:boolean; can_install:boolean; notice:boolean;
  identity:string; source:string; reinstall:boolean; notes:string; packaged:boolean; local_manifest_verified:boolean;
};

export function receiveUpdate(store:HostStore, value:UpdateState) {
  const previous=store.get<UpdateState|null>('updates',null);
  if(previous && BigInt(previous.revision)>BigInt(value.revision))return;
  store.set('updates',value);
}
