# ReiMind（レイマインド）

麗澤ポータルの課題リマインダー（スマホ版）

GitHub Actions が数時間おきに麗澤ポータルから課題を取得し、GitHub Pages のスマホ用アプリ（PWA）に表示します。
課題データは合言葉で暗号化して公開するので、合言葉を知らない人には読めません。

## 必要な Secrets（Settings → Secrets and variables → Actions）

| 名前 | 中身 |
|---|---|
| `REITAKU_ID` | ポータルの User ID |
| `REITAKU_PW` | ポータルのパスワード |
| `KADAI_PASSPHRASE` | スマホで入れる合言葉（6文字以上。ポータルのパスワードとは別のもの） |
| `NTFY_TOPIC` | （任意）ntfy の通知用トピック名 |

## 安全のための仕組み

- ログインに失敗すると自動更新を止めます（10回失敗でアカウントがロックされるため）。
  パスワードを直したら Actions →「課題を更新」→ Run workflow で `force` にチェックを入れて実行してください。
- 課題名は Actions のログに出しません。
