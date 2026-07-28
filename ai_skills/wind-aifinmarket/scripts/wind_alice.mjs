#!/usr/bin/env node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const args = process.argv.slice(2);
const diagnostic = (stage, fields = {}) => console.error(JSON.stringify({
  type: "wind_aifinmarket_network",
  stage,
  entry: "wind_alice",
  timestamp: Date.now() / 1000,
  ...fields,
}));
const isHelp = args.length === 0 || args.includes("--help") || args.includes("-h");
const isList = args[0] === "list-skills" || args.includes("--list-skills");
if (!isHelp && !isList) diagnostic("submit");
if (!isHelp && !isList && !args.some((item) => item === "--prompt" || item === "-p")) {
  diagnostic("error", { error_code: "PROMPT_REQUIRED" });
  console.error(JSON.stringify({ ok: false, error: { code: "PROMPT_REQUIRED", message: "--prompt is required." } }));
  process.exit(1);
}
if (!isHelp && !isList && !process.env.WIND_API_KEY?.trim()) {
  diagnostic("error", { error_code: "AUTH_ERROR" });
  console.error(JSON.stringify({ ok: false, error: { code: "AUTH_ERROR", message: "WIND_API_KEY is not configured in the Cowork capability center." } }));
  process.exit(1);
}
if (!isHelp && !isList && !process.env.COWORK_WORKSPACE_DIR?.trim()) {
  diagnostic("error", { error_code: "WORKSPACE_REQUIRED" });
  console.error(JSON.stringify({ ok: false, error: { code: "WORKSPACE_REQUIRED", message: "COWORK_WORKSPACE_DIR is required for Alice downloads." } }));
  process.exit(1);
}
diagnostic("start");
const childPath = fileURLToPath(new URL("../skills/wind-alice/scripts/wind-alice.mjs", import.meta.url));
const child = spawn(process.execPath, [childPath, ...args], {
  stdio: "inherit",
  env: { ...process.env, COWORK_MANAGED_SKILL: "1" },
});
diagnostic("run");
child.once("error", (error) => {
  diagnostic("error", { error_type: error.name });
  process.exitCode = 1;
});
child.once("exit", (code, signal) => {
  diagnostic(code === 0 && !signal ? "finish" : "error", { exit_code: code ?? 1 });
  process.exitCode = signal ? 1 : (code ?? 1);
});
