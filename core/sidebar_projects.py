"""Project-list disclosure independent of individual conversation disclosure."""

PROJECT_LIST_INITIAL_LIMIT = 5
SIDEBAR_SEARCH_PAGE_SIZE = 50


def page_sidebar_search(projects, unassigned, limit):
    """Bound widget creation in render order; a project's header accompanies hits."""
    ordered = []
    pinned = [entry for project in projects for entry in project["sessions"] if entry.get("pinned")]
    pinned += [entry for entry in unassigned if entry.get("pinned")]
    for project in projects:
        if project.get("pinned"):
            ordered.extend(("session", entry["id"]) for entry in project["sessions"] if not entry.get("pinned"))
            if not project["sessions"]:
                ordered.append(("project", project["path"]))
    ordered.extend(("session", entry["id"]) for entry in sorted(pinned, key=lambda e: -int(e.get("updated_at") or 0)))
    for project in projects:
        if not project.get("pinned"):
            ordered.extend(("session", entry["id"]) for entry in project["sessions"] if not entry.get("pinned"))
            if not project["sessions"]:
                ordered.append(("project", project["path"]))
    ordered.extend(("session", entry["id"]) for entry in sorted(unassigned, key=lambda e: -int(e.get("updated_at") or 0)))
    ordered = list(dict.fromkeys(ordered))
    selected = set(ordered[:max(1, int(limit))])
    visible_projects = []
    for project in projects:
        sessions = [entry for entry in project["sessions"] if ("session", entry["id"]) in selected]
        if sessions or ("project", project["path"]) in selected:
            visible_projects.append({**project, "sessions": sessions})
    return (visible_projects, [entry for entry in unassigned if ("session", entry["id"]) in selected],
            max(0, len(ordered) - len(selected)))


def visible_sidebar_projects(projects, required_paths=(), *, expanded=False, query=False):
    projects = list(projects)
    if expanded or query:
        return projects
    required = set(required_paths)
    ordinary = [item for item in projects if not item.get("pinned") and item["path"] not in required]
    recent = sorted(ordinary, key=lambda item: (
        -int(item.get("updated_at") or 0),
        item.get("name", "").casefold(),
        item["path"],
    ))
    visible = required | {item["path"] for item in recent[:PROJECT_LIST_INITIAL_LIMIT]}
    return [item for item in projects if item.get("pinned") or item["path"] in visible]
