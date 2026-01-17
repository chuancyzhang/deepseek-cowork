import sys
import subprocess
import tempfile
import os
import ast
import re
import json
import platform
from datetime import datetime
from PySide6.QtCore import QThread, Signal
from core.skill_manager import SkillManager

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

class SecurityError(Exception):
    pass

def validate_code_safety(code, allowed_dir):
    """AST 静态分析代码安全性"""
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

    def __init__(self, code, cwd):
        super().__init__()
        self.code = code
        self.cwd = cwd
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
        try:
            validate_code_safety(self.code, self.cwd)
        except SecurityError as e:
            self.output_signal.emit(f"❌ {str(e)}")
            self.finished_signal.emit()
            return

        # Prepend input() override to capture user interaction
        input_override = """
import sys
def input(prompt=""):
    print(f"__REQUEST_INPUT__:{prompt}", flush=True)
    return sys.stdin.readline().strip()
"""
        full_code = input_override + "\n" + self.code

        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(full_code)
            temp_path = f.name

        # Determine python executable
        python_exe = sys.executable
        if getattr(sys, 'frozen', False):
            # If frozen (packaged), sys.executable is the exe itself.
            # We need to find the system python to run the script.
            # Try finding 'python' in PATH
            import shutil
            sys_python = shutil.which("python")
            if sys_python:
                python_exe = sys_python
            else:
                # Fallback: try standard install paths or warn user
                self.output_signal.emit("⚠️ Warning: System 'python' not found in PATH. Execution might fail if 'sys.executable' points to this app.")
                # We stick to sys.executable but it likely won't work for scripts if onefile
                python_exe = "python" 

        try:
            if self.is_stopped: return

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
                bufsize=0 # Unbuffered for real-time
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
                    self.output_signal.emit(f"Error: {stderr}")

            self.process.wait()
        except Exception as e:
            self.output_signal.emit(f"Execution failed: {str(e)}")
        finally:
            try:
                os.remove(temp_path)
            except:
                pass
            self.finished_signal.emit()

class LLMWorker(QThread):
    """后台调用 LLM API 的线程，支持 Tool Calls 和多轮思考"""
    finished_signal = Signal(dict)
    step_signal = Signal(str) # 用于输出中间步骤日志

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
            "Policy [IMPORTANT]: When you solve a task by generating and executing Python code (via 'run_python_code' or similar), you MUST evaluate if this code is reusable.",
            "If the code is a reusable solution (e.g., a utility function, a specific calculation, a file operation):",
            "1. Refactor the code into a clean, standalone Python function.",
            "2. IMMEDIATELY use the 'create_new_skill' tool to save this function as a new local skill.",
            "3. Use a descriptive skill_name (e.g., 'image-resizer') and tool_name (e.g., 'resize_image').",
            "4. Inform the user that you have saved this capability as a new skill for future use.",
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

            if self.api_key and OPENAI_AVAILABLE:
                try:
                    client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com")
                    response = client.chat.completions.create(
                        model="deepseek-reasoner", # or deepseek-chat with extra_body
                        messages=current_messages,
                        tools=self.tools,
                        # extra_body={"thinking": {"type": "enabled"}} # If using deepseek-chat
                    )
                    
                    msg = response.choices[0].message
                    content = msg.content or ""
                    tool_calls = msg.tool_calls
                    
                    # Extract reasoning
                    reasoning = getattr(msg, 'reasoning_content', "") or ""
                    if reasoning:
                        full_reasoning += f"\n[Step {turn_count}]: {reasoning}"
                        self.step_signal.emit(f"Thinking: {reasoning[:50]}...")

                    # Append Assistant Message to History
                    current_messages.append(msg)
                    
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
                            
                            # Execute via Skill Manager
                            # Pass step_signal as context to allow tools to log
                            result = self.skill_manager.call_tool(name, args, context={"step_signal": self.step_signal, "config_manager": self.config_manager})
                            
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
                import time
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
            "role": "assistant"
        })
