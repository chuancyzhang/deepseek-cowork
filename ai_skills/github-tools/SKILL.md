---
name: github-tools
description: Tools for interacting with GitHub repositories (clone, analyze).
description_cn: GitHub 仓库操作工具（克隆、分析）。
license: MIT
type: bundled_plugin
source_type: bundled_plugin
default_enabled: false
created_by: system
allowed-tools: clone_repository, analyze_repository
---

# GitHub Tools

This skill provides tools to clone and analyze GitHub repositories. It is essential for the "GitHub to Skill" workflow, allowing the agent to read source code from open-source projects.

Use the generated analysis as source material for `skill-importer` or `skill-builder` when turning a repository pattern into a reusable Cowork skill.

## Tools

### clone_repository
Clones a GitHub repository to a temporary directory.

**Parameters:**
- `repo_url`: The URL of the repository (e.g., `https://github.com/yt-dlp/yt-dlp`).
- `workspace_dir` (optional): The base workspace directory.

**Returns:**
- The local path to the cloned repository.

### analyze_repository
Analyzes a local repository directory to extract file structure and content of key files (README, requirements.txt, setup.py).

**Parameters:**
- `repo_path`: The local path to the repository.

**Returns:**
- A markdown-formatted string containing the analysis summary.

## Current Runtime Notes
- `clone_repository` performs network and filesystem writes, so it must be called directly and never through `parallel_tools`.
- `analyze_repository` is read-oriented once the repository exists and can be combined with other independent read-only lookups when exposed in the current mode.
- Prefer official repository documentation and tests when deciding which capabilities should become Skill guidance.
