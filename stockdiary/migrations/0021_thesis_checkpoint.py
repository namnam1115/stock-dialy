# 仮説キャプチャの賭け化（→ docs/thesis_capture_redesign.md）:
#   確認の目印(checkpoint)＋向き(checkpoint_direction) を追加し、
#   想定検証期間(horizon) の既定を「次の決算まで」に変更する。
# ※ makemigrations が同時検出した memo 削除・diary FK 変更は本件と無関係な
#   既存ドリフト（improvement_plan §9 の別タスク）のため、原則5「変更を混ぜない」
#   に従い本 migration には含めない。
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stockdiary', '0020_earnings_calendar_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='thesis',
            name='checkpoint',
            field=models.CharField(blank=True, help_text='答え合わせで見るものを1つ。数字に限らずKPI・イベント結果・定性チェックでも可。例: 次決算の資金利益', max_length=200, verbose_name='確認の目印'),
        ),
        migrations.AddField(
            model_name='thesis',
            name='checkpoint_direction',
            field=models.CharField(blank=True, choices=[('up', '上がる'), ('down', '下がる'), ('flat', '横ばい'), ('happened', '実現する'), ('not_happened', '起きない')], max_length=16, verbose_name='目印の向き'),
        ),
        migrations.AlterField(
            model_name='thesis',
            name='horizon',
            field=models.CharField(choices=[('next_earnings', '次の決算まで'), ('3m', '3ヶ月'), ('6m', '6ヶ月'), ('1y', '1年'), ('long', '長期（1年超）')], default='next_earnings', max_length=20, verbose_name='想定検証期間'),
        ),
    ]
