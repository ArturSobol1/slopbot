"""One-shot structured requests to Claude.

Uses the Claude API when ANTHROPIC_API_KEY is set, otherwise the Claude Code CLI
(logged in with your Claude subscription). Both return parsed JSON matching `schema`.
"""
import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import anthropic

from .config import CLAUDE_MODEL, env


def ask(system: str, prompt: str, schema: dict, images: list[Path] | None = None) -> dict:
    if env("ANTHROPIC_API_KEY"):
        return _via_api(system, prompt, schema, images or [])
    return _via_cli(system, prompt, schema, images or [])


def _via_api(system: str, prompt: str, schema: dict, images: list[Path]) -> dict:
    content = []
    for image in images:
        content.append({"type": "text", "text": f"Image: {image.name}"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(image.read_bytes()).decode()}})
    content.append({"type": "text", "text": prompt})

    response = anthropic.Anthropic().beta.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason == "refusal":
        raise SystemExit("Claude declined this request.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off (max_tokens reached).")
    return json.loads(next(block.text for block in response.content if block.type == "text"))


def _cli_path() -> str:
    candidates = [
        env("CLAUDE_CLI"),
        Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
        Path.home() / ".local/bin/claude.exe",
        shutil.which("claude"),
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return str(path)
    raise SystemExit("No ANTHROPIC_API_KEY in .env and the Claude Code CLI isn't installed.\n"
                     "Install it with: npm install -g @anthropic-ai/claude-code")


def _via_cli(system: str, prompt: str, schema: dict, images: list[Path]) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        system_file = Path(tmp) / "system.txt"
        system_file.write_text(system, encoding="utf-8")
        workdir = images[0].parent if images else Path(tmp)
        if images:
            names = ", ".join(image.name for image in images)
            prompt = f"First use the Read tool to look at each of these images: {names}\n\n{prompt}"
        tools = ["--tools", "Read", "--allowedTools", "Read"] if images else ["--tools", ""]
        cmd = [_cli_path(), "-p",
               "--output-format", "json",
               "--json-schema", json.dumps(schema),
               "--system-prompt-file", str(system_file),
               *tools,
               "--model", CLAUDE_MODEL,
               "--no-session-persistence",
               "--setting-sources", "",
               "--strict-mcp-config"]
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=workdir, timeout=1200)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Claude Code CLI failed:\n{(proc.stderr or proc.stdout)[-1500:]}")

    if data.get("is_error"):
        message = str(data.get("result", ""))
        if "not logged in" in message.lower() or "/login" in message:
            raise SystemExit("Claude Code isn't logged in. Open a terminal, run `claude`, "
                             "and log in with your Claude account (one time only).")
        raise RuntimeError(f"Claude Code error: {message}")

    if isinstance(data.get("structured_output"), dict):
        return data["structured_output"]
    text = str(data.get("result", ""))
    return json.loads(text[text.find("{"):text.rfind("}") + 1])
