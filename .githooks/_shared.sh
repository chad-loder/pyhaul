#!/usr/bin/env bash
# Shared helpers for local git/pre-commit hooks (source this file; do not execute)

# Centralized agent detection pattern
AGENT_PATTERN='^(AGENT|AI_AGENT|AIDER_|CURSOR_|GEMINI_|CLAUDE_|OPENHANDS|OPENDEVIN|SWE_AGENT|GITHUB_AGENTIC|OPENCLAW|CLAW_|CODEIUM_)'

is_agent() {
  env | grep -qiE "$AGENT_PATTERN"
}

is_truly_interactive() {
  [ ! -t 0 ] && return 1
  is_agent && return 1
  return 0
}

agent_refusal() {
  local reason=$1
  local instruction=$2
  echo "----------------------------------------------------------------"
  echo "SECURITY/POLICY VIOLATION: AI Agent detected"
  echo "REASON: $reason"
  echo "INSTRUCTION: $instruction"
  echo "----------------------------------------------------------------"
}
