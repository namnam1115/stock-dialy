"""日記データ移行：インポートサービス。

JSON または CSV(ZIP) のアップロードファイルを、エクスポートと同一の中間dict（payload）へ
正規化し、検証・サマリ・本登録を行う。JSON/ZIP のどちらも同じ payload に落とし込むため、
validate / summarize / import_payload は 1 実装で両形式に対応する。

移行（別アカウントへのコピー）が目的のため:
- すべてのレコードを import 実行ユーザー（self.user）に紐付ける。ファイル内の user は信用しない。
- Tag は (self.user, name) で get_or_create して重複統合する。
- 取引・分割は bulk_create で投入し、Transaction.save() の都度集計を避ける。
  AggregateService.deferred(diary) ブロックで囲み、ブロック出口で diary 単位の
  再集計を1回だけ確定する（手動 recalculate の呼び忘れを構造的に防ぐ）。
- 株式分割は二重適用を避けるため is_applied=False で取り込み、apply_split() は呼ばない。
- 仮説（Thesis）は diary×1、検証（Verdict）は thesis×1。claim が空なら取り込まない。
"""

import io
import json
import zipfile
from decimal import Decimal, InvalidOperation

import chardet
from django.db import transaction as db_transaction

from ..models import (
    StockDiary, Transaction, StockSplit, DiaryNote, DiaryTagDirection,
    Thesis, Verdict,
)
from ..services.aggregate_service import AggregateService
from ..utils import find_duplicate_diaries
from .migration_export_service import PAYLOAD_FORMAT, PAYLOAD_VERSION, CSV_FILES, TAG_SEPARATOR

# 件数上限（事故・DoS防止）。DATA_UPLOAD_MAX_NUMBER_FIELDS=50000 と整合。
MAX_DIARIES = 2000
MAX_TRANSACTIONS = 50000

# 旧フォーマット（memo 列を持つエクスポート）との後方互換用ヘッダ。
# memo フィールドは廃止済み（improvement_plan 論点9）。旧 CSV/JSON の memo は reason へ統合する。
LEGACY_MEMO_HEADER = '旧メモ'


def _merge_reason_memo(reason: str, memo: str) -> str:
    """旧 memo を reason 末尾へ統合する（migration 0011 と同一フォーマット）。"""
    reason = (reason or '').strip()
    memo = (memo or '').strip()
    if not memo:
        return reason
    if not reason:
        return f'{LEGACY_MEMO_HEADER}: {memo}'
    return f'{reason}\n\n---\n{LEGACY_MEMO_HEADER}: {memo}'


# 列挙値の許容セット（モデル choices と一致）
VALID_CURRENCY = {'JPY', 'USD'}
VALID_TRANSACTION_TYPE = {'buy', 'sell'}
VALID_NOTE_TYPE = {'analysis', 'news', 'earnings', 'insight', 'risk', 'retrospective', 'other'}
VALID_DIRECTION = {'up', 'down', 'neutral'}
VALID_HORIZON = {'next_earnings', '3m', '6m', '1y', 'long'}
VALID_THESIS_STATUS = {'open', 'verified', 'abandoned'}
VALID_HYP_RESULT = {'hit', 'partial', 'miss', 'unknown'}
VALID_PNL_RESULT = {'profit', 'loss', 'flat', 'holding'}


def _parse_bool(value, default=False):
    """CSV/JSON 由来の真偽値表現を bool に。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ('true', '1', 'yes', 'はい', 'on')


def _to_decimal(value):
    """文字列/数値 → Decimal。空・None・不正は None。"""
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


class ImportError(Exception):
    """インポート処理の致命的エラー（パース不能など）。"""


class ImportService:
    def __init__(self, user):
        self.user = user

    # ------------------------------------------------------------------
    # 形式判定
    # ------------------------------------------------------------------
    @staticmethod
    def detect_format(uploaded_file) -> str:
        """'json' | 'zip' を返す。判定不能なら ImportError。"""
        name = (getattr(uploaded_file, 'name', '') or '').lower()
        if name.endswith('.zip'):
            return 'zip'
        if name.endswith('.json'):
            return 'json'
        raise ImportError('対応していないファイル形式です（.json または .zip を選択してください）')

    # ------------------------------------------------------------------
    # パース（ファイル → payload）
    # ------------------------------------------------------------------
    def parse(self, uploaded_file) -> dict:
        fmt = self.detect_format(uploaded_file)
        raw = uploaded_file.read()
        if fmt == 'json':
            payload = self._parse_json(raw)
        else:
            payload = self._parse_zip(raw)
        self._check_meta(payload.get('meta', {}))
        return payload

    def _parse_json(self, raw: bytes) -> dict:
        try:
            detected = chardet.detect(raw)
            encoding = detected.get('encoding') or 'utf-8'
            try:
                text = raw.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                text = raw.decode('utf-8-sig')
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ImportError(f'JSONの形式が不正です: {e}')
        except Exception as e:
            raise ImportError(f'JSONファイルを読み込めませんでした: {e}')

    def _parse_zip(self, raw: bytes) -> dict:
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            raise ImportError('ZIPファイルが破損しています')

        names = set(zf.namelist())
        if CSV_FILES['diaries'] not in names:
            raise ImportError(f"必須ファイル {CSV_FILES['diaries']} が見つかりません")

        # meta.json（任意）
        meta = {}
        if 'meta.json' in names:
            try:
                meta = json.loads(zf.read('meta.json').decode('utf-8-sig'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                meta = {}

        def read_csv(filename):
            if filename not in names:
                return []
            content = zf.read(filename).decode('utf-8-sig')
            import csv
            return list(csv.DictReader(io.StringIO(content)))

        tag_rows = read_csv(CSV_FILES['tags'])
        diary_rows = read_csv(CSV_FILES['diaries'])
        tx_rows = read_csv(CSV_FILES['transactions'])
        split_rows = read_csv(CSV_FILES['stock_splits'])
        note_rows = read_csv(CSV_FILES['notes'])
        direction_rows = read_csv(CSV_FILES['tag_directions'])
        # v2 で追加。旧 ZIP には存在しないため read_csv は [] を返す。
        thesis_rows = read_csv(CSV_FILES['theses'])
        verdict_rows = read_csv(CSV_FILES['verdicts'])

        # 親子を export_key で再結合し、JSON と同じネスト構造へ
        diaries_by_key = {}
        diaries = []
        for row in diary_rows:
            key = (row.get('export_key') or '').strip()
            if not key:
                continue
            tags_field = (row.get('tags') or '').strip()
            diary = {
                'export_key': key,
                'stock_symbol': row.get('stock_symbol', ''),
                'stock_name': row.get('stock_name', ''),
                'currency': row.get('currency', 'JPY'),
                'reason': row.get('reason', ''),
                'memo': row.get('memo', ''),
                'sector': row.get('sector', ''),
                'is_excluded': _parse_bool(row.get('is_excluded')),
                'latest_disclosure_date': (row.get('latest_disclosure_date') or '') or None,
                'latest_disclosure_doc_type_name': row.get('latest_disclosure_doc_type_name', ''),
                'image_filename': None,
                'tags': [t for t in tags_field.split(TAG_SEPARATOR) if t] if tags_field else [],
                'tag_directions': [],
                'transactions': [],
                'stock_splits': [],
                'notes': [],
                'thesis': None,
            }
            diaries_by_key[key] = diary
            diaries.append(diary)

        def attach(rows, target_field, builder):
            for row in rows:
                key = (row.get('diary_export_key') or '').strip()
                diary = diaries_by_key.get(key)
                if diary is None:
                    continue
                diary[target_field].append(builder(row))

        attach(tx_rows, 'transactions', lambda r: {
            'transaction_type': r.get('transaction_type', ''),
            'transaction_date': r.get('transaction_date', ''),
            'price': r.get('price', ''),
            'quantity': r.get('quantity', ''),
            'memo': r.get('memo', ''),
            'is_margin': _parse_bool(r.get('is_margin')),
        })
        attach(split_rows, 'stock_splits', lambda r: {
            'split_date': r.get('split_date', ''),
            'split_ratio': r.get('split_ratio', ''),
            'memo': r.get('memo', ''),
            'is_applied': _parse_bool(r.get('is_applied')),
        })
        attach(note_rows, 'notes', lambda r: {
            'date': r.get('date', ''),
            'content': r.get('content', ''),
            'current_price': r.get('current_price', '') or None,
            'note_type': r.get('note_type', 'analysis'),
            'topic': r.get('topic', ''),
            'source_doc_id': r.get('source_doc_id', '') or None,
            'image_filename': None,
        })
        attach(direction_rows, 'tag_directions', lambda r: {
            'tag': r.get('tag', ''),
            'direction': r.get('direction', 'neutral'),
        })

        # 仮説（diary×1）。export_key 単位で先頭行のみ採用。
        for row in thesis_rows:
            key = (row.get('diary_export_key') or '').strip()
            diary = diaries_by_key.get(key)
            if diary is None or diary['thesis'] is not None:
                continue
            basis_field = (row.get('basis_tags') or '').strip()
            diary['thesis'] = {
                'claim': row.get('claim', ''),
                'basis_tags': [t for t in basis_field.split(TAG_SEPARATOR) if t] if basis_field else [],
                'horizon': row.get('horizon', '6m'),
                'worst_case': row.get('worst_case', ''),
                'review_due_date': (row.get('review_due_date') or '') or None,
                'status': row.get('status', 'open'),
                'verdict': None,
            }
        # 検証（thesis×1）。対応する仮説がある export_key のみ。
        for row in verdict_rows:
            key = (row.get('diary_export_key') or '').strip()
            diary = diaries_by_key.get(key)
            if diary is None or not diary['thesis'] or diary['thesis']['verdict'] is not None:
                continue
            diary['thesis']['verdict'] = {
                'hypothesis_result': row.get('hypothesis_result', ''),
                'pnl_result': row.get('pnl_result', ''),
                'decision_quality': row.get('decision_quality', '3'),
                'missed_factor': row.get('missed_factor', ''),
                'is_repeatable': _parse_bool(row.get('is_repeatable')),
                'learning': row.get('learning', ''),
            }

        tags = [
            {
                'name': r.get('name', ''),
                'axis': r.get('axis', 'theme'),
                'parent': (r.get('parent') or '') or None,
                'df': int(r['df']) if (r.get('df') or '').isdigit() else 0,
            }
            for r in tag_rows if (r.get('name') or '').strip()
        ]

        return {'meta': meta, 'tags': tags, 'diaries': diaries}

    @staticmethod
    def _check_meta(meta: dict):
        """format / version の整合をチェック。"""
        fmt = meta.get('format')
        if fmt and fmt != PAYLOAD_FORMAT:
            raise ImportError(f'このファイルはカブログの移行データではありません（format={fmt}）')
        version = meta.get('version')
        if version and int(version) > PAYLOAD_VERSION:
            raise ImportError(
                f'このファイルは新しいバージョン（v{version}）で作成されています。'
                f'アプリを更新してください（対応 v{PAYLOAD_VERSION}）'
            )

    # ------------------------------------------------------------------
    # 検証
    # ------------------------------------------------------------------
    def validate(self, payload: dict) -> list:
        """致命的でない警告のリストを返す。致命的問題は ImportError を送出。"""
        warnings = []
        diaries = payload.get('diaries', [])
        if not isinstance(diaries, list):
            raise ImportError('日記データ（diaries）が見つかりません')

        if len(diaries) > MAX_DIARIES:
            raise ImportError(f'日記の件数が上限（{MAX_DIARIES}件）を超えています: {len(diaries)}件')

        total_tx = sum(len(d.get('transactions', [])) for d in diaries)
        if total_tx > MAX_TRANSACTIONS:
            raise ImportError(f'取引の件数が上限（{MAX_TRANSACTIONS}件）を超えています: {total_tx}件')

        for i, d in enumerate(diaries, start=1):
            label = d.get('stock_name') or d.get('stock_symbol') or f'#{i}'
            if not (d.get('stock_name') or '').strip():
                warnings.append(f'日記 {label}: 銘柄名が空のためスキップされます')
            if d.get('currency') not in VALID_CURRENCY:
                warnings.append(f'日記 {label}: 通貨「{d.get("currency")}」が不正なため JPY に補正します')
            for tx in d.get('transactions', []):
                if tx.get('transaction_type') not in VALID_TRANSACTION_TYPE:
                    warnings.append(f'日記 {label}: 不正な取引種別「{tx.get("transaction_type")}」をスキップします')
                if _to_decimal(tx.get('price')) is None or _to_decimal(tx.get('quantity')) is None:
                    warnings.append(f'日記 {label}: 価格/数量が不正な取引をスキップします')

        return warnings

    # ------------------------------------------------------------------
    # サマリ（プレビュー用）
    # ------------------------------------------------------------------
    def summarize(self, payload: dict) -> dict:
        """件数と、import 実行ユーザー側での重複候補を返す。"""
        diaries = payload.get('diaries', [])
        duplicates = []
        for d in diaries:
            existing = find_duplicate_diaries(
                self.user,
                stock_symbol=d.get('stock_symbol', ''),
                stock_name=d.get('stock_name', ''),
            )
            count = existing.count()
            if count:
                duplicates.append({
                    'stock_name': d.get('stock_name', ''),
                    'stock_symbol': d.get('stock_symbol', ''),
                    'existing_count': count,
                })

        meta = payload.get('meta', {})
        return {
            'counts': {
                'diaries': len(diaries),
                'tags': len(payload.get('tags', [])),
                'transactions': sum(len(d.get('transactions', [])) for d in diaries),
                'stock_splits': sum(len(d.get('stock_splits', [])) for d in diaries),
                'notes': sum(len(d.get('notes', [])) for d in diaries),
                'tag_directions': sum(len(d.get('tag_directions', [])) for d in diaries),
                'theses': sum(1 for d in diaries if d.get('thesis')),
                'verdicts': sum(1 for d in diaries if d.get('thesis') and d['thesis'].get('verdict')),
            },
            'duplicates': duplicates,
            'source_username': meta.get('source_username', ''),
            'exported_at': meta.get('exported_at', ''),
        }

    # ------------------------------------------------------------------
    # 本登録
    # ------------------------------------------------------------------
    @db_transaction.atomic
    def import_payload(self, payload: dict) -> dict:
        """payload を DB に登録する。atomic。結果カウントを返す。"""
        result = {
            'created_diaries': 0,
            'transactions': 0,
            'stock_splits': 0,
            'notes': 0,
            'theses': 0,
            'verdicts': 0,
            'tags_reused': 0,
            'tags_created': 0,
            'skipped_diaries': 0,
            'warnings': [],
        }

        tag_map = self._import_tags(payload.get('tags', []), result)

        for d in payload.get('diaries', []):
            if not (d.get('stock_name') or '').strip():
                result['skipped_diaries'] += 1
                continue
            self._import_diary(d, tag_map, result)

        self._recalc_tag_df(tag_map.values())
        return result

    def _import_tags(self, tags_payload, result) -> dict:
        """name -> Tag のマップを返す（2パスで parent を解決）。"""
        from tags.models import Tag

        tag_map = {}
        # パス1: get_or_create
        for t in tags_payload:
            name = (t.get('name') or '').strip()
            if not name:
                continue
            axis = t.get('axis') if t.get('axis') in dict(Tag.AXIS_CHOICES) else Tag.AXIS_THEME
            tag, created = Tag.objects.get_or_create(
                user=self.user, name=name,
                defaults={'axis': axis, 'df': 0},
            )
            tag_map[name] = tag
            if created:
                result['tags_created'] += 1
            else:
                result['tags_reused'] += 1

        # パス2: parent 解決（ファイル内に親が存在する場合のみ）
        for t in tags_payload:
            name = (t.get('name') or '').strip()
            parent_name = (t.get('parent') or '').strip()
            if name and parent_name and parent_name in tag_map:
                tag = tag_map[name]
                if tag.parent_id != tag_map[parent_name].id:
                    tag.parent = tag_map[parent_name]
                    tag.save(update_fields=['parent'])
        return tag_map

    def _import_diary(self, d, tag_map, result):
        from tags.models import Tag

        currency = d.get('currency') if d.get('currency') in VALID_CURRENCY else 'JPY'
        diary = StockDiary.objects.create(
            user=self.user,
            stock_symbol=d.get('stock_symbol', '') or '',
            stock_name=d.get('stock_name', ''),
            currency=currency,
            # 旧フォーマットの memo は reason 末尾へ統合（memo フィールドは廃止済み）
            reason=_merge_reason_memo(d.get('reason', ''), d.get('memo', '')),
            sector=d.get('sector', '') or '',
            is_excluded=_parse_bool(d.get('is_excluded')),
            latest_disclosure_date=d.get('latest_disclosure_date') or None,
            latest_disclosure_doc_type_name=d.get('latest_disclosure_doc_type_name', '') or '',
        )
        result['created_diaries'] += 1

        # タグ M2M（ファイル内 tags リストに無い名前も get_or_create で許容）
        diary_tags = []
        for tag_name in d.get('tags', []):
            tag_name = (tag_name or '').strip()
            if not tag_name:
                continue
            tag = tag_map.get(tag_name)
            if tag is None:
                tag, _ = Tag.objects.get_or_create(
                    user=self.user, name=tag_name, defaults={'axis': Tag.AXIS_THEME, 'df': 0},
                )
                tag_map[tag_name] = tag
            diary_tags.append(tag)
        if diary_tags:
            diary.tags.set(diary_tags)

        # タグ方向（diary×tag は unique）
        directions = []
        seen_tags = set()
        for td in d.get('tag_directions', []):
            tag_name = (td.get('tag') or '').strip()
            tag = tag_map.get(tag_name)
            if tag is None or tag.id in seen_tags:
                continue
            direction = td.get('direction') if td.get('direction') in VALID_DIRECTION else 'neutral'
            directions.append(DiaryTagDirection(diary=diary, tag=tag, direction=direction))
            seen_tags.add(tag.id)
        if directions:
            DiaryTagDirection.objects.bulk_create(directions, ignore_conflicts=True)

        # 取引・分割は bulk_create で save() の自動集計を回避（行順＝挿入順を維持）。
        # deferred() で囲み、ブロック出口で diary 単位の再集計を1回だけ確定する
        # （手動 recalculate の呼び忘れを構造的に防ぐ）。
        with AggregateService.deferred(diary):
            tx_objs = []
            for tx in d.get('transactions', []):
                if tx.get('transaction_type') not in VALID_TRANSACTION_TYPE:
                    continue
                price = _to_decimal(tx.get('price'))
                quantity = _to_decimal(tx.get('quantity'))
                if price is None or quantity is None or price <= 0 or quantity <= 0:
                    continue
                if not tx.get('transaction_date'):
                    continue
                tx_objs.append(Transaction(
                    diary=diary,
                    transaction_type=tx['transaction_type'],
                    transaction_date=tx['transaction_date'],
                    price=price,
                    quantity=quantity,
                    memo=tx.get('memo', '') or '',
                    is_margin=_parse_bool(tx.get('is_margin')),
                ))
            if tx_objs:
                Transaction.objects.bulk_create(tx_objs)
                result['transactions'] += len(tx_objs)

            # 株式分割（is_applied=False で取り込み、apply_split は呼ばない＝二重適用防止）
            split_objs = []
            for sp in d.get('stock_splits', []):
                ratio = _to_decimal(sp.get('split_ratio'))
                if ratio is None or ratio <= 0 or not sp.get('split_date'):
                    continue
                split_objs.append(StockSplit(
                    diary=diary,
                    split_date=sp['split_date'],
                    split_ratio=ratio,
                    memo=sp.get('memo', '') or '',
                    is_applied=False,
                    applied_at=None,
                ))
            if split_objs:
                StockSplit.objects.bulk_create(split_objs)
                result['stock_splits'] += len(split_objs)

        # 継続記録（DiaryNote.save() は full_clean を呼ぶため個別 create）
        for note in d.get('notes', []):
            if not note.get('date'):
                continue
            note_type = note.get('note_type') if note.get('note_type') in VALID_NOTE_TYPE else 'other'
            try:
                DiaryNote.objects.create(
                    diary=diary,
                    date=note['date'],
                    content=note.get('content', '') or '',
                    current_price=_to_decimal(note.get('current_price')),
                    note_type=note_type,
                    topic=note.get('topic', '') or '',
                    source_doc_id=note.get('source_doc_id') or None,
                )
                result['notes'] += 1
            except Exception as e:  # noqa: BLE001 - 1件のノート不正で全体を止めない
                result['warnings'].append(f'{diary.stock_name}: 継続記録の取り込みに失敗（{e}）')

        # 仮説（Thesis）と検証（Verdict）。claim が空なら取り込まない。
        self._import_thesis(diary, d.get('thesis'), tag_map, result)

    def _import_thesis(self, diary, thesis_payload, tag_map, result):
        from tags.models import Tag

        if not thesis_payload or not (thesis_payload.get('claim') or '').strip():
            return
        horizon = thesis_payload.get('horizon')
        horizon = horizon if horizon in VALID_HORIZON else 'next_earnings'
        status = thesis_payload.get('status')
        status = status if status in VALID_THESIS_STATUS else Thesis.STATUS_OPEN
        try:
            thesis = Thesis.objects.create(
                diary=diary,
                claim=(thesis_payload.get('claim') or '')[:200],
                checkpoint=(thesis_payload.get('checkpoint') or '')[:200],
                checkpoint_direction=(thesis_payload.get('checkpoint_direction') or '')[:16],
                horizon=horizon,
                worst_case=(thesis_payload.get('worst_case') or '')[:300],
                review_due_date=thesis_payload.get('review_due_date') or None,
                status=status,
            )
        except Exception as e:  # noqa: BLE001 - 仮説1件の不正で全体を止めない
            result['warnings'].append(f'{diary.stock_name}: 仮説の取り込みに失敗（{e}）')
            return
        result['theses'] += 1

        # 根拠タグ（M2M）。ファイル内に無い名前も get_or_create で許容。
        basis = []
        for tag_name in thesis_payload.get('basis_tags', []):
            tag_name = (tag_name or '').strip()
            if not tag_name:
                continue
            tag = tag_map.get(tag_name)
            if tag is None:
                tag, _ = Tag.objects.get_or_create(
                    user=self.user, name=tag_name, defaults={'axis': Tag.AXIS_THEME, 'df': 0},
                )
                tag_map[tag_name] = tag
            basis.append(tag)
        if basis:
            thesis.basis_tags.set(basis)

        # 検証（Verdict）。仮説の当否・損益が不正なら取り込まない。
        v = thesis_payload.get('verdict')
        if not v:
            return
        hyp = v.get('hypothesis_result')
        pnl = v.get('pnl_result')
        if hyp not in VALID_HYP_RESULT or pnl not in VALID_PNL_RESULT:
            result['warnings'].append(f'{diary.stock_name}: 検証の当否/損益が不正なためスキップします')
            return
        dq = v.get('decision_quality')
        try:
            dq = max(1, min(5, int(dq)))
        except (TypeError, ValueError):
            dq = 3
        try:
            Verdict.objects.create(
                thesis=thesis,
                hypothesis_result=hyp,
                pnl_result=pnl,
                decision_quality=dq,
                missed_factor=(v.get('missed_factor') or '')[:300],
                is_repeatable=_parse_bool(v.get('is_repeatable')),
                learning=(v.get('learning') or '')[:200],
            )
            result['verdicts'] += 1
        except Exception as e:  # noqa: BLE001
            result['warnings'].append(f'{diary.stock_name}: 検証の取り込みに失敗（{e}）')

    def _recalc_tag_df(self, tags):
        """インポートしたタグの df（出現銘柄数）を再計算する。

        views.py の `tag.df = tag.stockdiary_set.values('stock_symbol').distinct().count()`
        と同じ算出方法。
        """
        for tag in tags:
            tag.df = tag.stockdiary_set.filter(user=self.user).values('stock_symbol').distinct().count()
            tag.save(update_fields=['df'])
