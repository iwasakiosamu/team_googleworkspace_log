# check_terms.py 仕様書

## 概要

Google Workspace / Google Cloud の規約ページを定期的に監視し、前回のスナップショットと比較して変更点を検出するPythonスクリプト。変更結果はMarkdownレポートとして出力され、GitHubへの自動pushおよびmacOS通知も行う。

---

## 監視対象ページ

| キー | ページ名 | URL |
|---|---|---|
| `user_features` | Google Workspace サービスの概要 | https://workspace.google.com/terms/user_features/ |
| `service_terms` | Google Workspace サービス固有の利用規約 | https://workspace.google.com/intl/ja/terms/service-terms/ |
| `premier_terms` | Google Cloud 利用規約 | https://workspace.google.com/intl/ja/terms/premier_terms/ |
| `gen_ai_indemnified` | 生成AI補償対象サービス | https://cloud.google.com/terms/generative-ai-indemnified-services |

各ターゲットには `content_start_marker`（正規表現）が定義されており、ページ本文の開始位置を特定するために使用される。

---

## ディレクトリ構成

```
ai-news-collector/
├── check_terms.py          # 本スクリプト
├── terms_snapshots/        # スナップショット保存先
│   ├── <key>.txt           # 各ページのテキストスナップショット
│   └── metadata.json       # メタデータ（更新日、ハッシュ等）
├── terms_reports/
│   └── README.md           # 生成されたレポート
└── logs/
    └── check_terms.log     # 実行ログ
```

---

## 処理フロー

```
1. 初期化（ディレクトリ作成、メタデータ読み込み）
       ↓
2. 各監視対象ページに対してループ処理
   2-1. curl でページHTML取得
   2-2. HTMLをプレーンテキストに変換
   2-3. メインコンテンツ抽出（ナビ・フッター除去）
   2-4. 最終更新日の抽出
   2-5. コンテンツハッシュ計算（SHA-256 先頭16文字）
   2-6. 前回のハッシュと比較
        - 一致 → 変更なし
        - 不一致 → 詳細差分を生成
   2-7. スナップショット保存・メタデータ更新
       ↓
3. Markdownレポート生成・保存
       ↓
4. GitHubへ自動push
       ↓
5. macOS通知送信
       ↓
6. ログ記録
```

---

## 主要機能の詳細

### 1. ページ取得 (`fetch_page`)

- **方式:** `curl` コマンドによるHTTP GET
- **理由:** macOSのSSL証明書問題を回避するため、Pythonの `urllib` / `requests` ではなく `curl` を使用
- **オプション:**
  - `-s`: サイレントモード
  - `-L`: リダイレクト追従
  - `--max-time 30`: タイムアウト30秒
  - `User-Agent`: Chrome on macOSを偽装
  - `Accept-Language: ja,en;q=0.9`: 日本語ページを優先取得

### 2. HTML→テキスト変換 (`HTMLTextExtractor`, `html_to_text`)

- Pythonの `html.parser.HTMLParser` を継承したカスタムパーサー
- **除外タグ:** `script`, `style`, `noscript`, `svg`, `path`
- **ブロックタグ:** `p`, `br`, `div`, `h1`〜`h6`, `li`, `tr`, `td`, `th`, `dt`, `dd`, `blockquote`, `section`, `article`, `main`, `aside`
  - ブロックタグの前後に改行を挿入してテキストの可読性を確保
- 連続する空白・改行を正規化

### 3. メインコンテンツ抽出 (`extract_main_content`)

- `content_start_marker` の正規表現でメインコンテンツの開始位置を特定
- マーカーが見つからない場合はテキスト全体の後半75%を使用（フォールバック）
- **フッター除去:** 以下のパターンに一致する部分以降を除去
  - `概要|ビジネス向け|料金|営業への問い合わせ` + `Google について|プライバシー`
  - `Google Cloud について`
  - `登録して最新情報をお届け`
- 5文字未満の重複行を除去（メニュー項目等のノイズ対策）

### 4. 更新日抽出 (`extract_update_date`)

以下のパターンを優先順に検索：

1. `最終更新日：2025年12月10日` 形式（日本語）
2. `2025年12月10日に更新` 形式（日本語）
3. `2025年12月10日` 形式（ページ冒頭の日付）
4. `Last modified: January 19, 2026` 形式（英語）
5. `January 19, 2026` 形式（英語）

一致しない場合は `"不明"` を返す。

### 5. 変更検知の仕組み

**2段階の比較方式:**

1. **ハッシュ比較（高速）:** SHA-256ハッシュ（先頭16文字）で前回と比較。一致すれば即座に「変更なし」と判定
2. **テキスト差分（詳細）:** ハッシュが異なる場合、`difflib.unified_diff` で行単位の差分を生成

**変更ステータス:**

| ステータス | 条件 | 表示 |
|---|---|---|
| `initial` | 前回のスナップショットが存在しない | 📋 初回チェック |
| `unchanged` | ハッシュ一致 or 差分なし | ✅ 変更なし |
| `changed`（更新日変更あり） | 更新日が前回と異なる | 🔴 更新日が変更されました |
| `changed`（更新日同一） | コンテンツのみ変更 | 🟡 コンテンツに変更を検出 |

### 6. 差分レポート (`generate_diff_report`)

- `difflib.unified_diff` で行単位の差分を取得
- 削除行は `~~取り消し線~~` で表示（最大30行）
- 追加行は引用ブロック `>` で表示（最大30行）
- 30行を超える場合は残余行数を表示

### 7. レポート出力

- **保存先:** `terms_reports/README.md`
- **形式:** Markdown
- **構成:**
  - タイトルとチェック日時
  - 変更サマリー（全ページの結果一覧）
  - 各ページの詳細（URL、更新日、前回情報、ハッシュ、差分）

### 8. GitHub自動push (`push_to_github`)

- `terms_reports/` ディレクトリ内で `git add` → `git commit` → `git push origin main`
- コミットメッセージ:
  - 変更あり: `🔴 規約変更検知レポート: YYYY-MM-DD`
  - 変更なし: `✅ 規約チェックレポート: YYYY-MM-DD（変更なし）`
- コミットする変更がない場合はスキップ

### 9. macOS通知 (`send_notification`)

- `osascript` コマンドで macOS のネイティブ通知を送信
- 通知内容は結果に応じて3パターン:
  - 変更検知時: `📋 規約変更検知`
  - エラー発生時: `⚠️ 規約チェックエラー`
  - 変更なし: `✅ 規約チェック完了`

### 10. ログ記録

- **保存先:** `logs/check_terms.log`
- **形式:** `[ISO8601タイムスタンプ] チェック完了 — 変更: True/False, エラー: True/False`
- 追記モード（`append`）

---

## スナップショット・メタデータ

### スナップショット (`terms_snapshots/<key>.txt`)

各ページのメインコンテンツをプレーンテキストとして保存。次回実行時の差分比較に使用。

### メタデータ (`terms_snapshots/metadata.json`)

```json
{
  "<key>": {
    "last_update_date": "ページの最終更新日",
    "content_hash": "SHA-256ハッシュ先頭16文字",
    "last_checked": "最終チェック日時（YYYY年MM月DD日 HH:MM）"
  }
}
```

---

## 依存関係

### 標準ライブラリのみ（外部パッケージ不要）

| モジュール | 用途 |
|---|---|
| `re` | 正規表現（マーカー検索、更新日抽出、テキスト整形） |
| `os` | ファイル・ディレクトリ操作 |
| `sys` | 終了コード制御 |
| `json` | メタデータの読み書き |
| `difflib` | テキスト差分生成 |
| `hashlib` | SHA-256ハッシュ計算 |
| `subprocess` | curl実行、git操作、osascript通知 |
| `datetime` | 日時取得・フォーマット |
| `html.parser` | HTML解析 |

### 外部コマンド

| コマンド | 用途 |
|---|---|
| `curl` | Webページ取得 |
| `git` | GitHub自動push |
| `osascript` | macOS通知送信 |

---

## 終了コード

| コード | 意味 |
|---|---|
| `0` | 正常終了（エラーなし） |
| `1` | 一部のページ取得でエラーが発生 |

---

## 実行方法

```bash
python3 check_terms.py
```

### 自動実行（launchd）

`setup_terms_checker.sh` により macOS の `launchd` で毎朝10時に自動実行されるよう設定可能。
