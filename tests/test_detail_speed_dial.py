"""詳細画面の入力動線をスピードダイヤルに統一：記録/取引/株式分割/仮説の追加。

追加はスピードダイヤルに集約、編集・削除は各所インラインのまま（本テストは追加導線を検証）。
"""
import pytest
from django.urls import reverse

from stockdiary.models import Thesis


@pytest.mark.django_db
def test_speed_dial_contains_all_add_actions(authenticated_client, sample_diary):
    resp = authenticated_client.get(reverse('stockdiary:detail', kwargs={'pk': sample_diary.pk}))
    assert resp.status_code == 200
    html = resp.content.decode()
    # 4つの追加アクションがスピードダイヤルに含まれる（data-action-id で判定）
    assert 'data-action-id="add-note"' in html
    assert 'data-action-id="add-transaction"' in html
    assert 'data-action-id="add-split"' in html
    assert 'data-action-id="add-thesis"' in html
    # 分割・仮説ともボトムシート方式に統一（他の入力と同じくモーダルで確実に表示）
    assert "openBottomSheet('addSplitSheet')" in html
    assert "openBottomSheet('addThesisSheet')" in html
    # 仮説追加シートの実体がページに存在する
    assert 'id="addThesisSheet"' in html
    # 保存後は ?view=thesis で記録タブの『仮説』ビューへ着地する導線を持つ。
    # 着地ロジック(goToThesisView)はインラインから static/js/diary-detail-page.js へ
    # 外部化したため、ページが同スクリプトを読み込むこと＋スクリプト実体に
    # 導線コードが存在することの2点で検証する。
    assert 'js/diary-detail-page.js' in html
    from pathlib import Path
    page_js = Path('static/js/diary-detail-page.js').read_text(encoding='utf-8')
    assert "getElementById('notes-tab')" in page_js
    assert "switchNotesView('thesis')" in page_js


@pytest.mark.django_db
def test_persistent_inline_add_rows_removed(authenticated_client, sample_diary_with_transaction):
    """追加導線はスピードダイヤルに一本化：各タブ上部の常設「追加」ボタン行(.tab-action-row)を撤去。

    編集・削除は取引カードのインラインに残す（showEditForm / deleteTransaction）。
    """
    resp = authenticated_client.get(
        reverse('stockdiary:detail', kwargs={'pk': sample_diary_with_transaction.pk})
    )
    assert resp.status_code == 200
    html = resp.content.decode()
    # 常設のインライン追加ボタン行は撤去済み（CSSコメントは class= を含まないため誤検知しない）
    assert 'class="tab-action-row"' not in html
    # 編集・削除のインライン導線は維持
    assert 'showEditForm(' in html
    assert 'deleteTransaction(' in html


@pytest.mark.django_db
def test_thesis_add_button_only_in_empty_state(authenticated_client, sample_diary):
    """仮説追加導線：仮説ゼロ時のみ常設CTA(.karte-add-thesis)を出し、
    既に仮説がある場合は撤去して FAB(スピードダイヤル)へ集約する。
    追加フォームは FAB・空状態CTAとも仮説ボトムシート(#addThesisSheet)で開く。"""
    detail_url = reverse('stockdiary:detail', kwargs={'pk': sample_diary.pk})

    # 仮説ゼロ：空状態CTAボタンが出る（class= で判定。CSS セレクタ文字列は誤検知しない）
    html = authenticated_client.get(detail_url).content.decode()
    assert 'class="karte-add-thesis"' in html
    assert '当時の仮説を記録する' in html
    # 空状態CTAも仮説ボトムシートを開く（FABと同じ単一の追加導線）
    assert "openBottomSheet('addThesisSheet')" in html
    # 旧インライン作成導線(data-thesis-create-url)はシート化に伴い撤去
    assert 'data-thesis-create-url=' not in html

    # 仮説を1件追加すると、常設の追加ボタンは消える（FAB＝シートに一本化）
    Thesis.objects.create(diary=sample_diary, claim='主張', basis='根拠')
    html2 = authenticated_client.get(detail_url).content.decode()
    assert 'class="karte-add-thesis"' not in html2
    assert '当時の仮説を記録する' not in html2
    # FAB の add-thesis 導線とシート本体は維持
    assert 'data-action-id="add-thesis"' in html2
    assert 'id="addThesisSheet"' in html2
    # テンプレートコメントが本文へ漏れていないこと（複数行 {# #} 事故の回帰防止）
    assert 'openThesisForm() が data-thesis-create-url' not in html2


@pytest.mark.django_db
def test_thesis_sheet_plain_post_creates_and_redirects(authenticated_client, sample_diary):
    """仮説ボトムシートは通常POST（HX-Requestなし）で送信される。

    バグ: 旧実装ではFABからHTMXで仮説フォームを起動していたが、移設後に
    タブ/ビュー切替との兼ね合いでフォームが表示されないことがあった。これを
    他の入力（取引・株式分割シート）と同じ通常POST→リダイレクト方式に統一する。
    通常POSTでは保存後に詳細(?view=thesis)へリダイレクトし、仮説ビューへ着地させる。
    """
    from stockdiary.models import Thesis
    create_url = reverse('stockdiary:thesis_create', kwargs={'diary_id': sample_diary.id})

    resp = authenticated_client.post(create_url, {
        'claim': '円安継続で輸出採算が改善する',
        'basis': '受注残が積み上がっている',
        'horizon': '6m',
    })
    # 通常POSTはHTMX部分ではなく詳細ページへのリダイレクト（仮説ビュー着地）
    assert resp.status_code == 302
    assert '?view=thesis' in resp['Location']
    assert Thesis.objects.filter(diary=sample_diary, claim='円安継続で輸出採算が改善する').exists()


@pytest.mark.django_db
def test_thesis_htmx_post_still_returns_partial(authenticated_client, sample_diary):
    """インライン編集（HTMX）からのPOSTは従来どおり検証ループ部分テンプレートを返す
    （リダイレクトしない）。シート化で通常POSTを足したことの回帰防止。"""
    create_url = reverse('stockdiary:thesis_create', kwargs={'diary_id': sample_diary.id})
    resp = authenticated_client.post(create_url, {
        'claim': 'HTMX経由の主張',
        'horizon': '6m',
    }, HTTP_HX_REQUEST='true')
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'id="karte-block"' in html
    assert 'HTMX経由の主張' in html


@pytest.mark.django_db
def test_overview_tag_dedup_and_thesis_relocated_to_notes_tab(authenticated_client, sample_diary, user):
    """① 概要タブのタグ重複を解消する。
    ④ 検証ループ(仮説)は概要タブから「記録」タブの『仮説』ビューへ移設し、
       テーマ別/活動と同じトグル並びに統一する（UX の自然さ向上）。

    旧仕様では検証ループを概要タブの投資理由直後に固めていたが、記録系の導線（テーマ別・活動）
    と並べた方が自然なため記録タブへ移した。概要側の目次・本文に仮説ブロックが残っていると
    「隠れたタブ内要素へジャンプする死にリンク」になるため、その回帰も防ぐ。

    注: サイドバー目次は本文（背景）が長い記録でだけ表示する仕様になったため
    （短い記録では飛ぶ意味が薄くカードを畳む・Phase3-4）、目次リンクの回帰確認は
    背景を十分長くした状態で行う。
    """
    from tags.models import Tag
    sample_diary.tags.add(Tag.objects.create(user=user, name='長期投資'))
    sample_diary.reason = 'この銘柄の背景。' * 80  # 目次が出る長さ（>400字）にする
    sample_diary.save(update_fields=['reason'])

    html = authenticated_client.get(
        reverse('stockdiary:detail', kwargs={'pk': sample_diary.pk})
    ).content.decode()

    # ① タグ表示は「テーマ×感応」に一本化。素のサイドバー用チップ(sidebar-tags)は重複出力しない
    assert 'id="section-theme-sensitivity"' in html
    assert 'class="sidebar-tags"' not in html
    # サイドバー目次からテーマ×感応へジャンプできる（重複の代わりの導線）
    assert 'data-target="section-theme-sensitivity"' in html

    # ④ 検証ループ(karte-block)は記録タブの『仮説』ビュー(#notes-view-thesis)内へ移設
    assert html.index('id="notes-content"') < html.index('id="karte-block"')
    assert html.index('id="notes-view-thesis"') < html.index('id="karte-block"')
    # トグルは テーマ別 → 仮説 → 活動 の並び（仮説は中央）
    assert html.index('data-view="topic"') < html.index('data-view="thesis"') < html.index('data-view="activity"')
    # 概要タブ目次に「仮説と検証」への死にリンクが残っていない
    assert 'data-target="karte-block"' not in html


@pytest.mark.django_db
def test_timeline_tab_removed_from_top_level(authenticated_client, sample_diary):
    """② タブ数削減: 旧「時系列(活動履歴)」トップレベルタブを撤去（6→5タブ）。"""
    html = authenticated_client.get(
        reverse('stockdiary:detail', kwargs={'pk': sample_diary.pk})
    ).content.decode()
    assert 'data-bs-target="#events-content"' not in html
    # 既存の主要タブは維持
    for target in ('#basic-content', '#notes-content', '#transactions-content', '#related-content'):
        assert f'data-bs-target="{target}"' in html
    # テンプレートコメントが本文へ漏れていないこと（複数行 {# #} 事故の回帰防止）
    assert 'event_timeline でも出す' not in html
    assert 'フィルタの参照元として DOM に常設' not in html
