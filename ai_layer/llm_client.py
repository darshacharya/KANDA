"""
KANDA AI Layer — Gemini LLM Client
Sends the hardware description prompt to Google Gemini and returns a parsed
JSON command dict.

Standalone test:
    export GEMINI_API_KEY=your_key_here
    python3 llm_client.py
    → sends a sample obstacle prompt and prints the returned command
"""

import json
import logging
import re
from typing import Optional

import google.generativeai as genai

import config

logger = logging.getLogger(__name__)

_client_initialised = False


def _ensure_initialised() -> None:
    global _client_initialised
    if not _client_initialised:
        if not config.GEMINI_API_KEY:
            raise ValueError(
                "GEMINI_API_KEY is not set. "
                "Export it: export GEMINI_API_KEY=your_key_here"
            )
        genai.configure(api_key=config.GEMINI_API_KEY)
        _client_initialised = True


def query(prompt: str, image_b64: Optional[str] = None) -> dict:
    """
    Send a prompt to Gemini and return a parsed command dict.

    Args:
        prompt:     Full hardware description + sensor context string
        image_b64:  Base64-encoded JPEG camera frame (Phase 4, None for now)

    Returns:
        dict with keys 'action' (str) and 'speed' (int)

    Raises:
        ValueError  — if the model response cannot be parsed as valid JSON
        Exception   — on API errors (caller should handle and fall back to stop)
    """
    _ensure_initialised()

    model = genai.GenerativeModel(config.GEMINI_MODEL)

    # Build content parts — text only for now, image added in Phase 4
    parts = [prompt]
    if image_b64:
        parts.append({
            "mime_type": "image/jpeg",
            "data": image_b64,
        })

    logger.debug("Querying Gemini (%s)...", config.GEMINI_MODEL)
    response = model.generate_content(
        parts,
        generation_config=genai.GenerationConfig(
            temperature=0.2,        # low temperature for deterministic JSON output
            max_output_tokens=64,   # command is tiny — no need for large budget
        ),
    )

    raw_text = response.text.strip()
    logger.debug("Gemini raw response: %r", raw_text)

    return _parse_response(raw_text)


def _parse_response(text: str) -> dict:
    """
    Extract the first JSON object from the model's text output.
    Handles cases where the model wraps JSON in markdown fences.
    """
    # Strip markdown fences if present: ```json ... ```
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)

    # Find the first {...} block in the text
    match = re.search(r"\{[^{}]*\}", text)
    if not match:
        raise ValueError(f"No JSON object found in Gemini response: {text!r}")

    try:
        cmd = json.loads(match.group())
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON parse error in Gemini response: {exc}") from exc

    logger.debug("Parsed command: %s", cmd)
    return cmd


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s  %(message)s")

    from context_builder import build_prompt

    test_scenarios = [
        {
            "name": "Open corridor",
            "telemetry": {"front": 120.0, "left": 60.0, "right": 55.0, "action": "FORWARD"},
        },
        {
            "name": "Obstacle ahead, more space on left",
            "telemetry": {"front": 15.0, "left": 80.0, "right": 20.0, "action": "STOP"},
        },
        {
            "name": "Tight space — both sides close",
            "telemetry": {"front": 10.0, "left": 12.0, "right": 10.0, "action": "STOP"},
        },
    ]

    for scenario in test_scenarios:
        print(f"\n{'='*55}")
        print(f"Scenario: {scenario['name']}")
        print(f"{'='*55}")
        prompt = build_prompt(scenario["telemetry"])
        try:
            cmd = query(prompt)
            print(f"Command: {cmd}")
        except Exception as exc:
            print(f"Error: {exc}")
