from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv
from pathlib import Path
try:
    from openai import OpenAI
except ModuleNotFoundError:
    OpenAI = None

load_dotenv(Path(__file__).resolve().parent / '.env')

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '').strip()
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini').strip()
OPENAI_BASE_URL = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
_LAST_CHAT_REPLY = ''


def _extract_text(payload: dict[str, Any]) -> str:
    if not payload:
        return ''
    if isinstance(payload.get('output_text'), str):
        return payload.get('output_text', '').strip()
    parts: list[str] = []
    for item in payload.get('output', []) or []:
        if item.get('type') == 'message':
            for content in item.get('content', []) or []:
                if content.get('type') in {'output_text', 'text'} and content.get('text'):
                    parts.append(str(content.get('text')))
    return '\n'.join(p.strip() for p in parts if p and str(p).strip()).strip()


def _build_system_prompt(context: str, question: str) -> str:
    is_quick = "Intent:" in (question or "")
    if is_quick:
        return (
            "You are RouteNova's AI traffic analyst. "
            "Use only the provided data. If data is missing, say it is unavailable. "
            "Answer in 1-2 short lines only. Do not include bullet points. "
            f"Context: {context}"
        )
    return (
        "You are RouteNova's AI traffic analyst. "
        "Use only the provided data. If data is missing, say it is unavailable. "
        "Be concise, practical, and avoid speculation. "
        "Answer the user's question directly first, then provide 3-5 short bullet points and one final recommendation sentence. "
        f"Context: {context}"
    )


def _build_chat_prompt(data: dict[str, Any]) -> str:
    user_query = data.get('question') or ''
    q = user_query.lower()
    focus = "general traffic guidance"
    if "best_travel_time_today" in q or "best time" in q or "travel time today" in q or ("time" in q and "travel" in q):
        focus = "optimal travel time window today"
    elif "best route" in q or "route" in q:
        focus = "best route explanation using ETA, delay, and distance"
    elif "traffic" in q or "congestion" in q:
        focus = "current congestion level and hotspots"
    elif "weather" in q:
        focus = "weather impact on traffic and travel"
    return (
        "You are an AI traffic assistant.\n\n"
        f"User Query:\n{user_query}\n\n"
        "Context:\n"
        f"Traffic Data: {data.get('traffic')}\n"
        f"Weather Data: {data.get('weather')}\n"
        f"Route Data: {data.get('route')}\n"
        "\nInstructions:\n"
        f"- Focus on: {focus}\n"
        "- Answer ONLY based on the user query\n"
        "- Provide a specific response relevant to the query\n"
        "- Do NOT repeat generic answers\n"
        "- Do NOT give same response for different queries\n"
        "- Keep the response short and useful (2-4 lines max)\n"
    )


def _build_user_prompt(data: dict[str, Any]) -> str:
    return (
        "USER QUESTION (answer this specifically):\n"
        f"{data.get('question')}\n\n"
        "Traffic data:\n"
        f"{data.get('traffic')}\n\n"
        "Weather data:\n"
        f"{data.get('weather')}\n\n"
        "Route data:\n"
        f"{data.get('route')}\n\n"
        "History data:\n"
        f"{data.get('history')}\n\n"
        "Urban essentials data:\n"
        f"{data.get('urban')}\n\n"
    )


def _fallback_text(context: str, data: dict[str, Any]) -> str:
    traffic = data.get('traffic') or {}
    weather = data.get('weather') or {}
    route = data.get('route') or {}
    question = (data.get('question') or '').strip()
    bullets = []
    if traffic:
        bullets.append(f"- Current traffic: {traffic.get('status', 'Unknown')}")
    if weather:
        bullets.append(f"- Weather: {weather.get('summary', 'Unknown')}")
    if route:
        bullets.append(f"- Route ETA: {route.get('eta', 'Unknown')} min, delay: {route.get('delay', 'Unknown')} min")
    if not bullets:
        bullets.append("- No live data available; showing dataset-based hotspots only.")
    bullets.append("- Recommendation: prefer alternate routes during peak hours and avoid known congestion hotspots.")
    return "\n".join(bullets)


def generate_ai_response(context: str, data: dict[str, Any], temperature: float = 0.2) -> str:
    question = data.get('question') or ''
    if not OPENAI_API_KEY:
        return _fallback_text(context, data)

    url = f"{OPENAI_BASE_URL}/responses"
    headers = {
        'Authorization': f"Bearer {OPENAI_API_KEY}",
        'Content-Type': 'application/json',
    }
    payload = {
        'model': OPENAI_MODEL,
        'input': [
            {
                'role': 'system',
                'content': [{'type': 'input_text', 'text': _build_system_prompt(context, question)}],
            },
            {
                'role': 'user',
                'content': [{'type': 'input_text', 'text': _build_user_prompt(data)}],
            },
        ],
        'max_output_tokens': 350,
        'temperature': temperature,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        if resp.status_code >= 400:
            return _fallback_text(context, data)
        text = _extract_text(resp.json())
        return text or _fallback_text(context, data)
    except Exception:
        return _fallback_text(context, data)


def _fallback_chat_response(user_query: str, data: dict[str, Any]) -> str:
    q = (user_query or '').lower()
    traffic = data.get('traffic') or {}
    weather = data.get('weather') or {}
    route = data.get('route') or {}
    if 'route' in q or 'best route' in q:
        if route:
            return (
                f"Route: {route.get('name', 'Best route')} | "
                f"ETA: {route.get('eta', '--')} min | "
                f"Delay: {route.get('delay', '--')} min"
            )
        return "Please compute a route first to get accurate suggestions."
    if 'weather' in q:
        return (
            f"Weather: {weather.get('summary', 'Unknown')}. "
            "Plan extra time if conditions are adverse."
        )
    if 'best_travel_time_today' in q or 'best time' in q or 'travel time today' in q or ('time' in q and 'travel' in q):
        status = str(traffic.get('status', 'Unknown'))
        if 'heavy' in status.lower() or 'congestion' in status.lower():
            return "Best travel time today is after peak hours (around 8:30 PM to 10:00 PM) for smoother traffic."
        if 'moderate' in status.lower():
            return "Best travel time today is in mid-day off-peak hours (around 11:00 AM to 1:00 PM)."
        return "Best travel time today is now or during early morning off-peak hours (before 8:00 AM)."
    if 'traffic' in q or 'congestion' in q:
        return (
            f"Traffic: {traffic.get('status', 'Unknown')} | "
            f"Speed: {traffic.get('speed_kmph', '--')} km/h"
        )
    return "Please ask about routes, traffic, or travel insights."


def generate_chat_response(context: str, data: dict[str, Any]) -> str:
    global _LAST_CHAT_REPLY
    user_query = data.get('question') or ''
    print('User Query:', user_query)
    prompt = _build_chat_prompt(data)
    print('Final Prompt:\n', prompt)
    if not OPENAI_API_KEY or OpenAI is None:
        # Always return useful guidance instead of surfacing config/package errors in UI.
        return _fallback_chat_response(user_query, data)

    client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a traffic assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.8,
        )
        text = (response.choices[0].message.content or '').strip()
        print('OpenAI Response:', text)
        if text and text == _LAST_CHAT_REPLY:
            prompt = prompt + "\nEnsure response is unique and directly answers the query."
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a traffic assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.9,
            )
            text = (response.choices[0].message.content or '').strip()
            print('OpenAI Response (retry):', text)
        if text:
            _LAST_CHAT_REPLY = text
            return text
        return _fallback_chat_response(user_query, data)
    except Exception as exc:
        print('OpenAI Error:', str(exc))
        return _fallback_chat_response(user_query, data)
