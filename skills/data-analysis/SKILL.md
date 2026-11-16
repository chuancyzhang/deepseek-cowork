---
name: data-analysis
description: Analyze structured data to answer comparisons, trends, metric movements and decision questions; check data quality and validate calculations. Not for file renaming, format-only conversion or spreadsheet styling.
description_cn: 分析结构化数据，核对口径与质量，完成统计比较、趋势、指标诊断和结论验证。
type: builtin
source_type: builtin
default_enabled: true
metadata:
  author: deepseek-cowork team
  version: "1.0"
allowed-tools: [document_read, run_python_code, view_image]
---

# 数据分析

根据问题选择深度，简单计算直接完成；仅处理任务相关数据，不无目的遍历全部字段。输入 Excel 不决定交付格式；单纯重命名、转换格式或调整样式不启动本流程。

## 从问题到证据

1. 明确问题、比较对象、时间范围和决策用途。资料足够直接推进，只有缺失信息会改变核心口径才询问。
2. 确认来源、字段含义、每行代表什么、单位、时区、计数与分母。已有定义优先；无法确认时明确假设，不凭列名猜定业务含义。
3. 按影响检查缺失、重复、连接、覆盖和异常。质量、复杂比较或指标诊断任务读取 [质量与诊断](references/quality-and-diagnostics.md)。
4. 使用 run_python_code 与随包 pandas、NumPy、SciPy、Matplotlib、seaborn、scikit-learn。保留原数据，清洗与结果另存。运行时计算，向模型仅返回必要摘要与结果。
5. 独立复算重要或意外数字，检查总分关系、分母、连接前后行数与金额、过滤和日期边界。两个图共用同一计算函数不算独立核验。
6. 先给发现与影响，再给必要证据；事实、解释、假设和未解决问题分开。指标贡献不等于因果解释。

## 分析规则

- ID、编码、前导零不能当普通数值清洗；日期编码、货币、比例与单位先确认。
- 区分零、缺失、未匹配与空结果。异常值、退款、重复记录先判断业务含义，不自动删除或补零。
- 整体比率从总分子/总分母计算，不随意平均组比率、均值或分位数。
- 使用可比周期，不把未完整月份与整月等价比较。必要时对齐观察窗口并保留限制。
- 大文件按需选列、分块或聚合。抽样不能冒充全量统计，说明其适用范围。
- 仅在问题需要时建模、预测或检验，先读 [统计与建模](references/statistics.md)，不因库可用就自动训练。
- 数据不足时指出具体缺口，完成有依据的部分，不用演示数据或猜测冒充真实观测。

## 结果与协作

普通问题在聊天回答，表格用 Markdown。交互探索交给 visualize，静态图用绘图库，Word/PPT 分别使用 documents/presentations。复用同一份核验结果、来源与限定条件，不在排版时另算一套。

修改时间、筛选、分母或定义后，同步更新受影响计算、图表、结论及导出文件，不能只改数字留下旧结论。保留已有成果与用户选择的格式。

复杂分析保留实际执行的脚本/SQL、关键结果与必要来源以便复算；简单问题不额外生成 Notebook、看板、报告或过程档案。不因问题多就建站、发布或安排定时任务。外部数据复用已有连接器，不假设数仓、Pages、Sites 或专用 Data 运行时存在。随包库无需重复安装或升级。
