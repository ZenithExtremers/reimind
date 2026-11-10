#!/usr/bin/env python3
"""
ReiMind（麗澤ポータル 課題リマインダー）

使い方:
  python reitaku_kadai.py              ポータルから課題を取得して通知
  python reitaku_kadai.py --show       ブラウザを表示して取得（動作確認用）
  python reitaku_kadai.py --text a.txt ポータルからコピーした文章を読み込む（ログイン不要）
  python reitaku_kadai.py --list       保存済みの課題を一覧表示
  python reitaku_kadai.py --done 2     一覧の2番を完了にする
  python reitaku_kadai.py --ics        カレンダー用ファイル(kadai.ics)を書き出す
  python reitaku_kadai.py --setup      ID・パスワード・スマホ通知の設定

準備:
  pip install playwright keyring plyer requests
  python -m playwright install chromium
"""
import argparse
import datetime as dt
import getpass
import json
import os
import re
import sys
from pathlib import Path

JST = dt.timezone(dt.timedelta(hours=9))
PORTAL_URL = "https://rp.reitaku-u.ac.jp/uprx/"
BASE = Path(__file__).resolve().parent
_STATE = Path(os.environ["KADAI_STATE_DIR"]) if os.environ.get("KADAI_STATE_DIR") else BASE
DATA = _STATE / "kadai.json"
CONF = BASE / "config.json"
DEBUG = BASE / "debug"  # クラウドでは公開も保存もされない場所
KEYRING_SERVICE = "reitaku-portal"


# ---------------- 設定 ----------------
def load_conf():
    if CONF.exists():
        return json.loads(CONF.read_text(encoding="utf-8"))
    return {}


def save_conf(c):
    CONF.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")


def get_password(user_id):
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, user_id)
    except Exception:
        return None


def setup():
    c = load_conf()
    uid = input(f"ポータルのUser ID [{c.get('user_id', '')}]: ").strip() or c.get("user_id", "")
    pw = getpass.getpass("パスワード（入力は表示されません／空欄で変更なし）: ")
    c["user_id"] = uid
    if pw:
        try:
            import keyring
            keyring.set_password(KEYRING_SERVICE, uid, pw)
            print("パスワードはOSの資格情報ストアに保存しました（ファイルには書きません）")
        except Exception as e:
            print("keyring に保存できませんでした:", e)
            print("代わりに毎回入力するか、環境変数 REITAKU_PW を使ってください")
    print("\nスマホ通知には無料アプリ「ntfy」を使います。")
    print("ntfy アプリで、他人に推測されない名前（例: reitaku-xxxx-8392）を購読してください。")
    topic = input(f"ntfy のトピック名 [{c.get('ntfy_topic', '')}]（空欄で使わない）: ").strip()
    if topic:
        c["ntfy_topic"] = topic
    h = input(f"何時間前から通知するか [{c.get('notify_hours', 48)}]: ").strip()
    if h:
        c["notify_hours"] = int(h)
    save_conf(c)
    print("設定を保存しました:", CONF)


# ---------------- 解析 ----------------
DUE_RE = re.compile(
    r"期限\s*[:：]?\s*(\d{4})\s*[/\-年]\s*(\d{1,2})\s*[/\-月]\s*(\d{1,2})日?"
    r"(?:\s*[（(][^)）]*[)）])?\s*(\d{1,2}:\d{2})?"
)
HEAD_RE = re.compile(
    r"^(?:(課題|レポート|小テスト|テスト|アンケート|試験)\s*)?\d{4}/\d{1,2}/\d{1,2}(?:\s*\d{1,2}:\d{2})?$"
)
LABEL_RE = re.compile(r"^(課題|レポート|小テスト|テスト|アンケート|試験)$")
SKIP = {"期限あり", "学生支援情報"}


def parse(text):
    lines = [re.sub(r"[\s\u3000]+", " ", s).strip() for s in text.splitlines()]
    lines = [s for s in lines if s]
    out, last = [], -1
    for i, line in enumerate(lines):
        m = DUE_RE.search(line)
        if not m:
            continue
        body, k = [], i - 1
        while k > last and len(body) < 6:
            if HEAD_RE.match(lines[k]) or DUE_RE.search(lines[k]):
                break
            body.insert(0, lines[k])
            k -= 1
        last = i
        body = [s for s in body if not LABEL_RE.match(s) and s not in SKIP]
        before = line[: m.start()].strip()
        if before:
            body.append(before)
        if not body:
            continue
        teacher = body.pop() if len(body) >= 2 and len(body[-1]) <= 16 else ""
        y, mo, d, t = m.groups()
        hh, mm = (t or "23:59").split(":")
        out.append({
            "title": " ".join(body),
            "teacher": teacher,
            "due": f"{y}-{int(mo):02d}-{int(d):02d}T{int(hh):02d}:{mm}",
        })
    return out


# ---------------- 保存 ----------------
def load_items():
    if DATA.exists():
        return json.loads(DATA.read_text(encoding="utf-8"))
    return []


def save_items(items):
    DATA.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def key_of(it):
    return re.sub(r"\s+", "", it["title"] + "|" + it["due"])


def merge(items, found):
    keys = {key_of(i) for i in items}
    added = []
    for f in found:
        if key_of(f) not in keys:
            f.update(done=False, notified=[])
            items.append(f)
            keys.add(key_of(f))
            added.append(f)
    return added


def due_dt(it):
    return dt.datetime.fromisoformat(it["due"]).replace(tzinfo=JST)


def fmt(it):
    d = due_dt(it)
    wd = "月火水木金土日"[d.weekday()]
    return f"{d.month}/{d.day}({wd}) {d:%H:%M}"


def remain(it, now):
    sec = (due_dt(it) - now).total_seconds()
    if sec < 0:
        return "期限切れ"
    if sec < 86400:
        return f"あと{int(sec // 3600)}時間"
    return f"あと{int(sec // 86400)}日"


# ---------------- ポータル取得 ----------------
def get_credentials():
    """保存済みの ID・パスワードを返す（無ければ None）"""
    import os
    c = load_conf()
    uid = os.environ.get("REITAKU_ID") or c.get("user_id")
    pw = os.environ.get("REITAKU_PW") or (get_password(uid) if uid else None)
    return uid, (pw.strip() if pw else None)


def fetch(show=False, uid=None, pw=None, log=print):
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    if not uid or not pw:
        u2, p2 = get_credentials()
        uid, pw = uid or u2, pw or p2
    if not uid:
        raise RuntimeError("User ID が未設定です。先に設定してください")
    if not pw:
        pw = getpass.getpass("ポータルのパスワード: ").strip()
    if not pw.isascii():
        raise RuntimeError("パスワードに全角文字が入っています。日本語入力をオフにして入れ直してください")

    DEBUG.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not show)
        ctx = browser.new_context(**p.devices["Pixel 7"], locale="ja-JP", timezone_id="Asia/Tokyo")
        page = ctx.new_page()
        page.set_default_timeout(20000)
        try:
            log("ポータルを開いています…")
            page.goto(PORTAL_URL)
            # スマホ版のログイン画面に切り替える（課題一覧が読みやすい）
            try:
                page.get_by_text(re.compile("スマートフォンはこちら")).first.click(timeout=4000)
                page.wait_for_load_state("networkidle")
            except PWTimeout:
                pass
            uid_box = page.get_by_placeholder(re.compile("User ?ID", re.I)).first
            pw_box = page.get_by_placeholder(re.compile("Password", re.I)).first
            uid_box.click()
            uid_box.fill("")
            uid_box.press_sequentially(uid, delay=40)
            pw_box.click()
            pw_box.fill("")
            pw_box.press_sequentially(pw, delay=40)
            if len(pw_box.input_value()) != len(pw):
                raise RuntimeError("パスワード欄に正しく入力できませんでした。ログインボタンは押していません")
            log("ログインしています…")
            page.get_by_role("button", name=re.compile("LOGIN", re.I)).or_(
                page.get_by_text("LOGIN", exact=True)).first.click()
            ok = page.get_by_text(re.compile("ポータルトップ|期限あり|ログアウト|logout|掲示")).first
            try:
                ok.wait_for(state="visible", timeout=30000)
            except PWTimeout:
                page.screenshot(path=str(DEBUG / "login.png"), full_page=True)
                msg = ""
                for sel in (".ui-messages-error", ".ui-growl-message", "[class*=error]", "[class*=message]"):
                    try:
                        if page.locator(sel).count():
                            msg = page.locator(sel).first.inner_text().strip()
                            if msg:
                                break
                    except Exception:
                        pass
                raise RuntimeError("ログインできませんでした。" + (msg or "") +
                                   "\n※10回失敗するとロックされるので、パスワードを確認してから再実行してください")
            page.wait_for_timeout(1000)

            log("課題一覧を開いています…")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)
            link = page.get_by_text(re.compile(r"課題\s*\d+\s*件")).first
            opened = False
            for attempt in range(4):
                if link.count() and link.is_visible():
                    opened = True
                    break
                head = page.get_by_text("期限あり", exact=True).first
                try:
                    if attempt % 2 == 0:
                        head.tap(timeout=5000)      # スマホ版はタップで開く
                    else:
                        head.click(timeout=5000)
                except Exception:
                    pass
                try:
                    link.wait_for(state="visible", timeout=6000)
                except PWTimeout:
                    # 通信エラーが出たら、ポータルトップを開き直してやり直す
                    if page.get_by_text("通信中にエラー").count():
                        log("ポータルの通信エラー。やり直しています…")
                        page.reload()
                        page.wait_for_load_state("networkidle")
                        page.wait_for_timeout(2000)
            if opened:
                link.click()
                page.wait_for_load_state("networkidle")
                try:
                    page.get_by_text(re.compile(r"期限\s*[:：]")).first.wait_for(state="visible", timeout=10000)
                except PWTimeout:
                    pass
                page.wait_for_timeout(800)
            elif page.get_by_text("期限あり", exact=True).count():
                log("「期限あり」に課題がありません")

            text = page.inner_text("body")
            for f in page.frames[1:]:
                try:
                    text += "\n" + f.inner_text("body")
                except Exception:
                    pass
            (DEBUG / "last_page.txt").write_text(text, encoding="utf-8")
            found = parse(text)
            if not found:
                page.screenshot(path=str(DEBUG / "no_items.png"), full_page=True)
                log("締切つきの課題は見つかりませんでした（画面は debug フォルダに保存）")
            return found
        except Exception:
            try:
                page.screenshot(path=str(DEBUG / "error.png"), full_page=True)
            except Exception:
                pass
            raise
        finally:
            browser.close()


# ---------------- 通知 ----------------
def notify(title, message):
    c = load_conf()
    sent = False
    topic = os.environ.get("NTFY_TOPIC") or c.get("ntfy_topic")
    if topic:
        try:
            import requests
            requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
                          headers={"Title": title.encode("utf-8"), "Tags": "books"}, timeout=10)
            sent = True
        except Exception as e:
            print("スマホ通知に失敗:", e)
    try:
        from plyer import notification
        notification.notify(title=title, message=message[:250], app_name="ReiMind", timeout=10)
        sent = True
    except Exception:
        pass
    if not sent and not os.environ.get("CI"):  # GitHub のログは公開されるので課題名を出さない
        print(f"[通知] {title}\n{message}")


def remind(items, added):
    c = load_conf()
    hours = c.get("notify_hours", 48)
    now = dt.datetime.now(JST)
    lines = []
    for it in sorted(items, key=lambda x: x["due"]):
        if it.get("done"):
            continue
        left = (due_dt(it) - now).total_seconds() / 3600
        if left < -24:
            continue
        # 段階ごとに1回だけ通知（新着／48時間前／24時間前／3時間前）
        reached = [s for s, th in (("3h", 3), ("24h", 24), (f"{hours}h", hours)) if left <= th]
        done_stages = it.setdefault("notified", [])
        stage = "new" if it in added else (reached[0] if reached else None)
        if stage and stage not in done_stages:
            done_stages.extend(s for s in [stage] + reached if s not in done_stages)
            mark = "🆕" if stage == "new" else "⏰"
            lines.append(f"{mark} {fmt(it)}（{remain(it, now)}）\n{it['title']}")
    if lines:
        notify(f"ReiMind（{len(lines)}件）", "\n\n".join(lines))
    return len(lines)


# ---------------- カレンダー ----------------
def write_ics(items):
    def f(d):
        return d.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def e(s):
        return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

    now = dt.datetime.now(JST)
    ev = []
    for it in items:
        if it.get("done") or due_dt(it) < now:
            continue
        end = due_dt(it)
        uid = re.sub(r"[^0-9A-Za-z]", "", key_of(it).encode("utf-8").hex())[:40]
        ev += ["BEGIN:VEVENT", f"UID:{uid}@reitaku-kadai", f"DTSTAMP:{f(now)}",
               f"DTSTART:{f(end - dt.timedelta(minutes=30))}", f"DTEND:{f(end)}",
               f"SUMMARY:{e('【締切】' + it['title'])}",
               f"DESCRIPTION:{e(it.get('teacher', '') + chr(10) + PORTAL_URL)}",
               "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{e('明日締切：' + it['title'])}",
               "TRIGGER:-P1D", "END:VALARM",
               "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{e('あと3時間：' + it['title'])}",
               "TRIGGER:-PT3H", "END:VALARM", "END:VEVENT"]
    path = BASE / "kadai.ics"
    path.write_text("\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//reitaku-kadai//JA"]
                                + ev + ["END:VCALENDAR"]) + "\r\n", encoding="utf-8")
    return path, len(ev) // 16



# ---------------- クラウド（GitHub Actions）用 ----------------
def encrypt_json(obj, passphrase):
    """合言葉で暗号化（PBKDF2-SHA256 + AES-GCM）。ブラウザの WebCrypto で復号できる形式"""
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    salt, iv, it = os.urandom(16), os.urandom(12), 200_000
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=it).derive(passphrase.encode())
    ct = AESGCM(key).encrypt(iv, json.dumps(obj, ensure_ascii=False).encode(), None)
    b = lambda x: base64.b64encode(x).decode()
    return {"v": 1, "iter": it, "salt": b(salt), "iv": b(iv), "ct": b(ct)}


def cloud(out_path):
    """取得 → 通知 → 暗号化した data.json を書き出す。失敗しても前回の課題は残す"""
    passphrase = os.environ.get("KADAI_PASSPHRASE", "")
    if len(passphrase) < 6:
        sys.exit("KADAI_PASSPHRASE（合言葉、6文字以上）を設定してください")
    _STATE.mkdir(parents=True, exist_ok=True)
    lock = _STATE / "LOGIN_FAILED"
    items = load_items()
    error = ""
    if lock.exists() and os.environ.get("FORCE") != "1":
        error = ("前回ログインに失敗したため、自動更新を止めています（10回失敗するとロックされるため）。"
                 "パスワードを確認してから、Actions で手動実行してください")
        print(error)
    else:
        try:
            found = fetch()
            lock.unlink(missing_ok=True)
            added = merge(items, found)
            print(f"ポータルの課題 {len(found)}件 ／ 新しく追加 {len(added)}件")
            n = remind(items, added)
            print(f"{n}件を通知")
        except Exception as e:
            error = str(e)
            print("取得に失敗:", error)
            if "ログインできませんでした" in error:
                lock.write_text(error, encoding="utf-8")
                notify("ReiMind：ログイン失敗", "自動更新を止めました。パスワードを確認してください")
    # 終わった課題は1週間で整理
    now = dt.datetime.now(JST)
    items = [i for i in items if (due_dt(i) - now).total_seconds() > -7 * 86400]
    save_items(items)
    payload = {
        "updated": now.isoformat(timespec="minutes"),
        "error": error,
        "items": [{"id": key_of(i), "title": i["title"], "teacher": i.get("teacher", ""), "due": i["due"]}
                  for i in sorted(items, key=lambda x: x["due"])],
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(encrypt_json(payload, passphrase)), encoding="utf-8")
    print("書き出し:", out)


# ---------------- 表示 ----------------
def show_list(items):
    now = dt.datetime.now(JST)
    todo = sorted([i for i in items if not i.get("done")], key=lambda x: x["due"])
    if not todo:
        print("未完了の課題はありません")
    for n, it in enumerate(todo, 1):
        t = f" ／ {it['teacher']}" if it.get("teacher") else ""
        print(f"{n:2}. [{remain(it, now):>6}] {fmt(it)}  {it['title']}{t}")
    return todo


def main():
    ap = argparse.ArgumentParser(description="麗澤ポータル 課題リマインダー")
    ap.add_argument("--setup", action="store_true", help="ID・パスワード・通知の設定")
    ap.add_argument("--show", action="store_true", help="ブラウザを表示して取得")
    ap.add_argument("--text", metavar="FILE", help="コピーした文章から読み込む（- で標準入力）")
    ap.add_argument("--list", action="store_true", help="一覧を表示")
    ap.add_argument("--done", type=int, metavar="N", help="一覧のN番を完了にする")
    ap.add_argument("--ics", action="store_true", help="kadai.ics を書き出す")
    ap.add_argument("--cloud", metavar="OUT", help="GitHub Actions 用：取得して暗号化した data.json を書き出す")
    a = ap.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if a.cloud:
        return cloud(a.cloud)
    if a.setup:
        return setup()
    items = load_items()

    if a.done:
        todo = sorted([i for i in items if not i.get("done")], key=lambda x: x["due"])
        if not 1 <= a.done <= len(todo):
            sys.exit("番号が正しくありません（--list で確認してください）")
        todo[a.done - 1]["done"] = True
        save_items(items)
        print("完了にしました:", todo[a.done - 1]["title"])
        return
    if a.list:
        show_list(items)
        return
    if a.ics:
        path, n = write_ics(items)
        print(f"{n}件を書き出しました: {path}")
        return

    if a.text:
        text = sys.stdin.read() if a.text == "-" else Path(a.text).read_text(encoding="utf-8")
        found = parse(text)
    else:
        try:
            found = fetch(show=a.show)
        except RuntimeError as e:
            sys.exit(str(e))

    added = merge(items, found)
    print(f"ポータルの課題 {len(found)}件 ／ 新しく追加 {len(added)}件")
    n = remind(items, added)
    save_items(items)
    if n:
        print(f"{n}件を通知しました")
    show_list(items)


if __name__ == "__main__":
    main()
