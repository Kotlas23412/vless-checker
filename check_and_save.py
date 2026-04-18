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
from urllib.parse import unquote

# Ссылки на источники (теперь обрабатываются как единый пул)
URLS = [
    "https://raw.githubusercontent.com/kort0881/vpn-checker-backend/refs/heads/main/checked/RU_Best/ru_white_all_WHITE.txt"
]

MAX_WORKERS = 30
TEST_TIMEOUT = 5
MAX_LATENCY_MS = 2000

# Настройки поиска стран (ключевые слова в ссылке или в названии)
COUNTRIES = {
    "baltics":     ["lithuania", "estonia", "latvia", "li", "lv", "ee"],
    "finland":     ["finland", "fi"],
    "germany":     ["germany", "de"],
    "sweden":      ["sweden", "se"],
    "netherlands": ["netherlands", "nl"],
    "poland":      ["poland", "pl"],
    "usa":         ["usa", "united states", "us"],
    "kazakhstan":  ["kazakhstan", "kz"],
    "turkey":      ["turkey", "tr", "turkiye"],
    "russia":      ["russia", "ru"],
}

# Список всех ключевых слов для фильтрации "other"
ALL_KEYWORDS = [kw for kws in COUNTRIES.values() for kw in kws]

def parse_country_from_fragment(key):
    """Пытается достать название страны и флаг из части после #"""
    if '#' not in key:
        return None, None
    fragment = unquote(key.split('#', 1)[1])
    # Ищем название страны перед запятой или вертикальной чертой
    match = re.search(
        r'([A-Z][A-Za-z\u00C0-\u017E](?:[A-Za-z\u00C0-\u017E\s\-]*[A-Za-z\u00C0-\u017E])?)(?:\s*[,|])',
        fragment
    )
    if not match:
        return None, None
    country = match.group(1).strip()
    flag = fragment[:match.start()].strip()
    return country, flag

def fetch_all_keys(urls):
    all_keys = set()
    for url in urls:
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            lines = resp.text.strip().splitlines()
            for line in lines:
                clean = line.strip()
                if clean.startswith("vless://"):
                    all_keys.add(clean)
        except Exception as e:
            print(f"Ошибка при загрузке {url}: {e}")
    return list(all_keys)

def get_country_mode(key):
    """Определяет, к какой группе отнести ключ на основе подстрок"""
    lower_key = key.lower()
    # Сначала проверяем фрагмент (название после #)
    fragment = lower_key.split('#')[-1] if '#' in lower_key else ""
    
    for mode, keywords in COUNTRIES.items():
        for kw in keywords:
            # Ищем ключевое слово как отдельный элемент (окруженный знаками или в начале/конце)
            pattern = rf"(\W|^){re.escape(kw)}(\W|$)"
            if re.search(pattern, fragment) or re.search(pattern, lower_key):
                return mode
    return "other"

def parse_host_port(key):
    try:
        without_scheme = key[len("vless://"):]
        at_idx = without_scheme.rfind("@")
        after_at = without_scheme[at_idx + 1:]
        host_port = after_at.split("?")[0].split("#")[0]
        if ":" in host_port:
            host, port = host_port.rsplit(":", 1)
            return host.strip("[]"), int(port)
    except:
        pass
    return None, None

def test_key(key):
    host, port = parse_host_port(key)
    if not host: return None
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except: return None
    
    best_latency = None
    for (family, socktype, proto, canonname, sockaddr) in infos:
        start = time.time()
        try:
            sock = socket.socket(family, socktype)
            sock.settimeout(TEST_TIMEOUT)
            res = sock.connect_ex(sockaddr)
            sock.close()
            elapsed = round((time.time() - start) * 1000, 1)
            if res == 0 and elapsed <= MAX_LATENCY_MS:
                if best_latency is None or elapsed < best_latency:
                    best_latency = elapsed
        except: continue
        
    if best_latency is not None:
        return {"key": key, "latency_ms": best_latency}
    return None

def check_group(keys, old_first_seen):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    working = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(test_key, k): k for k in keys}
        for f in as_completed(futures):
            res = f.result()
            if res:
                res["first_seen"] = old_first_seen.get(res["key"], now)
                working.append(res)
    
    working.sort(key=lambda x: x["latency_ms"])
    return {
        "best": working[0]["key"] if working else None,
        "top10": working[:10],
        "total_working": len(working),
        "total_checked": len(keys)
    }

def load_old_first_seen():
    try:
        with open("docs/keys.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            seen = {}
            # Собираем даты из всех разделов старого файла
            def extract(obj):
                if isinstance(obj, dict):
                    if "key" in obj and "first_seen" in obj:
                        seen[obj["key"]] = obj["first_seen"]
                    for v in obj.values(): extract(v)
                elif isinstance(obj, list):
                    for i in obj: extract(i)
            extract(data)
            return seen
    except: return {}

def main():
    old_first_seen = load_old_first_seen()
    print("Загрузка всех ключей...")
    all_keys = fetch_all_keys(URLS)
    print(f"Всего уникальных ключей: {len(all_keys)}")

    # Группировка
    groups = defaultdict(list)
    for k in all_keys:
        mode = get_country_mode(k)
        groups[mode].append(k)

    results = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "countries": {}
    }

    # Проверка основных групп
    for country in COUNTRIES.keys():
        keys = groups.get(country, [])
        if not keys: continue
        print(f"Проверка {country} ({len(keys)} шт)...")
        results["countries"][country] = check_group(keys, old_first_seen)

    # Обработка "Other" (динамическое определение стран по тегам)
    other_raw_keys = groups.get("other", [])
    if other_raw_keys:
        print(f"Обработка прочих стран ({len(other_raw_keys)} шт)...")
        other_subgroups = defaultdict(list)
        other_flags = {}
        
        for k in other_raw_keys:
            name, flag = parse_country_from_fragment(k)
            name = name if name else "Unknown"
            other_flags[name] = flag if flag else "🌍"
            other_subgroups[name].append(k)
            
        results["other_countries"] = {}
        for name, keys in other_subgroups.items():
            checked = check_group(keys, old_first_seen)
            if checked["total_working"] > 0:
                checked["flag"] = other_flags[name]
                results["other_countries"][name] = checked

    os.makedirs("docs", exist_ok=True)
    with open("docs/keys.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("Готово! Результат в docs/keys.json")

if __name__ == "__main__":
    main()
