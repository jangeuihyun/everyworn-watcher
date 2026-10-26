#!/usr/bin/env python3
"""
everyworn 재고 감시기 — 사이즈별 재고 변동을 텔레그램으로 알림

동작:
  1. EXTRA_IDX에 적힌 상품(+ NAME_PATTERN을 쓰면 이름이 맞는 상품)을 감시 대상으로 삼습니다.
  2. 각 상품의 사이즈별 재고를 /shop/load_option.cm 에서 가져옵니다.
  3. state.json 에 저장된 직전 상태와 비교해서 바뀐 게 있으면 텔레그램으로 알립니다.
  4. STATUS.md 에 현재 재고표를 씁니다 (재고가 바뀔 때만 갱신).

외부 패키지 없이 파이썬 표준 라이브러리만 씁니다.

환경변수:
  TELEGRAM_BOT_TOKEN   텔레그램 봇 토큰 (없으면 알림 대신 콘솔에만 출력)
  TELEGRAM_CHAT_ID     알림 받을 채팅 ID
  NOTIFY_SOLDOUT       "false"로 두면 재입고만 알리고 품절 전환은 알리지 않음 (기본 true)
"""

import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://www.everyworn.com"
# ── 감시 대상 설정 ─────────────────────────────────────────────
# 감시할 상품 번호 (상품 주소 https://www.everyworn.com/shop/?idx=288 의 숫자)
#   288: Flannel Relaxed Curve Shirt (Green)
EXTRA_IDX = [288]

# (선택) 이름으로 자동 감시: 쇼핑몰 목록에서 이름이 이 패턴에 맞는 상품을 모두 감시.
#   새 색상이 올라와도 자동 포함돼요. 쓰지 않으면 None.
#   예) Raglan Crewneck 전 색상: re.compile(r"raglan\s*crew", re.I)
NAME_PATTERN = None
SEED_IDX = []  # NAME_PATTERN을 쓸 때, 목록 조회가 실패해도 감시할 기본 번호
# ───────────────────────────────────────────────────────────────

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
STATUS_FILE = os.path.join(HERE, "STATUS.md")

KST = timezone(timedelta(hours=9))
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
FAIL_ALERT_THRESHOLD = 6  # 연속 실패가 이 횟수에 도달하면 한 번 알림


# ---------------------------------------------------------------- HTTP

def http(url, data=None, retries=3):
    headers = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers.update({
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{BASE}/shop/?idx={data.get('prod_idx', '')}",
        })
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url} 요청 실패: {last}")


# ---------------------------------------------------------------- 파싱

PROPS_RE = re.compile(r"data-product-properties=(['\"])(.*?)\1", re.S)
OPTION_RE = re.compile(r"<a[^>]*_requireOption[^>]*>(.*?)</a>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
SIZE_RE = re.compile(r"^(.*?)\s+[\d,]+\s*원")


def discover_products(shop_html):
    """목록 HTML에서 {idx: name} 중 이름이 패턴에 맞는 것만."""
    found = {}
    if NAME_PATTERN is None:
        return found
    for _, raw in PROPS_RE.findall(shop_html):
        try:
            p = json.loads(html.unescape(raw))
        except ValueError:
            continue
        name = p.get("name", "")
        if NAME_PATTERN.search(name):
            found[int(p["idx"])] = name
    return found


def parse_options(option_html):
    """옵션 HTML → {사이즈 라벨: 재고있음(bool)} (순서 유지)."""
    sizes = {}
    for inner in OPTION_RE.findall(option_html or ""):
        text = re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", inner))).strip()
        if not text:
            continue
        m = SIZE_RE.match(text)
        label = (m.group(1) if m else text.replace("(품절)", "")).strip()
        sizes[label] = "품절" not in text
    return sizes


def fetch_product(idx):
    raw = http(f"{BASE}/shop/load_option.cm", {"type": "prod", "prod_idx": idx})
    j = json.loads(raw)
    if j.get("msg") != "SUCCESS":
        raise RuntimeError(f"idx={idx} 응답 이상: {j.get('msg')}")
    return j.get("prod_name") or "", parse_options(j.get("option_html", ""))


# ---------------------------------------------------------------- 상태

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def short_name(name):
    """Raglan Crewneck은 'Red'처럼 색상만, 다른 상품은 'Flannel Relaxed Curve Shirt (Green)'처럼 전체 이름."""
    if NAME_PATTERN is None or not NAME_PATTERN.search(name):
        return name.strip()
    m = re.findall(r"\(([^)]*)\)", name)
    color = m[-1] if m else name
    return f"{color} (우먼)" if "woman" in name.lower() else color


def product_url(idx):
    return f"{BASE}/shop/?idx={idx}"


# ---------------------------------------------------------------- 알림

_STATE_REF = {}  # main()에서 state를 넣어둠 (chat id 기억용)


def resolve_chat_id(token):
    """TELEGRAM_CHAT_ID가 없으면, 봇에게 마지막으로 말을 건 대화방을 찾아 state에 기억."""
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if chat:
        return chat
    chat = str(_STATE_REF.get("telegram_chat_id", "") or "")
    if chat:
        return chat
    try:
        res = json.loads(http(f"https://api.telegram.org/bot{token}/getUpdates"))
        for upd in reversed(res.get("result", [])):
            msg = upd.get("message") or upd.get("edited_message") or {}
            cid = (msg.get("chat") or {}).get("id")
            if cid:
                _STATE_REF["telegram_chat_id"] = cid
                return str(cid)
    except Exception as e:  # noqa: BLE001
        print(f"chat id 조회 실패: {e}", file=sys.stderr)
    print("chat id를 못 찾았어요. 텔레그램에서 봇에게 아무 메시지나 보낸 뒤 다시 실행하세요.",
          file=sys.stderr)
    return ""


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    print("----- 알림 -----\n" + text + "\n----------------")
    if not token:
        print("(텔레그램 설정이 없어 콘솔에만 출력)")
        return False
    chat = resolve_chat_id(token)
    if not chat:
        return False
    try:
        http(
            f"https://api.telegram.org/bot{token}/sendMessage",
            {"chat_id": chat, "text": text, "parse_mode": "HTML",
             "disable_web_page_preview": "true"},
        )
        return True
    except Exception as e:  # noqa: BLE001
        print(f"텔레그램 전송 실패: {e}", file=sys.stderr)
        return False


def fmt_sizes(sizes):
    return " · ".join(f"{k} {'✅' if v else '❌'}" for k, v in sizes.items())


def write_status(products, changed_at):
    lines = [
        "# everyworn 재고 현황",
        "",
        f"마지막 재고 변동 감지: {changed_at}",
        "",
    ]
    all_sizes = []
    for p in products.values():
        for s in p["sizes"]:
            if s not in all_sizes:
                all_sizes.append(s)
    def size_key(label):
        m = re.match(r"\s*(\d+)", label)
        return (0, int(m.group(1))) if m else (1, label)
    all_sizes.sort(key=size_key)
    lines.append("| 상품 | " + " | ".join(all_sizes) + " |")
    lines.append("|---|" + "---|" * len(all_sizes))
    order = sorted(products.items(),
                   key=lambda kv: (-sum(kv[1]["sizes"].values()), kv[1]["name"]))
    for idx, p in order:
        cells = []
        for s in all_sizes:
            v = p["sizes"].get(s)
            cells.append("✅" if v else ("❌" if v is False else "–"))
        lines.append(f"| [{short_name(p['name'])}]({product_url(idx)}) | " + " | ".join(cells) + " |")
    lines += ["", "✅ 재고 있음 · ❌ 품절", ""]
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------- 메인

def main():
    notify_soldout = os.environ.get("NOTIFY_SOLDOUT", "true").lower() != "false"
    state = load_state()
    _STATE_REF.update(state)
    prev = state.get("products", {})
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    # 1) 감시 대상 찾기
    targets = {i: "" for i in SEED_IDX + EXTRA_IDX}
    targets.update({int(k): v.get("name", "") for k, v in prev.items()})
    if NAME_PATTERN is not None:
        try:
            targets.update(discover_products(http(f"{BASE}/shop")))
        except Exception as e:  # noqa: BLE001
            print(f"목록 조회 실패(기존 목록으로 계속): {e}", file=sys.stderr)

    # 2) 재고 조회
    current, errors = {}, []
    for idx in sorted(targets):
        try:
            name, sizes = fetch_product(idx)
            if not sizes:
                raise RuntimeError("사이즈 옵션을 찾지 못함 (사이트 구조 변경 가능성)")
            current[str(idx)] = {"name": name or targets[idx], "sizes": sizes}
        except Exception as e:  # noqa: BLE001
            errors.append(f"idx={idx}: {e}")
            if str(idx) in prev:  # 실패한 상품은 직전 상태 유지 → 잘못된 알림 방지
                current[str(idx)] = prev[str(idx)]
        time.sleep(0.5)

    # 연속 실패 처리
    fails = state.get("consecutive_failures", 0)
    if errors and len(errors) >= len(targets):
        fails += 1
        print("전체 조회 실패:\n" + "\n".join(errors), file=sys.stderr)
        if fails == FAIL_ALERT_THRESHOLD:
            send_telegram(
                "⚠️ <b>everyworn 감시기</b>\n"
                f"재고 조회가 {fails}회 연속 실패했어요. 사이트가 막혔거나 구조가 바뀌었을 수 있어요.\n"
                + html.escape(errors[0])
            )
        state["consecutive_failures"] = fails
        save_json(STATE_FILE, state)
        return
    if errors:
        print("일부 실패:\n" + "\n".join(errors), file=sys.stderr)
    state["consecutive_failures"] = 0

    # 3) 첫 실행: 현재 상태 저장 + 시작 알림
    if not prev:
        msg = ["👀 <b>everyworn 재고 감시 시작</b>", ""]
        for idx, p in sorted(current.items(), key=lambda kv: -sum(kv[1]["sizes"].values())):
            msg.append(f"<a href=\"{product_url(idx)}\">{html.escape(short_name(p['name']))}</a>: {fmt_sizes(p['sizes'])}")
        sent = send_telegram("\n".join(msg))
        if not sent:
            # 시작 알림이 안 갔으면(토큰 미등록 등) 저장하지 않고 다음 실행에서 다시 시도
            return
        state.update({"products": current, "last_change": now})
        if _STATE_REF.get("telegram_chat_id"):
            state["telegram_chat_id"] = _STATE_REF["telegram_chat_id"]
        save_json(STATE_FILE, state)
        write_status(current, now)
        return

    # 4) 변경 비교
    restocked, soldout, new_items = [], [], []
    for idx, p in current.items():
        label = f"<a href=\"{product_url(idx)}\">{html.escape(short_name(p['name']))}</a>"
        if idx not in prev:
            new_items.append(f"🆕 {label}: {fmt_sizes(p['sizes'])}")
            continue
        old = prev[idx]["sizes"]
        up = [s for s, ok in p["sizes"].items() if ok and not old.get(s, False)]
        down = [s for s, ok in p["sizes"].items() if not ok and old.get(s, False)]
        if up:
            restocked.append(f"🟢 {label} — {', '.join(up)} 재입고!")
        if down:
            soldout.append(f"🔴 {label} — {', '.join(down)} 품절")

    changed = bool(restocked or soldout or new_items)
    if changed:
        parts = ["<b>everyworn 재고 변동</b>", ""]
        parts += restocked + new_items
        if notify_soldout:
            parts += soldout
        if restocked or new_items or (notify_soldout and soldout):
            send_telegram("\n".join(parts))
        state["last_change"] = now
        write_status(current, now)
    else:
        print(f"{now} 변동 없음 ({len(current)}개 상품)")

    state["products"] = current
    if _STATE_REF.get("telegram_chat_id"):
        state["telegram_chat_id"] = _STATE_REF["telegram_chat_id"]
    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
