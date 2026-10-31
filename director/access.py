"""存取碼與每日用量上限：把導演架在網路上給測試者用時，擋掉沒有存取碼的請求，並限制每人每天的生成次數。

存取碼檔（JSON）只存雜湊，不存存取碼本身；存取碼在新增時印出一次，交給測試者。
用量記在旁邊的 <檔名>.usage.json，以 UTC 日期分天，保留最近 14 天。
存取碼檔可以是唯讀的（例如 Kubernetes Secret）：用 usage_path（導演的 --usage 或環境變數
ZEWORLD_DIRECTOR_USAGE）把用量改寫到別的可寫位置。

python -m director.access add  --file codes.json --name 阿明 [--daily-packs 40] [--daily-lines 800]
python -m director.access list --file codes.json
python -m director.access disable --file codes.json --name 阿明
python -m director.access total --file codes.json --daily-packs 200   # 所有人合計的每日生成上限
"""
import argparse
import datetime
import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path

DEFAULT_DAILY_PACKS = 40
DEFAULT_DAILY_LINES = 800
DEFAULT_DAILY_JUDGES = 2000
DEFAULT_TOTAL_DAILY_PACKS = 200
KEEP_DAYS = 14
# 用量種類 -> (個人上限欄位, 沒寫時的預設)
KINDS = {"packs": ("daily_packs", DEFAULT_DAILY_PACKS), "lines": ("daily_lines", DEFAULT_DAILY_LINES),
         "judges": ("daily_judges", DEFAULT_DAILY_JUDGES)}


def digest(code):
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def today():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


class AccessDenied(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class AccessCodes:
    def __init__(self, path, clock=today, usage_path=None):
        self.path = Path(path)
        self.usage_path = Path(usage_path) if usage_path else self.path.with_name(self.path.name + ".usage.json")
        self.clock = clock
        self.lock = threading.Lock()
        self.reload()

    def reload(self):
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.codes = data.get("codes", [])
        self.total_daily_packs = int(data.get("total_daily_packs", DEFAULT_TOTAL_DAILY_PACKS))
        self.usage = json.loads(self.usage_path.read_text(encoding="utf-8")) if self.usage_path.exists() else {}

    def save(self):
        self.path.write_text(json.dumps({"total_daily_packs": self.total_daily_packs, "codes": self.codes},
                                        ensure_ascii=False, indent=2), encoding="utf-8")

    def _save_usage(self):
        for day in sorted(self.usage)[:-KEEP_DAYS]:
            del self.usage[day]
        self.usage_path.write_text(json.dumps(self.usage, ensure_ascii=False, indent=2), encoding="utf-8")

    def find(self, code):
        h = digest(code or "")
        for entry in self.codes:
            if hmac.compare_digest(entry["sha256"], h):
                return entry
        return None

    def used(self, name, kind, day=None):
        return int(self.usage.get(day or self.clock(), {}).get(name, {}).get(kind, 0))

    def charge(self, code, kind):
        """檢查存取碼與上限，通過就記一次用量並回傳測試者名稱；不通過丟 AccessDenied。
        請求一送進模型就花錢，所以在呼叫模型前記帳，失敗的生成也算。"""
        entry = self.find(code)
        if entry is None or entry.get("disabled"):
            raise AccessDenied(401, "access code missing or invalid")
        name = entry["name"]
        with self.lock:
            day = self.clock()
            field, default = KINDS[kind]
            if self.used(name, kind, day) >= int(entry.get(field, default)):
                raise AccessDenied(429, f"daily {kind} limit reached")
            if kind == "packs":
                total = sum(int(u.get("packs", 0)) for u in self.usage.get(day, {}).values())
                if total >= self.total_daily_packs:
                    raise AccessDenied(429, "server daily packs limit reached")
            counts = self.usage.setdefault(day, {}).setdefault(name, {})
            counts[kind] = int(counts.get(kind, 0)) + 1
            self._save_usage()
        return name

    def add(self, name, daily_packs=DEFAULT_DAILY_PACKS, daily_lines=DEFAULT_DAILY_LINES):
        if any(e["name"] == name for e in self.codes):
            raise ValueError(f"name already exists: {name}")
        code = "zw-" + secrets.token_urlsafe(12)
        self.codes.append({"name": name, "sha256": digest(code), "daily_packs": daily_packs,
                           "daily_lines": daily_lines, "disabled": False})
        self.save()
        return code


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m director.access", description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["add", "list", "disable", "enable", "total"])
    ap.add_argument("--file", required=True, help="存取碼檔，例如 codes.json（不要放進版控）")
    ap.add_argument("--name", help="測試者名稱")
    ap.add_argument("--daily-packs", type=int)
    ap.add_argument("--daily-lines", type=int)
    ap.add_argument("--usage", default=os.environ.get("ZEWORLD_DIRECTOR_USAGE"),
                    help="用量檔；沒給時用 <存取碼檔>.usage.json")
    args = ap.parse_args(argv)
    access = AccessCodes(args.file, usage_path=args.usage)
    if args.command == "add":
        if not args.name:
            ap.error("add 需要 --name")
        code = access.add(args.name, args.daily_packs or DEFAULT_DAILY_PACKS, args.daily_lines or DEFAULT_DAILY_LINES)
        print(f"{args.name} 的存取碼（只顯示這一次，請交給測試者）：\n{code}")
    elif args.command in ("disable", "enable"):
        entry = next((e for e in access.codes if e["name"] == args.name), None)
        if entry is None:
            ap.error(f"找不到 {args.name}")
        entry["disabled"] = args.command == "disable"
        access.save()
        print(f"{args.name}: {'停用' if entry['disabled'] else '啟用'}")
    elif args.command == "total":
        if args.daily_packs is None:
            ap.error("total 需要 --daily-packs")
        access.total_daily_packs = args.daily_packs
        access.save()
        print(f"所有人合計每日生成上限：{args.daily_packs}")
    else:
        day = access.clock()
        print(f"{day}（UTC）合計上限 {access.total_daily_packs} 個事件")
        for e in access.codes:
            state = "停用" if e.get("disabled") else ""
            print(f"- {e['name']}: 事件 {access.used(e['name'], 'packs')}/{e['daily_packs']}，"
                  f"台詞 {access.used(e['name'], 'lines')}/{e['daily_lines']} {state}")


if __name__ == "__main__":
    main()
