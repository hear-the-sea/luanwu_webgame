"""为当前测试赛季启用公共后勤；保留阵营、进度和已领奖记录。"""

import copy
import json
from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone

CAMPAIGN = json.loads(
    r"""{
  "version": 2,
  "registration": {
    "days": 3
  },
  "economy": {
    "tick_seconds": 300,
    "initial_resources": {
      "grain": 5000,
      "wood": 3000,
      "iron": 2000
    },
    "resource_caps": {
      "grain": 100000,
      "wood": 80000,
      "iron": 60000
    },
    "resource_rates": {
      "grain": 120,
      "wood": 80,
      "iron": 50
    },
    "initial_troops": {
      "dao_ke": 1000,
      "qiang_ke": 1000,
      "jian_shi": 800
    },
    "troop_caps": {
      "dao_ke": 10000,
      "qiang_ke": 10000,
      "jian_shi": 10000
    },
    "troop_rates": {
      "dao_ke": 15,
      "qiang_ke": 15,
      "jian_shi": 10
    },
    "grain_per_troop": 1,
    "daily_troop_quota": 1200,
    "upgrade_merit": 10,
    "daily_upgrade_merit_cap": 50,
    "level_unlock_days": [
      1,
      8,
      15,
      29,
      43,
      57
    ],
    "buildings": {
      "logistics": {
        "name": "后勤工坊",
        "max_level": 6,
        "base_cost": {
          "grain": 400,
          "wood": 500,
          "iron": 200
        },
        "cost_multiplier": 2
      },
      "training": {
        "name": "阵营募训营",
        "max_level": 6,
        "base_cost": {
          "grain": 600,
          "wood": 250,
          "iron": 400
        },
        "cost_multiplier": 2
      },
      "technology": {
        "name": "阵营军略",
        "max_level": 6,
        "base_cost": {
          "grain": 300,
          "wood": 400,
          "iron": 600
        },
        "cost_multiplier": 2
      }
    },
    "technology_keys": [
      "dao_attack",
      "dao_defense",
      "dao_hp",
      "qiang_attack",
      "qiang_defense",
      "qiang_hp",
      "jian_attack",
      "jian_defense",
      "jian_hp"
    ],
    "pve": {
      "mountain:inn": {
        "resources": {
          "grain": 200,
          "wood": 100,
          "iron": 50
        },
        "merit": 20,
        "failure_merit": 10
      },
      "mountain:ruins": {
        "resources": {
          "grain": 100,
          "wood": 100,
          "iron": 200
        },
        "merit": 25,
        "failure_merit": 10
      },
      "river:docks": {
        "resources": {
          "grain": 250,
          "wood": 180,
          "iron": 80
        },
        "merit": 25,
        "failure_merit": 10
      },
      "forest:granary": {
        "resources": {
          "grain": 400,
          "wood": 80,
          "iron": 80
        },
        "merit": 25,
        "failure_merit": 10
      },
      "forest:lodge": {
        "resources": {
          "grain": 150,
          "wood": 250,
          "iron": 150
        },
        "merit": 25,
        "failure_merit": 10
      }
    }
  },
  "expeditions": {
    "max_active": 2,
    "combat_days": 83,
    "daily_actions": 12,
    "minimum_travel_seconds": 60,
    "travel_seconds": {
      "mountain:inn": 180,
      "mountain:gate": 300,
      "mountain:ruins": 300,
      "river:town": 600,
      "river:docks": 300,
      "river:market": 180,
      "river:warehouse": 180,
      "forest:camp": 300,
      "forest:granary": 360,
      "forest:lodge": 300
    },
    "station_seconds": 3600,
    "max_battles": 3,
    "transport_seconds": 900,
    "cargo": {
      "grain": 300,
      "wood": 150,
      "iron": 100
    },
    "city_weekdays": [
      5,
      6
    ],
    "attack_merit": 30,
    "failure_merit": 10,
    "defend_merit": 20,
    "repair_amount": 100,
    "repair_cost": {
      "wood": 80,
      "iron": 40
    },
    "demolition_amount": 100,
    "control_amount": 25,
    "timezone": "Asia/Shanghai",
    "score_interval_seconds": 3600,
    "garrison_capacity": 8,
    "delivery_multiplier": 1.25,
    "enemy_levels": {
      "mountain:inn": 3,
      "mountain:ruins": 10,
      "mountain:gate": 15,
      "river:town": 30,
      "river:docks": 15,
      "forest:granary": 25,
      "forest:lodge": 20
    }
  },
  "sites": {
    "mountain:gate": {
      "name": "青石关",
      "unlock_day": 1,
      "guards": 3,
      "durability": 600,
      "control_required": 100,
      "score_per_tick": 1,
      "protection_seconds": 1800
    },
    "river:town": {
      "name": "河口古城",
      "unlock_day": 15,
      "guards": 5,
      "durability": 1200,
      "control_required": 100,
      "score_per_tick": 3,
      "protection_seconds": 3600
    },
    "river:docks": {
      "name": "渡口码头",
      "unlock_day": 15,
      "guards": 2,
      "durability": 400,
      "control_required": 100,
      "score_per_tick": 1,
      "protection_seconds": 1800
    },
    "forest:granary": {
      "name": "前线粮仓",
      "unlock_day": 36,
      "guards": 2,
      "durability": 400,
      "control_required": 100,
      "score_per_tick": 1,
      "protection_seconds": 1800
    }
  },
  "rewards": {
    "minimum_merit": 100,
    "tiers": [
      {
        "key": "elite",
        "name": "阵营前一成",
        "percentile": 10,
        "items": {
          "experience_fruit": 40,
          "xuan_tie_essence": 30
        }
      },
      {
        "key": "distinguished",
        "name": "阵营前三成",
        "percentile": 30,
        "items": {
          "experience_fruit": 25,
          "xuan_tie_essence": 20
        }
      },
      {
        "key": "participant",
        "name": "阵营参与奖",
        "percentile": 100,
        "items": {
          "experience_fruit": 15,
          "xuan_tie_essence": 10
        }
      }
    ],
    "winner_items": {
      "experience_fruit": 15,
      "xuan_tie_essence": 10
    }
  }
}"""
)


def enable_existing_campaigns(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    Member = apps.get_model("gameplay", "SeasonMember")
    Faction = apps.get_model("gameplay", "SeasonFaction")
    Site = apps.get_model("gameplay", "SeasonSite")
    now = timezone.now()
    database = schema_editor.connection.alias
    for season in Season.objects.using(database).filter(closed=False).iterator():
        rules = copy.deepcopy(season.rules)
        if "campaign" not in rules:
            rules["campaign"] = copy.deepcopy(CAMPAIGN)
            season.rules = rules
            season.save(using=database, update_fields=["rules"])
        config = rules["campaign"]
        # 预排赛季及仍在集结期的赛季不能补发报名结束前的生产。
        production_start = max(now, season.starts_at + timedelta(days=config["registration"]["days"]))
        for member in Member.objects.using(database).filter(season=season).select_related("manor").iterator():
            member.prestige_snapshot = member.manor.prestige
            member.save(using=database, update_fields=["prestige_snapshot"])
        for faction in ("fiction", "history"):
            Faction.objects.using(database).get_or_create(
                season=season,
                faction=faction,
                defaults={
                    "resources": config["economy"]["initial_resources"],
                    "troops": config["economy"]["initial_troops"],
                    "levels": {key: 1 for key in config["economy"]["buildings"]},
                    "technology": {key: 0 for key in config["economy"]["technology_keys"]},
                    "produced_at": production_start,
                    "score": getattr(season, faction + "_score"),
                },
            )
        for key, site in config["sites"].items():
            Site.objects.using(database).get_or_create(
                season=season,
                key=key,
                defaults={
                    "guards": site["guards"],
                    "durability": site["durability"],
                    "last_score_at": now,
                },
            )


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0246_season_campaign")]
    operations = [
        migrations.AlterField(
            model_name="seasonmember",
            name="faction",
            field=models.CharField(
                blank=True, max_length=16, choices=[("fiction", "小说阵营"), ("history", "历史阵营")]
            ),
        ),
        migrations.RunPython(enable_existing_campaigns, migrations.RunPython.noop),
    ]
