"""remap_diary_tags コマンドのトークン置換ロジック（_replace_token）のテスト。

タグは reason＋ノートの `@タグ` の和集合から同期されるため、再マッピングは
テキストをトークン単位で厳密一致置換する必要がある。特に `@半導体` を
`@半導体製造装置` の一部に誤マッチさせない点を守る。
"""
from stockdiary.management.commands.remap_diary_tags import _replace_token


class TestReplaceToken:
    def test_rename_exact_token(self):
        assert _replace_token('`@半導体` `@AI`', '半導体', 'イメージセンサー') == '`@イメージセンサー` `@AI`'

    def test_rename_does_not_corrupt_longer_token(self):
        # @半導体 の rename が @半導体製造装置 を壊さない（前方一致の罠）
        out = _replace_token('@半導体 と @半導体製造装置', '半導体', 'イメージセンサー')
        assert out == '@イメージセンサー と @半導体製造装置'

    def test_rename_preserves_direction_arrow(self):
        assert _replace_token('@円安↑ の話', '円安', '為替') == '@為替↑ の話'

    def test_remove_backticked_token_leaves_no_empty_backticks(self):
        out = _replace_token('`@国土強靭化` `@建設補修`', '国土強靭化', None)
        assert out == '`@建設補修`'
        assert '``' not in out

    def test_remove_bare_token_consumes_trailing_space(self):
        out = _replace_token('@国土強靭化 の需要。@建設補修 中心。', '国土強靭化', None)
        assert out == 'の需要。@建設補修 中心。'

    def test_remove_does_not_corrupt_longer_token(self):
        out = _replace_token('@半導体 と @半導体製造装置', '半導体', None)
        assert out == 'と @半導体製造装置'

    def test_empty_text(self):
        assert _replace_token('', '半導体', 'イメージセンサー') == ''
        assert _replace_token(None, '半導体', None) is None
