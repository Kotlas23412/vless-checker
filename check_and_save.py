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

# Источники
URLS = [
    "https://raw.githubusercontent.com/kort0881/vpn-checker-backend/refs/heads/main/checked/RU_Best/ru_white_all_WHITE.txt"
]

MAX_WORKERS = 50
TEST_TIMEOUT = 7
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
    try:
        part = key.split("://")[1]
        connection_part = re.split(r'[?#]', part)[0]
        if "@" in connection_part:
            connection_part = connection_part.split("@")[1]
        if ":" in connection_part:
            host, port = connection_part.rsplit(":", 1)
            return host.strip("[]"), int(port)
    except: pass
    return None, None

def test_key(key):
    host, port = parse_host_port(key)
    if not host or not port: return None
    start = time.time()
    try:
        addr_info = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        for family, socktype, proto, canonname, sockaddr in addr_info:
            with socket.socket(family, socktype) as s:
                s.settimeout(TEST_TIMEOUT)
                if s.connect_ex(sockaddr) == 0:
                    latency = round((time.time() - start) * 1000, 1)
                    return {"key": key, "latency_ms": latency}
    except: pass
    return None

def get_country_mode(key):
    lower_key = key.lower()
    fragment = lower_key.split('#')[-1] if '#' in lower_key else lower_key
    for mode, keywords in COUNTRIES.items():
        for kw in keywords:
            if re.search(rf"(\b|_){re.escape(kw)}(\b|_)", fragment):
                return mode
    return "other"

def main():
    os.makedirs("docs", exist_ok=True)
    all_keys = fetch_all_keys(URLS)
    print(f"Найдено ключей: {len(all_keys)}")

    grouped_keys = defaultdict(list)
    for k in all_keys:
        grouped_keys[get_country_mode(k)].append(k)

    results = {"updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "countries": {}}
    all_working_keys = [] # Для TXT файла

    for country in list(COUNTRIES.keys()) + ["other"]:
        keys_to_test = grouped_keys.get(country, [])
        if not keys_to_test: continue
        
        print(f"Проверка {country.upper()}...")
        working = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(test_key, k): k for k in keys_to_test}
            for f in as_completed(futures):
                res = f.result()
                if res: working.append(res)
        
        working.sort(key=lambda x: x["latency_ms"])
        all_working_keys.extend(working)

        if country != "other":
            results["countries"][country] = {
                "total_working": len(working),
                "top10": working[:10]
            }

    # Сохраняем JSON
    with open("docs/keys.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # Сохраняем TXT (только ссылки рабочих ключей, отсортированные по пингу)
    all_working_keys.sort(key=lambda x: x["latency_ms"])
    with open("docs/working_keys.txt", "w", encoding="utf-8") as f:
        for item in all_working_keys:
            f.write(item["key"] + "\n")

    print(f"Готово. Рабочих ключей сохранено: {len(all_working_keys)}")

if __name__ == "__main__":
    main()
