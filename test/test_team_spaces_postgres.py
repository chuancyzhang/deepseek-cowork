"""Run the same contract on an explicitly supplied disposable PostgreSQL DB."""
import os
import unittest
import uuid
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
import test_team_spaces as contracts


@unittest.skipUnless(os.environ.get("COWORK_TEST_POSTGRES_URL"), "未配置独立 PostgreSQL 测试库")
class TeamSpacePostgresTests(contracts.TeamSpaceTests):
    def setUp(self):
        url = make_url(os.environ["COWORK_TEST_POSTGRES_URL"])
        if url.host not in {"localhost", "127.0.0.1"} or not url.database.endswith("_qa"):
            raise ValueError("测试只允许本机 *_qa 数据库，并使用独立临时 schema。")
        self.pg_schema = "space_qa_" + uuid.uuid4().hex
        self.pg_engine = create_engine(url)
        with self.pg_engine.begin() as connection:
            connection.exec_driver_sql('CREATE SCHEMA "' + self.pg_schema + '"')
        def cleanup():
            with self.pg_engine.begin() as connection:
                connection.exec_driver_sql('DROP SCHEMA "' + self.pg_schema + '" CASCADE')
            self.pg_engine.dispose()
        self.addCleanup(cleanup)
        self.pg_url = url.update_query_dict({"options": "-csearch_path=" + self.pg_schema}).render_as_string(hide_password=False)
        super().setUp()

    def database_url(self):
        return self.pg_url
