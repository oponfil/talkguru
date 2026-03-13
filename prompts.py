# prompts.py — Все промпты для ИИ

SYSTEM_PROMPT = (
    "You are a smart assistant that helps users write responses to messages. "
    "Be concise, helpful, and to the point. "
    "Always respond in the same language as the user's message."
)

TRANSLATE_MESSAGES_PROMPT = """Translate each string from the array `messages` into: {language_code}

IMPORTANT:
- Keep ALL placeholders {{...}} and [[...]] exactly as they are.
- Preserve ALL emoji without changes.
- Translate ONLY the text inside the strings.
- Return the result strictly as a JSON array with {message_count} elements in the same order.
- No comments, explanations, or extra text around the JSON.
- In JSON, string values must not contain raw newlines or tabs; use \\n and \\t inside strings if needed.

Example response (for two items):
["translation of the first string", "translation of the second string"]

messages = {messages_json}
"""
