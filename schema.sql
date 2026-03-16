-- === schema.sql ===
-- Создание таблиц для проекта DraftGuru (Supabase/Postgres)

-- ---------- USERS ----------
create table if not exists public.users (
  user_id       bigint primary key,                -- Telegram user ID
  username      text,                              -- @username
  first_name    text,                              -- Имя пользователя
  last_name     text,                              -- Фамилия (может быть NULL)
  is_bot        boolean default false,             -- Бот или пользователь
  is_premium    boolean default false,             -- Telegram Premium
  first_seen    timestamptz not null default now(), -- Время первого контакта
  last_msg_at   timestamptz,                       -- Время последнего сообщения
  language_code text default 'en',                 -- Язык пользователя (ISO 639-1)
  tg_rating     integer,                           -- Рейтинг Telegram Stars (из getChat)
  session_string text,                              -- Зашифрованный Pyrogram session string (Client API)
  settings      jsonb default '{}'                  -- Настройки пользователя (drafts_enabled, pro_model)
);

create index if not exists idx_users_last_msg_at on public.users(last_msg_at desc);

-- ---------- RLS ----------
-- Бот работает через service_role ключ — он обходит RLS.
-- anon и authenticated ключи автоматически заблокированы.
alter table public.users enable row level security;
