"""StockDiary.memo を migration state から正式に外す（＋DB列を冪等に落とす）。

背景（不具合）:
  memo は reason へ統合済みでモデルからは削除されているが、0012_remove_stockdiary_memo は
  RunPython でDB列を落とすだけで **migration state** を直していなかった。このため:
    (1) makemigrations が毎回 RemoveField('stockdiary','memo') を再生成し続ける。
    (2) state に memo が残るため、SQLite が後続の AlterField 等で stockdiary テーブルを
        state から再構築する際に memo 列（NOT NULL・デフォルト無し）を**復活**させ、
        新規 StockDiary の作成が `NOT NULL constraint failed: stockdiary_stockdiary.memo`
        で失敗していた（状態ドリフト）。

対応:
  - state から memo を確実に外す（RemoveField を state_operations に）→ 再生成・再構築を止める。
  - DB列は「存在するときだけ DROP」する冪等操作にする（本番＝既に無ければ no-op、
    ドリフトで残っている環境＝ここで除去）。0012 と同じ安全な形を踏襲する。
  - あわせて Thesis.diary の FK 定義（related_name='theses'）を state に反映する
    （モデル変更が state に取り込まれておらず makemigrations が差分検出していたため）。

テストは --nomigrations のため本 migration は実行されない（モデル基準で memo 無し）。
"""
import django.db.models.deletion
from django.db import migrations, models


TABLE = 'stockdiary_stockdiary'
COLUMN = 'memo'


def _column_exists(schema_editor):
    conn = schema_editor.connection
    with conn.cursor() as cursor:
        return COLUMN in [
            col.name for col in conn.introspection.get_table_description(cursor, TABLE)
        ]


def drop_memo_if_exists(apps, schema_editor):
    if _column_exists(schema_editor):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(f'ALTER TABLE {TABLE} DROP COLUMN {COLUMN}')


def add_memo_if_missing(apps, schema_editor):
    if not _column_exists(schema_editor):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(f'ALTER TABLE {TABLE} ADD COLUMN {COLUMN} text')


class Migration(migrations.Migration):

    dependencies = [
        ('stockdiary', '0023_stockdiary_user_created_at_idx'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name='stockdiary', name='memo'),
            ],
            database_operations=[
                migrations.RunPython(drop_memo_if_exists, add_memo_if_missing),
            ],
        ),
        migrations.AlterField(
            model_name='thesis',
            name='diary',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='theses',
                to='stockdiary.stockdiary',
            ),
        ),
    ]
