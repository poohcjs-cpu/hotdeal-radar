"""설정 검사, 시간, 파일 저장.

저장 구조 (저장소 루트 기준)
  data/community.json        최근 커뮤니티 글과 검증 결과 (keep_hours 동안 보관)
  data/products.json         다나와에서 지켜보는 제품 (최근 쇼핑몰별 가격 포함)
  data/state.json            출처별 수집 상태, 다나와 검색 캐시, 키워드 갱신 시각
  data/history/YYYY-MM.csv   제품별 가격 기록 (최저가·평균가·쇼핑몰 수)
  data/notified.json         텔레그램으로 이미 알린 딜
  docs/data/deals.json       앱이 읽는 결과 (build.py)
"""
from __future__ import annotations

import csv
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
HISTORY_FIELDS = ["date", "time", "pcode", "lowest", "average", "malls", "best_mall"]
_CAT_ID = re.compile(r"^[a-z0-9_-]{1,32}$")


class ConfigError(ValueError):
    pass


def now_kst() -> datetime:
    return datetime.now(KST).replace(microsecond=0)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def repo_root(arg: str | None = None) -> Path:
    if arg:
        return Path(arg).resolve()
    return Path(__file__).resolve().parent.parent


# ── 설정 ───────────────────────────────────────────────────

def validate_config(cfg: dict) -> dict:
    cats = cfg.get("categories")
    if not isinstance(cats, list) or not cats:
        raise ConfigError("config.json: categories에 제품군을 하나 이상 넣어 주세요.")
    seen = set()
    for i, c in enumerate(cats, 1):
        cid = c.get("id")
        if not isinstance(cid, str) or not _CAT_ID.match(cid) or cid == "etc":
            raise ConfigError(f"config.json: {i}번째 제품군의 id는 영어 소문자·숫자·-·_ 로만 써 주세요 ('etc'는 예약어예요). 지금: {cid!r}")
        if cid in seen:
            raise ConfigError(f"config.json: 제품군 id '{cid}'가 두 번 나와요.")
        seen.add(cid)
        if not isinstance(c.get("name"), str) or not c["name"].strip():
            raise ConfigError(f"config.json: 제품군 '{cid}'에 name(화면에 보일 이름)이 없어요.")
        for key in ("keywords", "match_words"):
            v = c.setdefault(key, [])
            if not isinstance(v, list) or not all(isinstance(k, str) and k.strip() for k in v):
                raise ConfigError(f"config.json: 제품군 '{cid}'의 {key}는 글자 목록이어야 해요.")
    for section in ("sources", "community", "danawa", "pricing", "run", "storage", "alerts", "output"):
        if not isinstance(cfg.setdefault(section, {}), dict):
            raise ConfigError(f"config.json: {section}는 {{ }} 묶음이어야 해요.")
    watch = cfg.setdefault("watch", [])
    if not isinstance(watch, list) or not all(re.fullmatch(r"\d{4,12}", str(x)) for x in watch):
        raise ConfigError("config.json: watch에는 다나와 상품 번호(pcode, 숫자)만 넣어 주세요. 예: [\"20480012\"]")
    from .community import SOURCE_BY_ID
    for sid in cfg["sources"]:
        if sid not in SOURCE_BY_ID:
            raise ConfigError(f"config.json: sources의 '{sid}'는 모르는 출처예요. 쓸 수 있는 값: {', '.join(SOURCE_BY_ID)}")
    for cid in cfg["alerts"].get("categories", []) or []:
        if cid not in seen and cid != "etc":
            raise ConfigError(f"config.json: alerts.categories의 '{cid}'는 categories에 없는 id예요.")
    return cfg


def load_config(root: Path, path: str | None = None) -> dict:
    cfg_path = Path(path) if path else root / "config.json"
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        raise ConfigError(f"설정 파일을 찾지 못했어요: {cfg_path}") from None
    except json.JSONDecodeError as e:
        raise ConfigError(f"config.json 문법 오류 ({e.lineno}번째 줄 {e.colno}번째 글자): 쉼표나 따옴표를 확인해 주세요.") from None
    return validate_config(cfg)


# ── 파일 ───────────────────────────────────────────────────

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def write_json(path: Path, obj, compact: bool = False) -> None:
    if compact:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True)
    _atomic_write(path, text + "\n")


def read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ── 가격 기록 ───────────────────────────────────────────────

def history_path(root: Path, d: date) -> Path:
    return root / "data" / "history" / f"{d:%Y-%m}.csv"


def append_history(root: Path, rows: list[dict]) -> None:
    by_file: dict = {}
    for r in rows:
        by_file.setdefault(history_path(root, date.fromisoformat(r["date"])), []).append(r)
    for path, items in by_file.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with open(path, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS, lineterminator="\n", extrasaction="ignore")
            if new:
                w.writeheader()
            for r in items:
                w.writerow(r)


def read_history(root: Path, since: date) -> dict:
    """{pcode: [(date, lowest, average, malls)]} — 하루에 여러 번이면 그날 마지막 값."""
    out: dict = {}
    d = root / "data" / "history"
    if not d.exists():
        return out
    for path in sorted(d.glob("*.csv")):
        try:
            month = datetime.strptime(path.stem, "%Y-%m").date()
        except ValueError:
            continue
        if month < since.replace(day=1):
            continue
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                try:
                    day = date.fromisoformat(r["date"])
                except (KeyError, ValueError):
                    continue
                if day < since:
                    continue
                daymap = out.setdefault(r["pcode"], {})
                daymap[day] = (int(r["lowest"] or 0) or None, int(r["average"] or 0) or None, int(r["malls"] or 0))
    return {p: sorted((d, *v) for d, v in m.items()) for p, m in out.items()}


def prune_history(root: Path, today: date, keep_days: int) -> int:
    d = root / "data" / "history"
    if not d.exists():
        return 0
    cutoff = (today - timedelta(days=keep_days)).replace(day=1)
    removed = 0
    for path in d.glob("*.csv"):
        try:
            if datetime.strptime(path.stem, "%Y-%m").date() < cutoff:
                path.unlink()
                removed += 1
        except ValueError:
            continue
    return removed
