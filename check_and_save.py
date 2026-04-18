#!/usr/bin/env python3
import re
import requests
import socket
import time
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote, urlparse

# Источники ключей
URLS = [
    "https://raw.githubusercontent.com/kort0881/vpn-checker-backend/refs/heads/main/checked/RU_Best/ru_white_all_WHITE.txt"
]

MAX_WORKERS = 50  # Увеличил количество потоков для скорости
TEST_TIMEOUT = 7   # Чуть больше времени на ожидание ответа
MAX_LATENCY_MS = 3500

COUNTRIES = {
    "baltics":     ["lithuania", "estonia", "latvia", "li", "lv", "ee", "baltic"],
    "finland":     ["finland", "fi"],
    "germany":     ["germany", "de"],
    "sweden":      ["sweden", "se"],
    "netherlands": ["netherlands", "nl"],
    "poland":      ["poland", "pl"],
    "usa":         ["usa", "united states", "us"],
    "kazakhstan":  ["kazakhstan", "kz"],
    "turkey":      ["turkey", "tr", "turkiye"],
    "italy":       ["italy", "it", "italia"],
    "switzerland": ["switzerland", "ch", "suisse", "schweiz", "svizzera"],
    "russia":      ["russia", "ru"],
}

def fetch_all_keys(urls):
    all_keys = set()
    for url in urls:
        try:
            print(f"Загрузка: {url}")
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            for line in resp.text.splitlines():
                line = line.strip()
                if line.startswith("vless://"):
                    all_keys.add(line)
        except Exception as e:
            print(f"Ошибка загрузки {url}: {e}")
    return list(all_keys)

def parse_host_port(key):
    """Извлекает хост и порт, игнорируя UUID и параметры."""
    try:
        # Убираем схему
        part = key.split("://")[1]
        # Отсекаем всё после ? или #
        connection_part = re.split(r'[?#]', part)[0]
        # Берем часть после @
        if "@" in connection_part:
            connection_part = connection_part.split("@")[1]
        
        if ":" in connection_part:
            host, port = connection_part.rsplit(":", 1)
            return host.strip("[]"), int(port)
    except:
        pass
    return None, None

def test_key(key):
    host, port = parse_host_port(key)
    if not host or not port:
        return None
    
    # Пытаемся подключиться
    start = time.time()
    try:
        # Пробуем разрешить адрес (DNS)
        addr_info = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        for family, socktype, proto, canonname, sockaddr in addr_info:
            with socket.socket(family, socktype) as s:
                s.settimeout(TEST_TIMEOUT)
                result = s.connect_ex(sockaddr)
                if result == 0:
                    latency = round((time.time() - start) * 1000, 1)
                    if latency <= MAX_LATENCY_MS:
                        return {"key": key, "latency_ms": latency}
    except:
        pass
    return None

def get_country_mode(key):
    lower_key = key.lower()
    # Ищем в части после # (название сервера)
    fragment = lower_key.split('#')[-1] if '#' in lower_key else lower_key
    
    for mode, keywords in COUNTRIES.items():
        for kw in keywords:
            # Ищем слово целиком (чтобы 'it' не ловилось в 'digital')
            if re.search(rf"(\b|_){re.escape(kw)}(\b|_)", fragment):
                return mode
    return "other"

def check_group(keys, old_first_seen):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    working = []
    
    # Запуск тестов в несколько потоков
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_key = {executor.submit(test_key, k): k for k in keys}
        for future in as_completed(future_to_key):
            res = future.result()
            if res:
                res["first_seen"] = old_first_seen.get(res["key"], now)
                working.append(res)
    
    working.sort(key=lambda x: x["latency_ms"])
    return working

def main():
    # Загружаем старые даты появления ключей
    old_first_seen = {}
    if os.path.exists("docs/keys.json"):
        try:
            with open("docs/keys.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                def find_keys(d):
                    if isinstance(d, dict):
                        if "key" in d and "first_seen" in d:
                            old_first_seen[d["key"]] = d["first_seen"]
                        for v in d.values(): find_keys(v)
                    elif isinstance(d, list):
                        for i in d: find_keys(i)
                find_keys(data)
        except: pass

    all_keys = fetch_all_keys(URLS)
    print(f"Найдено уникальных ключей: {len(all_keys)}")

    # Группируем ключи по странам перед проверкой
    grouped_keys = defaultdict(list)
    for k in all_keys:
        grouped_keys[get_country_mode(k)].append(k)

    results = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "countries": {}
    }

    # Проверяем каждую группу
    for country in COUNTRIES.keys():
        keys_to_test = grouped_keys.get(country, [])
        if not keys_to_test:
            continue
            
        print(f"Тестируем {country.upper()} ({len(keys_to_test)} шт.)...")
        working_list = check_group(keys_to_test, old_first_seen)
        
        results["countries"][country] = {
            "best": working_list[0]["key"] if working_list else None,
            "top10": working_list[:10],
            "total_working": len(working_list),
            "total_checked": len(keys_to_test)
        }

    # Прочие страны
    other_keys = grouped_keys.get("other", [])
    if other_keys:
        print(f"Тестируем прочие страны ({len(other_keys)} шт.)...")
        working_other = check_group(other_keys, old_first_seen)
        if working_other:
            results["other_countries"] = {
                "list": working_other[:20], # Ограничим вывод прочих
                "total_working": len(working_other),
                "total_checked": len(other_keys)
            }

    os.makedirs("docs", exist_ok=True)
    with open("docs/keys.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("Готово. Результаты сохранены в docs/keys.json")

if __name__ == "__main__":
    main()
