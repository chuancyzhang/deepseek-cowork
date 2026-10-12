import unittest

from core.sidebar_projects import visible_sidebar_projects


class SidebarProjectTests(unittest.TestCase):
    def test_recent_five_disclosure_keeps_current_running_and_pinned_projects(self):
        # Name sorting must not hide a recently added project without conversations.
        projects = [dict(path=str(i), name=str(i), updated_at=i, has_conversations=True) for i in range(10)]
        projects += [dict(path="empty", name="empty", updated_at=999, has_conversations=False),
                     dict(path="pin", name="pin", pinned=True)]
        required = {"0", "1"}  # Current and running/pending-interaction projects.
        selected = visible_sidebar_projects(projects, required)
        self.assertEqual([p["path"] for p in selected], ["0", "1", "6", "7", "8", "9", "empty", "pin"])
        self.assertEqual(visible_sidebar_projects(projects, required, expanded=True), projects)
        self.assertEqual(visible_sidebar_projects(projects, required, query=True), projects)
        self.assertEqual(visible_sidebar_projects(projects, required), selected)
        projects[2]["updated_at"] = 1000
        self.assertIn(projects[2], visible_sidebar_projects(projects, required))
        self.assertNotIn(projects[6], visible_sidebar_projects(projects, required))
