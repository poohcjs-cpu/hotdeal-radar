"""수집 → 정리 → 알림을 한 번에 (또는 일정 간격으로 계속) 실행한다.

  python -m collector.run                 # 한 번
  python -m collector.run --every 30      # 30분마다 계속 (내 컴퓨터에서 돌릴 때. 멈추려면 Ctrl+C)
  python -m collector.run --mock          # 가짜 사이트로 동작 확인

앱 보기:  python -m http.server 8000 -d docs   → 브라우저에서 http://localhost:8000
"""
from __future__ import annotations

import argparse
import sys
import time

from . import build, collect, notify, store


def once(root, cfg, mock: bool = False) -> None:
    now = store.now_kst()
    argv = ["--root", str(root)] + (["--mock"] if mock else [])
    collect.main(argv)
    out = build.build(root, cfg, now)
    s = out["stats"]
    print(f"[정리] 커뮤니티 글 {s['posts']}개(가격 비교 {s['verified']}개) · 딜 {s['deals']}개 · "
          f"기준({cfg['alerts'].get('threshold', 30)}%) 이상 {s['hot']}개")
    try:
        notify.notify(root, cfg, now)
    except Exception as e:  # 알림이 실패해도 수집은 계속
        print(f"[알림] 실패: {e}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="핫딜 수집·정리·알림")
    ap.add_argument("--root")
    ap.add_argument("--every", type=float, default=0, help="이 간격(분)마다 계속 실행. 15분보다 짧게는 못 정해요")
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args(argv)
    root = store.repo_root(args.root)
    try:
        cfg = store.load_config(root)
    except store.ConfigError as e:
        print(f"[설정 오류] {e}", file=sys.stderr)
        return 2
    if not args.every:
        once(root, cfg, args.mock)
        return 0
    every = max(15.0, args.every) * 60
    while True:
        started = time.monotonic()
        try:
            cfg = store.load_config(root)  # 실행 중에 config.json을 고쳐도 다음 번부터 반영
            once(root, cfg, args.mock)
        except store.ConfigError as e:
            print(f"[설정 오류] {e} — 고칠 때까지 이전 설정으로 기다려요", file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        wait = max(60.0, every - (time.monotonic() - started))
        print(f"[대기] {wait / 60:.0f}분 뒤 다시 실행해요 (멈추려면 Ctrl+C)")
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
