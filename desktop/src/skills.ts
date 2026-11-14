export const skillScenes = [['search_browse', '查找资料'], ['docs_knowledge', '处理文档'], ['data_analysis', '分析数据'], ['content_creation', '制作内容'], ['financial_research', '金融研究']] as const;
export function skillInMode(skill: any, mode: string) {
  if (mode === 'builtin') return skill.source_kind === 'core_builtin';
  if (mode === 'library') return skill.source_type === 'bundled_plugin';
  return skill.source_kind !== 'core_builtin' && skill.source_type !== 'bundled_plugin' && skill.source_format !== 'mcp' && skill.editable;
}
export function skillMatches(skill: any, query: string) {
  return [skill.name, skill.display_name, skill.description, skill.user_description, ...Object.values(skill.presentation || {}), ...(skill.use_cases || [])]
    .join(' ').toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
}
