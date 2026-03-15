import asyncio
import os
import sys
import qrcode
from telethon import TelegramClient

# Добавляем корневую папку в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import PYROGRAM_API_ID, PYROGRAM_API_HASH

# Фикс для вывода кириллицы в консоль Windows
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

async def main():
    print("=== Telethon QR Code Login ===")
    
    if not PYROGRAM_API_ID or not PYROGRAM_API_HASH:
        print("❌ PYROGRAM_API_ID и PYROGRAM_API_HASH не заданы в .env!")
        return

    # Telethon client
    client = TelegramClient('anon_telethon', PYROGRAM_API_ID, PYROGRAM_API_HASH)
    await client.connect()

    qr = await client.qr_login()
    print("Откройте Telegram на телефоне -> Настройки -> Устройства -> Привязать устройство")
    print("Отсканируйте этот QR-код:\n")
    
    qr_obj = qrcode.QRCode()
    qr_obj.add_data(qr.url)
    qr_obj.print_ascii()
    
    print("\n⏳ Ожидание сканирования... (у вас есть примерно 30-60 секунд)")
    
    try:
        await qr.wait(timeout=60)
        print("\n✅ QR-код отсканирован, выгружаем сессию для Pyrogram...")
        
        import struct
        import base64
        from pyrogram.storage.storage import Storage

        telethon_session = client.session
        dc_id = telethon_session.dc_id
        auth_key = telethon_session.auth_key.key
        user_id = (await client.get_me()).id
        
        # Упаковываем данные как это делает Pyrogram
        api_id_int = int(PYROGRAM_API_ID)
        packed = struct.pack(
            Storage.SESSION_STRING_FORMAT,
            dc_id,
            api_id_int,
            False,   # test_mode
            auth_key,
            user_id,
            False    # is_bot
        )
        session_string = base64.urlsafe_b64encode(packed).decode().rstrip("=")
        
        await client.disconnect()

        # Удаляем временную сессию Telethon
        if os.path.exists("anon_telethon.session"):
            os.remove("anon_telethon.session")
            
        print("\n" + "="*50)
        print("АВТОРИЗАЦИЯ УСПЕШНА!")
        print("="*50 + "\n")
        print("📍 Ваш SESSION_STRING:\n")
        print(session_string)
        print("\nИспользуйте эту строку только через код приложения.")
        print("Не вставляйте SESSION_STRING напрямую в Supabase в открытом виде.")
        
    except Exception as e:
        print(f"\n❌ Ошибка или истекло время: {e}")

if __name__ == "__main__":
    asyncio.run(main())
