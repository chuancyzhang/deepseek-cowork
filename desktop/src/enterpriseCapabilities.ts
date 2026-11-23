import {useCallback, useEffect, useState} from 'react';
import {rpc} from './managementApi';
import type {Conversation, Settings} from './types';

export type EnterpriseCapabilityKind = 'skill' | 'mcp' | 'expert';
export type EnterpriseCapability = {
  id: string;
  kind: EnterpriseCapabilityKind;
  name: string;
  description: string;
  version: number | string;
  selection_key: string;
  available?: boolean;
  reason?: string;
  default_model_id?: string;
  requires_credential?: boolean;
  credential_configured?: boolean;
  endpoint_host?: string;
};

type Catalog = {
  status: 'ready' | 'signed_out' | 'expired' | 'unsupported';
  items: EnterpriseCapability[];
  platform_name?: string;
};

export function useEnterpriseCapabilities(visible: boolean) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [failure, setFailure] = useState('');
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => {
    setCatalog(null);
    setFailure('');
    setRevision(value => value + 1);
  }, []);
  useEffect(() => {
    setCatalog(null);
    setFailure('');
    if (!visible) return;
    let current = true;
    const unsubscribe = window.cowork.onEvents(events => {
      if (events.some(event => event.event === 'enterprise_changed')) {
        current = false;
        reload();
      }
    });
    void rpc<Catalog>('enterprise.capabilities').then(result => {
      if (current) setCatalog(result);
    }).catch(error => {
      if (current) setFailure((error as Error).message);
    });
    return () => {current = false; unsubscribe();};
  }, [visible, revision, reload]);
  const current = visible ? catalog : null;
  return {catalog: current, failure: visible ? failure : '', loading: visible && !current && !failure, reload};
}

export function enterpriseSource(platformName: string | undefined, capability: EnterpriseCapability) {
  return `${platformName || '企业'} · 版本 ${capability.version}`;
}

export function enterpriseExpertModel(expert: EnterpriseCapability, conversation: Conversation | null, settings: Settings | null) {
  const model = settings?.models.find(item => item.id === expert.default_model_id);
  return model?.configured && model.chat_supported !== false && !conversation?.meta.team_space_required ? model.id : undefined;
}
