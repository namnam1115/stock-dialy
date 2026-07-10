"""Phase 8a: 検証ループ（Thesis / Verdict）のテスト"""
import pytest
from datetime import date

from django.template.loader import render_to_string
from django.urls import reverse

from stockdiary.models import Thesis, Verdict

pytestmark = pytest.mark.django_db(transaction=True)


class TestVerdictQuadrant:
    """意思決定の質 × 結果 の2×2分類（成長OSの心臓）"""

    def _make(self, diary, hyp, pnl):
        thesis = Thesis.objects.create(diary=diary, claim='テスト主張')
        return Verdict.objects.create(thesis=thesis, hypothesis_result=hyp, pnl_result=pnl)

    def test_hit_profit_is_skill(self, sample_diary):
        v = self._make(sample_diary, Verdict.HYP_HIT, Verdict.PNL_PROFIT)
        assert v.quadrant == 'skill'
        assert v.quadrant_label == '再現すべき勝ち'

    def test_hit_loss_is_unlucky(self, sample_diary):
        # 仮説は正しいが損失 = 一級市民として表現できる
        v = self._make(sample_diary, Verdict.HYP_HIT, Verdict.PNL_LOSS)
        assert v.quadrant == 'unlucky'

    def test_miss_profit_is_lucky(self, sample_diary):
        # 仮説は外れたが利益（偶然・危険）
        v = self._make(sample_diary, Verdict.HYP_MISS, Verdict.PNL_PROFIT)
        assert v.quadrant == 'lucky'

    def test_miss_loss_is_discipline(self, sample_diary):
        v = self._make(sample_diary, Verdict.HYP_MISS, Verdict.PNL_LOSS)
        assert v.quadrant == 'discipline'

    def test_stars(self, sample_diary):
        v = self._make(sample_diary, Verdict.HYP_HIT, Verdict.PNL_PROFIT)
        v.decision_quality = 3
        assert v.stars == '★★★☆☆'


class TestThesisDue:
    def test_is_due_when_past_and_open(self, sample_diary):
        t = Thesis.objects.create(diary=sample_diary, claim='c',
                                  review_due_date=date(2000, 1, 1))
        assert t.is_due is True

    def test_not_due_when_verified(self, sample_diary):
        t = Thesis.objects.create(diary=sample_diary, claim='c',
                                  review_due_date=date(2000, 1, 1),
                                  status=Thesis.STATUS_VERIFIED)
        assert t.is_due is False


class TestThesisVerifyViews:
    def test_create_thesis_via_htmx(self, authenticated_client, sample_diary):
        url = reverse('stockdiary:thesis_create', args=[sample_diary.id])
        # GET returns the form
        r = authenticated_client.get(url, HTTP_HX_REQUEST='true')
        assert r.status_code == 200
        # POST creates the thesis and returns the karte block
        r = authenticated_client.post(url, {
            'claim': '円安継続で輸出採算が改善する',
            'horizon': '6m',
            'worst_case': '円高反転',
        }, HTTP_HX_REQUEST='true')
        assert r.status_code == 200
        sample_diary.refresh_from_db()
        thesis = Thesis.objects.get(diary=sample_diary)
        assert thesis.claim == '円安継続で輸出採算が改善する'
        # review_due_date は horizon から自動補完される
        assert thesis.review_due_date is not None

    def test_create_thesis_with_basis_tags(self, authenticated_client, sample_diary, sample_tags):
        # チップ＋サジェストUIは name="basis_tags" の hidden input を複数送る
        url = reverse('stockdiary:thesis_create', args=[sample_diary.id])
        r = authenticated_client.post(url, {
            'claim': 'テーマで持続する',
            'horizon': '6m',
            'basis_tags': [sample_tags[0].id, sample_tags[1].id],
        }, HTTP_HX_REQUEST='true')
        assert r.status_code == 200
        thesis = Thesis.objects.get(diary=sample_diary)
        assert set(thesis.basis_tags.values_list('id', flat=True)) == {sample_tags[0].id, sample_tags[1].id}

    def test_thesis_form_get_provides_all_tags(self, authenticated_client, sample_diary, sample_tags):
        url = reverse('stockdiary:thesis_create', args=[sample_diary.id])
        r = authenticated_client.get(url, HTTP_HX_REQUEST='true')
        assert r.status_code == 200
        names = [t['name'] for t in r.context['all_tags']]
        assert sample_tags[0].name in names

    def test_verify_creates_verdict_and_sets_status(self, authenticated_client, sample_diary):
        thesis = Thesis.objects.create(diary=sample_diary, claim='主張')
        url = reverse('stockdiary:thesis_verify', args=[sample_diary.id, thesis.id])
        r = authenticated_client.get(url, HTTP_HX_REQUEST='true')
        assert r.status_code == 200
        r = authenticated_client.post(url, {
            'hypothesis_result': Verdict.HYP_MISS,
            'pnl_result': Verdict.PNL_PROFIT,
            'decision_quality': 2,
            'learning': '為替単独を根拠に投資しない',
        }, HTTP_HX_REQUEST='true')
        assert r.status_code == 200
        thesis.refresh_from_db()
        assert thesis.status == Thesis.STATUS_VERIFIED
        assert thesis.verdict.quadrant == 'lucky'
        assert thesis.verdict.learning == '為替単独を根拠に投資しない'

    def test_holding_verdict_keeps_thesis_open_and_reschedules(self, authenticated_client, sample_diary):
        """保有中の答え合わせ（中間検証）は仮説を閉じず、期日を先送りして見張り続ける。

        なぜこのテストがあるか:
          pnl_result=保有中で検証すると無条件に status=verified になり、is_due の
          母集団から外れて想起が二度と来なくなっていた。保有継続中こそ見張りが
          必要なので、open のまま検証予定日を未来に更新する不変条件を固定する。
          確定損益（profit/loss/flat）での検証は従来どおり verified に閉じる。
        """
        from datetime import date as _date, timedelta as _td
        thesis = Thesis.objects.create(
            diary=sample_diary, claim='c', horizon='6m',
            review_due_date=_date.today() - _td(days=1))
        url = reverse('stockdiary:thesis_verify', args=[sample_diary.id, thesis.id])
        r = authenticated_client.post(url, {
            'hypothesis_result': Verdict.HYP_HIT,
            'pnl_result': Verdict.PNL_HOLDING,
        }, HTTP_HX_REQUEST='true')
        assert r.status_code == 200
        thesis.refresh_from_db()
        assert thesis.status == Thesis.STATUS_OPEN
        assert thesis.review_due_date > _date.today()
        assert thesis.verdict.pnl_result == Verdict.PNL_HOLDING
        assert thesis.is_due is False  # 想起は次の期日まで静かになる

        # 確定損益で見直すと閉じる
        r = authenticated_client.post(url, {
            'hypothesis_result': Verdict.HYP_HIT,
            'pnl_result': Verdict.PNL_PROFIT,
        }, HTTP_HX_REQUEST='true')
        assert r.status_code == 200
        thesis.refresh_from_db()
        assert thesis.status == Thesis.STATUS_VERIFIED

    def test_verify_saves_with_minimal_input(self, authenticated_client, sample_diary):
        """答え合わせは最小2問（当否＋損益）で保存できる。

        なぜこのテストがあるか:
          検証フォームは6項目あり「答え合わせが重い」の一因だった。判断の質・
          見落とし・再現フラグは折りたたみの任意入力とし、未送信でも保存できる
          （decision_quality はモデル既定の3に落ちる）挙動を固定する。
        """
        thesis = Thesis.objects.create(diary=sample_diary, claim='主張')
        url = reverse('stockdiary:thesis_verify', args=[sample_diary.id, thesis.id])
        r = authenticated_client.post(url, {
            'hypothesis_result': Verdict.HYP_HIT,
            'pnl_result': Verdict.PNL_PROFIT,
        }, HTTP_HX_REQUEST='true')
        assert r.status_code == 200
        thesis.refresh_from_db()
        assert thesis.verdict.decision_quality == 3
        assert thesis.verdict.quadrant == 'skill'

    def test_cannot_touch_others_diary(self, authenticated_client, another_user):
        from stockdiary.models import StockDiary
        other = StockDiary.objects.create(user=another_user, stock_name='他人', stock_symbol='9999')
        url = reverse('stockdiary:thesis_create', args=[other.id])
        r = authenticated_client.post(url, {'claim': 'x', 'horizon': '6m'}, HTTP_HX_REQUEST='true')
        assert r.status_code == 404


class TestThesisEntryOnDiaryCreate:
    """日記作成フォームの「確認の目印」から検証ループが始まる。

    なぜこのテストがあるか:
      仮説（Thesis）の入力が作成フローに無く、作成後に詳細ページ→記録タブ→
      仮説ビューまで掘って書きに行く必要があった（ループの入口の欠落）。
      書く動機が最も高い「作成の瞬間」に目印を1行書けば Thesis が自動作成される
      挙動（claim 自動生成・horizon=next_earnings・検証予定日補完）を固定する。
    """

    def _post(self, client, **extra):
        data = {
            'stock_name': 'テスト製作所', 'stock_symbol': '9876',
            'currency': 'JPY', 'reason': '本文',
        }
        data.update(extra)
        return client.post(reverse('stockdiary:create'), data)

    def test_checkpoint_on_create_makes_thesis(self, authenticated_client):
        from datetime import date as _date
        r = self._post(authenticated_client,
                       thesis_checkpoint='次決算の営業利益率',
                       thesis_checkpoint_direction='up')
        assert r.status_code == 302
        from stockdiary.models import StockDiary
        diary = StockDiary.objects.get(stock_name='テスト製作所')
        thesis = diary.theses.get()
        assert thesis.checkpoint == '次決算の営業利益率'
        assert thesis.checkpoint_direction == 'up'
        assert thesis.claim == '次決算の営業利益率が上がる'
        assert thesis.horizon == 'next_earnings'
        assert thesis.review_due_date and thesis.review_due_date > _date.today()

    def test_no_checkpoint_creates_no_thesis(self, authenticated_client):
        r = self._post(authenticated_client)
        assert r.status_code == 302
        from stockdiary.models import StockDiary
        diary = StockDiary.objects.get(stock_name='テスト製作所')
        assert diary.theses.count() == 0

    def test_invalid_direction_is_dropped(self, authenticated_client):
        r = self._post(authenticated_client,
                       thesis_checkpoint='増配の発表',
                       thesis_checkpoint_direction='hacked')
        assert r.status_code == 302
        from stockdiary.models import StockDiary
        thesis = StockDiary.objects.get(stock_name='テスト製作所').theses.get()
        assert thesis.checkpoint_direction == ''
        assert thesis.claim == '増配の発表'

    def test_create_form_shows_checkpoint_field(self, authenticated_client):
        r = authenticated_client.get(reverse('stockdiary:create'))
        assert 'thesis_checkpoint' in r.content.decode()


class TestVerifyDeepLink:
    """?verify=<thesis_id> で検証フォームがサーバー側から描画される。

    なぜこのテストがあるか:
      想起カード・ライブラリの「答え合わせをする」は detail?verify=<id> に着地する。
      以前は load 後にタブ切替→300ms 待って HTMX ボタンを JS で click する多段連鎖で
      フォームを開いており、HTMX 未ロード（CDN 遮断）やタイミング競合で無反応になった。
      答え合わせはループ最重要の導線なので、サーバー側で最初から描画する挙動を固定する。
    """

    def test_verify_param_renders_verdict_form(self, authenticated_client, sample_diary):
        thesis = Thesis.objects.create(diary=sample_diary, claim='c')
        url = reverse('stockdiary:detail', args=[sample_diary.id])
        r = authenticated_client.get(f'{url}?verify={thesis.id}')
        assert r.status_code == 200
        html = r.content.decode()
        verify_url = reverse('stockdiary:thesis_verify', args=[sample_diary.id, thesis.id])
        assert f'hx-post="{verify_url}"' in html
        assert '検証（答え合わせ）' in html

    def test_invalid_verify_param_falls_back_to_karte_block(self, authenticated_client, sample_diary):
        Thesis.objects.create(diary=sample_diary, claim='c')
        url = reverse('stockdiary:detail', args=[sample_diary.id])
        for param in ('999999', 'abc', ''):
            r = authenticated_client.get(f'{url}?verify={param}')
            assert r.status_code == 200
            assert '検証（答え合わせ）' not in r.content.decode()

    def test_verify_param_ignores_other_users_thesis(self, authenticated_client, sample_diary, another_user):
        from stockdiary.models import StockDiary
        other = StockDiary.objects.create(user=another_user, stock_name='他人', stock_symbol='9999')
        other_thesis = Thesis.objects.create(diary=other, claim='x')
        url = reverse('stockdiary:detail', args=[sample_diary.id])
        r = authenticated_client.get(f'{url}?verify={other_thesis.id}')
        assert r.status_code == 200
        assert '検証（答え合わせ）' not in r.content.decode()


class TestMemoDiaryThesis:
    """買っていない監視メモの日記でも仮説を記録できる（学びの土台）。"""

    def test_add_thesis_button_shown_for_memo_diary(self, sample_memo_diary):
        # 取引のないメモ日記でも「当時の仮説を記録する」導線が出る
        assert sample_memo_diary.is_memo is True
        rendered = render_to_string('stockdiary/partials/_karte_block.html',
                                    {'diary': sample_memo_diary, 'theses': []})
        assert '当時の仮説を記録する' in rendered

    def test_create_thesis_on_memo_diary(self, authenticated_client, sample_memo_diary):
        url = reverse('stockdiary:thesis_create', args=[sample_memo_diary.id])
        r = authenticated_client.post(url, {
            'claim': '次の決算が良ければ買う。需要回復が本物か見極める',
            'horizon': '3m',
        }, HTTP_HX_REQUEST='true')
        assert r.status_code == 200
        thesis = Thesis.objects.get(diary=sample_memo_diary)
        assert thesis.claim.startswith('次の決算が良ければ')
        # 取引が無くても検証予定日は補完される（基準は今日）
        assert thesis.review_due_date is not None

    def test_verify_memo_diary_thesis(self, authenticated_client, sample_memo_diary):
        # 買わなかったが「仮説は当たっていた」= 機会損失という学びを残せる
        thesis = Thesis.objects.create(diary=sample_memo_diary, claim='需要回復が本物')
        url = reverse('stockdiary:thesis_verify', args=[sample_memo_diary.id, thesis.id])
        r = authenticated_client.post(url, {
            'hypothesis_result': Verdict.HYP_HIT,
            'pnl_result': Verdict.PNL_FLAT,
            'decision_quality': 2,
            'learning': '確信があるなら、監視で終わらせず打診買いする',
        }, HTTP_HX_REQUEST='true')
        assert r.status_code == 200
        verdict = Thesis.objects.get(diary=sample_memo_diary).verdict
        assert verdict.hypothesis_result == Verdict.HYP_HIT
        assert verdict.learning.startswith('確信があるなら')


class TestRecallDueTheses:
    """Phase 8b: 検証予定日が来た未検証の仮説をホーム想起に出す"""

    def test_due_open_thesis_surfaces(self, sample_diary):
        from stockdiary.services.recall_service import RecallService
        t = Thesis.objects.create(diary=sample_diary, claim='検証待ち',
                                  review_due_date=date(2000, 1, 1))
        recall = RecallService.build(sample_diary.user)
        assert t in recall['due_theses']
        assert recall['has_content'] is True

    def test_verified_thesis_does_not_surface(self, sample_diary):
        from stockdiary.services.recall_service import RecallService
        Thesis.objects.create(diary=sample_diary, claim='済', review_due_date=date(2000, 1, 1),
                              status=Thesis.STATUS_VERIFIED)
        recall = RecallService.build(sample_diary.user)
        assert recall['due_theses'] == []

    def test_future_due_does_not_surface(self, sample_diary):
        from stockdiary.services.recall_service import RecallService
        Thesis.objects.create(diary=sample_diary, claim='未来', review_due_date=date(2999, 1, 1))
        recall = RecallService.build(sample_diary.user)
        assert recall['due_theses'] == []


class TestRecallQueue:
    """想起をスワイプ式の単一フォーカスキューへ統合する（ZIP の RecallZone）。

    「今日の想起」と「答え合わせを待つ仮説」を1つのキューにまとめる UI 改修に伴い、
    RecallService.build に queue キーを追加した。検証予定日が来た仮説が
    理由付き(kind='due', thesis_id 付き)で先頭に並ぶことを担保する。
    """

    def test_due_thesis_is_first_queue_item_with_reason(self, sample_diary):
        from stockdiary.services.recall_service import RecallService
        t = Thesis.objects.create(diary=sample_diary, claim='検証待ちの主張',
                                  review_due_date=date(2000, 1, 1))
        recall = RecallService.build(sample_diary.user)
        assert recall['queue'], 'queue が空であってはならない'
        head = recall['queue'][0]
        assert head['kind'] == 'due'
        assert head['reason'] == '検証予定日が来た'
        assert head['thesis_id'] == t.id
        assert head['diary_id'] == sample_diary.id
        assert head['claim'] == '検証待ちの主張'
        # テンプレートが参照するキーが揃っていること
        for key in ('reason_icon', 'stock_name', 'code', 'meta'):
            assert key in head

    def test_queue_empty_without_content(self, sample_diary):
        from stockdiary.services.recall_service import RecallService
        recall = RecallService.build(sample_diary.user)
        assert recall['queue'] == []
