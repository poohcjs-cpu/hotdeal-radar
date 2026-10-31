"""새 핫딜을 텔레그램으로 알린다.

  python -m collector.notify      # build.py 다음에 실행

필요한 환경 변수 (없으면 조용히 건너뛴다)
  TELEGRAM_BOT_TOKEN   @BotFather에서 받은 봇 토큰
  TELEGRAM_CHAT_ID     알림을 받을 대화 id
  APP_URL              (선택) 앱 주소. 없으면 GitHub Pages 주소를 GITHUB_REPOSITORY로 만든다

알림 기준은 config.json의 alerts에서 정한다.
  threshold          쇼핑몰 평균가보다 이만큼(%) 이상 싼 딜을 알린다
  categories         비워 두면 모든 제품군, 넣으면 그 제품군만 (예: ["pc", "audio"])
  keywords           이 단어가 이름에 들어간 상품은 keyword_threshold만 넘어도 알린다
  max_per_run        한 번에 알릴 최대 개수
  renotify_drop      이미 알린 상품은 그때보다 이만큼(%) 더 내려가야 다시 알린다
  remember_days      알린 기록을 이 기간 동안 기억한다
  min_votes          커뮤니티 딜은 추천 수가 이만큼 이상일 때만 (추천 수를 모르는 게시판은 그대로 알림)
가격 확인이 필요한 딜(평균가보다 60% 넘게 싼 것)과 끝난 딜은 알리지 않는다.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import timedelta

from . import store

TELEGRAM_LIMIT = 3900  # 메시지 최대 4096자에서 여유를 둔다


def norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


def app_url() -> str:
    url = os.environ.get("APP_URL", "").strip()
    if url:
        return url if url.endswith("/") else url + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        owner = owner.lower()
        if name.lower() == f"{owner}.github.io":
            return f"https://{owner}.github.io/"
        return f"https://{owner}.github.io/{name}/"
    return ""


def won(n) -> str:
    return f"{int(round(n)):,}"


def deal_key(d: dict) -> str:
    return f"{d.get('pcode') or d['id']}|{d.get('mall', '')}"


def pick_deals(deals: dict, alerts: dict, state: dict, now) -> tuple[list, int]:
    threshold = float(alerts.get("threshold", 30))
    kw_threshold = float(alerts.get("keyword_threshold", 15))
    cats = set(alerts.get("categories") or [])
    keywords = [norm(k) for k in alerts.get("keywords") or [] if k and k.strip()]
    renotify = float(alerts.get("renotify_drop", 3)) / 100
    remember = timedelta(days=int(alerts.get("remember_days", 14)))
    min_votes = int(alerts.get("min_votes", 0) or 0)

    picked = []
    for d in deals.get("deals", []):
        if d.get("suspicious") or d.get("ended") or not d.get("price"):
            continue
        if min_votes and d.get("kind") == "community" and d.get("votes") is not None and d["votes"] < min_votes:
            continue
        rate = float(d.get("rate") or 0)
        name = norm(f"{d.get('name', '')} {d.get('danawaName', '')}")
        hit_kw = next((k for k in keywords if k in name), None)
        by_cat = rate >= threshold and (not cats or d.get("cat") in cats)
        by_kw = hit_kw is not None and rate >= kw_threshold
        if not (by_cat or by_kw):
            continue
        prev = state.get(deal_key(d))
        if prev and now - store.parse_time(prev["at"]) < remember and d["price"] > prev["price"] * (1 - renotify):
            continue  # 최근에 알린 딜은 그때보다 충분히 더 내려갔을 때만 다시 알린다
        picked.append({**d, "_kw": hit_kw})
    picked.sort(key=lambda d: -float(d.get("rate") or 0))
    limit = int(alerts.get("max_per_run", 10))
    return picked[:limit], max(0, len(picked) - limit)


def format_messages(picked: list, extra: int, deals: dict, url: str) -> list[str]:
    cats = {c["id"]: c for c in deals.get("categories", [])}
    sources = {s["id"]: s for s in deals.get("sources", [])}
    head = f"🔥 <b>새 핫딜 {len(picked) + extra}개</b> · 쇼핑몰 평균가 기준"
    blocks = []
    for d in picked:
        c = cats.get(d.get("cat"), {})
        name = html.escape(d.get("name") or d.get("danawaName") or "")
        link = d.get("url") or (f"{url}#deal-{d['id']}" if url else "")
        title = f'<a href="{html.escape(link, quote=True)}">{name}</a>' if link else name
        ref = "쇼핑몰 평균" if d.get("reference") == "average" else "다나와 최저가"
        malls = f" ({d['malls']}곳)" if d.get("malls") else ""
        src = sources.get(d.get("source"), {}).get("name", "")
        found = f"{src} 글" if d.get("kind") == "community" else "다나와 가격 비교에서 발견"
        tag = " · 🔔 키워드" if d.get("_kw") else ""
        app = f' · <a href="{html.escape(url + "#deal-" + d["id"], quote=True)}">앱</a>' if url else ""
        blocks.append(
            f"<b>-{int(float(d['rate']))}%</b> {title}\n"
            f"{won(d['price'])}원 · {ref} {won(d['referencePrice'])}원{malls}\n"
            f"{html.escape(d.get('mall', ''))} · {html.escape(c.get('name', ''))} · {html.escape(found)}{tag}{app}"
        )
    tail = []
    if extra:
        tail.append(f"외 {extra}개 더 있어요.")
    if url:
        tail.append(f'<a href="{html.escape(url, quote=True)}">앱에서 모두 보기</a>')
    messages, cur = [], head
    for b in blocks:
        if len(cur) + len(b) + 2 > TELEGRAM_LIMIT:
            messages.append(cur)
            cur = b
        else:
            cur += "\n\n" + b
    if tail:
        t = "\n\n" + "\n".join(tail)
        if len(cur) + len(t) > TELEGRAM_LIMIT:
            messages.append(cur)
            cur = t.strip()
        else:
            cur += t
    messages.append(cur)
    return messages


def send_telegram(token: str, chat_id: str, text: str, timeout: float = 15.0) -> None:
    base = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
    body = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }).encode("utf-8")
    req = urllib.request.Request(f"{base}/bot{token}/sendMessage", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"텔레그램 전송 실패 (HTTP {e.code}): {detail}") from None
    if not data.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {data}")


def notify(root, cfg: dict, now, log=print) -> int:
    """보낸 딜 수를 돌려준다. 알림 설정이 없으면 0."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log("[알림] 텔레그램 설정이 없어 건너뛰어요.")
        return 0
    alerts = cfg.get("alerts", {}) or {}
    deals = store.read_json(root / cfg.get("output", {}).get("path", "docs/data/deals.json"), None)
    if not deals or not deals.get("deals"):
        log("[알림] 아직 가격을 비교한 딜이 없어요.")
        return 0

    state_path = root / "data" / "notified.json"
    state = store.read_json(state_path, {})
    picked, extra = pick_deals(deals, alerts, state, now)
    if not picked:
        log("[알림] 새로 알릴 핫딜이 없어요.")
        return 0

    for text in format_messages(picked, extra, deals, app_url()):
        send_telegram(token, chat_id, text)

    for d in picked:
        state[deal_key(d)] = {"price": d["price"], "at": now.isoformat()}
    keep = timedelta(days=int(alerts.get("remember_days", 14)))
    state = {k: v for k, v in state.items() if now - store.parse_time(v["at"]) < keep}
    store.write_json(state_path, state)
    log(f"[알림] 핫딜 {len(picked)}개를 텔레그램으로 보냈어요." + (f" (기준을 넘는 딜 {extra}개 더 있음)" if extra else ""))
    return len(picked)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="새 핫딜 텔레그램 알림")
    ap.add_argument("--root")
    ap.add_argument("--config")
    ap.add_argument("--now")
    args = ap.parse_args(argv)
    root = store.repo_root(args.root)
    try:
        cfg = store.load_config(root, args.config)
    except store.ConfigError as e:
        print(f"[설정 오류] {e}", file=sys.stderr)
        return 2
    now = store.parse_time(args.now) if args.now else store.now_kst()
    try:
        notify(root, cfg, now)
    except Exception as e:  # 알림이 실패해도 수집·배포는 계속되게 한다
        print(f"::warning::{e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
