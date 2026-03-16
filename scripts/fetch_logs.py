#!/usr/bin/env python3
"""Скачивает логи продакшена DraftGuru из Railway для локального анализа.

Требования:
    - Railway CLI: npm i -g @railway/cli
    - В .env: RAILWAY_TOKEN, RAILWAY_PROJECT_ID, RAILWAY_SERVICE_NAME

Использование:
    python scripts/fetch_logs.py                      # последние 500 строк
    python scripts/fetch_logs.py --lines 2000         # последние 2000 строк
    python scripts/fetch_logs.py --all                # все доступные (до 5000)
    python scripts/fetch_logs.py --filter ERROR       # только ERROR
    python scripts/fetch_logs.py --since 1h           # за последний час
    python scripts/fetch_logs.py -o logs/prod.log     # сохранить в файл

Результат сохраняется в logs/production_YYYY-MM-DD_HH-MM-SS.log
"""

import argparse
import io
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# Корень проекта
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

# Railway CLI ограничивает вывод логов этим количеством строк
RAILWAY_LOG_LIMIT = 5000
LOGS_DIR = PROJECT_ROOT / "logs"


def configure_stdio() -> None:
    """Включает UTF-8 для консоли Windows только при запуске скрипта."""
    if sys.platform != "win32":
        return

    # Не трогаем stdout/stderr под pytest/capture или при редиректе.
    if hasattr(sys.stdout, "isatty") and not sys.stdout.isatty():
        return
    if hasattr(sys.stderr, "isatty") and not sys.stderr.isatty():
        return

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    elif hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def check_railway_cli() -> str | None:
    """Проверяет наличие Railway CLI и возвращает имя команды."""
    for cmd in ("railway", "railway.cmd"):
        try:
            result = subprocess.run(
                [cmd, "--version"], capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return None


def get_railway_token() -> str | None:
    """Получает Railway API Token из переменных окружения."""
    token = os.getenv("RAILWAY_TOKEN")
    if not token:
        print("❌ RAILWAY_TOKEN не найден в .env")
        print("   Получить токен: https://railway.app/account/tokens")
        print('   Добавьте в .env: RAILWAY_TOKEN="your_token_here"')
        return None
    return token




def build_logs_command(
    cli_cmd: str,
    service: str | None,
    lines: int,
    filter_query: str | None,
    since: str | None,
) -> list[str]:
    """Собирает команду `railway logs` без побочных эффектов."""
    cmd = [cli_cmd, "logs"]

    if service:
        cmd.extend(["--service", service])

    if since:
        cmd.extend(["--since", since])
    else:
        cli_lines = min(lines, RAILWAY_LOG_LIMIT)
        cmd.extend(["--lines", str(cli_lines)])

    if filter_query:
        cmd.extend(["--filter", filter_query])

    return cmd


def fetch_logs(
    cli_cmd: str,
    token: str,
    project_id: str | None,
    service: str | None,
    lines: int,
    filter_query: str | None,
    since: str | None,
) -> str | None:
    """Получает логи через Railway CLI."""
    cmd = build_logs_command(cli_cmd, service, lines, filter_query, since)

    env = {**os.environ, "RAILWAY_TOKEN": token}
    if project_id:
        env["RAILWAY_PROJECT_ID"] = project_id
    timeout_sec = 120 if lines >= RAILWAY_LOG_LIMIT else 30

    print(f"⏳ Запрос: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=env,
            timeout=timeout_sec, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        print("❌ Railway CLI не найден.")
        return None
    except subprocess.TimeoutExpired:
        print(f"❌ Таймаут ({timeout_sec}s). Попробуйте уменьшить --lines.")
        return None

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "unauthorized" in stderr.lower():
            print("❌ Неверный RAILWAY_TOKEN. Проверьте .env")
        elif "not found" in stderr.lower():
            print(f"⚠️  Сервис '{service}' не найден, пробуем без --service...")
            cmd_retry = build_logs_command(cli_cmd, None, lines, filter_query, since)
            try:
                result = subprocess.run(
                    cmd_retry, capture_output=True, text=True, env=env,
                    timeout=timeout_sec, encoding="utf-8", errors="replace",
                )
                if result.returncode == 0:
                    return result.stdout
            except Exception:
                pass
            print(f"❌ Ошибка: {stderr}")
        else:
            print(f"❌ Railway CLI ошибка: {stderr}")
        return None

    return result.stdout


def print_summary(logs: str) -> None:
    """Выводит краткую статистику по логам."""
    lines = logs.splitlines()
    total = len(lines)
    errors = sum(1 for line in lines if "ERROR" in line)
    warnings = sum(1 for line in lines if "WARNING" in line)

    # Компоненты [BOT], [PYROGRAM], [OPENROUTER], [X402GATE], [TRANSLATE], [DB]
    components: dict[str, int] = {}
    for line in lines:
        if "] " in line:
            start = line.find("[")
            end = line.find("]", start)
            if start != -1 and end != -1:
                comp = line[start + 1:end]
                if comp.isupper() or comp.upper() == comp:
                    components[comp] = components.get(comp, 0) + 1

    print(f"\n📊 Статистика: {total} строк | ❌ ERROR: {errors} | ⚠️  WARNING: {warnings}")

    if components:
        top = sorted(components.items(), key=lambda x: -x[1])[:10]
        print("   Компоненты: " + ", ".join(f"[{c}]={n}" for c, n in top))


def main() -> None:
    """Точка входа."""
    configure_stdio()
    parser = argparse.ArgumentParser(description="Скачать логи продакшена DraftGuru из Railway")
    parser.add_argument("-n", "--lines", type=int, default=500, help="Количество строк (по умолчанию 500)")
    parser.add_argument("--all", action="store_true", help=f"Все доступные логи (до {RAILWAY_LOG_LIMIT})")
    parser.add_argument("-s", "--since", type=str, default=None, help="Период: 30s, 5m, 2h, 1d, 1w")
    parser.add_argument("-f", "--filter", type=str, default=None, help='Фильтр (например ERROR, WARNING)')
    parser.add_argument("-o", "--output", type=str, default=None, help="Путь для сохранения (по умолчанию logs/production_*.log)")
    args = parser.parse_args()

    # Проверяем Railway CLI
    cli_cmd = check_railway_cli()
    if not cli_cmd:
        print("❌ Railway CLI не найден. Установите: npm i -g @railway/cli")
        sys.exit(1)

    # Получаем токен
    token = get_railway_token()
    if not token:
        sys.exit(1)

    # Параметры проекта
    project_id = os.getenv("RAILWAY_PROJECT_ID")
    service_name = os.getenv("RAILWAY_SERVICE_NAME")
    lines_count = RAILWAY_LOG_LIMIT if args.all else args.lines

    print(f"📦 Сервис: {service_name or '(default)'}")
    if args.filter:
        print(f"🔍 Фильтр: {args.filter}")

    # Скачиваем логи
    logs = fetch_logs(cli_cmd, token, project_id, service_name, lines_count, args.filter, args.since)
    if not logs or not logs.strip():
        print("📭 Логи пусты.")
        sys.exit(1)

    # Сохраняем
    if args.output:
        output_path = Path(args.output)
    else:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        suffix = f"_{args.filter.lower().replace(' ', '_')}" if args.filter else ""
        output_path = LOGS_DIR / f"production_{ts}{suffix}.log"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(logs, encoding="utf-8")

    line_count = len(logs.splitlines())
    print(f"✅ Сохранено {line_count} строк → {output_path}")

    # Статистика
    print_summary(logs)


if __name__ == "__main__":
    main()
