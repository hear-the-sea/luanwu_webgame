"""赛季商店复用物品模板及仓库图标，礼包逐项展示内容。"""

from django.templatetags.static import static


def item_visuals(amounts, templates):
    result = []
    for key, quantity in amounts.items():
        template = templates.get(key)
        result.append(
            {
                "key": key,
                "name": template.name if template else "待载入物品",
                "quantity": quantity,
                "image": template.image if template and template.image else static("images/season-item-parcel.svg"),
                "icon": template.icon if template else "",
                "rarity": template.rarity if template else "black",
            }
        )
    return result
