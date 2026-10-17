# 数据库结构

在部署文件夹内运行 `python -m server migrate`。版本 1 的完整结构定义在 `server/database.py`，初始化使用 SQLAlchemy 建表，并在 `platform.schema_version` 记录版本。

启动也检查结构版本；未知版本立即失败，不自动猜测、删除或重建数据。当前结构版本 1 在启动时增量创建独立管理员凭据表 `local_administrator` 和初始化状态表 `installation`，不修改既有表列或删除数据；已有成员或认证来源时不会开放初始化。后续结构升级须新增逐版迁移并保留回滚前备份，不能依赖 `create_all` 修改已有列。

同一数据库只服务一个 `platform_id`。不同企业必须使用独立数据库、加密密钥和文件卷。正式环境仅支持 PostgreSQL。
