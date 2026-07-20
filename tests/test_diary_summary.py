"""銘柄サマリー（diary_summary、旧 stock_list 統合後）のテスト"""
import pytest

from django.urls import reverse

from stockdiary.models import StockDiary

pytestmark = pytest.mark.django_db(transaction=True)


class TestDiarySummaryView:

    def test_page_renders(self, authenticated_client, sample_diary):
        response = authenticated_client.get(reverse('stockdiary:diary_summary'))
        assert response.status_code == 200
        html = response.content.decode()
        assert 'トヨタ自動車' in html
        assert '7203' in html

    def test_sector_shown_and_filterable(self, authenticated_client, user, sample_diary):
        StockDiary.objects.create(
            user=user, stock_symbol='8306', stock_name='三菱UFJ', sector='銀行業',
        )
        response = authenticated_client.get(reverse('stockdiary:diary_summary'))
        summary = {s['symbol']: s for s in response.context['summary_list']}
        assert summary['7203']['sector'] == '輸送用機器'
        assert '銀行業' in response.context['sectors']

        # 業種フィルター
        response = authenticated_client.get(
            reverse('stockdiary:diary_summary'), {'sector': '銀行業'}
        )
        symbols = [s['symbol'] for s in response.context['summary_list']]
        assert symbols == ['8306']

    def test_holding_status_flag(self, authenticated_client, sample_diary_with_transaction, sample_sold_diary):
        response = authenticated_client.get(reverse('stockdiary:diary_summary'))
        summary = {s['symbol']: s for s in response.context['summary_list']}
        assert summary['7203']['is_holding'] is True   # 買い取引あり・保有中
        assert summary['9984']['is_holding'] is False  # 売却完結済み

    def test_search_matches_sector(self, authenticated_client, sample_diary):
        response = authenticated_client.get(
            reverse('stockdiary:diary_summary'), {'q': '輸送用'}
        )
        assert len(response.context['summary_list']) == 1

    def test_old_stock_list_url_redirects(self, authenticated_client):
        response = authenticated_client.get(reverse('stockdiary:stock_list'))
        assert response.status_code == 302
        assert response.url == reverse('stockdiary:diary_summary')

    def test_other_user_not_included(self, authenticated_client, another_user):
        StockDiary.objects.create(
            user=another_user, stock_symbol='9999', stock_name='他人の銘柄',
        )
        response = authenticated_client.get(reverse('stockdiary:diary_summary'))
        symbols = [s['symbol'] for s in response.context['summary_list']]
        assert '9999' not in symbols

    def test_table_view_aggregates_volume_and_outcome(
        self, authenticated_client, diary_with_notes, complex_diary_with_multiple_transactions
    ):
        """銘柄別テーブルは「向き合った量×成果」を集計する。

        record_count（日記＋ノート）と txn_count（取引回数）が
        リストでは見えない銘柄単位の累計として context に載ることを保証する。
        """
        response = authenticated_client.get(reverse('stockdiary:diary_summary'))
        table = {s['symbol']: s for s in response.context['table_list']}

        # ノート付き日記: 記録数 = 日記1 + ノート数。集計フィールドが揃っている
        notes_sym = diary_with_notes.stock_symbol
        assert notes_sym in table
        row = table[notes_sym]
        assert row['record_count'] == row['diary_count'] + row['note_count']
        assert row['record_count'] >= 1
        # 量×成果の対比に必要なキーが欠けていない
        for key in ('txn_count', 'realized_profit', 'verdict_hit', 'verdict_total'):
            assert key in row

        # 複数取引の銘柄は取引回数が積み上がる
        multi_sym = complex_diary_with_multiple_transactions.stock_symbol
        assert table[multi_sym]['txn_count'] >= 2

    def test_table_sort_by_record_count(self, authenticated_client, diary_with_notes, sample_memo_diary):
        """tsort=record_desc で記録数の多い銘柄が先頭に並ぶ（デフォルト軸）。"""
        response = authenticated_client.get(
            reverse('stockdiary:diary_summary'), {'tsort': 'record_desc'}
        )
        counts = [s['record_count'] for s in response.context['table_list']]
        assert counts == sorted(counts, reverse=True)

    def test_no_leaked_template_comments(self, authenticated_client, sample_diary):
        """テンプレートコメントが本文に漏れない（複数行 {# #} は生表示される罠の回帰）。

        Django の {# #} は複数行に使えず、複数行だと生テキストとして描画される。
        両ビュー（list/table）の該当箇所を含め、リテラル '{#' が出力に無いことを保証する。
        """
        for params in ({}, {'lens': 'theme'}, {'view': 'list'}):
            html = authenticated_client.get(
                reverse('stockdiary:diary_summary'), params
            ).content.decode()
            assert '{#' not in html
            assert '{% ' not in html

    def test_list_lenses_are_grouping_only(self, authenticated_client, sample_diary):
        """役割で二分: リストのレンズは「まとめる（状態/テーマ）」の2つだけ。

        なぜ（IA再構成）: 旧「時系列」「銘柄」レンズは並べ替えであり、
        銘柄別テーブルの tsort（最近順/コード順）と重複していた。並べ替えはテーブルへ
        集約し、リストはグルーピング専用にした。
        """
        response = authenticated_client.get(reverse('stockdiary:diary_summary'))
        lens_ids = [t[0] for t in response.context['lens_tabs']]
        assert lens_ids == ['state', 'theme']
        assert 'time' not in lens_ids and 'symbol' not in lens_ids

    def test_table_has_recent_and_code_sorts(self, authenticated_client, sample_diary):
        """並べ替えはテーブルに集約: 最近順(recent_desc)・コード順(symbol)を持つ。"""
        response = authenticated_client.get(reverse('stockdiary:diary_summary'))
        sort_ids = [t[0] for t in response.context['table_sorts']]
        assert 'recent_desc' in sort_ids   # 旧・時系列レンズの移設先
        assert 'symbol' in sort_ids        # 旧・銘柄レンズの相当

    def test_table_recent_sort_orders_by_latest_date(
        self, authenticated_client, diary_with_notes, sample_memo_diary
    ):
        """tsort=recent_desc で最新記録日の新しい銘柄が先頭に並ぶ。"""
        response = authenticated_client.get(
            reverse('stockdiary:diary_summary'), {'tsort': 'recent_desc'}
        )
        from datetime import date
        dates = [s['latest_date'] or date.min for s in response.context['table_list']]
        assert dates == sorted(dates, reverse=True)

    def test_legacy_lens_falls_back_without_error(self, authenticated_client, sample_diary):
        """撤去済みレンズ（?lens=symbol / ?lens=time）でも 200 で状態フィルタに落ちる。"""
        for legacy in ('symbol', 'time'):
            response = authenticated_client.get(
                reverse('stockdiary:diary_summary'), {'lens': legacy}
            )
            assert response.status_code == 200
            html = response.content.decode()
            # 状態フィルタのチップ帯が出る（グルーピングではなく選択フィルタ）
            assert 'ds-chips' in html

    def test_state_filter_renders_chips_and_sections(
        self, authenticated_client, sample_diary_with_transaction, sample_sold_diary
    ):
        """状態レンズ: チップ（選択フィルタ）と、対応する data-target のセクションが揃う。

        なぜ（UX修正）: 見出しで束ねて全部並べると結局スクロールで探すことになり
        グルーピングが機能しなかった。状態を選ぶチップにし、選んだ状態の銘柄だけを
        表示する（クライアント側フィルタ）。チップの data-target とセクション id が一致することを担保。
        """
        import re
        response = authenticated_client.get(reverse('stockdiary:diary_summary'))
        html = response.content.decode()
        assert 'ds-chip' in html
        targets = set(re.findall(r'data-target="(ds-sec-[^"]+)"', html))
        assert targets, 'チップの data-target が無い'
        for t in targets:
            assert f'id="{t}"' in html, f'チップ {t} に対応するセクションが無い'

    def test_theme_filter_renders_chips(self, authenticated_client, sample_diary, sample_tags):
        """テーマレンズでもチップ（選択フィルタ）＋セクションが揃う。"""
        import re
        sample_diary.tags.add(sample_tags[0])
        response = authenticated_client.get(
            reverse('stockdiary:diary_summary'), {'lens': 'theme'}
        )
        html = response.content.decode()
        targets = set(re.findall(r'data-target="(ds-sec-theme-[^"]+)"', html))
        assert targets, 'テーマのチップ data-target が無い'
        for t in targets:
            assert f'id="{t}"' in html

    def test_theme_high_cardinality_tools(self, authenticated_client, user):
        """テーマが多数でも壁にならない: 絞り込み入力・展開ボタン・上位折り畳み・data-label を備える。

        なぜ（UX修正）: 179銘柄で数十テーマになるとチップが壁になり選べない。
        使用数降順で上位のみ既定表示し、絞り込み入力と「他N件」でスケールさせる。
        JS挙動そのものは描画で担保できないため、必要な足場（入力・more・collapse・label）を検証する。
        """
        from stockdiary.models import StockDiary
        from tags.models import Tag
        # 多数テーマを付与
        for i in range(15):
            d = StockDiary.objects.create(
                user=user, stock_symbol=f'T{i:03d}', stock_name=f'銘柄{i}',
            )
            d.tags.add(Tag.objects.create(user=user, name=f'テーマ{i}'))
        html = authenticated_client.get(
            reverse('stockdiary:diary_summary'), {'lens': 'theme'}
        ).content.decode()
        assert 'id="ds-theme-filter"' in html          # 絞り込み入力
        assert 'id="ds-theme-more"' in html            # 展開ボタン
        assert 'data-collapse=' in html                # 上位のみ既定表示の足場
        assert 'data-label=' in html                   # 絞り込み対象ラベル

    def test_theme_chips_sorted_by_count_desc(self, authenticated_client, user):
        """テーマチップは使用数降順（既定選択が最大テーマ・未分類は末尾）。"""
        from stockdiary.models import StockDiary
        from tags.models import Tag
        big = Tag.objects.create(user=user, name='大テーマ')
        small = Tag.objects.create(user=user, name='小テーマ')
        for i in range(3):
            d = StockDiary.objects.create(user=user, stock_symbol=f'B{i}', stock_name=f'b{i}')
            d.tags.add(big)
        d = StockDiary.objects.create(user=user, stock_symbol='S0', stock_name='s0')
        d.tags.add(small)
        theme_keys = list(
            authenticated_client.get(
                reverse('stockdiary:diary_summary'), {'lens': 'theme'}
            ).context['theme_groups'].keys()
        )
        # 大テーマ(3) が 小テーマ(1) より前、'' (未分類) は末尾
        assert theme_keys.index('大テーマ') < theme_keys.index('小テーマ')
        if '' in theme_keys:
            assert theme_keys[-1] == ''

    def test_lens_links_carry_view_list(self, authenticated_client, sample_diary):
        """レンズ（状態/テーマ/銘柄/時系列）タブのリンクは view=list を持つ。

        なぜこのテストが必要か（回帰）:
          レンズタブはページ再読込を伴う <a> リンクだが、読込時スクリプトが
          常に table ビューを開いていたため、リスト表示中にレンズを選ぶと
          銘柄別（table）へ戻され「銘柄タブが再選択される」不具合があった。
          リンクに view=list を付け、読込時にこれを尊重してリストビューへ着地させる。
          そのサーバー側レンダリング（?view=list 付与）を担保する。
        """
        response = authenticated_client.get(reverse('stockdiary:diary_summary'))
        html = response.content.decode()
        # 全レンズリンクが view=list を含む（table へ戻されない）
        import re
        lens_links = re.findall(r'href="(\?lens=[^"]*)"[^>]*class="ds-lens', html)
        assert lens_links, 'レンズリンクが見つからない'
        for href in lens_links:
            assert 'view=list' in href, f'レンズリンクに view=list が無い: {href}'
