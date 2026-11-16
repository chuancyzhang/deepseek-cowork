// Resource identity is independent of the number of spaces sharing it.
export function libraryCollections(catalog: any) {
  const resources = new Map<string, any>();
  const organizations = new Map((catalog?.organizations || []).map((item:any) => [String(item.id), item.name]));
  for (const group of ['mine', 'shared', 'others']) for (const item of catalog?.[group] || []) {
    const id = String(item.id);
    const organization = item.organization_name || organizations.get(String(item.organization_id)) || '';
    const existing = resources.get(id);
    if (existing) {
      if (organization && !existing.sharedWith.includes(organization)) existing.sharedWith.push(organization);
      continue;
    }
    resources.set(id, {...item, id, group:group==='mine'?'mine':'shared', sharedWith:organization?[organization]:[]});
  }
  return [...resources.values()];
}
