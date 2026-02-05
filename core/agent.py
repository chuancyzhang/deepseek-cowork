import sys
import subprocess
import tempfile
import os
import ast
import re
import json
import platform
import time
import shutil
from datetime import datetime
from PySide6.QtCore import QThread, Signal, QObject, QMutex, QWaitCondition
from core.skill_manager import SkillManager
from core.env_utils import get_python_executable
from core.llm.factory import LLMFactory

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

class SecurityError(Exception):
    pass

def validate_code_safety(code, allowed_dir, god_mode=False):
    """AST 静态分析代码安全性"""
    if god_mode:
        return True

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SecurityError(f"Syntax Error: {e}")

    allowed_dir = os.path.abspath(allowed_dir).lower()

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if ".." in val:
                 raise SecurityError(f"Security Alert: Path traversal '..' detected in string: '{val}'")
            if os.path.isabs(val):
                abs_val = os.path.abspath(val).lower()
                if not abs_val.startswith(allowed_dir):
                     raise SecurityError(f"Security Alert: Unauthorized absolute path access: '{val}'")
    return True

class CodeWorker(QThread):
    """后台执行 Python 代码的线程"""
    output_signal = Signal(str)
    finished_signal = Signal()
    input_request_signal = Signal(str)

    def __init__(self, code, cwd, god_mode=False):
        super().__init__()
        self.code = code
        self.cwd = cwd
        self.god_mode = god_mode
        self.process = None
        self.is_stopped = False

    def provide_input(self, text):
        """Write user input to stdin"""
        if self.process and self.process.stdin:
            try:
                self.process.stdin.write(text + "\n")
                self.process.stdin.flush()
            except Exception as e:
                print(f"Error writing to stdin: {e}")

    def stop(self):
        self.is_stopped = True
        if self.process:
            try:
                self.process.terminate() # Try graceful termination
                self.output_signal.emit("System: Terminating process...")
            except:
                pass

    def run(self):
        temp_path = None
        try:
            # 1. Validation
            try:
                validate_code_safety(self.code, self.cwd, god_mode=self.god_mode)
            except SecurityError as e:
                self.output_signal.emit(f"❌ {str(e)}")
                # We will let the finally block emit finished_signal
                return

            # Prepend input() override to capture user interaction
            input_override = """
import sys
import io

# Set stdout/stderr to utf-8 explicitly for Windows console
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def input(prompt=""):
    print(f"__REQUEST_INPUT__:{prompt}", flush=True)
    return sys.stdin.readline().strip()
"""
            full_code = input_override + "\n" + self.code

            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                f.write(full_code)
                temp_path = f.name

            # Determine python executable
            python_exe = get_python_executable()

            if self.is_stopped: return

            # Force environment variables for UTF-8 encoding
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            
            # In frozen mode, we might need to adjust PATH to include the bundled python dir
            # so that subprocesses can find DLLs etc. (optional but good practice)
            if getattr(sys, 'frozen', False):
                 python_dir = os.path.dirname(python_exe)
                 env["PATH"] = python_dir + os.pathsep + env.get("PATH", "")

            self.output_signal.emit(f"Running with {python_exe} in: {self.cwd}...")
            self.process = subprocess.Popen(
                [python_exe, temp_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE, # Enable stdin for input()
                text=True,
                cwd=self.cwd,
                encoding='utf-8', # 强制 UTF-8 避免中文乱码
                errors='replace',
                bufsize=0, # Unbuffered for real-time
                env=env # Apply environment variables
            )
            
            # Real-time output reading
            while True:
                if self.is_stopped:
                    self.process.kill()
                    self.output_signal.emit("⚠️ Process stopped by user.")
                    break
                
                output = self.process.stdout.readline()
                if output == '' and self.process.poll() is not None:
                    break
                if output:
                    output = output.strip()
                    if output.startswith("__REQUEST_INPUT__:"):
                        prompt = output.split(":", 1)[1]
                        self.input_request_signal.emit(prompt)
                    else:
                        self.output_signal.emit(output)
            
            if not self.is_stopped:
                stderr = self.process.stderr.read()
                if stderr:
                    self.output_signal.emit(f"Error Output:\n{stderr}")
            
        except Exception as e:
            self.output_signal.emit(f"Execution Error: {e}")
            # Also print to console for debugging
            import traceback
            traceback.print_exc()
            
        finally:
            # Clean up temp file
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
            self.finished_signal.emit()

def clear_reasoning_content(messages):
    """
    Helper to clear reasoning content from messages list to prevent repetition.
    Returns a new list of cleaned messages (shallow copy of dicts with keys removed).
    """
    cleaned = []
    for msg in messages:
        clean_msg = msg.copy()
        if 'reasoning_content' in clean_msg:
            del clean_msg['reasoning_content']
        if 'reasoning' in clean_msg: # Also clear our internal key
            del clean_msg['reasoning']
        cleaned.append(clean_msg)
    return cleaned

class LLMWorker(QThread):
    """后台调用 LLM API 的线程，支持 Tool Calls 和多轮思考"""
    finished_signal = Signal(dict)
    step_signal = Signal(str) # 用于输出中间步骤日志
    thinking_signal = Signal(str) # 用于实时输出思考过程
    skill_used_signal = Signal(str) # Signal to report active skill usage
    tool_call_signal = Signal(dict)
    tool_result_signal = Signal(dict)
    content_signal = Signal(str)
    output_signal = Signal(str) # For generic output/errors
    agent_state_signal = Signal(dict) # Signal to report sub-agent status
    abort_signal = Signal() # Signal emitted when the worker is stopped

    def __init__(self, messages, config_manager, workspace_dir=None, parent_agent_id=None):
        super().__init__()
        self.messages = messages
        self.config_manager = config_manager
        self.api_key = config_manager.get("api_key")
        self.workspace_dir = workspace_dir
        self.parent_agent_id = parent_agent_id
        
        # Flags for control
        self.is_paused = False
        self.is_stopped = False
        
        # Initialize Skill Manager
        self.skill_manager = SkillManager(workspace_dir, config_manager)
        self.tools = self.skill_manager.get_tool_definitions()

    def pause(self):
        self.is_paused = True
        self.step_signal.emit("System: Paused.")

    def resume(self):
        self.is_paused = False
        self.step_signal.emit("System: Resumed.")

    def stop(self):
        self.is_stopped = True
        self.is_paused = False # Ensure loop breaks if paused
        self.step_signal.emit("System: Stopping...")
        self.abort_signal.emit()

    def run(self):
        # Work on a copy of messages to handle multi-turn locally
        # CRITICAL: Clear previous reasoning content to avoid duplication/confusion in new turn
        current_messages = clear_reasoning_content(self.messages)
        
        # Construct System Context
        context_lines = [
            f"当前工作区: {self.workspace_dir}",
            f"操作系统: {platform.system()} {platform.release()}",
            f"Python 版本: {sys.version.split()[0]}",
            f"当前日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "注意: 你正在指定的工作区内操作。除非明确允许使用绝对路径，否则所有文件操作都应相对于此路径。",
            "能力: 你可以使用 'create_new_skill' 创建新的技能/工具。",
            "策略 [技能创建]:",
            "1. 鼓励创建新技能来封装可复用的任务（例如：特定的文件处理、复杂计算、数据转换、系统操作等）。",
            "2. 当你发现某个任务可能在未来被再次使用，或者通过代码实现比通过纯文本生成更可靠时，请果断创建技能。",
            "3. 不要受到过度限制，灵活运用技能来增强你的能力。",
            "",
            "策略 [自我进化]:",
            "1. 你拥有 'update_experience' 工具，用于记录重要的经验教训、配置偏好或特定的工具使用技巧。",
            "2. 当你成功解决一个难题、发现某个工具的最佳实践或遇到并修复了错误时，请务必使用 'update_experience' 记录下来。",
            "3. 这些经验将在未来类似场景中自动注入，帮助你变得更聪明。",
            "",
            "策略 [记忆]:",
            "1. 你拥有 'read_memories' 与 'write_memories' 工具，用于读取/更新 memories.md（可能不存在或为空）。",
            "2. 在每次对话结束后，若出现长期稳定偏好、重要背景、持续项目约定、用户身份/环境信息，才更新 memories.md；否则不要更新。",
            "3. 避免写入敏感信息或临时细节；默认追加，只有在需要整体整理时才使用替换模式。",
            "",
            "策略 [交互]: 如果你需要向用户提问或获取确认（例如：删除文件、澄清需求或下一步操作），你必须使用 'ask_user_confirmation' 工具。",
            "不要在文本回复中直接提问。文本回复仅用于展示推理过程和最终答案。请使用工具来触发弹出对话框。",
            "",
            "策略 [思考规范]:",
            "1. 你的思考过程 (Reasoning) 仅用于分析问题、规划步骤和反思结果。",
            "2. 严禁将最终给用户的回复（如任务总结、文件列表、结果汇报）放在思考过程中。",
            "3. 思考过程对用户是折叠的，用户主要阅读的是你的最终 Content 回复。"
        ]
        if self.parent_agent_id:
            context_lines.append(f"Note: You are a sub-agent (ID: {self.parent_agent_id}). Perform your assigned task efficiently.")

        memories_text = ""
        if self.config_manager:
            try:
                history_dir = self.config_manager.get_chat_history_dir()
                memories_path = os.path.join(history_dir, "memories.md")
                if os.path.exists(memories_path):
                    with open(memories_path, "r", encoding="utf-8") as f:
                        memories_text = f.read().strip()
            except Exception:
                memories_text = ""
        if memories_text:
            context_lines.append("\n# Memories\n" + memories_text)

        # Append Skill-Specific Prompts (e.g. usage guidelines, learned experiences)
        if self.skill_manager.skill_prompts:
            context_lines.append("\n# Skill Capabilities & Guidelines")
            context_lines.extend(self.skill_manager.skill_prompts)

        system_prompt = "\n".join(context_lines)
        
        # Insert System Message
        current_messages.insert(0, {"role": "system", "content": system_prompt})
        
        full_reasoning = ""
        final_content = ""
        turn_count = 0
        total_duration = 0
        generated_messages = []
        
        last_tool_signature = None
        repetition_count = 0
        
        last_turn_reasoning = None
        reasoning_repetition_count = 0
        
        while True:
            # Check Control Flags
            while self.is_paused:
                if self.is_stopped: break
                self.msleep(100)
            if self.is_stopped: 
                final_content = "⚠️ Operation stopped by user."
                break

            turn_count += 1
            self.step_signal.emit(f"Turn {turn_count}: Requesting LLM...")

            # --- Hot Reload Skills ---
            # Check if any new skills were added or modified
            if self.skill_manager.check_for_updates():
                self.step_signal.emit("System: Detecting skill updates... Reloading.")
                self.skill_manager.load_skills()
                self.tools = self.skill_manager.get_tool_definitions()
            # -------------------------

            # Reset reasoning for the current turn (for UI display)
            current_turn_reasoning = ""

            if self.api_key:
                try:
                    start_time = time.time()
                    
                    # Create Provider via Factory
                    provider = LLMFactory.create_provider(self.config_manager)
                    stream = provider.chat_stream(current_messages, tools=self.tools)
                    
                    # Streaming Buffers
                    chunk_reasoning = ""
                    chunk_content = ""
                    tool_calls_buffer = {} # Index -> ToolCall object (dict)
                    
                    for chunk in stream:
                        # Check Pause/Stop during stream
                        while self.is_paused:
                             if self.is_stopped: break
                             self.msleep(100)
                        if self.is_stopped: break
                        
                        type_ = chunk.get("type")
                        
                        # 1. Handle Reasoning
                        if type_ == "reasoning":
                            r_content = chunk["content"]
                            current_turn_reasoning += r_content
                            full_reasoning += r_content
                            self.thinking_signal.emit(r_content)
                            
                        # 2. Handle Content
                        elif type_ == "content":
                            c_content = chunk["content"]
                            chunk_content += c_content
                            self.content_signal.emit(c_content)
                        
                        # 3. Handle Tool Calls
                        elif type_ == "tool_call":
                            index = chunk.get("index", 0) # Default to 0 if not provided
                            
                            if index not in tool_calls_buffer:
                                tool_calls_buffer[index] = {
                                    "id": chunk.get("id"),
                                    "type": "function",
                                    "function": {
                                        "name": chunk["function"].get("name", ""),
                                        "arguments": ""
                                    }
                                }
                            
                            # Append arguments
                            if "arguments" in chunk["function"]:
                                tool_calls_buffer[index]["function"]["arguments"] += chunk["function"]["arguments"]
                        
                        # 4. Handle Error
                        elif type_ == "error":
                            self.output_signal.emit(f"Provider Error: {chunk['content']}")

                    end_time = time.time()
                    duration = end_time - start_time
                    total_duration += duration
                    
                    # --- Reasoning Loop Detection ---
                    if current_turn_reasoning and len(current_turn_reasoning) > 10: # Ignore very short reasonings
                        if current_turn_reasoning == last_turn_reasoning:
                            reasoning_repetition_count += 1
                        else:
                            reasoning_repetition_count = 0
                            last_turn_reasoning = current_turn_reasoning
                            
                        if reasoning_repetition_count >= 3:
                            self.step_signal.emit("系统: 🛑 检测到思维死循环 (重复的思考过程)。自动停止。")
                            final_content = "⚠️ 操作已停止: 检测到思维死循环 (重复的思考过程)。"
                            break
                    # --------------------------------

                    # Reconstruct final message object from buffers
                    content = chunk_content
                    
                    # Reconstruct tool_calls list
                    tool_calls = []
                    if tool_calls_buffer:
                        # Convert buffer to list of objects mimicking OpenAI ToolCall
                        # We need to be careful to match the structure expected by the loop logic
                        for idx in sorted(tool_calls_buffer.keys()):
                            t_data = tool_calls_buffer[idx]
                            # Create a simple object structure
                            class ToolCallObj:
                                pass
                            class FunctionObj:
                                pass
                                
                            t_obj = ToolCallObj()
                            t_obj.id = t_data["id"]
                            t_obj.type = t_data["type"]
                            t_obj.function = FunctionObj()
                            t_obj.function.name = t_data["function"]["name"]
                            t_obj.function.arguments = t_data["function"]["arguments"]
                            
                            tool_calls.append(t_obj)

                    # Append Assistant Message to History (Manual reconstruction)
                    assistant_msg = {
                        "role": "assistant",
                        "content": content
                    }
                    # CRITICAL: For tool calls WITHIN the same turn, DeepSeek requires reasoning_content
                    # We must use current_turn_reasoning, NOT full_reasoning, to avoid duplication in history
                    # Always include the key, even if empty, to satisfy API requirements
                    assistant_msg["reasoning_content"] = current_turn_reasoning
                    # Also add 'reasoning' for UI compatibility (used by MainWindow)
                    assistant_msg["reasoning"] = current_turn_reasoning
                        
                    if tool_calls:
                         # For history, we need the dict representation
                         assistant_msg["tool_calls"] = [
                             {
                                 "id": t.id,
                                 "type": t.type,
                                 "function": {
                                     "name": t.function.name,
                                     "arguments": t.function.arguments
                                 }
                             } for t in tool_calls
                         ]
                    current_messages.append(assistant_msg)
                    generated_messages.append(assistant_msg)
                    
                    if tool_calls:
                        # --- Loop Detection ---
                        try:
                            current_signature = json.dumps(
                                sorted([{"name": t.function.name, "args": json.loads(t.function.arguments)} for t in tool_calls], key=lambda x: x['name']),
                                sort_keys=True
                            )
                            if current_signature == last_tool_signature:
                                repetition_count += 1
                            else:
                                repetition_count = 0
                                last_tool_signature = current_signature
                                
                            if repetition_count >= 3: # Same toolset called 4 times in a row
                                self.step_signal.emit("系统: 🛑 检测到循环 (重复的工具调用)。自动停止。")
                                final_content = "⚠️ 操作已停止: 检测到死循环 (重复的工具调用)。"
                                break
                        except Exception as e:
                            print(f"Loop detection error: {e}")
                        # ----------------------

                        self.step_signal.emit(f"Tool Calls Detected: {len(tool_calls)}")
                        for tool in tool_calls:
                            # Check Control Flags inside tool loop
                            while self.is_paused:
                                if self.is_stopped: break
                                self.msleep(100)
                            if self.is_stopped: break
                            
                            name = tool.function.name
                            args = json.loads(tool.function.arguments)
                            self.step_signal.emit(f"Executing Tool: {name}({args})")
                            
                            # Emit Tool Call Signal
                            self.tool_call_signal.emit({
                                "id": tool.id,
                                "name": name,
                                "args": args
                            })
                            
                            # Report Active Skill
                            skill_name = self.skill_manager.get_skill_of_tool(name)
                            if skill_name:
                                self.skill_used_signal.emit(skill_name)
                            
                            # Execute via Skill Manager
                            # Pass step_signal as context to allow tools to log
                            result = self.skill_manager.call_tool(
                                name, 
                                args, 
                                context={
                                    "step_signal": self.step_signal, 
                                    "config_manager": self.config_manager,
                                    "skill_manager": self.skill_manager,
                                    "agent_state_signal": self.agent_state_signal,
                                    "tool_call_id": tool.id,
                                    "abort_signal": self.abort_signal
                                }
                            )
                            
                            # Emit Tool Result Signal
                            self.tool_result_signal.emit({
                                "id": tool.id,
                                "result": str(result)
                            })

                            tool_msg = {
                                "role": "tool",
                                "tool_call_id": tool.id,
                                "content": str(result) # Ensure content is string to avoid API errors
                            }
                            current_messages.append(tool_msg)
                            generated_messages.append(tool_msg)
                            self.step_signal.emit(f"Tool Result: {result}")
                        # Loop continues to let LLM see tool results
                        continue
                    else:
                        # Final Answer
                        final_content = content
                        break
                        
                except Exception as e:
                    self.finished_signal.emit({"error": str(e)})
                    return
            else:
                # --- Mock Logic / Warning for Missing API Key ---
                time.sleep(1)
                
                reasoning = "检测到 API Key 未配置或 OpenAI 库不可用。无法连接到 DeepSeek 模型。"
                full_reasoning += f"\n[System]: {reasoning}"
                self.step_signal.emit(f"System: {reasoning}")
                
                final_content = (
                    "⚠️ **未配置 API Key**\n\n"
                    "请点击右上角的 **⚙️ 设置** 按钮配置您的 DeepSeek API Key。\n"
                    "配置完成后，我将能够为您执行复杂的文件操作和代码生成任务。"
                )
                
                break

        self.finished_signal.emit({
            "reasoning": full_reasoning.strip(),
            "content": final_content,
            "role": "assistant",
            "duration": total_duration,
            "generated_messages": generated_messages
        })

        self.agent_state_signal.emit({
            "agent_id": self.parent_agent_id or "Main", 
            "status": "completed", 
            "content": final_content
        })
