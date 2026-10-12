"""Project-list disclosure independent of individual conversation disclosure."""

PROJECT_LIST_INITIAL_LIMIT = 5


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
