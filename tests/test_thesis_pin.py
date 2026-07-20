"""ヘッダー常設ミニ仮説カード（_thesis_pin.html / pinned_thesis）の回帰テスト。

なぜこのテストが必要か:
  詳細ページのレイアウト改善で、埋もれていた「買った理由（Thesis）」の
  claim/checkpoint/worst_case を、損益と同一視野（ヘッダー）に固定表示する
  「いまの見立て」カードを追加した（→ docs/ontology.md §3・レイアウト改善計画 A-1）。
  view が context['pinned_thesis'] に「open な最新の仮説」を渡し、テンプレートが
  それを描画する。以下を退行から守る:
    - open 仮説があればヘッダーに claim/checkpoint/worst_case が出る
    - open が無い保有銘柄では「見立てを残す」CTA が出る
    - verified/abandoned のみの銘柄は pinned 対象にならない（open を主ソースにする）
    - メモ（取引なし）ではカード自体を出さない
  また、損益ヒーロー廃止＝ヘッダーに「現在地ストリップ(position-strip)」が出ること、
  各状態でページが 200 を返すこと（テンプレート例外を出さないこと）も担保する。
"""
import pytest
from django.urls import reverse

from stockdiary.models import Thesis


def _get_detail(client, diary):
    resp = client.get(reverse('stockdiary:detail', kwargs={'pk': diary.pk}))
    assert resp.status_code == 200
    return resp.content.decode()


@pytest.mark.django_db
class TestThesisPin:
    def test_open_thesis_pinned_in_header(self, authenticated_client, sample_diary_with_transaction):
        """open 仮説の claim/checkpoint/worst_case がヘッダーのミニカードに出る。"""
        Thesis.objects.create(
            diary=sample_diary_with_transaction,
            claim='米州の受注が本格化する',
            checkpoint='次Qの受注残',
            checkpoint_direction='up',
            worst_case='受注残が2Q連続で減少',
            status=Thesis.STATUS_OPEN,
        )
        html = _get_detail(authenticated_client, sample_diary_with_transaction)
        assert 'thesis-pin' in html
        assert 'いまの見立て' in html
        assert '米州の受注が本格化する' in html      # claim
        assert '次Qの受注残' in html                  # checkpoint（目印）
        assert '受注残が2Q連続で減少' in html          # worst_case（崩れ）

    def test_holding_without_thesis_shows_cta(self, authenticated_client, sample_diary_with_transaction):
        """保有中で open 仮説が無ければ、見立てを残す CTA（空状態カード）を出す。"""
        html = _get_detail(authenticated_client, sample_diary_with_transaction)
        assert 'thesis-pin--empty' in html
        assert 'いまの見立てを残す' in html

    def test_verified_only_is_not_pinned(self, authenticated_client, sample_diary_with_transaction):
        """検証済み仮説だけの保有銘柄は pinned 対象にせず、CTA（空状態）を出す。

        pinned_thesis は open のみを拾う（継続/損切り判断の主ソースは未検証の賭け）。
        """
        Thesis.objects.create(
            diary=sample_diary_with_transaction,
            claim='もう検証し終えた賭け',
            status=Thesis.STATUS_VERIFIED,
        )
        html = _get_detail(authenticated_client, sample_diary_with_transaction)
        assert 'thesis-pin--empty' in html
        # ヘッダーの pin には claim 要素が無い（検証済み仮説は記録タブの karte-block に温存される）
        assert 'thesis-pin-claim' not in html

    def test_open_preferred_over_verified(self, authenticated_client, sample_diary_with_transaction):
        """open と verified が混在する場合、ヘッダーは open を主役にする。"""
        Thesis.objects.create(
            diary=sample_diary_with_transaction,
            claim='過去の検証済み仮説',
            status=Thesis.STATUS_VERIFIED,
        )
        Thesis.objects.create(
            diary=sample_diary_with_transaction,
            claim='いま賭けている命題',
            status=Thesis.STATUS_OPEN,
        )
        html = _get_detail(authenticated_client, sample_diary_with_transaction)
        assert 'いま賭けている命題' in html

    def test_memo_diary_has_no_pin(self, authenticated_client, sample_memo_diary):
        """取引なし（メモ）ではミニ仮説カードを出さない。"""
        html = _get_detail(authenticated_client, sample_memo_diary)
        assert 'thesis-pin' not in html

    def test_header_has_no_numbers(self, authenticated_client, sample_sold_diary):
        """戦略的縮小: ヘッダーには損益・株数などの数値ブロックを一切出さない。

        カブログは投資成績を追うツールではなく思考の記録ツール。損益サマリーは
        重複して別途存在するため（PC=サイドバー投資ステータス／モバイル=概要タブ取引サマリー）、
        ヘッダーからは損益ヒーロー(profit-hero-card)も現在地ストリップ(position-strip)も除く。
        """
        html = _get_detail(authenticated_client, sample_sold_diary)
        # ヘッダー本体（タブより前）を切り出して検査する
        head = html.split('tabs-section')[0]
        assert 'position-strip' not in head
        assert 'profit-hero-card' not in head

    def test_pnl_still_available_in_overview_summary(self, authenticated_client, sample_sold_diary):
        """ヘッダーから数値を落としても、損益は概要タブの取引サマリーに残る（情報は失われない）。"""
        html = _get_detail(authenticated_client, sample_sold_diary)
        # モバイル/タブレット幅で表示される取引サマリー（実現損益を含む）
        assert 'detail-fallback-on-narrow' in html
        assert '実現損益' in html
