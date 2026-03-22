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
  phone_number  text,                              -- Номер телефона пользователя
  bio           text,                              -- Биография пользователя (из getChat)
  tg_rating     integer,                           -- Рейтинг Telegram Stars (из getChat)
  session_string text,                              -- Зашифрованный Pyrogram session string (Client API)
  settings      jsonb default '{}'                  -- Настройки пользователя (drafts_enabled, pro_model)
);

create index if not exists idx_users_last_msg_at on public.users(last_msg_at desc);

-- ---------- RLS ----------
-- Бот работает через service_role ключ — он обходит RLS.
-- anon и authenticated ключи автоматически заблокированы.
alter table public.users enable row level security;

-- ---------- KNOWLEDGE_CHUNKS (RAG) ----------
create extension if not exists vector;

create table if not exists public.knowledge_chunks (
  id         bigint generated always as identity primary key,
  source     text not null,                    -- Файл-источник (README.md, config.py, ...)
  section    text,                             -- Функция/класс/секция
  content    text not null,                    -- Текст чанка
  embedding  vector(1536) not null,            -- Embedding (text-embedding-3-small = 1536 dims)
  created_at timestamptz default now()
);

create index if not exists idx_knowledge_embedding
  on public.knowledge_chunks
  using ivfflat (embedding vector_cosine_ops)
  with (lists = 20);

alter table public.knowledge_chunks enable row level security;

-- RPC: similarity search для RAG
create or replace function match_knowledge_chunks(
  query_embedding vector(1536),
  match_count int default 5,
  match_threshold float default 0.3
)
returns table (
  id bigint,
  source text,
  section text,
  content text,
  similarity float
)
language sql stable
as $$
  select
    id, source, section, content,
    1 - (embedding <=> query_embedding) as similarity
  from public.knowledge_chunks
  where 1 - (embedding <=> query_embedding) > match_threshold
  order by embedding <=> query_embedding
  limit match_count;
$$;
