from django.contrib.admin import AdminSite


class GameAdminSite(AdminSite):
    index_template = "admin/operations_index.html"

    def index(self, request, extra_context=None):
        # 使用 Django 已过滤权限的菜单，避免向无权限人员暴露入口。
        groups = {
            name: []
            for name in ("玩家与庄园", "消息与运营", "竞技与战斗", "门客与养成", "交易与帮会", "配置与技术记录")
        }
        for app in self.get_app_list(request):
            for model in app["models"]:
                name = model["object_name"]
                if name in {"User", "Manor", "ResourceEvent"}:
                    group = "玩家与庄园"
                elif name in {"Message", "GlobalMailCampaign", "GlobalMailDelivery", "LogEntry"}:
                    group = "消息与运营"
                elif (
                    name.startswith("Arena")
                    and "Virtual" not in name
                    and name not in {"ArenaRuntimeBudget", "ArenaFillServiceEpisode", "ArenaFillServiceOutcome"}
                    or name in {"BattleReport", "RaidRun", "ScoutRecord"}
                ):
                    group = "竞技与战斗"
                elif app["app_label"] == "guests" and "Template" not in name and name != "RecruitmentPool":
                    group = "门客与养成"
                elif app["app_label"] in {"trade", "guilds"} and "Template" not in name:
                    group = "交易与帮会"
                else:
                    group = "配置与技术记录"
                groups[group].append(model)
        context = {
            "operation_groups": [{"name": name, "models": models} for name, models in groups.items() if models],
            **(extra_context or {}),
        }
        return super().index(request, context)
