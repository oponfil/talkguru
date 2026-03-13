# database/__init__.py — Инициализация Supabase клиента

from supabase import create_client, Client

from config import SUPABASE_URL, SUPABASE_KEY


# Глобальный экземпляр Supabase клиента
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
