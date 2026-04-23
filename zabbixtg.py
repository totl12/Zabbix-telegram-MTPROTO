#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zabbix → Telegram Alert Script
Поддержка: прямое подключение, SOCKS5, HTTP-прокси, MTProto-прокси.

Установка:
    1. pip3 install requests pysocks python-telegram-bot==13.15 telethon PySocks
    2. Скопировать скрипт в AlertScriptsPath (обычно /usr/lib/zabbix/alertscripts/)
    3. chmod +x zabbix_telegram.py
    4. Создать конфиг zabbix_telegram.conf рядом со скриптом
    5. В Zabbix: Administration → Media types → Script
       Параметры: {ALERT.SENDTO}  {ALERT.SUBJECT}  {ALERT.MESSAGE}

Использование:
    ./zabbix_telegram.py <chat_id> <subject> <message>
    ./zabbix_telegram.py --test          # тест отправки
    ./zabbix_telegram.py --config /path  # указать путь к конфигу
"""

import sys
import os
import json
import logging
import argparse
import socket
from pathlib import Path
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any

# ---------------------------------------------------------------------------
#  Конфигурация по умолчанию
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    # --- Telegram Bot ---
    "bot_token": "",                    # токен от @BotFather
    "default_chat_id": "",              # chat_id для тестов

    # --- Режим прокси: none | socks5 | http | mtproto ---
    "proxy_mode": "none",

    # --- SOCKS5 / HTTP прокси ---
    "proxy_host": "",
    "proxy_port": 1080,
    "proxy_username": "",
    "proxy_password": "",

    # --- MTProto прокси ---
    "mtproto_host": "",
    "mtproto_port": 443,
    "mtproto_secret": "",               # hex-строка секрета

    # --- Telethon (нужен только для режима mtproto) ---
    "api_id": 0,                        # получить на https://my.telegram.org
    "api_hash": "",
    "session_name": "zabbix_bot",

    # --- Форматирование ---
    "parse_mode": "HTML",               # HTML или Markdown
    "disable_web_preview": True,
    "connect_timeout": 10,
    "read_timeout": 10,

    # --- Логирование ---
    "log_file": "/var/log/zabbix/zabbix_telegram.log",
    "log_level": "INFO",
}


class ProxyMode(Enum):
    NONE = "none"
    SOCKS5 = "socks5"
    HTTP = "http"
    MTPROTO = "mtproto"


# ---------------------------------------------------------------------------
#  Загрузка конфигурации
# ---------------------------------------------------------------------------

def find_config(explicit_path: Optional[str] = None) -> Path:
    """Поиск конфигурационного файла."""
    if explicit_path:
        return Path(explicit_path)

    script_dir = Path(__file__).resolve().parent
    candidates = [
        script_dir / "zabbix_telegram.conf",
        script_dir / "zabbix_telegram.json",
        Path("/etc/zabbix/zabbix_telegram.conf"),
        Path("/etc/zabbix/zabbix_telegram.json"),
    ]
    for p in candidates:
        if p.exists():
            return p

    return script_dir / "zabbix_telegram.conf"


def load_config(config_path: Path) -> Dict[str, Any]:
    """Загрузить конфигурацию из JSON-файла, дополнив значениями по умолчанию."""
    cfg = dict(DEFAULT_CONFIG)
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        cfg.update(user_cfg)
    return cfg


# ---------------------------------------------------------------------------
#  Логирование
# ---------------------------------------------------------------------------

def setup_logging(cfg: Dict[str, Any]) -> logging.Logger:
    logger = logging.getLogger("zabbix_telegram")
    logger.setLevel(getattr(logging, cfg["log_level"].upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Файл (если доступен)
    log_file = cfg.get("log_file", "")
    if log_file:
        log_dir = Path(log_file).parent
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except PermissionError:
            pass  # нет прав — пишем только в stderr

    # stderr (всегда)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


# ---------------------------------------------------------------------------
#  Форматирование сообщения
# ---------------------------------------------------------------------------

SEVERITY_EMOJI = {
    "not classified": "⚪",
    "information":    "🔵",
    "warning":        "🟡",
    "average":        "🟠",
    "high":           "🔴",
    "disaster":       "🔥",
    "resolved":       "✅",
    "ok":             "✅",
    "problem":        "🔴",
}


def detect_severity(subject: str) -> str:
    """Определить severity из темы для выбора эмодзи."""
    subj_lower = subject.lower()
    for key in SEVERITY_EMOJI:
        if key in subj_lower:
            return key
    if "ok" in subj_lower or "resolved" in subj_lower:
        return "resolved"
    return "problem"


def format_message(subject: str, body: str, parse_mode: str = "HTML") -> str:
    """Сформировать красивое сообщение для Telegram."""
    severity = detect_severity(subject)
    emoji = SEVERITY_EMOJI.get(severity, "🔔")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if parse_mode.upper() == "HTML":
        return (
            f"{emoji} <b>{escape_html(subject)}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{escape_html(body)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {ts}"
        )
    else:
        return (
            f"{emoji} *{escape_md(subject)}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{escape_md(body)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {ts}"
        )


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_md(text: str) -> str:
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


# ---------------------------------------------------------------------------
#  Отправка: Bot API (direct / socks5 / http)
# ---------------------------------------------------------------------------

def send_via_bot_api(
    cfg: Dict[str, Any],
    chat_id: str,
    text: str,
    logger: logging.Logger,
) -> bool:
    """Отправка через Telegram Bot API (requests + прокси)."""
    import requests

    token = cfg["bot_token"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    proxies = None
    mode = ProxyMode(cfg["proxy_mode"])

    if mode == ProxyMode.SOCKS5:
        auth = ""
        if cfg.get("proxy_username"):
            auth = f"{cfg['proxy_username']}:{cfg['proxy_password']}@"
        proxy_url = f"socks5h://{auth}{cfg['proxy_host']}:{cfg['proxy_port']}"
        proxies = {"https": proxy_url, "http": proxy_url}
        logger.info(f"Используется SOCKS5-прокси: {cfg['proxy_host']}:{cfg['proxy_port']}")

    elif mode == ProxyMode.HTTP:
        auth = ""
        if cfg.get("proxy_username"):
            auth = f"{cfg['proxy_username']}:{cfg['proxy_password']}@"
        proxy_url = f"http://{auth}{cfg['proxy_host']}:{cfg['proxy_port']}"
        proxies = {"https": proxy_url, "http": proxy_url}
        logger.info(f"Используется HTTP-прокси: {cfg['proxy_host']}:{cfg['proxy_port']}")

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": cfg.get("parse_mode", "HTML"),
        "disable_web_page_preview": cfg.get("disable_web_preview", True),
    }

    resp = requests.post(
        url,
        json=payload,
        proxies=proxies,
        timeout=(cfg["connect_timeout"], cfg["read_timeout"]),
    )

    if resp.status_code == 200 and resp.json().get("ok"):
        logger.info(f"Сообщение отправлено в чат {chat_id} (Bot API)")
        return True

    logger.error(f"Ошибка Bot API [{resp.status_code}]: {resp.text}")
    return False


# ---------------------------------------------------------------------------
#  Отправка: MTProto (через Telethon)
# ---------------------------------------------------------------------------

def send_via_mtproto(
    cfg: Dict[str, Any],
    chat_id: str,
    text: str,
    logger: logging.Logger,
) -> bool:
    """Отправка через MTProto-прокси (Telethon)."""
    try:
        import asyncio
        from telethon import TelegramClient
        from telethon.network import connection as tconn

        api_id = cfg["api_id"]
        api_hash = cfg["api_hash"]
        session = cfg.get("session_name", "zabbix_bot")

        # Путь для session-файла — рядом со скриптом
        script_dir = Path(__file__).resolve().parent
        session_path = str(script_dir / session)

        # MTProto прокси
        secret = cfg.get("mtproto_secret", "")
        proxy = (
            cfg["mtproto_host"],
            int(cfg["mtproto_port"]),
            bytes.fromhex(secret) if secret else b"",
        )

        logger.info(
            f"MTProto-прокси: {cfg['mtproto_host']}:{cfg['mtproto_port']}"
        )

        async def _send():
            client = TelegramClient(
                session_path,
                api_id,
                api_hash,
                proxy=proxy,
                connection=tconn.ConnectionTcpMTProxyRandomizedIntermediate,
                timeout=cfg["connect_timeout"],
            )

            # Авторизация бота
            await client.start(bot_token=cfg["bot_token"])

            target = int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
            await client.send_message(
                target,
                text,
                parse_mode="html" if cfg.get("parse_mode", "HTML").upper() == "HTML" else "md",
                link_preview=not cfg.get("disable_web_preview", True),
            )
            await client.disconnect()
            return True

        # Запуск event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_send())
        finally:
            loop.close()

        logger.info(f"Сообщение отправлено в чат {chat_id} (MTProto)")
        return result

    except ImportError:
        logger.error(
            "Telethon не установлен. Выполните: pip3 install telethon"
        )
        return False
    except Exception as e:
        logger.error(f"Ошибка MTProto: {e}", exc_info=True)
        return False


# ---------------------------------------------------------------------------
#  Отправка: альтернатива MTProto через SOCKS5-обёртку
# ---------------------------------------------------------------------------

def send_via_mtproto_socks(
    cfg: Dict[str, Any],
    chat_id: str,
    text: str,
    logger: logging.Logger,
) -> bool:
    """
    Fallback: если MTProto-прокси экспортирует SOCKS5-интерфейс
    (многие MTProto-прокси серверы вроде mtg/mtprotoproxy
     не предоставляют SOCKS5 — тогда используйте send_via_mtproto).
    """
    fallback_cfg = dict(cfg)
    fallback_cfg["proxy_mode"] = "socks5"
    fallback_cfg["proxy_host"] = cfg.get("mtproto_host", cfg.get("proxy_host", ""))
    fallback_cfg["proxy_port"] = cfg.get("mtproto_port", cfg.get("proxy_port", 1080))
    return send_via_bot_api(fallback_cfg, chat_id, text, logger)


# ---------------------------------------------------------------------------
#  Диспетчер отправки
# ---------------------------------------------------------------------------

def send_message(
    cfg: Dict[str, Any],
    chat_id: str,
    subject: str,
    body: str,
    logger: logging.Logger,
) -> bool:
    """Выбрать способ отправки и отправить сообщение."""

    text = format_message(subject, body, cfg.get("parse_mode", "HTML"))
    mode = ProxyMode(cfg.get("proxy_mode", "none"))

    logger.info(f"Режим: {mode.value} | chat_id: {chat_id}")
    logger.debug(f"Тема: {subject}")

    if mode == ProxyMode.MTPROTO:
        # Пробуем нативный MTProto, при ошибке — fallback на SOCKS5
        ok = send_via_mtproto(cfg, chat_id, text, logger)
        if not ok:
            logger.warning("MTProto не удался, пробуем SOCKS5 fallback...")
            ok = send_via_mtproto_socks(cfg, chat_id, text, logger)
        return ok

    # direct / socks5 / http — через Bot API
    return send_via_bot_api(cfg, chat_id, text, logger)


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------

def generate_sample_config(path: Path):
    """Создать пример конфигурации."""
    sample = dict(DEFAULT_CONFIG)
    sample["bot_token"] = "123456:ABC-DEF..."
    sample["default_chat_id"] = "-1001234567890"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sample, f, indent=2, ensure_ascii=False)
    print(f"Пример конфигурации сохранён: {path}")


def main():
    parser = argparse.ArgumentParser(
        description="Zabbix → Telegram (с поддержкой MTProto-прокси)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("chat_id", nargs="?", help="Telegram chat ID")
    parser.add_argument("subject", nargs="?", default="Test", help="Тема")
    parser.add_argument("message", nargs="?", default="Test message from Zabbix", help="Текст")
    parser.add_argument("--config", "-c", help="Путь к конфигу")
    parser.add_argument("--test", action="store_true", help="Тестовая отправка")
    parser.add_argument("--init", action="store_true", help="Создать пример конфига")
    args = parser.parse_args()

    config_path = find_config(args.config)

    if args.init:
        generate_sample_config(config_path)
        return

    cfg = load_config(config_path)
    logger = setup_logging(cfg)

    if not cfg.get("bot_token"):
        logger.error(f"bot_token не задан. Создайте конфиг: {config_path}")
        print(f"Ошибка: bot_token пуст. Запустите --init для создания конфига.", file=sys.stderr)
        sys.exit(1)

    chat_id = args.chat_id or cfg.get("default_chat_id", "")
    if not chat_id:
        logger.error("chat_id не указан ни в аргументах, ни в конфиге")
        sys.exit(1)

    subject = args.subject or "Test"
    message = args.message or "Test message from Zabbix"

    if args.test:
        subject = "✅ Zabbix Test Alert"
        message = (
            f"Тестовое сообщение\n"
            f"Хост: {socket.gethostname()}\n"
            f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Режим прокси: {cfg.get('proxy_mode', 'none')}"
        )

    ok = send_message(cfg, chat_id, subject, message, logger)

    if ok:
        logger.info("Отправка завершена успешно")
        sys.exit(0)
    else:
        logger.error("Отправка не удалась")
        sys.exit(1)


if __name__ == "__main__":
    main()
