#!/usr/bin/env python3
"""Настройка Flash Call в Plusofon для стенда kc-plusofon.

Использует ОСНОВНОЙ токен личного кабинета (Разработчикам → Доступ к API).
Для группы методов flash-call токен должен иметь соответствующее право —
при 403 "Not enough permissions" включите право «Flash Call» у токена в ЛК.

Шаги (автоматизирует этот скрипт):
  1. GET  /api/v1/flash-call                 — список аккаунтов Flash Call
  2. POST /api/v1/flash-call                 — создать аккаунт (если своего нет)
  3. GET  /api/v1/flash-call/proposals       — список доступных пакетов
  4. PUT  /api/v1/flash-call/proposals/{id}  — подключить пакет (для обратного
     Flash Call с параметром number = 8-800, на который звонят пользователи)

Примеры:
  export PLUSOFON_LK_TOKEN=...        # основной токен ЛК
  python3 scripts/setup_flashcall.py list
  python3 scripts/setup_flashcall.py packages            # все пакеты
  python3 scripts/setup_flashcall.py packages --reverse  # только обратный FC
  python3 scripts/setup_flashcall.py create --name kc-plusofon-dev
  python3 scripts/setup_flashcall.py connect <proposal_id> --number 88001002030
  python3 scripts/setup_flashcall.py token [--name kc-plusofon-dev]  # access_token аккаунта
"""
import argparse
import json
import os
import sys

import httpx

BASE = os.environ.get("PLUSOFON_API_BASE", "https://restapi.plusofon.ru")
CLIENT = "10553"


def headers(token: str) -> dict:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Client": CLIENT,
        "Authorization": f"Bearer {token}",
    }


def api(client: httpx.Client, method: str, path: str, token: str, **kwargs):
    resp = client.request(method, f"{BASE}{path}", headers=headers(token), **kwargs)
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text}
    if resp.status_code != 200 or data.get("success") is False:
        print(f"! {method} {path} -> HTTP {resp.status_code}: "
              f"{json.dumps(data, ensure_ascii=False)[:400]}", file=sys.stderr)
        if resp.status_code == 403:
            print("  Токен без нужного права. В ЛК: Разработчикам → Доступ к API → "
                  "у токена включите право «Flash Call» (и сохраните токен).",
                  file=sys.stderr)
        sys.exit(1)
    return data


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["list", "packages", "create", "connect", "token"])
    p.add_argument("--name", default="kc-plusofon-dev", help="имя аккаунта Flash Call")
    p.add_argument("proposal_id", nargs="?", help="ИД пакета (для connect)")
    p.add_argument("--number", help="номер 8-800 для обратного Flash Call (для connect)")
    args = p.parse_args()

    token = os.environ.get("PLUSOFON_LK_TOKEN", "")
    if not token:
        sys.exit("Задайте PLUSOFON_LK_TOKEN (основной токен ЛК Плюсофон)")

    with httpx.Client(timeout=30) as c:
        if args.command == "list":
            data = api(c, "GET", "/api/v1/flash-call", token)
            for a in data.get("data") or []:
                print(f"id={a['id']:<4} name={a['name']!r} access_token={a['access_token']}")
            if not (data.get("data") or []):
                print("аккаунтов нет — создайте: python3 scripts/setup_flashcall.py create")

        elif args.command == "packages":
            data = api(c, "GET", "/api/v1/flash-call/proposals", token)
            for pkg in data.get("data") or []:
                rev = bool(pkg.get("flashcall_reverse"))
                ivr = bool(pkg.get("flashcall_ivr"))
                if args.reverse and not rev:
                    continue
                tag = "REVERSE" if rev else ("ivr    " if ivr else "обычный ")
                print(f"{tag} id={pkg['id']}  {pkg['name']:<20} "
                      f"вызовов={pkg['count_call'].split('.')[0]:>7}  "
                      f"{pkg['amount']} руб/мес  "
                      f"(в пакете {pkg['amount_in_packet']} / сверх {pkg['amount_out_packet']})")

        elif args.command == "create":
            data = api(c, "POST", "/api/v1/flash-call", token, json={"name": args.name})
            d = data.get("data") or {}
            print(f"создан аккаунт: id={d.get('account_id')} "
                  f"access_token={d.get('access_token')}")
            print(f"для стенда: PLUSOFON_FC_ACCOUNT_TOKEN={d.get('access_token')}")

        elif args.command == "connect":
            if not args.proposal_id or not args.number:
                sys.exit("usage: connect <proposal_id> --number 8800XXXXXXXXX")
            accounts = (api(c, "GET", "/api/v1/flash-call", token).get("data") or [])
            acc = next((a for a in accounts if a["name"] == args.name), None)
            if acc is None:
                sys.exit(f"аккаунт с именем {args.name!r} не найден (см. list/create)")
            api(c, "PUT", f"/api/v1/flash-call/proposals/{acc['id']}", token,
                json={"proposal_id": args.proposal_id, "number": args.number})
            print(f"пакет {args.proposal_id} подключен к аккаунту {acc['id']} "
                  f"(номер {args.number})")

        elif args.command == "token":
            accounts = (api(c, "GET", "/api/v1/flash-call", token).get("data") or [])
            acc = next((a for a in accounts if a["name"] == args.name), None)
            if acc is None:
                sys.exit(f"аккаунт с именем {args.name!r} не найден (см. list/create)")
            print(acc["access_token"])


if __name__ == "__main__":
    main()
