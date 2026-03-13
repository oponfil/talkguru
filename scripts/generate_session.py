#!/usr/bin/env python3
"""
Генерация String Session для Pyrogram.

Запустите этот скрипт локально в терминале для авторизации:
    python utils/generate_session.py

Скрипт запросит номер телефона, код из Telegram и пароль 2FA (если есть).
После успешной авторизации выведет String Session.
"""

import asyncio
import os
import sys

# Добавляем корневую папку в sys.path, чтобы импорты работали
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import PYROGRAM_API_ID, PYROGRAM_API_HASH

# Фикс для вывода кириллицы в консоль Windows
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

async def main():
    try:
        from pyrogram import Client
    except ImportError:
        print("❌ pyrogram не установлен. Установите: pip install pyrogram tgcrypto")
        return

    print("=== Pyrogram String Session Generator ===\n")

    if not PYROGRAM_API_ID or not PYROGRAM_API_HASH:
        print("❌ PYROGRAM_API_ID и PYROGRAM_API_HASH не заданы в .env!")
        return

    print(f"Используем API_ID: {PYROGRAM_API_ID}")

    # async with Client(...) автоматически вызывает start(), который:
    # 1. Спрашивает номер телефона в консоли
    # 2. Отправляет код
    # 3. Спрашивает код в консоли
    # 4. Спрашивает пароль 2FA в консоли (если нужно)
    # Это встроенная надежная механика Pyrogram
    async with Client(
        name="talkguru_session_generator",
        api_id=int(PYROGRAM_API_ID),
        api_hash=PYROGRAM_API_HASH,
        in_memory=True
    ) as app:
        session_string = await app.export_session_string()
        print("\n✅ Авторизация успешна!")
        print(f"\nВаш SESSION_STRING:\n\n{session_string}\n")
        print("Скопируйте эту строку и используйте её для подключения ботом,")
        print("либо сохраните напрямую в базу данных в поле session_string.")


if __name__ == "__main__":
    asyncio.run(main())
