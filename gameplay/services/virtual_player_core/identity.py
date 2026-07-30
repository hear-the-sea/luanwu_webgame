from __future__ import annotations

import random

_MANOR_NAME_SURNAMES = (
    "沈",
    "陆",
    "顾",
    "萧",
    "谢",
    "苏",
    "林",
    "周",
    "温",
    "叶",
    "秦",
    "唐",
    "宋",
    "楚",
    "云",
    "白",
    "江",
    "许",
    "程",
    "傅",
)
_MANOR_NAME_GIVEN = (
    "清远",
    "怀瑾",
    "映雪",
    "知微",
    "明渊",
    "青岚",
    "景行",
    "疏桐",
    "云舟",
    "听澜",
    "照夜",
    "归鸿",
    "问渠",
    "少卿",
    "知白",
    "长风",
)
_MANOR_NAME_PREFIXES = (
    "青竹",
    "松月",
    "听雨",
    "归云",
    "临溪",
    "怀雪",
    "枕霞",
    "晴川",
    "墨泉",
    "竹隐",
    "云麓",
    "栖梧",
    "照水",
    "南柯",
    "北辰",
    "秋声",
)
_MANOR_NAME_SUFFIXES = (
    "山庄",
    "别院",
    "小筑",
    "草堂",
    "书斋",
    "雅舍",
    "庄园",
    "庭",
    "坞",
    "居",
    "轩",
    "庐",
)
_MANOR_NAME_INTERNET_PREFIXES = (
    "摸鱼",
    "开摆",
    "随缘",
    "夜猫子",
    "奶茶续命",
    "快乐老家",
    "人间清醒",
    "低调发财",
    "菜但爱玩",
    "非酋",
    "欧皇",
    "一键收菜",
    "余额不足",
    "周末上线",
    "慢慢变强",
    "路过看看",
    "今日份",
    "刚睡醒",
    "再来一局",
    "不急着赢",
    "下班以后",
    "在线等风",
)
_MANOR_NAME_INTERNET_SUFFIXES = (
    "山庄",
    "小筑",
    "根据地",
    "休息区",
    "补给站",
    "快乐屋",
    "慢慢来",
    "先发育",
    "不掉线",
    "来收菜",
    "等好运",
    "随便玩",
    "今晚在线",
    "明天再说",
    "营业中",
    "集合点",
    "避风港",
    "后花园",
)
_MANOR_NAME_INTERNET_STANDALONE = (
    "听到涛声",
    "今天也想躺平",
    "打不过就跑",
    "路过不要打我",
    "先苟住再说",
    "上号收个菜",
    "差点就赢了",
    "全靠同行衬托",
    "别看我会输",
    "不想加班",
    "精神状态良好",
    "好运加载中",
    "这把随缘",
    "风紧扯呼",
    "等等我再上",
    "今天手气不错",
    "先把日常做完",
    "晚点再认真打",
    "刚来还不太会",
    "慢慢玩比较快",
    "让我再发育会儿",
    "路过顺手收个菜",
    "上线看看就走",
    "今天不宜硬刚",
    "差一点点起飞",
    "先喝口茶再说",
    "周末才有空玩",
    "等一个好运气",
    "随手点进来的",
    "别急正在赶路",
    "这一局先稳住",
    "明天一定变强",
)
_MANOR_NAME_NICKNAME_STANDALONE = (
    "晚风",
    "南桥",
    "半糖",
    "小满",
    "十一",
    "阿七",
    "木棉",
    "青团",
    "栗子",
    "乌龙",
    "夏末",
    "星河",
    "山茶",
    "初九",
    "清欢",
    "玖玖",
    "北落",
    "三月",
    "白桃",
    "雾眠",
    "小禾",
    "小雨",
    "阿宁",
    "团子",
    "慢热",
    "未晚",
    "一川",
    "听夏",
    "有光",
    "云朵",
    "小鱼干",
    "松子糖",
)
_MANOR_NAME_NICKNAME_PREFIXES = (
    "小",
    "阿",
    "老",
    "一只",
    "隔壁的",
    "晚睡的",
    "路过的",
    "发呆的",
    "爱喝茶的",
    "慢半拍的",
    "不着急的",
    "刚上线的",
)
_MANOR_NAME_NICKNAME_CORES = (
    "栗子",
    "青团",
    "乌龙",
    "晚风",
    "小禾",
    "山茶",
    "木棉",
    "团子",
    "南桥",
    "白桃",
    "云朵",
    "星河",
    "小满",
    "听夏",
    "雨声",
    "松子",
    "月亮",
    "茶壶",
)


def select_manor_name_style(roll: float) -> str:
    if roll < 0.50:
        return "modern"
    if roll < 0.80:
        return "classical"
    return "nickname"


def build_manor_name_candidate(rng: random.Random, *, style: str, variant: int) -> str:
    if style == "modern":
        if rng.random() < 0.30:
            return rng.choice(_MANOR_NAME_INTERNET_STANDALONE)
        return f"{rng.choice(_MANOR_NAME_INTERNET_PREFIXES)}{rng.choice(_MANOR_NAME_INTERNET_SUFFIXES)}"
    if style == "nickname":
        if rng.random() < 0.50:
            return rng.choice(_MANOR_NAME_NICKNAME_STANDALONE)
        return f"{rng.choice(_MANOR_NAME_NICKNAME_PREFIXES)}{rng.choice(_MANOR_NAME_NICKNAME_CORES)}"
    if style != "classical":
        raise ValueError(f"Unsupported bot manor name style: {style}")

    classical_variant = variant % 5
    if classical_variant == 0:
        return f"{rng.choice(_MANOR_NAME_SURNAMES)}{rng.choice(_MANOR_NAME_GIVEN)}的庄园"
    if classical_variant == 1:
        return f"{rng.choice(_MANOR_NAME_SURNAMES)}{rng.choice(_MANOR_NAME_GIVEN)}的{rng.choice(_MANOR_NAME_SUFFIXES)}"
    if classical_variant == 2:
        return f"{rng.choice(_MANOR_NAME_PREFIXES)}{rng.choice(_MANOR_NAME_SURNAMES)}{rng.choice(_MANOR_NAME_SUFFIXES)}"
    if classical_variant == 3:
        return f"{rng.choice(_MANOR_NAME_GIVEN)}{rng.choice(_MANOR_NAME_PREFIXES)}{rng.choice(_MANOR_NAME_SUFFIXES)}"
    return f"{rng.choice(_MANOR_NAME_PREFIXES)}{rng.choice(_MANOR_NAME_GIVEN)}{rng.choice(_MANOR_NAME_SUFFIXES)}"


def fallback_manor_name_candidate(rng: random.Random) -> str:
    return (
        f"{rng.choice(_MANOR_NAME_SURNAMES)}"
        f"{rng.choice(_MANOR_NAME_GIVEN)}"
        f"{rng.choice(_MANOR_NAME_PREFIXES)}"
        f"{rng.choice(_MANOR_NAME_SUFFIXES)}"
    )


__all__ = [
    "build_manor_name_candidate",
    "fallback_manor_name_candidate",
    "select_manor_name_style",
]
