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

class PlanGeneratorWorker(QThread):
    """
    专门用于 Deep Plan Mode 的规划线程。
    任务：分析用户请求 -> 生成 Markdown 格式的详细计划 -> 返回计划内容。
    注意：不执行任何写操作或代码运行。
    """
    finished_signal = Signal(str)
    step_signal = Signal(str)
    thinking_signal = Signal(str)

    def __init__(self, messages, config_manager, workspace_dir=None):
        super().__init__()
        self.messages = messages
        self.config_manager = config_manager
        self.api_key = config_manager.get("api_key")
        self.workspace_dir = workspace_dir
        
        # Skill Manager (Read-only tools preferred, but for simplicity we load all and restrict usage in Prompt)
        # Actually, for planning, we might need 'list_files', 'read_file' to know context.
        self.skill_manager = SkillManager(workspace_dir, config_manager)
        self.tools = self.skill_manager.get_tool_definitions()
        
    def run(self):
        self.step_signal.emit("Planning: Analyzing request and environment...")
        
        # Construct System Context for Planner
        context_lines = [
            f"Current Workspace: {self.workspace_dir}",
            f"Operating System: {platform.system()} {platform.release()}",
            f"Current Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "ROLE: You are an expert Technical Planner.",
            "TASK: Analyze the user's request and the current workspace state. Generate a detailed, step-by-step execution plan.",
            "OUTPUT FORMAT: Pure Markdown. Structure it with '## Goal', '## Analysis', '## Execution Plan' (numbered steps).",
            "CONSTRAINT 1: You are in READ-ONLY mode. You can use tools like `list_files` or `read_file` to gather context, but DO NOT propose any code execution or file modification yet.",
            "CONSTRAINT 2: Do not ask for user confirmation. Just output the best possible plan based on your analysis.",
            "CONSTRAINT 3: Your output MUST be the content of the plan itself. DO NOT output code blocks to write files.",
            "IMPORTANT: If the user request is simple, you MUST still generate a plan."
        ]
        
        system_prompt = "\n".join(context_lines)
        current_messages = self.messages.copy()
        current_messages.insert(0, {"role": "system", "content": system_prompt})
        
        final_plan = ""
        
        if self.api_key and OPENAI_AVAILABLE:
            try:
                client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com")
                
                # Single turn for planning
                # We might need a loop if the planner wants to call read tools first
                
                turn_count = 0
                while True:
                    turn_count += 1
                    if turn_count > 5: # Safety break
                        break
                        
                    response = client.chat.completions.create(
                        model="deepseek-reasoner",
                        messages=current_messages,
                        tools=self.tools,
                        stream=False # Simplify for planner
                    )
                    
                    message = response.choices[0].message
                    
                    # Handle Thinking (if available in message, though non-stream might hide it or put in reasoning_content)
                    if hasattr(message, 'reasoning_content') and message.reasoning_content:
                         self.thinking_signal.emit(message.reasoning_content)

                    if message.tool_calls:
                        # Execute Read-Only tools
                        current_messages.append(message)
                        
                        for tool_call in message.tool_calls:
                            func_name = tool_call.function.name
                            args = json.loads(tool_call.function.arguments)
                            
                            # Security Filter for Planner
                            if func_name not in ['list_files', 'read_file', 'search_codebase', 'glob']:
                                self.step_signal.emit(f"Planner: Skipping restricted tool {func_name}")
                                tool_result = "Tool execution skipped: Planner is in Read-Only mode."
                            else:
                                self.step_signal.emit(f"Planner: Checking context via {func_name}...")
                                try:
                                    func = self.skill_manager.get_tool_function(func_name)
                                    # Inject workspace_dir if needed
                                    import inspect
                                    sig = inspect.signature(func)
                                    if 'workspace_dir' in sig.parameters:
                                        args['workspace_dir'] = self.workspace_dir
                                        
                                    tool_result = str(func(**args))
                                except Exception as e:
                                    tool_result = f"Error: {e}"
                            
                            current_messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": tool_result
                            })
                    else:
                        # Final Plan
                        final_plan = message.content
                        break
                        
            except Exception as e:
                final_plan = f"Planning Failed: {e}"
        else:
            final_plan = "Error: OpenAI/DeepSeek API not initialized."

        self.finished_signal.emit(final_plan)

class LLMWorker(QThread):
    """后台调用 LLM API 的线程，支持 Tool Calls 和多轮思考"""
    finished_signal = Signal(dict)
    step_signal = Signal(str) # 用于输出中间步骤日志
    thinking_signal = Signal(str) # 用于实时输出思考过程
    skill_used_signal = Signal(str) # Signal to report active skill usage
    tool_call_signal = Signal(dict)
    tool_result_signal = Signal(dict)

    def __init__(self, messages, config_manager, workspace_dir=None, parent_agent_id=None, plan_mode=False):
        super().__init__()
        self.messages = messages
        self.config_manager = config_manager
        self.api_key = config_manager.get("api_key")
        self.workspace_dir = workspace_dir
        self.parent_agent_id = parent_agent_id
        self.plan_mode = plan_mode
        
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

    def run(self):
        # Work on a copy of messages to handle multi-turn locally
        current_messages = self.messages.copy()
        
        # Construct System Context
        context_lines = [
            f"Current Workspace: {self.workspace_dir}",
            f"Operating System: {platform.system()} {platform.release()}",
            f"Python Version: {sys.version.split()[0]}",
            f"Current Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "Note: You are operating within the specified workspace. All file operations should be relative to this path unless explicitly absolute and allowed.",
            "Capability: You can create new skills/tools using 'create_new_skill'.",
            "Policy [SKILL CREATION]:",
            "1. ONLY create new skills for reusable *algorithmic* or *system operation* tasks (e.g., specific file processing, complex calculations, data transformation).",
            "2. DO NOT create skills for tasks that you can perform naturally as an LLM (e.g., text summarization, translation, creative writing, code explanation). Just output the result directly.",
            "3. When you encounter a task that requires a new reusable tool, define it as a skill.",
            "",
            "Policy [INTERACTION]: If you need to ask the user a question or get confirmation (e.g., for deleting files, clarification, or next steps), you MUST use the 'ask_user_confirmation' tool.",
            "DO NOT ask the question in the text response. The text response is for reasoning and final answers only. Use the tool to trigger a popup dialog."
        ]
        if self.parent_agent_id:
            context_lines.append(f"Note: You are a sub-agent (ID: {self.parent_agent_id}). Perform your assigned task efficiently.")

        system_prompt = "\n".join(context_lines)
        
        # Insert System Message
        current_messages.insert(0, {"role": "system", "content": system_prompt})
        
        full_reasoning = ""
        final_content = ""
        turn_count = 0
        total_duration = 0
        
        last_tool_signature = None
        repetition_count = 0
        
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

            # Reset reasoning for the current turn (for UI display)
            current_turn_reasoning = ""

            if self.api_key and OPENAI_AVAILABLE:
                try:
                    start_time = time.time()
                    client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com")
                    
                    # Streaming Call
                    stream = client.chat.completions.create(
                        model="deepseek-reasoner",
                        messages=current_messages,
                        tools=self.tools,
                        stream=True
                    )
                    
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
                        
                        delta = chunk.choices[0].delta
                        
                        # 1. Handle Reasoning
                        if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                            r_content = delta.reasoning_content
                            current_turn_reasoning += r_content
                            full_reasoning += r_content
                            self.thinking_signal.emit(r_content)
                            
                        # 2. Handle Content
                        if delta.content:
                            chunk_content += delta.content
                        
                        # 3. Handle Tool Calls
                        if delta.tool_calls:
                            for tc in delta.tool_calls:
                                index = tc.index
                                if index not in tool_calls_buffer:
                                    tool_calls_buffer[index] = {
                                        "id": tc.id,
                                        "type": tc.type,
                                        "function": {
                                            "name": tc.function.name,
                                            "arguments": ""
                                        }
                                    }
                                
                                # Append arguments
                                if tc.function and tc.function.arguments:
                                    tool_calls_buffer[index]["function"]["arguments"] += tc.function.arguments
                    
                    end_time = time.time()
                    duration = end_time - start_time
                    total_duration += duration
                    
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
                    if full_reasoning:
                        assistant_msg["reasoning_content"] = full_reasoning
                        
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
                                self.step_signal.emit("System: 🛑 Loop detected (repeated tool calls). Stopping automatically.")
                                final_content = "⚠️ Operation stopped: Infinite loop detected (repeated tool calls)."
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
                                    "skill_manager": self.skill_manager
                                }
                            )
                            
                            # Emit Tool Result Signal
                            self.tool_result_signal.emit({
                                "id": tool.id,
                                "result": str(result)
                            })

                            current_messages.append({
                                "role": "tool",
                                "tool_call_id": tool.id,
                                "content": str(result) # Ensure content is string to avoid API errors
                            })
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
            "duration": total_duration
        })
