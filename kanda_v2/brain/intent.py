"""Intent classification and question answering via Groq LLM."""

from __future__ import annotations

import json
import logging

import httpx

from config import settings
from brain.prompts import SYSTEM_IDENTITY, INTENT_PROMPT, QUESTION_PROMPT, SCENE_DESCRIBE_PROMPT

logger = logging.getLogger(__name__)


async def _call_groq(messages: list[dict], temperature: float = 0.1) -> str | None:
    """Make an async call to Groq chat completions."""
    if not settings.groq_api_key:
        logger.error("[brain] GROQ_API_KEY not set")
        return None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                settings.groq_endpoint,
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.groq_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": 1024,
                },
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
            else:
                logger.error(f"[brain] Groq error {resp.status_code}: {resp.text[:200]}")
                return None
    except Exception:
        logger.exception("[brain] Groq API call failed")
        return None


async def _call_vlm(image_b64: str, prompt: str) -> str | None:
    """Call NVIDIA NIM vision-language model with retry."""
    if not settings.nvidia_api_key:
        logger.error("[brain] NVIDIA_API_KEY not set")
        return None

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    settings.nvidia_endpoint,
                    headers={
                        "Authorization": f"Bearer {settings.nvidia_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.nvidia_vlm_model,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                            ],
                        }],
                        "max_tokens": 256,
                        "temperature": 0.1,
                    },
                )
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"].strip()
                else:
                    logger.error(f"[brain] VLM error {resp.status_code}: {resp.text[:200]}")
                    return None
        except httpx.ReadTimeout:
            logger.warning(f"[brain] VLM timeout (attempt {attempt+1}/2)")
            if attempt == 0:
                continue
            return None
        except Exception:
            logger.exception("[brain] VLM call failed")
            return None
    return None


async def classify_intent(
    transcript: str,
    sensors,
    motion,
) -> dict:
    """Classify user input into structured intent."""
    prompt = INTENT_PROMPT.format(
        identity=SYSTEM_IDENTITY,
        front=sensors.front,
        left=sensors.left,
        right=sensors.right,
        current_action=motion.current_action,
        speed_normal=settings.speed_normal,
        transcript=transcript,
    )

    result = await _call_groq([{"role": "user", "content": prompt}])

    if not result:
        return {"intent": "UNKNOWN", "reply": "I had trouble understanding that."}

    try:
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(cleaned)
    except (json.JSONDecodeError, IndexError):
        logger.warning(f"[brain] failed to parse intent JSON: {result[:200]}")
        return {"intent": "UNKNOWN", "reply": "I had trouble understanding that."}


async def answer_question(text: str, camera, sensors) -> str:
    """Answer a user question, optionally using camera."""
    scene = ""
    vision_keywords = ("see", "look", "show", "what is", "describe", "in front")
    if any(k in text.lower() for k in vision_keywords):
        img_b64 = await camera.capture_base64()
        if img_b64:
            scene = await _call_vlm(img_b64, SCENE_DESCRIBE_PROMPT) or "Unable to see right now."
        else:
            scene = "Camera unavailable."
    else:
        scene = "No visual context needed."

    prompt = QUESTION_PROMPT.format(
        identity=SYSTEM_IDENTITY,
        scene=scene,
        question=text,
    )

    answer = await _call_groq([{"role": "user", "content": prompt}], temperature=0.3)
    return answer or "I'm not sure how to answer that."


async def describe_scene(camera) -> str:
    """Get a VLM description of the current camera view."""
    img_b64 = await camera.capture_base64()
    if not img_b64:
        return "Camera unavailable."
    result = await _call_vlm(img_b64, SCENE_DESCRIBE_PROMPT)
    return result or "Unable to describe the scene."


async def check_for_goal(camera, goal: str) -> bool | None:
    """Ask VLM if the target goal is visible. Returns True/False/None."""
    from brain.prompts import SEARCH_CHECK_PROMPT

    img_b64 = await camera.capture_base64()
    if not img_b64:
        return None

    prompt = SEARCH_CHECK_PROMPT.format(goal=goal)
    result = await _call_vlm(img_b64, prompt)

    if not result:
        return None

    word = result.strip().upper().split()[0] if result.strip() else ""
    if word == "YES":
        return True
    elif word == "NO":
        return False
    return None
