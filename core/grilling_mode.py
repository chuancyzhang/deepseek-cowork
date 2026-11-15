"""Grilling mode policy; the host still owns messages, tools and execution.

Prompt text, checkpoint protocol and transition events are preserved verbatim.
"""
import time
from core.clarify_mode import RUN_MODE_EXECUTION, RUN_MODE_GRILLING, GRILL_MAX_ROUNDS, GRILL_CHECKPOINT_PURPOSE


class GrillingModePolicy:
    @staticmethod
    def validate_input(worker, args):
        if not isinstance(args, dict):
            return ""
        purpose = str(args.get("purpose") or "").strip().lower()
        is_checkpoint = purpose == GRILL_CHECKPOINT_PURPOSE
        questions = args.get("questions")
        if worker._is_grilling_mode() and not questions:
            return "grilling input must use questionnaire questions."
        if not questions:
            return ""
        if not isinstance(questions, list):
            return "request_user_input questions must be a list."
        for index, item in enumerate(questions, start=1):
            if not isinstance(item, dict):
                return f"request_user_input question {index} must be an object."
            options = item.get("options")
            if not isinstance(options, list) or not options:
                return f"request_user_input question {index} must provide selectable options."
        if is_checkpoint:
            if len(questions) != 1:
                return "grill checkpoint must contain exactly one decision question."
            if str(questions[0].get("id") or "").strip() != "grill_next_action":
                return "grill checkpoint question id must be grill_next_action."
            option_values = [
                str(item.get("value") or item.get("label") or "").strip().lower()
                for item in (questions[0].get("options") or [])
                if isinstance(item, dict)
            ]
            if option_values != ["execute", "continue"]:
                return "grill checkpoint options must be execute then continue."
        return ""

    @staticmethod
    def _is_grill_checkpoint_args(args):
        return (
            isinstance(args, dict)
            and str(args.get("purpose") or "").strip().lower() == GRILL_CHECKPOINT_PURPOSE
        )

    @staticmethod
    def _grill_checkpoint_choice(result):
        if not isinstance(result, dict):
            return ""
        answers = result.get("answers") if isinstance(result.get("answers"), dict) else {}
        for answer in answers.values():
            if not isinstance(answer, dict):
                continue
            selected = [
                str(item or "").strip().lower()
                for item in (answer.get("selected_options") or [])
                if str(item or "").strip()
            ]
            if selected:
                return "custom" if selected[0] == "__custom__" else selected[0]
            if str(answer.get("text") or "").strip():
                return "custom"
        return ""

    @staticmethod
    def _grill_interaction_cancelled(result):
        if not isinstance(result, dict):
            return True
        response = result.get("interaction_response")
        if not isinstance(response, dict):
            return True
        return not bool(response.get("approved"))

    @staticmethod
    def apply_checkpoint(worker, args, result):
        if not worker._is_grilling_mode() or not worker._is_grill_checkpoint_args(args):
            return result
        if not isinstance(result, dict):
            return result
        choice = worker._grill_checkpoint_choice(result)
        if choice == "execute":
            previous_mode = worker._current_run_mode()
            worker.run_context["mode"] = RUN_MODE_EXECUTION
            worker.run_context["grill_execution_confirmed"] = True
            worker.run_context.pop("grill_checkpoint_cancelled", None)
            worker.run_context.pop("grill_input_cancelled", None)
            worker._refresh_tool_definitions()
            result["mode_transition"] = {
                "from": previous_mode,
                "to": RUN_MODE_EXECUTION,
                "reason": "user_confirmed",
            }
            result["content"] = (
                str(result.get("content") or "").rstrip()
                + "\n用户已确认执行；拷问阶段结束，现在按已确认的总结执行原任务。"
            ).strip()
            worker.observability_signal.emit({
                "type": "grill_execution_confirmed",
                "cycle": int(worker.run_context.get("grill_cycle_count") or 0) + 1,
                "round": int(worker.run_context.get("grill_round_count") or 0),
                "timestamp": time.time(),
            })
        elif choice in {"continue", "custom"}:
            worker.run_context["grill_cycle_count"] = int(
                worker.run_context.get("grill_cycle_count") or 0
            ) + 1
            worker.run_context["grill_round_count"] = 0
            worker.run_context["grill_execution_confirmed"] = False
            worker.run_context.pop("grill_checkpoint_cancelled", None)
            worker.run_context.pop("grill_input_cancelled", None)
            result["grill_cycle_transition"] = {
                "action": "continue",
                "choice": choice,
                "cycle": int(worker.run_context.get("grill_cycle_count") or 0),
                "round": 0,
            }
            result["content"] = (
                str(result.get("content") or "").rstrip()
                + "\n用户选择继续拷问；已开启新的 10 轮周期，请根据最新输入重建决策树。"
            ).strip()
            worker.observability_signal.emit({
                "type": "grill_cycle_started",
                "cycle": int(worker.run_context.get("grill_cycle_count") or 0) + 1,
                "round": 0,
                "timestamp": time.time(),
            })
        else:
            worker.run_context["grill_checkpoint_cancelled"] = True
            worker.run_context["grill_input_cancelled"] = True
            result["grill_checkpoint_cancelled"] = True
            result["grill_input_cancelled"] = True
            worker.observability_signal.emit({
                "type": "grill_checkpoint_cancelled",
                "cycle": int(worker.run_context.get("grill_cycle_count") or 0) + 1,
                "round": int(worker.run_context.get("grill_round_count") or 0),
                "status": str((result.get("interaction_response") or {}).get("status") or "cancelled"),
                "timestamp": time.time(),
            })
        return result

    @staticmethod
    def prompt_lines(context):
        run_mode = context.get("mode") or RUN_MODE_EXECUTION
        dynamic_state_lines = []
        if run_mode == RUN_MODE_GRILLING:
            grill_round = min(
                GRILL_MAX_ROUNDS,
                max(0, int(context.get("grill_round_count") or 0)),
            )
            grill_cycle = max(0, int(context.get("grill_cycle_count") or 0)) + 1
            dynamic_state_lines.extend(
                [
                    "",
                    "策略 [拷问模式]:",
                    "你当前处于拷问模式。目标是在执行原任务前，与用户形成可执行的共同理解；用户选择执行前，不得执行原任务或产生写入、发布、命令执行等副作用。",
                    "1. 先利用上下文和只读工具查明事实。能从环境、文件或工具获得的信息由你负责查找，不要反问用户。",
                    "2. 将需求建模为决策树：每项关键决策都可能解锁后续依赖决策。",
                    "3. 每轮重新计算“决策前沿”：只包含前置决策已经确定、现在可以回答的问题。在同一轮问卷中询问当前全部相互独立的高影响前沿问题；依赖本轮其他答案的问题留到下一轮。",
                    "4. 每个问题必须实质性影响目标、范围、约束、方案或验收标准。提供互斥选项，将推荐答案放在第一位，并在说明中给出推荐理由。系统会自动添加“自定义”选项。",
                    "5. 用户回答后重新构建决策树和决策前沿，不要机械重复预设问题。",
                    f"6. 当前是第 {grill_cycle} 个拷问周期，已完成 {grill_round}/{GRILL_MAX_ROUNDS} 轮。每个周期最多 {GRILL_MAX_ROUNDS} 轮；需求充分明确时可以提前总结，也可以在无需提问时直接总结。",
                    "7. 进入总结时，必须列出：目标、成功标准、范围、约束、关键决策、仍未解决的问题、风险、显式假设和执行步骤。不得隐藏或替用户裁决未决事项。",
                    "模型不得替用户判断是否安全或是否仍可执行。即使存在风险、范围歧义或未决项，也要完整写入总结并交由用户选择执行或继续拷问；进入 execution 后仍遵守既有审批和权限边界。",
                    "8. 总结后必须调用 `request_user_input`，设置 `purpose=\"grill_checkpoint\"`，并只提供一个问卷问题：id=`grill_next_action`，首选项为 label=`确认并执行`、value=`execute`，第二项为 label=`继续拷问`、value=`continue`。不要手工添加“自定义”。",
                    "9. 用户选择继续拷问或填写自定义内容时，系统会开启新的拷问周期并把轮次归零；你必须根据最新输入重建决策树。",
                    "10. 用户选择确认并执行后，系统会切换到 execution 模式。按照总结中的决定执行原任务，不要重新开始拷问；新出现的必要阻塞按普通澄清规则处理。",
                ]
            )
            if grill_round >= GRILL_MAX_ROUNDS:
                dynamic_state_lines.append(
                    "当前周期已达到 10 轮上限：不得再提出普通拷问问题，必须立即总结并展示 grill_checkpoint 决策卡。"
                )
            if context.get("grill_checkpoint_cancelled"):
                dynamic_state_lines.append(
                    "用户已取消或未在时限内完成决策卡：不得执行，也不得继续提问；请简短说明本次拷问已停止并结束当前任务。"
                )
        elif context.get("grill_execution_confirmed"):
            dynamic_state_lines.extend(
                [
                    "",
                    "策略 [拷问已确认]: 用户已经确认决策总结。立即按照已确认内容执行原任务，不要重新进入拷问模式。",
                ]
            )
        return dynamic_state_lines
