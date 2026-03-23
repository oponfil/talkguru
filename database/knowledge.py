# database/knowledge.py — Доступ к таблице knowledge_chunks (RAG)

from config import INDEX_BATCH_SIZE
from database import run_supabase, supabase


async def match_knowledge_chunks(
    query_embedding: list[float],
    match_count: int = 5,
    match_threshold: float = 0.3,
) -> list[dict]:
    """Ищет чанки документации по cosine similarity.

    Args:
        query_embedding: Вектор запроса (1536 dims)
        match_count: Макс. кол-во результатов
        match_threshold: Мин. cosine similarity (0..1)

    Returns:
        Список чанков с полями: id, source, section, content, similarity
    """
    result = await run_supabase(
        lambda: supabase.rpc(
            "match_knowledge_chunks",
            {
                "query_embedding": query_embedding,
                "match_count": match_count,
                "match_threshold": match_threshold,
            },
        ).execute()
    )
    return result.data if result.data else []


async def get_existing_hashes() -> dict[tuple[str, str | None], str]:
    """Загружает хэши существующих чанков из БД.

    Returns:
        Словарь {(source, section): content_hash}
    """
    result = await run_supabase(
        lambda: supabase.table("knowledge_chunks")
        .select("source, section, content_hash")
        .execute()
    )
    rows = result.data if result.data else []
    return {(r["source"], r.get("section")): r["content_hash"] for r in rows}


async def sync_chunks(
    new_rows: list[dict],
    all_keys: set[tuple[str, str | None]],
) -> tuple[int, int, int]:
    """Инкрементально синхронизирует чанки: INSERT новые, DELETE устаревшие.

    Args:
        new_rows: Список новых/изменённых чанков для INSERT
                  (source, section, content, content_hash, embedding)
        all_keys: Множество (source, section) ВСЕХ актуальных чанков
                  (для определения удалённых)

    Returns:
        Кортеж (added, deleted, unchanged)
    """
    # 1. Загружаем существующие ключи
    existing_hashes = await get_existing_hashes()
    existing_keys = set(existing_hashes.keys())

    # 2. Определяем устаревшие чанки (есть в БД, но нет в текущей кодовой базе)
    stale_keys = existing_keys - all_keys
    deleted = len(stale_keys)

    # 3. Удаляем устаревшие батчами (группируем по source)
    stale_by_source: dict[str, list[str | None]] = {}
    for source, section in stale_keys:
        stale_by_source.setdefault(source, []).append(section)

    for source, sections in stale_by_source.items():
        non_null = [s for s in sections if s is not None]
        has_null = any(s is None for s in sections)
        if has_null:
            await run_supabase(
                lambda s=source: supabase.table("knowledge_chunks")
                .delete()
                .eq("source", s)
                .is_("section", "null")
                .execute()
            )
        if non_null:
            await run_supabase(
                lambda s=source, secs=non_null: supabase.table("knowledge_chunks")
                .delete()
                .eq("source", s)
                .in_("section", secs)
                .execute()
            )

    # 4. Удаляем строки, которые будут заменены (батчами по source)
    changes_by_source: dict[str, list[str | None]] = {}
    for row in new_rows:
        changes_by_source.setdefault(row["source"], []).append(row["section"])

    for source, sections in changes_by_source.items():
        non_null = [s for s in sections if s is not None]
        has_null = any(s is None for s in sections)
        if has_null:
            await run_supabase(
                lambda s=source: supabase.table("knowledge_chunks")
                .delete()
                .eq("source", s)
                .is_("section", "null")
                .execute()
            )
        if non_null:
            await run_supabase(
                lambda s=source, secs=non_null: supabase.table("knowledge_chunks")
                .delete()
                .eq("source", s)
                .in_("section", secs)
                .execute()
            )

    # 5. INSERT новых/изменённых батчами
    added = len(new_rows)
    for i in range(0, len(new_rows), INDEX_BATCH_SIZE):
        batch = new_rows[i:i + INDEX_BATCH_SIZE]
        await run_supabase(
            lambda b=batch: supabase.table("knowledge_chunks").insert(b).execute()
        )

    unchanged = len(all_keys) - added
    return added, deleted, unchanged
