"""既存日記のタグを新タクソノミーに合わせて一括再マッピングする（1回限りの移行用）。

背景:
    タグは reason＋全ノートの `@タグ` の和集合から `_sync_hashtag_tags` で同期される。
    tag_master.md の親子見直し（@半導体→細分 等）に伴い、既に記録済みの日記の
    @タグを「reason・ノート横断でトークン単位に」置換して付け替える。

安全設計:
    - 既定は dry-run（--apply で初めて保存）。
    - 置換は @タグの語彙クラスに厳密一致（`@半導体` は `@半導体製造装置` の一部に誤マッチしない）。
    - reason と各ノートの本文を書き換えたうえで `_sync_hashtag_tags` を呼び、
      Tag M2M を再同期する（テキストが正、次回同期で戻らない）。
    - 対象ユーザーは既定 settings.ANALYSIS_API_USER（未設定なら --username 必須）。

使い方:
    python manage.py remap_diary_tags                 # dry-run（変更内容を表示）
    python manage.py remap_diary_tags --apply         # 実行
    python manage.py remap_diary_tags --username naotaro --apply
"""
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

# 承認済みマッピング（銘柄コード → [(旧タグ, 新タグ or None=削除), ...]）
REMAP: dict[str, list[tuple[str, str | None]]] = {
    '6758': [('半導体', 'イメージセンサー')],
    '8035': [('半導体', '半導体製造装置')],
    '6857': [('半導体', '半導体製造装置')],
    '7751': [('半導体', '半導体製造装置')],
    '4063': [('半導体', '半導体材料')],
    '4078': [('半導体', '半導体材料')],
    '4216': [('半導体', '半導体材料')],
    '6971': [('半導体', '半導体材料')],
    '1414': [('国土強靭化', None)],          # 親子重複解消（子=インフラ老朽化/建設補修 を残す）
    'CRWD': [('サイバーセキュリティ', None)],  # 親子重複解消（子=AIセキュリティ を残す）
}

# extract_hashtags_with_direction と同じ語彙クラス＋方向矢印
_TAG_TOKEN = re.compile(r'@([぀-ゟ゠-ヿ一-鿿ｦ-ﾟa-zA-Z0-9_&]+)([↑↓→]?)')


_TAG_CLASS = r'[぀-ゟ゠-ヿ一-鿿ｦ-ﾟa-zA-Z0-9_&]'


def _replace_token(text: str, old: str, new: str | None) -> str:
    """text 中の `@old`（厳密一致）を `@new` に置換、new=None なら削除。

    厳密一致：`@半導体` は `@半導体製造装置` の一部に誤マッチしない。
    削除時：バッククォート囲み・直後の空白1つも併せて除去し、跡を残さない。
    """
    if not text:
        return text
    esc = re.escape(old)
    if new is None:
        # バッククォート囲み（タグ行 `@old`）を丸ごと
        text = re.sub(r'`@' + esc + r'[↑↓→]?`[ \t　]?', '', text)
        # 素の @old（直後がタグ文字でない＝厳密一致）＋直後の空白1つ
        text = re.sub(r'@' + esc + r'[↑↓→]?(?!' + _TAG_CLASS + r')[ \t　]?', '', text)
        return text

    def repl(m):
        name, arrow = m.group(1), m.group(2)
        return f'@{new}{arrow}' if name == old else m.group(0)

    return _TAG_TOKEN.sub(repl, text)


class Command(BaseCommand):
    help = '既存日記のタグを新タクソノミーへ一括再マッピングする（既定 dry-run）。'

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, default=None,
                            help='対象ユーザー名（既定: settings.ANALYSIS_API_USER）')
        parser.add_argument('--apply', action='store_true',
                            help='実際に保存する（未指定は dry-run）')

    def handle(self, *args, **options):
        from stockdiary.models import StockDiary, DiaryNote
        from stockdiary.views import _sync_hashtag_tags

        username = options['username'] or getattr(settings, 'ANALYSIS_API_USER', '')
        if not username:
            raise CommandError('--username を指定するか settings.ANALYSIS_API_USER を設定してください。')
        User = get_user_model()
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f'ユーザー "{username}" が見つかりません。')

        apply = options['apply']
        mode = 'APPLY' if apply else 'DRY-RUN'
        self.stdout.write(f'== タグ再マッピング [{mode}] user={username} ==')

        changed = 0
        for symbol, rules in REMAP.items():
            diaries = StockDiary.objects.filter(user=user, stock_symbol=symbol)
            if not diaries:
                self.stdout.write(self.style.WARNING(f'  ! {symbol}: 該当日記なし（スキップ）'))
                continue
            for diary in diaries:
                before = sorted(diary.tags.values_list('name', flat=True))
                notes = list(DiaryNote.objects.filter(diary=diary))

                new_reason = diary.reason or ''
                note_updates = {}
                for old, new in rules:
                    new_reason = _replace_token(new_reason, old, new)
                    for n in notes:
                        updated = _replace_token(note_updates.get(n.id, n.content or ''), old, new)
                        note_updates[n.id] = updated

                reason_dirty = new_reason != (diary.reason or '')
                dirty_notes = [n for n in notes if note_updates.get(n.id) != (n.content or '')]

                if not reason_dirty and not dirty_notes:
                    self.stdout.write(f'  - {symbol} {diary.stock_name}: テキストに該当@タグ無し（変更なし）')
                    continue

                rule_str = ', '.join(f'@{o}→{"(削除)" if nw is None else "@"+nw}' for o, nw in rules)
                self.stdout.write(f'  ~ {symbol} {diary.stock_name}: {rule_str}'
                                  f'  reason={"○" if reason_dirty else "-"} notes={len(dirty_notes)}')

                if apply:
                    with transaction.atomic():
                        if reason_dirty:
                            diary.reason = new_reason
                            diary.save(update_fields=['reason'])
                        for n in dirty_notes:
                            n.content = note_updates[n.id]
                            n.save(update_fields=['content'])
                        _sync_hashtag_tags(diary, user)
                    after = sorted(diary.tags.values_list('name', flat=True))
                    self.stdout.write(f'      tags: {before} → {after}')
                else:
                    self.stdout.write(f'      tags(現在): {before}')
                changed += 1

        tail = '（--apply で保存されます）' if not apply else '（保存済み）'
        self.stdout.write(self.style.SUCCESS(f'対象 {changed} 件 {tail}'))
