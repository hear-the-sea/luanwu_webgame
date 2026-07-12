from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from guilds.decorators import require_guild_member
from guilds.services.blueprint_rewards import claim_guild_blueprint_reward


@login_required
@require_guild_member
@require_POST
def claim_blueprint_reward(request, blueprint_key: str):
    try:
        claim_guild_blueprint_reward(request.guild_member, blueprint_key)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "帮会图纸已领取并存入个人仓库")
    return redirect("guilds:warehouse")
