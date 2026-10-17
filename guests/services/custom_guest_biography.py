"""专属门客的江湖闻录；庄园名每次展示时读取。"""

from datetime import date

from gameplay.models import Manor


def build_custom_guest_biography(manor: Manor, arrived_on: date) -> str:
    name = manor.name or "无名庄园"
    date_text = f"{arrived_on.year}年{arrived_on.month}月{arrived_on.day}日"
    return (
        f"{name}的庄主，于{date_text}来到此庄园。初来时只闻其声、不见其人，"
        "靠几次鼠标点击便敢指点江山。后来终于领到专属肉身，从此亲自下场，"
        "见了装备两眼放光，听见“吞噬”立刻开饭。"
        "此人胸怀大志，誓要出任庄园CEO，坐拥良田万顷。奈何修行颇费家底，"
        "常常嘴里喊着“这次一定成”，手里又掏出一本易筋经。"
        "江湖人送外号：“吃装不吐铁，强化不信邪。”"
    )
