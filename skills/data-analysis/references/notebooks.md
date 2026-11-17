# Python Notebook

用户需要 .ipynb 时创建或修改 Notebook。使用 nbformat.v4 创建、nbformat.read(..., as_version=4) 读取、nbformat.validate 校验，不手拼未经检查的 JSON。保留用户已有 Markdown、代码和有意义的元数据；源文件保持不变，修改/执行另存。

每本笔记包含问题、来源/口径、必要清洗、计算、图表、核验与结论，不堆空单元格。路径相对当前工作区，随机过程固定种子，依赖使用随包库。不得把密钥写入代码或输出。

执行使用 run_skill_script(skill_name="data-analysis", script_name="execute_notebook", args=["input.ipynb", "executed.ipynb", "--cell-timeout", "120"], timeout_seconds=600)。文件位于当前工作区；输入输出必须不同，已有输出默认拒绝，明确覆盖时才加 --overwrite。整体 timeout_seconds 依任务设为有限值。

执行器使用当前应用 Python 的全新内核，清理旧输出后从头顺序执行，遇错误停止；不启动 JupyterLab 或长期服务。每个单元格前后原子保存：失败时已完成结果和错误单元格输出保留，超时/停止时至少保留最后完成的单元格。强制停止后 metadata.cowork_execution 可能仍是 running，此时以宿主工具 cancelled/timeout 为准，绝不能宣称整本成功。重新执行从头开始，避免依赖隐藏状态。

查看最终 execution_count、输出与错误，不以文件存在判定通过。检查关键表格，返回 figures 中的 PNG 必须用 view_image 实际查看；图形还保存在 notebook 输出中。错误定位使用保留文件中的 cell 索引及错误类型，不自动忽略错误继续。原始输入仍可恢复。

参考 API：[nbformat](https://nbformat.readthedocs.io/en/latest/api.html)、[nbclient](https://nbclient.readthedocs.io/en/latest/client.html)。
