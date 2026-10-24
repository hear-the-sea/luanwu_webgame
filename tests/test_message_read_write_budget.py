import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from battle.models import BattleReport
from gameplay.models import Message
from gameplay.services.utils.cache import CacheKeys
from gameplay.services.utils.messages import list_messages, mark_messages_read, unread_message_count


def _create_report_message(manor):
    now = timezone.now()
    report = BattleReport.objects.create(
        manor=manor,
        opponent_name="查询预算对手",
        winner="attacker",
        starts_at=now,
        completed_at=now,
        rounds=[{"details": "战斗详情" * 1000}],
    )
    message = Message.objects.create(manor=manor, kind=Message.Kind.BATTLE, title="战报消息", battle_report=report)
    return report, message


@pytest.mark.django_db
def test_message_list_loads_report_identifier_without_report_payload(manor_with_user):
    manor, client = manor_with_user
    report, message = _create_report_message(manor)

    with CaptureQueriesContext(connection) as captured:
        rows = list(list_messages(manor))
        assert rows[0].battle_report_id == report.pk

    assert len(captured) == 1
    assert all(BattleReport._meta.db_table not in query["sql"] for query in captured)

    with CaptureQueriesContext(connection) as page_queries:
        response = client.get(reverse("gameplay:messages"))

    assert response.status_code == 200
    assert message.title in response.content.decode()
    assert all(BattleReport._meta.db_table not in query["sql"] for query in page_queries)


@pytest.mark.django_db
@pytest.mark.parametrize("is_json", [False, True])
def test_message_report_redirect_never_fetches_report_payload(manor_with_user, is_json):
    manor, client = manor_with_user
    report, message = _create_report_message(manor)
    headers = {"HTTP_ACCEPT": "application/json"} if is_json else {}

    with CaptureQueriesContext(connection) as captured:
        response = client.get(reverse("gameplay:view_message", kwargs={"pk": message.pk}), **headers)

    expected_url = reverse("battle:report_detail", kwargs={"pk": report.pk})
    if is_json:
        assert response.status_code == 200
        assert response.json()["redirect_url"] == expected_url
    else:
        assert response.status_code == 302
        assert response["Location"] == expected_url
    assert all(BattleReport._meta.db_table not in query["sql"] for query in captured)


@pytest.mark.django_db
def test_repeated_mark_read_does_not_rewrite_read_messages(manor_with_user):
    manor, _client = manor_with_user
    unread = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="待阅读")
    read = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="已阅读", is_read=True)
    unrelated = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="未选择")
    cache.set(CacheKeys.unread_count(manor.id), 2)
    affected_rows = []

    def record_update(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if sql.lstrip().upper().startswith("UPDATE"):
            affected_rows.append(context["cursor"].rowcount)
        return result

    with connection.execute_wrapper(record_update):
        mark_messages_read(manor, [unread.pk, read.pk, unread.pk])
        mark_messages_read(manor, [unread.pk, read.pk])

    assert affected_rows == [1, 0]
    unread.refresh_from_db()
    unrelated.refresh_from_db()
    assert unread.is_read is True
    assert unrelated.is_read is False
    assert unread_message_count(manor) == Message.objects.filter(manor=manor, is_read=False).count()
