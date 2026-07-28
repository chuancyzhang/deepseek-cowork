#!/usr/bin/env node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const args = process.argv.slice(2);
const diagnostic = (stage, fields = {}) => console.error(JSON.stringify({
  type: "wind_aifinmarket_network",
  stage,
  entry: "wind_mcp",
  timestamp: Date.now() / 1000,
  ...fields,
}));
if (args.length === 0 || args.includes("--help") || args.includes("-h")) {
  console.log("Usage: wind_mcp.mjs call <server_type> <tool_name> '<params_json>'");
  process.exit(0);
}
diagnostic("submit", { server_type: args[1] || "", tool_name: args[2] || "" });
if (args[0] !== "call") {
  diagnostic("error", { error_code: "UNSUPPORTED_COMMAND" });
  console.error(JSON.stringify({ ok: false, error: { code: "UNSUPPORTED_COMMAND", message: "Only the call command is available in Cowork." } }));
  process.exit(1);
}
if (!process.env.WIND_API_KEY?.trim()) {
  diagnostic("error", { error_code: "AUTH_ERROR" });
  console.error(JSON.stringify({ ok: false, error: { code: "AUTH_ERROR", message: "WIND_API_KEY is not configured in the Cowork capability center." } }));
  process.exit(1);
}
diagnostic("start", { server_type: args[1] || "", tool_name: args[2] || "" });
const childPath = fileURLToPath(new URL("../skills/wind-mcp-skill/scripts/cli.mjs", import.meta.url));
const child = spawn(process.execPath, [childPath, ...args], {
  stdio: "inherit",
  env: { ...process.env, COWORK_MANAGED_SKILL: "1" },
});
diagnostic("run", { server_type: args[1] || "", tool_name: args[2] || "" });
child.once("error", (error) => {
  diagnostic("error", { error_type: error.name });
  process.exitCode = 1;
});
child.once("exit", (code, signal) => {
  diagnostic(code === 0 && !signal ? "finish" : "error", { exit_code: code ?? 1 });
  process.exitCode = signal ? 1 : (code ?? 1);
});
