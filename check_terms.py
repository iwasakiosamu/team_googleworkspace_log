#!/usr/bin/env python3
"""
Google Workspace / Cloud 規約変更監視スクリプト

対象URLのページを取得し、前回のスナップショットと比較して
変更点をMarkdownレポートとして出力する。
"""

import re
import os
import sys
import json
import difflib
import hashlib
import subprocess
from datetime import datetime
from html.parser import HTMLParser

# =============================================================================
# 設定
# =============================================================================

TARGETS = {
    "user_features": {
        "url": "https://workspace.google.com/terms/user_features/",
        "name": "Google Workspace サービスの概要",
        "content_start_marker": r"(Google Workspace サービスの概要|サービスの概要)",
    },
    "service_terms": {
        "url": "https://workspace.google.com/intl/ja/terms/service-terms/",
        "name": "Google Workspace サービス固有の利用規約",
        "content_start_marker": r"(サービス固有の利用規約)",
    },
    "premier_terms": {
        "url": "https://workspace.google.com/intl/ja/terms/premier_terms/",
        "name": "Google Cloud 利用規約",
        "content_start_marker": r"(Google Cloud 利用規約|利用規約が変更されました)",
    },
    "gen_ai_indemnified": {
        "url": "https://cloud.google.com/terms/generative-ai-indemnified-services",
        "name": "生成AI補償対象サービス",
        "content_start_marker": r"(Generative AI Indemnified|生成 AI|Indemnified Services)",
    },
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_DIR = os.path.join(BASE_DIR, "terms_snapshots")
REPORT_DIR = os.path.join(BASE_DIR, "terms_reports")
LOG_DIR = os.path.join(BASE_DIR, "logs")


# =============================================================================
# HTML → テキスト変換
# =============================================================================

class HTMLTextExtractor(HTMLParser):
    """HTMLからテキストを抽出（script/style/navを除外）"""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "path"}
    BLOCK_TAGS = {"p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                  "li", "tr", "td", "th", "dt", "dd", "blockquote", "section",
                  "article", "main", "aside"}

    def __init__(self):
        super().__init__()
        self.result = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
        if tag in self.BLOCK_TAGS and not self.skip_depth:
            self.result.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self.skip_depth > 0:
            self.skip_depth -= 1
        if tag in self.BLOCK_TAGS and not self.skip_depth:
            self.result.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.result.append(data)

    def get_text(self):
        raw = "".join(self.result)
        # 連続空白と改行を整理
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


# =============================================================================
# ページ取得・解析
# =============================================================================

def fetch_page(url):
    """curlコマンドでURLからHTMLを取得（macOS SSL証明書問題を回避）"""
    result = subprocess.run(
        [
            "curl", "-s", "-L",
            "--max-time", "30",
            "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
            "-H", "Accept-Language: ja,en;q=0.9",
            "-H", "Accept: text/html,application/xhtml+xml",
            url,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed (exit {result.returncode}): {result.stderr.strip()}")
    if not result.stdout:
        raise RuntimeError("curl returned empty response")
    return result.stdout


def html_to_text(html):
    """HTMLをプレーンテキストに変換"""
    extractor = HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


def extract_main_content(full_text, start_marker):
    """ナビゲーション等を除外し、メインコンテンツ部分のみ抽出"""
    match = re.search(start_marker, full_text)
    if match:
        content = full_text[match.start():]
    else:
        # マーカーが見つからない場合、テキスト全体の後半を使用
        lines = full_text.split("\n")
        mid = len(lines) // 4
        content = "\n".join(lines[mid:])

    # フッターを除去（よくあるフッターパターン）
    footer_patterns = [
        r"\n(概要|ビジネス向け|料金|営業への問い合わせ)\n.*(Google について|プライバシー)",
        r"\nGoogle Cloud について\n",
        r"\n登録して最新情報をお届け\n",
    ]
    for pattern in footer_patterns:
        footer_match = re.search(pattern, content, re.DOTALL)
        if footer_match:
            content = content[:footer_match.start()]

    # 各行をトリムして空行を整理
    lines = [line.strip() for line in content.split("\n")]
    lines = [line for line in lines if line]

    # 重複する短い行（メニュー項目など）を除去
    seen = set()
    unique_lines = []
    for line in lines:
        if len(line) < 5 and line in seen:
            continue
        seen.add(line)
        unique_lines.append(line)

    return "\n".join(unique_lines)


def extract_update_date(text):
    """ページテキストから最終更新日を抽出"""
    patterns = [
        # 日本語: 2025 年 12 月 10 日
        r"(?:最終更新日|最終変更日|更新日)[：:\s]*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)",
        r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*(?:に更新|更新)",
        # ページ冒頭の日付（これらのページでは更新日が冒頭に出る）
        r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)",
        # 英語: January 19, 2026 / Jan 19, 2026
        r"(?:Last (?:modified|updated))[:\s]*((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s*\d{4})",
        r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s*\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return "不明"


def compute_hash(text):
    """テキストのハッシュを計算"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# =============================================================================
# スナップショット管理
# =============================================================================

def load_snapshot(key):
    """前回のスナップショットを読み込む"""
    path = os.path.join(SNAPSHOT_DIR, f"{key}.txt")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return None


def save_snapshot(key, text):
    """スナップショットを保存"""
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = os.path.join(SNAPSHOT_DIR, f"{key}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def load_metadata():
    """メタデータ（前回の更新日等）を読み込む"""
    path = os.path.join(SNAPSHOT_DIR, "metadata.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_metadata(metadata):
    """メタデータを保存"""
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = os.path.join(SNAPSHOT_DIR, "metadata.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)


# =============================================================================
# 差分生成
# =============================================================================

def generate_diff_report(old_text, new_text, max_lines=30):
    """前回と今回のテキストを比較し、変更箇所をMarkdown形式で返す"""
    if old_text is None:
        return "initial", "（初回チェック — ベースラインを保存しました）"

    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))

    if not diff:
        return "unchanged", None

    added = []
    removed = []
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            text = line[1:].strip()
            if text:
                added.append(text)
        elif line.startswith("-") and not line.startswith("---"):
            text = line[1:].strip()
            if text:
                removed.append(text)

    parts = []
    if removed:
        parts.append("**🔻 削除・変更前の内容:**")
        for line in removed[:max_lines]:
            parts.append(f"> ~~{line}~~")
        if len(removed) > max_lines:
            parts.append(f"> ... 他 {len(removed) - max_lines} 行")

    if added:
        parts.append("")
        parts.append("**🔺 追加・変更後の内容:**")
        for line in added[:max_lines]:
            parts.append(f"> {line}")
        if len(added) > max_lines:
            parts.append(f"> ... 他 {len(added) - max_lines} 行")

    return "changed", "\n".join(parts)


# =============================================================================
# GitHub 自動 push
# =============================================================================

def push_to_github(report_path, today, has_changes):
    """レポートを GitHub に自動 push"""
    try:
        report_filename = os.path.basename(report_path)

        # git add
        subprocess.run(
            ["git", "add", report_filename],
            cwd=REPORT_DIR, capture_output=True, text=True, check=True,
        )

        # 変更があるか確認（git diff --cached）
        diff_result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=REPORT_DIR, capture_output=True,
        )
        if diff_result.returncode == 0:
            print("📦 Git: コミットする変更はありません")
            return

        # コミットメッセージ
        if has_changes:
            msg = f"🔴 規約変更検知レポート: {today}"
        else:
            msg = f"✅ 規約チェックレポート: {today}（変更なし）"

        # git commit
        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=REPORT_DIR, capture_output=True, text=True, check=True,
        )

        # git push
        result = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=REPORT_DIR, capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"📦 Git: GitHub に push しました — {msg}")
        else:
            print(f"⚠️ Git push エラー: {result.stderr.strip()}")

    except Exception as e:
        print(f"⚠️ Git 操作エラー: {e}")


# =============================================================================
# 通知
# =============================================================================

def send_notification(title, message):
    """macOSの通知を送信"""
    try:
        escaped_msg = message.replace('"', '\\"').replace("'", "\\'")
        escaped_title = title.replace('"', '\\"').replace("'", "\\'")
        subprocess.run([
            "osascript", "-e",
            f'display notification "{escaped_msg}" with title "{escaped_title}"'
        ], check=True, capture_output=True)
    except Exception:
        pass


# =============================================================================
# メイン処理
# =============================================================================

def main():
    os.makedirs(REPORT_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    metadata = load_metadata()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    now_str = now.strftime("%Y年%m月%d日 %H:%M")

    # レポート構築
    report = []
    summary = []
    has_changes = False
    has_errors = False

    report.append(f"# 📋 Google 規約変更チェックレポート")
    report.append(f"")
    report.append(f"**チェック日時:** {now_str}")
    report.append(f"")

    # サマリーセクション（後で挿入するため位置を記録）
    summary_index = len(report)
    report.append("")  # プレースホルダー

    report.append("---")
    report.append("")

    for key, config in TARGETS.items():
        url = config["url"]
        name = config["name"]
        marker = config["content_start_marker"]

        report.append(f"## 📄 {name}")
        report.append(f"")
        report.append(f"**URL:** [{url}]({url})")
        report.append(f"")

        try:
            # ページ取得
            html = fetch_page(url)
            full_text = html_to_text(html)
            content = extract_main_content(full_text, marker)
            current_date = extract_update_date(content)
            content_hash = compute_hash(content)

            prev_date = metadata.get(key, {}).get("last_update_date", "—")
            prev_hash = metadata.get(key, {}).get("content_hash", "")
            prev_checked = metadata.get(key, {}).get("last_checked", "—")

            report.append(f"| 項目 | 値 |")
            report.append(f"|---|---|")
            report.append(f"| 最終更新日 | {current_date} |")
            report.append(f"| 前回確認時の更新日 | {prev_date} |")
            report.append(f"| 前回チェック日 | {prev_checked} |")
            report.append(f"| コンテンツハッシュ | `{content_hash}` |")
            report.append(f"")

            # ハッシュで高速比較
            if prev_hash and prev_hash == content_hash:
                report.append("✅ **変更なし**")
                summary.append(f"- ✅ **{name}** — 変更なし（更新日: {current_date}）")
            else:
                # 詳細差分
                prev_text = load_snapshot(key)
                status, diff_text = generate_diff_report(prev_text, content)

                if status == "initial":
                    report.append(f"📋 {diff_text}")
                    summary.append(f"- 📋 **{name}** — 初回チェック（更新日: {current_date}）")
                    has_changes = True
                elif status == "unchanged":
                    report.append("✅ **変更なし**")
                    summary.append(f"- ✅ **{name}** — 変更なし（更新日: {current_date}）")
                else:
                    has_changes = True
                    date_changed = (prev_date != "—") and (current_date != prev_date)
                    if date_changed:
                        report.append(f"🔴 **更新日が変更されました！** `{prev_date}` → `{current_date}`")
                        summary.append(
                            f"- 🔴 **{name}** — 更新あり！ "
                            f"`{prev_date}` → `{current_date}`"
                        )
                    else:
                        report.append(f"🟡 **コンテンツに変更を検出**（更新日は同一: {current_date}）")
                        summary.append(
                            f"- 🟡 **{name}** — コンテンツ変更あり（更新日: {current_date}）"
                        )

                    report.append("")
                    report.append(diff_text)

            # スナップショット保存
            save_snapshot(key, content)

            # メタデータ更新
            if key not in metadata:
                metadata[key] = {}
            metadata[key]["last_update_date"] = current_date
            metadata[key]["content_hash"] = content_hash
            metadata[key]["last_checked"] = now_str

        except Exception as e:
            has_errors = True
            report.append(f"❌ **取得エラー:** `{e}`")
            summary.append(f"- ❌ **{name}** — 取得エラー: {e}")

        report.append("")
        report.append("---")
        report.append("")

    # サマリーを挿入
    summary_header = "## 📊 変更サマリー\n\n"
    if not has_changes and not has_errors:
        summary_header += "> すべてのページに変更はありませんでした。\n\n"
    elif has_changes:
        summary_header += "> ⚠️ **変更が検出されました。** 以下の詳細を確認してください。\n\n"
    summary_text = summary_header + "\n".join(summary) + "\n"
    report[summary_index] = summary_text

    # レポート保存
    report_path = os.path.join(REPORT_DIR, "README.md")
    report_content = "\n".join(report)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)

    # メタデータ保存
    save_metadata(metadata)

    # GitHub に自動 push
    push_to_github(report_path, today, has_changes)

    # macOS通知
    if has_changes:
        send_notification(
            "📋 規約変更検知",
            "Google規約に変更が検出されました。レポートを確認してください。"
        )
    elif has_errors:
        send_notification(
            "⚠️ 規約チェックエラー",
            "一部のページの取得に失敗しました。"
        )
    else:
        send_notification(
            "✅ 規約チェック完了",
            "すべてのページに変更はありませんでした。"
        )

    # ログ出力
    log_path = os.path.join(LOG_DIR, "check_terms.log")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{now.isoformat()}] チェック完了 — 変更: {has_changes}, エラー: {has_errors}\n")

    print(f"レポート保存先: {report_path}")
    print("")
    print(report_content)

    return 0 if not has_errors else 1


if __name__ == "__main__":
    sys.exit(main())
