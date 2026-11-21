export type LibrarySelection = {collection: string; item: string; title: string};
export type LibraryVisit = {
  source: string; identity: string; category: string; collection: string; collectionName: string; time: number;
  item: {id: string; title: string; is_folder: boolean; folder_path?: string; relative?: string; readable?: boolean};
  parents: {id: string; title: string; is_folder: boolean; folder_path?: string}[];
};

const recentKey = 'cowork-library-recent';
export const libraryIdentity = (account: any) => account && !account.unavailable
  ? JSON.stringify([account.connection_id, account.user_id, account.tenant_id]) : '';
export const libraryVisitKey = (visit: LibraryVisit) => JSON.stringify([visit.source, visit.identity, visit.collection, visit.item.id]);
export function loadLibraryVisits(): LibraryVisit[] {
  try {
    const value = JSON.parse(localStorage.getItem(recentKey) || '[]');
    return Array.isArray(value) ? value.filter(visit => typeof visit?.item?.id === 'string' && Array.isArray(visit.parents)) : [];
  } catch {return [];}
}
export function saveLibraryVisits(visits: LibraryVisit[]) {
  localStorage.setItem(recentKey, JSON.stringify(visits));
  return visits;
}
export const libraryItemId = (item: any) => String(item.item_id ?? item.id ?? item.knowledge_id ?? item.folder_path ?? '');
export const libraryItemKey = (item: any) => JSON.stringify([item.collection_id ?? item.knowledge_base_id ?? '', libraryItemId(item), Boolean(item.is_folder)]);

// Resource identity and navigation memberships are separate: dedupe within a view.
export function libraryCollections(catalog: any, category = 'all') {
  const organizations = new Map((catalog?.organizations || []).map((item:any) => [String(item.id), item.name]));
  const resources = new Map<string, any>();
  for (const group of ['mine', 'shared', 'others']) for (const item of catalog?.[group] || []) {
    const organization = item.organization_name || organizations.get(String(item.organization_id)) || '';
    if (category === 'mine' && group !== 'mine') continue;
    if (category === 'shared' && group === 'mine') continue;
    if (category.startsWith('org:') && (group !== 'shared' || String(item.organization_id || organization) !== category.slice(4))) continue;
    const id = String(item.id), existing = resources.get(id);
    if (existing) { if (organization && !existing.sharedWith.includes(organization)) existing.sharedWith.push(organization); continue; }
    resources.set(id, {...item, id, group:group==='mine'?'mine':'shared', sharedWith:organization?[organization]:[]});
  }
  return [...resources.values()];
}
export function librarySpaces(catalog: any) {
  const spaces = new Map<string, any>();
  for (const item of catalog?.organizations || []) spaces.set(String(item.id), {id:String(item.id), name:item.name});
  for (const item of catalog?.shared || []) {
    const id = String(item.organization_id || item.organization_name || '');
    if (id && !spaces.has(id)) spaces.set(id, {id, name:item.organization_name || '共享空间'});
  }
  return [...spaces.values()];
}
export function appendLibraryRows(previous: any[], items: any[]) {
  return [...new Map([...previous, ...items].map(item => [libraryItemKey(item), item])).values()];
}
