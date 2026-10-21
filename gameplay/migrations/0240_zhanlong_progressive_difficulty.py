"""战龙前三关逐级增强，并固化当前五虎生命，保留玩家与奖励数据。"""

from copy import deepcopy

from django.core.cache import cache
from django.db import migrations, transaction

MISSION_UPDATES = {
    "zhanlong_yanshan_shizi": {
        "description": "北地入秋，燕山古道霜寒马瘦。连年兵乱让沿途村庄十室九空，逃难百姓带着仅剩的粮食与家当，沿山道向南求生。溃兵出身的寨主铁背熊却盘踞险隘，劫粮掠财，将不肯交出财物的百姓扣在山口。商路由此断绝，附近村民也无人敢再上山。\n"
        "\n"
        "庄园主押运物资途经山下，忽有伤者拦路求援：一名白袍小将为救被困百姓，独骑闯入匪阵，已在山口苦战多时。此人自称常山赵云，枪法凌厉，却因顾念身后老幼，始终无法放手突围。铁背熊披甲持锤堵住正道，故意招锋引敌，坡上弓手正趁他吸引火力之际张弓搭箭。\n"
        "\n"
        "山道狭窄，伏兵将成。还请庄园主率门客抢攻坡口，击溃拦路匪众，助这位素未谋面的白袍小将解开眼前危局。坡上三千八百箭神先发制人，拳系护院可凭远程防护抵住开场箭雨，并以克弓之长压制弓阵；门客仍须应付寨主引敌与重锤，兼顾破阵和攻坚。\n",
        "enemy_troops": {"dao_sheng": 500, "qiang_wang": 500, "arrow_god": 3800},
    },
    "zhanlong_changban_tuwei": {
        "description": "荆襄骤变，曹军铁骑昼夜南下。刘备携百姓撤往江陵，军民相杂，车仗连绵，行至当阳长坂时，被曹军前锋追及。仓促之间，队伍四散，粮车倾覆，家眷失落；漫山遍野尽是奔逃的人影，呼救声淹没在马蹄与战鼓之中。\n"
        "\n"
        "混乱之际，有人看见一员白袍将军逆着人潮杀回阵中，正是昔日燕山相识的赵子龙。刘备的家眷仍陷在重围深处，他不肯独自撤离，正在曹军阵间往返寻人。虎豹骑统领曹纯却已收拢精骑，以迅疾突击截杀外围接应之人，几条通往长坂桥的道路正被逐一封锁。\n"
        "\n"
        "旧日所赠的白马银枪令尚在手中，故人却已身陷生死之地。此行需消耗一枚「白马银枪令」，请庄园主率门客突入长坂乱军，击破曹纯与四千四百嗜血铁骑的拦截，替赵云打开接应之路。铁骑已研习全部满级专属科技，不受五行兵种克制；宜以防御、反击或反伤护院承受先攻，以门客输出击倒曹纯，使其督战增伤从下一轮起失效。曹纯生命低于三成时将进入狂暴，运势增加100，其他属性不变，虎豹奔袭转为攻击三人，需兼顾输出与残血阶段的承伤。\n",
        "enemy_troops": {"bloodthirsty_cavalry": 4400},
    },
    "zhanlong_changban_duanqiao": {
        "description": "长坂桥前，张飞横矛立马，怒目巡视来路。桥后树林烟尘滚滚，旌旗时隐时现，战马奔踏之声连绵不绝。逃来的散卒惊呼林中伏有百万大军，连追至岸边的曹军也勒马驻足，不敢贸然向前。\n"
        "\n"
        "庄园主率门客赶到桥头，尚未来得及通报身份，便被张飞一声断喝拦住。如今败军之中真假难辨，曹军又有乔装混入之嫌，他奉命守桥，绝不肯放来历未明的武装队伍靠近。桥后尚有百姓等待撤离，要取得信任，还须当面试过本领与胆色。\n"
        "\n"
        "桥后尘土愈起愈高，林中究竟藏着多少兵马，无人看得分明。眼前那杆丈八蛇矛却已缓缓抬起。此行需消耗一枚「长坂护主令」，请庄园主整顿队伍，迎接张翼德的桥头考验。桥头张飞武力650、防御480、敏捷400，虎啸龙吟伤害倍率为演武版本的两倍，生命低于两成后改为同时攻击两人。重矛之下，宜由高防减伤门客承担风险，辅以眩晕技能争取输出机会；护院仍需抵住守桥铁骑的持续冲击，阵容须兼顾控制、承伤与攻坚。\n",
        "enemy_troops": {"bloodthirsty_cavalry": 5400},
    },
}

GUEST_UPDATES = {
    "task_zhanlong_tiebeixiong": {"base_hp": 650000},
    "task_zhanlong_caochun": {"base_hp": 800000},
    "task_zhanlong_zhangfei_bridge": {"base_hp": 850000, "base_attack": 650, "base_defense": 480},
    "task_zhanlong_guanyu": {"base_hp": 250000},
    "task_zhanlong_zhangfei": {"base_hp": 180000},
    "task_zhanlong_zhaoyun": {"base_hp": 280000},
    "task_zhanlong_machao": {"base_hp": 160000},
    "task_zhanlong_huangzhong": {"base_hp": 100000},
}


def sync_zhanlong_progression(apps, schema_editor):
    database = schema_editor.connection.alias
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    for key, updates in MISSION_UPDATES.items():
        MissionTemplate.objects.using(database).filter(key=key).update(**deepcopy(updates))
    for key, updates in GUEST_UPDATES.items():
        GuestTemplate.objects.using(database).filter(key=key).update(**updates)
    transaction.on_commit(lambda: cache.delete("battle:guest_templates"), using=database, robust=True)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0239_clear_regular_mission_order")]
    operations = [migrations.RunPython(sync_zhanlong_progression, migrations.RunPython.noop, atomic=True)]
