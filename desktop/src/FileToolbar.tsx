import {createContext,useCallback,useContext,useLayoutEffect,useMemo,useState,type ReactNode} from 'react';
import type {MenuAction} from './MoreActions';

type Contribution={controls?:ReactNode;menu?:MenuAction[]};
type ToolbarContext={contribution:Contribution;publish:(value:Contribution)=>()=>void};
const Context=createContext<ToolbarContext|null>(null);

/** One host-owned toolbar per file. Editor contributions expire with their view instance. */
export function FileToolbarProvider({children}:{children:ReactNode}){
  const [entry,setEntry]=useState<{owner:symbol;value:Contribution}|null>(null);
  const publish=useCallback((value:Contribution)=>{
    const owner=Symbol();setEntry({owner,value});
    return()=>setEntry(current=>current?.owner===owner?null:current);
  },[]);
  const value=useMemo(()=>({contribution:entry?.value||{},publish}),[entry,publish]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
export function FileToolbarControls(){return useContext(Context)?.contribution.controls||null;}
export function useFileToolbarMenu(){return useContext(Context)?.contribution.menu||[];}
export function useFileToolbarContribution(contribution:Contribution|null){
  const context=useContext(Context),publish=context?.publish;
  useLayoutEffect(()=>contribution&&publish?publish(contribution):undefined,[publish,contribution]);
  return Boolean(context);
}
