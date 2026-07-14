"""リクエスト時想起カード（RecallService）と重複日記チェックのテスト"""
import pytest
from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse

from django.utils import timezone

from stockdiary.models import StockDiary, Transaction, DiaryNote, ReasonVersion
from stockdiary.services.recall_service import RecallService, STALE_REASON_DAYS
from stockdiary.utils import find_duplicate_diaries
from stockdiary.forms import StockDiaryForm

pytestmark = pytest.mark.django_db(transaction=True)


class TestRecallService:
    """RecallService.build の各セクション"""

    def test_empty_user_has_no_content(self, user):
        recall = RecallService.build(user)
        assert recall['has_content'] is False
        assert recall['anniversary'] == []
        assert recall['unreviewed'] == []
        assert recall['disclosures'] == []

    def test_anniversary_note_found(self, sample_diary):
        """約1年前の継続記録が「1年前の今日」に出る"""
        DiaryNote.objects.create(
            diary=sample_diary,
            date=date.today() - timedelta(days=365),
            content='1年前の考察',
        )
        recall = RecallService.build(sample_diary.user)
        assert recall['has_content'] is True
        assert len(recall['anniversary']) == 1
        assert recall['anniversary'][0]['diary'].id == sample_diary.id
        assert recall['anniversary'][0]['kind'] == 'note'

    def test_anniversary_outside_window_excluded(self, sample_diary):
        """ウィンドウ外（±3日超）の記録は出ない"""
        DiaryNote.objects.create(
            diary=sample_diary,
            date=date.today() - timedelta(days=365 + 10),
            content='ウィンドウ外',
        )
        recall = RecallService.build(sample_diary.user)
        assert recall['anniversary'] == []

    def test_unreviewed_sold_diary_listed(self, sample_sold_diary):
        """売却完結済みで振り返りがない日記が出る"""
        recall = RecallService.build(sample_sold_diary.user)
        assert sample_sold_diary in recall['unreviewed']
        assert recall['unreviewed_count'] == 1

    def test_sold_diary_with_retrospective_excluded(self, sample_sold_diary):
        """retrospective ノートを書いた日記は出ない"""
        DiaryNote.objects.create(
            diary=sample_sold_diary,
            date=date.today(),
            content='高値掴みだった。次はエントリー根拠を数値で残す',
            note_type='retrospective',
        )
        recall = RecallService.build(sample_sold_diary.user)
        assert sample_sold_diary not in recall['unreviewed']
        assert recall['unreviewed_count'] == 0

    def test_holding_diary_not_in_unreviewed(self, sample_diary_with_transaction):
        """保有中の日記は振り返り対象外"""
        recall = RecallService.build(sample_diary_with_transaction.user)
        assert sample_diary_with_transaction not in recall['unreviewed']

    def test_recent_disclosure_listed(self, sample_diary):
        """直近の確定決算でレビュー未記入の日記が出る"""
        sample_diary.latest_disclosure_date = date.today() - timedelta(days=2)
        sample_diary.save(update_fields=['latest_disclosure_date'])
        recall = RecallService.build(sample_diary.user)
        assert sample_diary in recall['disclosures']

    def test_old_disclosure_excluded(self, sample_diary):
        """表示期間（30日）を過ぎた開示は出ない"""
        sample_diary.latest_disclosure_date = date.today() - timedelta(days=40)
        sample_diary.save(update_fields=['latest_disclosure_date'])
        recall = RecallService.build(sample_diary.user)
        assert sample_diary not in recall['disclosures']

    def test_disclosure_with_earnings_note_excluded(self, sample_diary):
        """開示日以降に決算ノート(note_type='earnings')を書いたら想起から消える"""
        sample_diary.latest_disclosure_date = date.today() - timedelta(days=2)
        sample_diary.save(update_fields=['latest_disclosure_date'])
        DiaryNote.objects.create(
            diary=sample_diary,
            date=date.today() - timedelta(days=1),
            note_type='earnings',
            content='決算レビュー',
        )
        recall = RecallService.build(sample_diary.user)
        assert sample_diary not in recall['disclosures']

    def test_disclosure_with_old_earnings_note_still_listed(self, sample_diary):
        """開示日より前の決算ノートはレビュー済みとみなさない"""
        sample_diary.latest_disclosure_date = date.today() - timedelta(days=2)
        sample_diary.save(update_fields=['latest_disclosure_date'])
        DiaryNote.objects.create(
            diary=sample_diary,
            date=date.today() - timedelta(days=10),
            note_type='earnings',
            content='前回の決算レビュー',
        )
        recall = RecallService.build(sample_diary.user)
        assert sample_diary in recall['disclosures']

    def test_other_users_data_not_leaked(self, sample_sold_diary, another_user):
        """他ユーザーの日記は想起に出ない"""
        recall = RecallService.build(another_user)
        assert recall['has_content'] is False


def _age_created(diary, days):
    """auto_now_add の created_at を過去日へ書き換える（テスト補助）"""
    StockDiary.objects.filter(pk=diary.pk).update(
        created_at=timezone.now() - timedelta(days=days)
    )
    diary.refresh_from_db()


class TestStaleReasonRecall:
    """OD5: 保有中なのに企業説明(reason)を長期間見直していない日記の想起。

    reason（企業説明）は「現状の企業の俯瞰」ゆえ時間とともに陳腐化する
    （ontology §3：reason は買った理由=Thesis とは別レイヤの現状説明）。保有を
    続けているのに前提を STALE_REASON_DAYS 見直していない銘柄を「前提の再点検」
    として想起し、削除でなく更新へ誘導する。最終更新時刻は最新
    ReasonVersion.created_at（無ければ日記作成時刻）で判定する。
    """

    def test_old_holding_reason_surfaces(self, sample_diary_with_transaction):
        """保有中・reason あり・作成から STALE_REASON_DAYS 超 → 想起に出る"""
        _age_created(sample_diary_with_transaction, STALE_REASON_DAYS + 40)
        recall = RecallService.build(sample_diary_with_transaction.user)
        assert sample_diary_with_transaction in recall['stale_reason']
        assert recall['has_content'] is True
        assert 'stale_reason' in [q['kind'] for q in recall['queue']]

    def test_recent_holding_not_stale(self, sample_diary_with_transaction):
        """作成が最近なら（前提が新しい）出ない"""
        recall = RecallService.build(sample_diary_with_transaction.user)
        assert sample_diary_with_transaction not in recall['stale_reason']

    def test_recent_reason_version_resets_staleness(self, sample_diary_with_transaction):
        """作成は古くても、最近 reason を更新（新しい ReasonVersion）していれば出ない。

        現在の reason が定着した時刻＝最新 ReasonVersion.created_at で判定するため。
        """
        _age_created(sample_diary_with_transaction, STALE_REASON_DAYS + 40)
        rv = ReasonVersion.objects.create(
            diary=sample_diary_with_transaction, content='旧見立て'
        )
        ReasonVersion.objects.filter(pk=rv.pk).update(
            created_at=timezone.now() - timedelta(days=10)
        )
        recall = RecallService.build(sample_diary_with_transaction.user)
        assert sample_diary_with_transaction not in recall['stale_reason']

    def test_old_reason_version_keeps_stale(self, sample_diary_with_transaction):
        """最新 ReasonVersion も STALE_REASON_DAYS より古ければ、なお陳腐化として出る"""
        _age_created(sample_diary_with_transaction, STALE_REASON_DAYS + 200)
        rv = ReasonVersion.objects.create(
            diary=sample_diary_with_transaction, content='さらに古い見立て'
        )
        ReasonVersion.objects.filter(pk=rv.pk).update(
            created_at=timezone.now() - timedelta(days=STALE_REASON_DAYS + 30)
        )
        recall = RecallService.build(sample_diary_with_transaction.user)
        assert sample_diary_with_transaction in recall['stale_reason']

    def test_memo_diary_not_stale(self, sample_diary):
        """取引ゼロ（保有していない）メモ日記は、古くても対象外（賭けていない）"""
        _age_created(sample_diary, STALE_REASON_DAYS + 40)
        recall = RecallService.build(sample_diary.user)
        assert sample_diary not in recall['stale_reason']

    def test_excluded_holding_not_stale(self, sample_diary_with_transaction):
        """アーカイブ済み(is_excluded)は想起の母集団から外れる（OD2 と一貫）"""
        _age_created(sample_diary_with_transaction, STALE_REASON_DAYS + 40)
        sample_diary_with_transaction.is_excluded = True
        sample_diary_with_transaction.save(update_fields=['is_excluded'])
        recall = RecallService.build(sample_diary_with_transaction.user)
        assert sample_diary_with_transaction not in recall['stale_reason']

    def test_empty_reason_not_stale(self, sample_diary_with_transaction):
        """reason が空なら見直す前提が無いので出ない"""
        _age_created(sample_diary_with_transaction, STALE_REASON_DAYS + 40)
        sample_diary_with_transaction.reason = ''
        sample_diary_with_transaction.save(update_fields=['reason'])
        recall = RecallService.build(sample_diary_with_transaction.user)
        assert sample_diary_with_transaction not in recall['stale_reason']

    def test_other_users_stale_not_leaked(self, sample_diary_with_transaction, another_user):
        """他ユーザーの陳腐化日記は出ない"""
        _age_created(sample_diary_with_transaction, STALE_REASON_DAYS + 40)
        recall = RecallService.build(another_user)
        assert recall['stale_reason'] == []


class TestArchiveLabeling:
    """OD2: ユーザー向け文言を「除外」→「アーカイブ（棚に戻す）」へ統一。

    内部名(is_excluded)・status クエリ値('excluded')・DB は不変。表示の言葉と導線
    だけを「消す」でなく「片付ける（アーカイブ）」に寄せ、North Star（過去の判断を
    消さない）を UX で担保する。
    """

    def test_home_status_filter_uses_archive_label(self, authenticated_client, sample_diary):
        response = authenticated_client.get(reverse('stockdiary:home'))
        body = response.content.decode()
        assert 'アーカイブ' in body
        assert '除外済み' not in body

    def test_detail_banner_says_archived(self, authenticated_client, sample_diary):
        sample_diary.is_excluded = True
        sample_diary.save(update_fields=['is_excluded'])
        response = authenticated_client.get(
            reverse('stockdiary:detail', kwargs={'pk': sample_diary.pk})
        )
        body = response.content.decode()
        assert 'アーカイブ済み' in body
        assert '一覧・グラフから除外されています' not in body

    def test_excluded_list_shows_restore_label(self, authenticated_client, sample_diary):
        """アーカイブ一覧（status=excluded）のカードは「アーカイブから戻す」を出す"""
        sample_diary.is_excluded = True
        sample_diary.save(update_fields=['is_excluded'])
        response = authenticated_client.get(
            reverse('stockdiary:home'), {'status': 'excluded'}
        )
        body = response.content.decode()
        assert 'アーカイブから戻す' in body
        assert '除外を解除' not in body

    def test_status_value_excluded_unchanged(self, authenticated_client, sample_diary):
        """内部の status クエリ値 'excluded' は温存（フィルタ契約は不変）"""
        sample_diary.is_excluded = True
        sample_diary.save(update_fields=['is_excluded'])
        response = authenticated_client.get(
            reverse('stockdiary:home'), {'status': 'excluded'}
        )
        assert response.status_code == 200
        assert sample_diary.stock_name in response.content.decode()


class TestFindDuplicateDiaries:
    """重複日記検出ヘルパー"""

    def test_same_symbol_found(self, sample_diary):
        dup = find_duplicate_diaries(sample_diary.user, stock_symbol='7203')
        assert sample_diary in dup

    def test_other_user_not_found(self, sample_diary, another_user):
        dup = find_duplicate_diaries(another_user, stock_symbol='7203')
        assert sample_diary not in dup

    def test_name_match_only_when_symbol_empty(self, user):
        """コードなしメモはコード空同士の名前一致のみ"""
        memo = StockDiary.objects.create(user=user, stock_symbol='', stock_name='投資アイデア')
        assert memo in find_duplicate_diaries(user, stock_name='投資アイデア')
        # コード付き日記は名前では引っかからない
        StockDiary.objects.create(user=user, stock_symbol='7203', stock_name='トヨタ自動車')
        assert list(find_duplicate_diaries(user, stock_name='トヨタ自動車')) == []

    def test_empty_input_returns_none(self, user):
        assert list(find_duplicate_diaries(user)) == []


class TestCheckDuplicateAPI:
    """重複チェックAPIエンドポイント"""

    def test_duplicate_found(self, authenticated_client, sample_diary):
        url = reverse('stockdiary:api_check_duplicate')
        response = authenticated_client.get(url, {'symbol': '7203'})
        assert response.status_code == 200
        data = response.json()
        assert data['exists'] is True
        assert data['diaries'][0]['id'] == sample_diary.id
        assert data['diaries'][0]['status'] == 'メモ'

    def test_no_duplicate(self, authenticated_client, sample_diary):
        url = reverse('stockdiary:api_check_duplicate')
        response = authenticated_client.get(url, {'symbol': '9999'})
        assert response.json()['exists'] is False

    def test_requires_login(self, client):
        url = reverse('stockdiary:api_check_duplicate')
        response = client.get(url, {'symbol': '7203'})
        assert response.status_code == 302


class TestStockDiaryFormDuplicate:
    """フォームの重複チェック（新規作成時のみ・allow_duplicate で許可）"""

    def _form_data(self, **overrides):
        data = {
            'stock_symbol': '7203',
            'stock_name': 'トヨタ自動車',
            'reason': '再エントリー検討',
            'memo': '',
            'sector': '',
            'currency': 'JPY',
        }
        data.update(overrides)
        return data

    def test_duplicate_blocked_without_allow(self, sample_diary):
        form = StockDiaryForm(data=self._form_data(), user=sample_diary.user)
        assert not form.is_valid()
        assert 'stock_symbol' in form.errors
        assert form.duplicate_diary == sample_diary

    def test_duplicate_allowed_with_flag(self, sample_diary):
        form = StockDiaryForm(
            data=self._form_data(allow_duplicate='on'),
            user=sample_diary.user,
        )
        assert form.is_valid(), form.errors

    def test_no_duplicate_passes(self, user):
        form = StockDiaryForm(data=self._form_data(), user=user)
        assert form.is_valid(), form.errors

    def test_update_not_blocked(self, sample_diary):
        """既存日記の編集は重複チェック対象外"""
        form = StockDiaryForm(
            data=self._form_data(reason=sample_diary.reason),
            instance=sample_diary,
            user=sample_diary.user,
        )
        assert form.is_valid(), form.errors


class TestMemoFieldRemoved:
    """memo フィールドは廃止し reason へ統合（improvement_plan 論点9）。
    書く場所は「投資仮説(reason)」と「時系列の追記(DiaryNote)」の2層。"""

    def test_memo_not_in_form_fields(self):
        form = StockDiaryForm()
        assert 'memo' not in form.fields

    def test_memo_attribute_removed_from_model(self, sample_diary):
        """StockDiary から memo 属性自体が消えている"""
        assert not hasattr(sample_diary, 'memo')

    def test_legacy_csv_memo_folded_into_reason(self, user):
        """旧フォーマット(memo列あり)のインポートで memo が reason 末尾へ統合される"""
        from stockdiary.services.migration_import_service import _merge_reason_memo
        merged = _merge_reason_memo('投資仮説', '旧メモの内容')
        assert '投資仮説' in merged
        assert '旧メモの内容' in merged

    def test_merge_reason_memo_empty_memo(self):
        from stockdiary.services.migration_import_service import _merge_reason_memo
        assert _merge_reason_memo('理由のみ', '') == '理由のみ'

    def test_merge_reason_memo_empty_reason(self):
        from stockdiary.services.migration_import_service import _merge_reason_memo
        assert _merge_reason_memo('', 'メモのみ') == '旧メモ: メモのみ'


class TestRetrospectivePrompt:
    """売却完結済み日記の振り返りプロンプト（detail バナー）"""

    def test_banner_shown_for_unreviewed_sold_diary(self, authenticated_client, sample_sold_diary):
        from django.urls import reverse
        response = authenticated_client.get(
            reverse('stockdiary:detail', kwargs={'pk': sample_sold_diary.pk})
        )
        assert response.status_code == 200
        assert '振り返りを書く' in response.content.decode()

    def test_banner_hidden_after_retrospective(self, authenticated_client, sample_sold_diary):
        from django.urls import reverse
        DiaryNote.objects.create(
            diary=sample_sold_diary, date=date.today(),
            content='振り返り済み', note_type='retrospective',
        )
        response = authenticated_client.get(
            reverse('stockdiary:detail', kwargs={'pk': sample_sold_diary.pk})
        )
        assert '振り返りを書く' not in response.content.decode()

    def test_banner_hidden_for_holding_diary(self, authenticated_client, sample_diary_with_transaction):
        from django.urls import reverse
        response = authenticated_client.get(
            reverse('stockdiary:detail', kwargs={'pk': sample_diary_with_transaction.pk})
        )
        assert '振り返りを書く' not in response.content.decode()


class TestRetrospectiveNoteType:
    """note_type に retrospective が追加されている"""

    def test_retrospective_choice_exists(self):
        assert 'retrospective' in dict(DiaryNote.TYPE_CHOICES)

    def test_create_retrospective_note(self, sample_sold_diary):
        note = DiaryNote.objects.create(
            diary=sample_sold_diary,
            date=date.today(),
            content='振り返り',
            note_type='retrospective',
        )
        assert note.get_note_type_display() == '振り返り'


class TestSummaryExtraction:
    """投資理由の冒頭要約を想起カード/タイムライン用に抽出する（extract_lead）"""

    def test_summary_heading_preferred(self):
        from stockdiary.utils import extract_lead
        text = '## なぜ投資する？\n本文\n## 要約\n割安と判断。中期で見直し余地。\n## リスク\n金利'
        assert extract_lead(text, 60) == '割安と判断。中期で見直し余地。'

    def test_hitokoto_summary_still_works(self):
        from stockdiary.utils import extract_lead
        text = '## ひとこと要約\n結論だけここ\n\n## 詳細\nあれこれ'
        assert extract_lead(text, 60) == '結論だけここ'

    def test_falls_back_when_summary_is_guidance_only(self):
        """要約欄が未記入（ガイダンス行のみ）でも、別見出しの本文を取りこぼさない"""
        from stockdiary.utils import extract_lead
        text = (
            '## ひとこと要約\n（1〜2文で結論。ここが想起カードに出ます）\n'
            '## なぜ投資する？\n実需が強く中期で伸びる'
        )
        assert extract_lead(text, 60) == '実需が強く中期で伸びる'

    def test_empty_when_nothing_written(self):
        from stockdiary.utils import extract_lead
        text = '## ひとこと要約\n（1〜2文で結論）\n## なぜ投資する？\n\n## リスク\n'
        assert extract_lead(text, 60) == ''

    def test_anniversary_diary_snippet_uses_summary(self, user):
        """1年前の日記カードのスニペットが要約セクションを反映する"""
        diary = StockDiary.objects.create(
            user=user, stock_symbol='7203', stock_name='トヨタ自動車',
            reason='## ひとこと要約\nEVシフトの本命と判断\n## 詳細\n長文の分析',
        )
        # created_at は auto_now_add のため、保存後に約1年前へ更新する
        from django.utils import timezone
        StockDiary.objects.filter(pk=diary.pk).update(
            created_at=timezone.now() - timedelta(days=365)
        )
        recall = RecallService.build(user)
        snippet = recall['anniversary'][0]['snippet']
        assert snippet == 'EVシフトの本命と判断'


class TestNewDiaryFormDefault:
    """新規作成フォームは構造を強制しない（既定は空。テンプレ選択に委ねる）"""

    def test_new_form_reason_is_blank(self):
        """投資理由は空スタート。構造はテンプレ選択（クライアント側で記憶/自動適用）に委ねる"""
        form = StockDiaryForm()
        assert not (form['reason'].value() or '')

    def test_edit_form_keeps_existing_reason(self, sample_diary):
        sample_diary.reason = '既存の理由テキスト'
        sample_diary.save(update_fields=['reason'])
        form = StockDiaryForm(instance=sample_diary)
        assert form['reason'].value() == '既存の理由テキスト'


class TestLeadPreviewAPI:
    """ライブプレビュー用エンドポイント（表示側 extract_lead に委譲）

    注: 本番では URL が /stockdiary/api/... となりセキュリティMW対象外だが、
    テストの ROOT_URLCONF(config.test_urls)では /api/ 直下にマウントされ、
    SecurityMiddleware がPOST本文の `#` 等を遮断する。Markdown見出しの抽出は
    TestSummaryExtraction で直接検証済みのため、ここでは配線（認証・メソッド・
    委譲・JSON形状）をプレーンテキストで確認する。
    """

    def test_returns_lead(self, authenticated_client):
        url = reverse('stockdiary:api_lead_preview')
        response = authenticated_client.post(url, {'reason': '割安と判断したので購入'})
        assert response.status_code == 200
        assert response.json()['lead'] == '割安と判断したので購入'

    def test_empty_for_blank(self, authenticated_client):
        url = reverse('stockdiary:api_lead_preview')
        response = authenticated_client.post(url, {'reason': '   '})
        assert response.status_code == 200
        assert response.json()['lead'] == ''

    def test_requires_login(self, client):
        url = reverse('stockdiary:api_lead_preview')
        response = client.post(url, {'reason': 'x'})
        assert response.status_code == 302

    def test_rejects_get(self, authenticated_client):
        url = reverse('stockdiary:api_lead_preview')
        response = authenticated_client.get(url)
        assert response.status_code == 405
