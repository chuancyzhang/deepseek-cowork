import type {Conversation, ProjectRecord} from './types';

export const pathKey = (path: string) => /^[a-z]:|\\/i.test(path)
  ? path.replace(/\\/g, '/').toLowerCase().replace(/\/+$/, '') : path.replace(/\/+$/, '');

export const chatWorkspace = ({meta}: Conversation) => meta.team_space_required || ['chat', 'team_space'].includes(meta.workspace_source)
  ? '' : pathKey(meta.workspace_dir || meta.workspace_path || '');

export const projectPaths = (project: ProjectRecord) => [project.path, ...(project.previous_paths || [])];

export const projectLayoutPath = (project: ProjectRecord) => project.previous_paths?.[0] || project.path;
