# Generated manually to add an index used by RecallService._build_anniversary

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("stockdiary", "0022_margin_short_tracking"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="stockdiary",
            index=models.Index(
                fields=["user", "-created_at"], name="stockdiary__user_id_6f898e_idx"
            ),
        ),
    ]
