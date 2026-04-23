# Zabbix → Telegram Alert Script

Скрипт отправки уведомлений Zabbix в Telegram с поддержкой MTProto-прокси.

## Возможности

- **Прямое подключение** — без прокси, через Bot API
- **SOCKS5-прокси** — через Bot API + SOCKS5
- **HTTP-прокси** — через Bot API + HTTP proxy
- **MTProto-прокси** — нативный протокол Telegram через Telethon

## Быстрый старт

### 1. Установка зависимостей

```bash
# Базовые (direct / socks5 / http)
pip3 install requests pysocks

# Для MTProto-прокси (дополнительно)
pip3 install telethon
```

### 2. Создание бота

1. Написать [@BotFather](https://t.me/BotFather) → `/newbot`
2. Сохранить полученный токен
3. Добавить бота в нужный чат/группу
4. Узнать `chat_id`: отправить сообщение боту, затем открыть
   `https://api.telegram.org/bot<TOKEN>/getUpdates`

### 3. Настройка конфига

```bash
# Скопировать файлы
cp zabbix_telegram.py /usr/lib/zabbix/alertscripts/
cp zabbix_telegram.conf /usr/lib/zabbix/alertscripts/
chmod +x /usr/lib/zabbix/alertscripts/zabbix_telegram.py

# Или сгенерировать пример конфига
./zabbix_telegram.py --init
```

Отредактировать `zabbix_telegram.conf` — заполнить `bot_token` и `default_chat_id`.

### 4. Тест

```bash
./zabbix_telegram.py --test
# или с конкретным chat_id:
./zabbix_telegram.py -1001234567890 "Test Subject" "Test message"
```

## Настройка прокси

### Без прокси

```json
{
  "proxy_mode": "none"
}
```

### SOCKS5

```json
{
  "proxy_mode": "socks5",
  "proxy_host": "127.0.0.1",
  "proxy_port": 1080,
  "proxy_username": "",
  "proxy_password": ""
}
```

### HTTP-прокси

```json
{
  "proxy_mode": "http",
  "proxy_host": "proxy.example.com",
  "proxy_port": 3128
}
```

### MTProto-прокси

Для MTProto нужны дополнительные API-ключи Telegram:

1. Зайти на [my.telegram.org/apps](https://my.telegram.org/apps)
2. Создать приложение → получить `api_id` и `api_hash`

```json
{
  "proxy_mode": "mtproto",
  "mtproto_host": "mtproxy.example.com",
  "mtproto_port": 443,
  "mtproto_secret": "dd00000000000000000000000000000000",
  "api_id": 12345678,
  "api_hash": "abcdef1234567890abcdef1234567890",
  "session_name": "zabbix_bot"
}
```

> **Первый запуск** с MTProto создаст session-файл. После этого
> авторизация кешируется и повторный ввод не нужен.

## Настройка в Zabbix

### Media Type

**Administration → Media types → Create media type**

| Параметр       | Значение                |
|----------------|-------------------------|
| Name           | Telegram MTProto        |
| Type           | Script                  |
| Script name    | zabbix_telegram.py      |
| Parameters     | `{ALERT.SENDTO}`        |
|                | `{ALERT.SUBJECT}`       |
|                | `{ALERT.MESSAGE}`       |

### User Media

**Administration → Users → (user) → Media → Add**

| Параметр   | Значение              |
|------------|----------------------|
| Type       | Telegram MTProto     |
| Send to    | `-1001234567890`     |

### Action (пример)

**Configuration → Actions → Create action**

Шаблон сообщения (Operations → Default message):

```
Host: {HOST.NAME}
Trigger: {TRIGGER.NAME}
Severity: {TRIGGER.SEVERITY}
Status: {TRIGGER.STATUS}
Time: {EVENT.DATE} {EVENT.TIME}

{ITEM.NAME}: {ITEM.LASTVALUE}
Event ID: {EVENT.ID}
```

## Формат сообщений

Скрипт автоматически добавляет эмодзи по severity:

| Severity         | Эмодзи |
|------------------|--------|
| Not classified   | ⚪     |
| Information      | 🔵     |
| Warning          | 🟡     |
| Average          | 🟠     |
| High             | 🔴     |
| Disaster         | 🔥     |
| Resolved / OK    | ✅     |

## Логи

По умолчанию: `/var/log/zabbix/zabbix_telegram.log`

```bash
tail -f /var/log/zabbix/zabbix_telegram.log
```

## Troubleshooting

| Проблема                         | Решение                                                    |
|----------------------------------|------------------------------------------------------------|
| `bot_token пуст`                 | Заполните `bot_token` в конфиге                            |
| `Telethon не установлен`         | `pip3 install telethon`                                    |
| `ConnectionError` через MTProto  | Проверьте `mtproto_secret`, хост и порт                    |
| Бот не отвечает в группе         | Добавьте бота в группу как админа или отключите Privacy Mode |
| `chat_id` не работает            | Для групп ID отрицательный, для супергрупп начинается с -100 |
