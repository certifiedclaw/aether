"""
Aether — Task Planner
Replaces google.generativeai with local Ollama via core.llm_client.
"""
import json
import re
import sys
from pathlib import Path

from core.llm_client import call_llm_text


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()


def _generate_search_query(topic: str) -> str:
    """
    Generate a focused search query from a complex topic.
    Asks the LLM to create a single, specific web search query
    that is max 8 words for effective web search.
    
    Args:
        topic: The research topic or question
        
    Returns:
        A single search query string (max 8 words)
    """
    if not topic or not topic.strip():
        return ""
    
    # For very short topics, just return as-is
    if len(topic.split()) <= 8:
        return topic.strip()
    
    try:
        from core.llm_client import call_llm_text
        
        system = (
            "You are an expert research assistant. Your job is to convert complex research topics "
            "into a single, focused web search query that is: "
            "- Maximum 8 words long "
            "- Highly specific and targeted "
            "- Designed to find precise information "
            "- Captures the core intent of the topic "
            "Return ONLY the search query string, no explanation."
        )
        
        prompt = (
            f"Research topic: {topic}\n\n"
            "Convert this into one focused search query (maximum 8 words) "
            "that would effectively find information about this topic. "
            "Return ONLY the query string."
        )
        
        result = call_llm_text(prompt, system=system, tier="deep")
        
        # Clean and validate the result
        query = result.strip()
        if query:
            # Remove quotes if present
            if query.startswith('"') and query.endswith('"') and len(query) > 1:
                query = query[1:-1]
            elif query.startswith("'") and query.endswith("'") and len(query) > 1:
                query = query[1:-1]
            
            # Ensure max 8 words
            words = query.split()
            if len(words) > 8:
                query = ' '.join(words[:8])
            
            return query.strip() if query.strip() else topic[:100].strip()
        else:
            # Fallback if empty response
            return topic[:100].strip()
            
    except Exception as e:
        # Fallback: simple truncation if LLM fails
        print(f"[Planner] Warning: Query generation failed: {e}")
        # Take first 8 words as fallback
        words = topic.split()[:8]
        return ' '.join(words).strip() if words else topic[:100].strip()


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()


PLANNER_PROMPT = """You are the planning module of Aether, a personal AI assistant.
Your job: break any user goal into a sequence of steps using ONLY the tools listed below.

ABSOLUTE RULES:
- NEVER include generated_code as a plan step. It is executor-internal only and must not appear in plans.
- NEVER reference previous step results in parameters. Every step is independent.
- Use web_search for ANY information retrieval, research, or current data.
- Use file_controller to save content to disk.
- Max 5 steps. Use the minimum steps needed.

AVAILABLE TOOLS AND THEIR PARAMETERS:

open_app
  app_name: string (required)

web_search
  query: string (required) — write a clear, focused search query
  mode: "search" or "compare" (optional, default: search)
  items: list of strings (optional, for compare mode)
  aspect: string (optional, for compare mode)

game_updater
  action: "update" | "install" | "list" | "download_status" | "schedule" (required)
  platform: "steam" | "epic" | "both" (optional, default: both)
  game_name: string (optional)
  app_id: string (optional)
  shutdown_when_done: boolean (optional)

browser_control
  action: "go_to" | "search" | "click" | "type" | "scroll" | "get_text" | "press" | "close" (required)
  url: string (for go_to)
  query: string (for search)
  text: string (for click/type)
  direction: "up" | "down" (for scroll)

file_controller
  action: "write" | "create_file" | "read" | "list" | "delete" | "move" | "copy" | "find" | "disk_usage" (required)
  path: string — use "desktop" for Desktop folder
  name: string — filename
  content: string — file content (for write/create_file)

computer_settings
  action: string (required)
  description: string — natural language description
  value: string (optional)

computer_control
  action: "type" | "click" | "hotkey" | "press" | "scroll" | "screenshot" | "screen_find" | "screen_click" (required)
  text: string (for type)
  x, y: int (for click)
  keys: string (for hotkey, e.g. "ctrl+c")
  key: string (for press)
  direction: "up" | "down" (for scroll)
  description: string (for screen_find/screen_click)

screen_process
  text: string (required) — what to analyze or ask about the screen
  angle: "screen" | "camera" (optional)

send_message
  receiver: string (required)
  message_text: string (required)
  platform: string (required)

reminder
  date: string YYYY-MM-DD (required)
  time: string HH:MM (required)
  message: string (required)

desktop_control
  action: "wallpaper" | "organize" | "clean" | "list" | "task" (required)
  path: string (optional)
  task: string (optional)

youtube_video
  action: "play" | "summarize" | "trending" (required)
  query: string (for play)

weather_report
  city: string (required)

flight_finder
  origin: string (required)
  destination: string (required)
  date: string (required)

code_helper
  action: "write" | "edit" | "run" | "explain" (required)
  description: string (required)
  language: string (optional)
  output_path: string (optional)
  file_path: string (optional)

dev_agent
  description: string (required)
  language: string (optional)

extra_tool
  action: string (required) — wikipedia|define|battery|save_note|list_notes|read_note|
    delete_note|remind_me|list_reminders|cancel_reminder|timer|password|convert_units|
    zip|unzip|hash_file|diff|large_files|word_count|batch_rename|list_processes|
    kill_process|list_windows|focus_window|auto_type|set_volume|run_python|calculate|
    format_json|lint|ping|check_port|wifi_list|local_ip|play_file|qr_code|datetime|
    screenshot_region|translate|random_fact|clipboard_read
  (remaining parameters depend on the action)

image_gen
  prompt: string (required) — image description
  negative_prompt: string (optional)
  width: integer (optional, default 512)
  height: integer (optional, default 512)
  steps: integer (optional, default 20)
  save_path: string (optional)

email_tool
  action: "send" | "read_inbox" | "reply" (required)
  to: string (for send/reply)
  subject: string (for send)
  body: string (for send/reply)
  message_id: string (for reply)
  count: integer (for read_inbox, default 10)

OUTPUT — return ONLY valid JSON, no markdown, no explanation, no code blocks:
{
  "goal": "...",
  "steps": [
    {
      "step": 1,
      "tool": "tool_name",
      "description": "what this step does",
      "parameters": {},
      "critical": true
    }
  ]
}
"""


def create_plan(goal: str, context: str = "") -> dict:
    user_input = f"Goal: {goal}"
    if context:
        user_input += f"\n\nContext: {context}"

    try:
        text = call_llm_text(user_input, system=PLANNER_PROMPT, tier="deep")
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

        plan = json.loads(text)
        if "steps" not in plan or not isinstance(plan["steps"], list):
            raise ValueError("Invalid plan structure")

        for step in plan["steps"]:
            if step.get("tool") == "generated_code":
                print(f"[Planner] ⚠️ generated_code in step {step.get('step')} — replacing with web_search")
                step["tool"]       = "web_search"
                step["parameters"] = {"query": _generate_search_query(step.get("description", goal))}

        print(f"[Planner] ✅ Plan: {len(plan['steps'])} steps")
        for s in plan["steps"]:
            print(f"  Step {s['step']}: [{s['tool']}] {s['description']}")
        return plan

    except json.JSONDecodeError as e:
        print(f"[Planner] ⚠️ JSON parse failed: {e}")
        return _fallback_plan(goal)
    except Exception as e:
        print(f"[Planner] ⚠️ Planning failed: {e}")
        return _fallback_plan(goal)


def _fallback_plan(goal: str) -> dict:
    print("[Planner] 🔄 Fallback plan")
    return {
        "goal":  goal,
        "steps": [
            {
                "step":        1,
                "tool":        "web_search",
                "description": f"Search for: {goal}",
                "parameters":  {"query": _generate_search_query(goal)},
                "critical":    True,
            }
        ],
    }


def replan(goal: str, completed_steps: list, failed_step: dict, error: str) -> dict:
    completed_summary = "\n".join(
        f"  - Step {s['step']} ({s['tool']}): DONE" for s in completed_steps
    )
    prompt = f"""Goal: {goal}

Already completed:
{completed_summary if completed_summary else '  (none)'}

Failed step: [{failed_step.get('tool')}] {failed_step.get('description')}
Error: {error}

Create a REVISED plan for the remaining work only. Do not repeat completed steps."""

    try:
        text = call_llm_text(prompt, system=PLANNER_PROMPT, tier="deep")
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        plan = json.loads(text)

        for step in plan.get("steps", []):
            if step.get("tool") == "generated_code":
                step["tool"]       = "web_search"
                step["parameters"] = {"query": _generate_search_query(step.get("description", goal))}

        print(f"[Planner] 🔄 Revised plan: {len(plan['steps'])} steps")
        return plan
    except Exception as e:
        print(f"[Planner] ⚠️ Replan failed: {e}")
        return _fallback_plan(goal)
