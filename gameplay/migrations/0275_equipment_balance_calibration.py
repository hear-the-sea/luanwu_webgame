"""按新校准战力标尺与流派分化方案调整 194 件装备属性及 3 套套装加成，维持蚩尤甲与灵魂饰品高端。"""

import copy
import hashlib
import json

from django.db import migrations

from core.config import GUEST

EQUIPMENT_PAYLOADS = json.loads(
    r"""
{
  "equip_action_beam_glove": {
    "force": 44,
    "agility": 15,
    "set_key": "kasukabe_defense_set",
    "set_description": "春日部防卫队",
    "set_bonus": [
      {
        "pieces": 2,
        "bonus": {
          "defense": 15,
          "attack": 20
        }
      },
      {
        "pieces": 4,
        "bonus": {
          "hp": 400,
          "agility": 15,
          "luck": 10
        }
      }
    ],
    "required_level": 25
  },
  "equip_baguajing": {
    "hp": 360,
    "defense": 16,
    "force": 16,
    "intellect": 24,
    "luck": 14,
    "required_level": 50
  },
  "equip_baoweiqiang": {
    "defense": 4,
    "force": 8,
    "troop_capacity": 12,
    "required_level": 10
  },
  "equip_bawangqiang": {
    "hp": 280,
    "defense": 12,
    "force": 48,
    "troop_capacity": 46,
    "required_level": 45
  },
  "equip_biyudao": {
    "hp": 200,
    "force": 36,
    "agility": 8,
    "luck": 18,
    "troop_capacity": 20,
    "required_level": 55
  },
  "equip_bupao": {
    "hp": 160,
    "defense": 10,
    "intellect": 12,
    "required_level": 1
  },
  "equip_businiaoyu": {
    "hp": 560,
    "defense": 14,
    "force": 50,
    "agility": 14,
    "required_level": 65
  },
  "equip_caomao": {
    "hp": 250,
    "defense": 10,
    "force": 5,
    "set_key": "starter_cloth_set",
    "set_description": "一蓑烟雨",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 120,
        "force": 12,
        "troop_capacity": 12,
        "luck": 10
      }
    },
    "required_level": 1
  },
  "equip_caoxie": {
    "hp": 200,
    "defense": 5,
    "agility": 10,
    "set_key": "starter_cloth_set",
    "set_description": "一蓑烟雨",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 120,
        "force": 12,
        "troop_capacity": 12,
        "luck": 10
      }
    },
    "required_level": 1
  },
  "equip_changbian": {
    "force": 8,
    "agility": 8,
    "luck": 4,
    "required_level": 10
  },
  "equip_changgong": {
    "force": 10,
    "agility": 6,
    "required_level": 1
  },
  "equip_changjian": {
    "hp": 100,
    "force": 14,
    "luck": 4,
    "required_level": 5
  },
  "equip_changshengjian": {
    "hp": 420,
    "defense": 28,
    "intellect": 54,
    "agility": 10,
    "luck": 10,
    "required_level": 80
  },
  "equip_chibiao": {
    "hp": 200,
    "defense": 10,
    "force": 10,
    "agility": 16,
    "required_level": 10
  },
  "equip_chituma": {
    "hp": 340,
    "defense": 18,
    "force": 38,
    "agility": 24,
    "troop_capacity": 36,
    "required_level": 80
  },
  "equip_chiyoujia": {
    "hp": 640,
    "defense": 48,
    "force": 36,
    "intellect": 12,
    "agility": 14,
    "luck": 15,
    "required_level": 80
  },
  "equip_chiyouzhanxue": {
    "hp": 220,
    "defense": 20,
    "force": 28,
    "agility": 34,
    "troop_capacity": 22,
    "required_level": 75
  },
  "equip_chuanguoyuxi": {
    "hp": 360,
    "defense": 12,
    "force": 22,
    "intellect": 22,
    "luck": 16,
    "troop_capacity": 92,
    "required_level": 80
  },
  "equip_chuanyungong": {
    "force": 34,
    "agility": 24,
    "required_level": 25
  },
  "equip_cubujia": {
    "hp": 160,
    "defense": 12,
    "troop_capacity": 6,
    "required_level": 10
  },
  "equip_dagoubang": {
    "hp": 260,
    "defense": 24,
    "force": 46,
    "agility": 18,
    "luck": 8,
    "required_level": 70
  },
  "equip_dawanma": {
    "hp": 260,
    "defense": 12,
    "agility": 20,
    "required_level": 10
  },
  "equip_dilu": {
    "hp": 400,
    "defense": 30,
    "agility": 16,
    "luck": 10,
    "required_level": 60
  },
  "equip_diwangkui": {
    "hp": 420,
    "defense": 36,
    "force": 44,
    "agility": 10,
    "troop_capacity": 20,
    "required_level": 80
  },
  "equip_duandao": {
    "hp": 60,
    "defense": 2,
    "force": 12,
    "agility": 2,
    "required_level": 1
  },
  "equip_duanhunbian": {
    "force": 34,
    "agility": 16,
    "luck": 10,
    "required_level": 35
  },
  "equip_duanjian": {
    "hp": 60,
    "force": 6,
    "intellect": 6,
    "agility": 6,
    "required_level": 1
  },
  "equip_duomingfeidao": {
    "hp": 60,
    "force": 36,
    "agility": 14,
    "luck": 12,
    "required_level": 55
  },
  "equip_duoqinghuan": {
    "hp": 200,
    "defense": 14,
    "force": 34,
    "intellect": 34,
    "luck": 8,
    "required_level": 45
  },
  "equip_fanqugong": {
    "defense": 4,
    "force": 14,
    "agility": 6,
    "required_level": 5
  },
  "equip_feiyunzui": {
    "hp": 220,
    "defense": 8,
    "agility": 26,
    "required_level": 25
  },
  "equip_fenghuangyu": {
    "hp": 140,
    "defense": 8,
    "intellect": 36,
    "luck": 18,
    "set_key": "fengling_set",
    "set_description": "凤翎套装",
    "set_bonus": {
      "pieces": 3,
      "bonus": {
        "intellect": 40,
        "luck": 20
      }
    },
    "required_level": 55
  },
  "equip_fenglingguan": {
    "hp": 320,
    "defense": 20,
    "intellect": 32,
    "luck": 12,
    "set_key": "fengling_set",
    "set_description": "凤翎套装",
    "set_bonus": {
      "pieces": 3,
      "bonus": {
        "intellect": 40,
        "luck": 20
      }
    },
    "required_level": 55
  },
  "equip_fenglingshan": {
    "hp": 160,
    "defense": 8,
    "intellect": 54,
    "luck": 18,
    "set_key": "fengling_set",
    "set_description": "凤翎套装",
    "set_bonus": {
      "pieces": 3,
      "bonus": {
        "intellect": 40,
        "luck": 20
      }
    },
    "required_level": 55
  },
  "equip_fengshenbang": {
    "hp": 360,
    "defense": 10,
    "intellect": 56,
    "agility": 10,
    "troop_capacity": 50,
    "required_level": 70
  },
  "equip_fengyuzan": {
    "hp": 120,
    "intellect": 20,
    "agility": 10,
    "luck": 14,
    "required_level": 25
  },
  "equip_guanxingdeng": {
    "hp": 120,
    "intellect": 14,
    "luck": 24,
    "required_level": 35
  },
  "equip_guiyuanzhu": {
    "hp": 460,
    "defense": 16,
    "luck": 24,
    "required_level": 45
  },
  "equip_hantiekui": {
    "hp": 280,
    "defense": 24,
    "intellect": 12,
    "luck": 6,
    "required_level": 40
  },
  "equip_hanxuebaoma": {
    "hp": 420,
    "defense": 18,
    "agility": 30,
    "troop_capacity": 18,
    "required_level": 70
  },
  "equip_heshibi": {
    "hp": 420,
    "defense": 26,
    "intellect": 24,
    "luck": 24,
    "required_level": 65
  },
  "equip_housipao": {
    "hp": 240,
    "defense": 16,
    "intellect": 12,
    "agility": 6,
    "required_level": 10
  },
  "equip_hundunlongpao": {
    "hp": 540,
    "defense": 24,
    "intellect": 54,
    "agility": 12,
    "luck": 14,
    "required_level": 65
  },
  "equip_hunyuansan": {
    "hp": 400,
    "defense": 20,
    "force": 32,
    "required_level": 60
  },
  "equip_hupixue": {
    "hp": 220,
    "defense": 6,
    "force": 12,
    "agility": 12,
    "required_level": 10
  },
  "equip_huwenkui": {
    "hp": 200,
    "defense": 14,
    "force": 16,
    "luck": 6,
    "required_level": 20
  },
  "equip_huxianjian": {
    "hp": 200,
    "defense": 6,
    "force": 36,
    "intellect": 50,
    "agility": 8,
    "set_key": "huxian_set",
    "set_description": "狐仙套装",
    "set_bonus": {
      "pieces": 3,
      "bonus": {
        "hp": 220,
        "intellect": 45,
        "luck": 15
      }
    },
    "required_level": 50
  },
  "equip_huxianpao": {
    "hp": 420,
    "defense": 20,
    "intellect": 32,
    "agility": 12,
    "set_key": "huxian_set",
    "set_description": "狐仙套装",
    "set_bonus": {
      "pieces": 3,
      "bonus": {
        "hp": 220,
        "intellect": 45,
        "luck": 15
      }
    },
    "required_level": 50
  },
  "equip_huxianxie": {
    "hp": 220,
    "defense": 10,
    "intellect": 24,
    "agility": 22,
    "luck": 6,
    "set_key": "huxian_set",
    "set_description": "狐仙套装",
    "set_bonus": {
      "pieces": 3,
      "bonus": {
        "hp": 220,
        "intellect": 45,
        "luck": 15
      }
    },
    "required_level": 50
  },
  "equip_huyaxianglian": {
    "hp": 220,
    "force": 26,
    "luck": 10,
    "required_level": 25
  },
  "equip_jianghuling": {
    "hp": 340,
    "force": 20,
    "intellect": 20,
    "agility": 18,
    "troop_capacity": 68,
    "required_level": 75
  },
  "equip_jiaodoushitoukui": {
    "hp": 300,
    "defense": 25,
    "force": 18,
    "set_key": "jiaodoushi_set",
    "set_description": "角斗士套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 300,
        "defense": 12,
        "force": 28,
        "agility": 15
      }
    },
    "required_level": 35
  },
  "equip_jiaodoushixiongjia": {
    "hp": 400,
    "defense": 28,
    "force": 18,
    "set_key": "jiaodoushi_set",
    "set_description": "角斗士套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 300,
        "defense": 12,
        "force": 28,
        "agility": 15
      }
    },
    "required_level": 35
  },
  "equip_jiaodoushizhichui": {
    "hp": 260,
    "defense": 14,
    "force": 42,
    "set_key": "jiaodoushi_set",
    "set_description": "角斗士套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 300,
        "defense": 12,
        "force": 28,
        "agility": 15
      }
    },
    "required_level": 35
  },
  "equip_jiaodoushizhixue": {
    "hp": 200,
    "defense": 14,
    "force": 12,
    "agility": 24,
    "set_key": "jiaodoushi_set",
    "set_description": "角斗士套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 300,
        "defense": 12,
        "force": 28,
        "agility": 15
      }
    },
    "required_level": 35
  },
  "equip_jicibian": {
    "force": 16,
    "agility": 4,
    "luck": 8,
    "required_level": 10
  },
  "equip_jifengxue": {
    "hp": 160,
    "defense": 6,
    "agility": 24,
    "required_level": 20
  },
  "equip_jingtiedao": {
    "hp": 80,
    "force": 22,
    "luck": 6,
    "required_level": 15
  },
  "equip_jinjingbailongju": {
    "hp": 320,
    "defense": 24,
    "agility": 22,
    "luck": 14,
    "troop_capacity": 40,
    "required_level": 80
  },
  "equip_jinlinxue": {
    "hp": 220,
    "defense": 16,
    "force": 14,
    "agility": 20,
    "luck": 6,
    "required_level": 40
  },
  "equip_jinlun": {
    "hp": 240,
    "defense": 10,
    "force": 50,
    "intellect": 24,
    "required_level": 60
  },
  "equip_jinshejian": {
    "hp": 240,
    "defense": 20,
    "force": 42,
    "agility": 10,
    "required_level": 50
  },
  "equip_jinshezui": {
    "hp": 80,
    "force": 18,
    "intellect": 30,
    "agility": 16,
    "luck": 8,
    "required_level": 55
  },
  "equip_jinsirujia": {
    "hp": 540,
    "defense": 24,
    "agility": 20,
    "luck": 14,
    "required_level": 70
  },
  "equip_jiulonggangbian": {
    "defense": 4,
    "force": 14,
    "agility": 10,
    "luck": 4,
    "required_level": 10
  },
  "equip_juanmaochitu": {
    "hp": 220,
    "defense": 8,
    "force": 20,
    "agility": 22,
    "required_level": 30
  },
  "equip_jueying": {
    "hp": 220,
    "defense": 10,
    "agility": 24,
    "luck": 10,
    "required_level": 45
  },
  "equip_junzijian": {
    "hp": 140,
    "defense": 8,
    "force": 22,
    "intellect": 34,
    "luck": 8,
    "required_level": 40
  },
  "equip_kaiyangkaijia": {
    "hp": 450,
    "defense": 25,
    "force": 10,
    "luck": 18,
    "required_level": 55
  },
  "equip_kongquelin": {
    "hp": 100,
    "force": 18,
    "intellect": 44,
    "agility": 18,
    "luck": 14,
    "required_level": 80
  },
  "equip_langyakui": {
    "hp": 280,
    "defense": 18,
    "force": 18,
    "agility": 12,
    "required_level": 40
  },
  "equip_libiegou": {
    "hp": 120,
    "defense": 8,
    "force": 42,
    "agility": 12,
    "luck": 10,
    "required_level": 50
  },
  "equip_liekongxue": {
    "hp": 180,
    "defense": 8,
    "force": 22,
    "agility": 24,
    "luck": 6,
    "required_level": 45
  },
  "equip_lingboxie": {
    "hp": 180,
    "defense": 8,
    "agility": 30,
    "required_level": 30
  },
  "equip_lingwenyubi": {
    "hp": 220,
    "defense": 8,
    "intellect": 28,
    "luck": 10,
    "required_level": 30
  },
  "equip_linwenzhanjia": {
    "hp": 440,
    "defense": 32,
    "force": 8,
    "required_level": 35
  },
  "equip_longdanqiang": {
    "hp": 200,
    "defense": 12,
    "force": 54,
    "agility": 20,
    "troop_capacity": 20,
    "required_level": 75
  },
  "equip_longhuangguan": {
    "hp": 360,
    "defense": 22,
    "intellect": 34,
    "agility": 15,
    "troop_capacity": 40,
    "required_level": 65
  },
  "equip_longjingzhu": {
    "hp": 220,
    "force": 20,
    "agility": 10,
    "luck": 16,
    "required_level": 45
  },
  "equip_longlinzhanjia": {
    "hp": 520,
    "defense": 32,
    "force": 34,
    "agility": 14,
    "troop_capacity": 26,
    "required_level": 75
  },
  "equip_longwenyupei": {
    "hp": 100,
    "force": 12,
    "troop_capacity": 36,
    "luck": 15,
    "required_level": 30
  },
  "equip_mengguma": {
    "hp": 240,
    "defense": 12,
    "force": 6,
    "agility": 14,
    "troop_capacity": 8,
    "required_level": 15
  },
  "equip_mingshejiebian": {
    "hp": 60,
    "force": 10,
    "agility": 6,
    "luck": 12,
    "required_level": 15
  },
  "equip_moyuyan": {
    "hp": 220,
    "defense": 8,
    "intellect": 24,
    "required_level": 15
  },
  "equip_niupibian": {
    "hp": 40,
    "force": 12,
    "agility": 4,
    "luck": 6,
    "required_level": 10
  },
  "equip_nohara_pan": {
    "hp": 200,
    "defense": 14,
    "force": 28,
    "agility": 8,
    "luck": 8,
    "set_key": "kasukabe_defense_set",
    "set_description": "春日部防卫队",
    "set_bonus": [
      {
        "pieces": 2,
        "bonus": {
          "defense": 15,
          "attack": 20
        }
      },
      {
        "pieces": 4,
        "bonus": {
          "hp": 400,
          "agility": 15,
          "luck": 10
        }
      }
    ],
    "required_level": 25
  },
  "equip_panlonggun": {
    "hp": 400,
    "defense": 28,
    "force": 68,
    "agility": 4,
    "troop_capacity": 34,
    "required_level": 65
  },
  "equip_pofengji": {
    "force": 25,
    "agility": 5,
    "troop_capacity": 15,
    "required_level": 20
  },
  "equip_pojunhufu": {
    "hp": 240,
    "force": 26,
    "agility": 12,
    "troop_capacity": 44,
    "required_level": 55
  },
  "equip_pojunji": {
    "hp": 160,
    "force": 42,
    "agility": 12,
    "troop_capacity": 38,
    "required_level": 45
  },
  "equip_pojunkui": {
    "hp": 300,
    "defense": 22,
    "force": 24,
    "agility": 10,
    "troop_capacity": 10,
    "required_level": 55
  },
  "equip_pojunxue": {
    "hp": 180,
    "defense": 10,
    "force": 16,
    "agility": 20,
    "troop_capacity": 10,
    "required_level": 30
  },
  "equip_pozhenqiang": {
    "hp": 140,
    "defense": 6,
    "force": 32,
    "agility": 8,
    "troop_capacity": 32,
    "required_level": 30
  },
  "equip_qiankunshengong": {
    "hp": 120,
    "defense": 6,
    "force": 46,
    "agility": 24,
    "required_level": 75
  },
  "equip_qiankunzhu": {
    "hp": 360,
    "defense": 24,
    "force": 34,
    "agility": 10,
    "luck": 12,
    "required_level": 70
  },
  "equip_qianliju": {
    "hp": 260,
    "defense": 14,
    "agility": 26,
    "troop_capacity": 10,
    "required_level": 25
  },
  "equip_qilin": {
    "hp": 420,
    "defense": 28,
    "intellect": 24,
    "agility": 18,
    "luck": 22,
    "troop_capacity": 15,
    "required_level": 65
  },
  "equip_qilindao": {
    "hp": 320,
    "defense": 14,
    "force": 58,
    "set_key": "qilin_set",
    "set_description": "麒麟套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 320,
        "force": 28,
        "agility": 12,
        "luck": 20
      }
    },
    "required_level": 55
  },
  "equip_qilinjia": {
    "hp": 440,
    "defense": 30,
    "force": 22,
    "luck": 8,
    "set_key": "qilin_set",
    "set_description": "麒麟套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 320,
        "force": 28,
        "agility": 12,
        "luck": 20
      }
    },
    "required_level": 55
  },
  "equip_qilinkui": {
    "hp": 340,
    "defense": 28,
    "force": 26,
    "luck": 8,
    "set_key": "qilin_set",
    "set_description": "麒麟套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 320,
        "force": 28,
        "agility": 12,
        "luck": 20
      }
    },
    "required_level": 55
  },
  "equip_qilinxue": {
    "hp": 280,
    "defense": 22,
    "force": 20,
    "agility": 20,
    "set_key": "qilin_set",
    "set_description": "麒麟套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 320,
        "force": 28,
        "agility": 12,
        "luck": 20
      }
    },
    "required_level": 55
  },
  "equip_qilinzui": {
    "hp": 240,
    "force": 34,
    "luck": 24,
    "set_key": "qilin_set",
    "set_description": "麒麟套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 320,
        "force": 28,
        "agility": 12,
        "luck": 20
      }
    },
    "required_level": 55
  },
  "equip_qingcongma": {
    "hp": 140,
    "defense": 4,
    "intellect": 6,
    "agility": 12,
    "required_level": 10
  },
  "equip_qingluan": {
    "hp": 320,
    "defense": 12,
    "intellect": 38,
    "agility": 30,
    "required_level": 75
  },
  "equip_qingshuanglu": {
    "hp": 240,
    "defense": 10,
    "intellect": 24,
    "agility": 20,
    "luck": 10,
    "required_level": 55
  },
  "equip_qingzongma": {
    "hp": 160,
    "defense": 6,
    "agility": 24,
    "troop_capacity": 10,
    "required_level": 20
  },
  "equip_qixingjian": {
    "hp": 180,
    "defense": 12,
    "force": 20,
    "intellect": 44,
    "luck": 10,
    "required_level": 45
  },
  "equip_ruanweijia": {
    "hp": 620,
    "defense": 48,
    "agility": 10,
    "luck": 10,
    "required_level": 80
  },
  "equip_shandianqiulongju": {
    "hp": 260,
    "defense": 10,
    "force": 24,
    "agility": 32,
    "required_level": 80
  },
  "equip_shangshanfaeling": {
    "hp": 260,
    "defense": 16,
    "force": 34,
    "agility": 10,
    "luck": 14,
    "troop_capacity": 32,
    "required_level": 70
  },
  "equip_shanheshejitu": {
    "hp": 350,
    "intellect": 34,
    "agility": 14,
    "luck": 10,
    "troop_capacity": 56,
    "required_level": 75
  },
  "equip_shanwenjia": {
    "hp": 420,
    "defense": 20,
    "force": 10,
    "required_level": 15
  },
  "equip_shapijia": {
    "hp": 200,
    "defense": 14,
    "force": 8,
    "luck": 4,
    "required_level": 10
  },
  "equip_shendiao": {
    "hp": 300,
    "defense": 10,
    "force": 20,
    "agility": 28,
    "luck": 12,
    "required_level": 80
  },
  "equip_shenghuoling": {
    "hp": 240,
    "intellect": 26,
    "luck": 16,
    "troop_capacity": 44,
    "required_level": 50
  },
  "equip_shenxingxue": {
    "hp": 240,
    "defense": 14,
    "force": 10,
    "agility": 46,
    "luck": 8,
    "required_level": 80
  },
  "equip_shuanghuagong": {
    "hp": 80,
    "defense": 10,
    "force": 36,
    "agility": 18,
    "required_level": 50
  },
  "equip_shuduanbaijinpao": {
    "hp": 460,
    "defense": 22,
    "force": 24,
    "agility": 22,
    "required_level": 75
  },
  "equip_suoyi": {
    "hp": 100,
    "defense": 10,
    "force": 5,
    "agility": 5,
    "set_key": "starter_cloth_set",
    "set_description": "一蓑烟雨",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 120,
        "force": 12,
        "troop_capacity": 12,
        "luck": 10
      }
    },
    "required_level": 1
  },
  "equip_suozijia": {
    "hp": 300,
    "defense": 20,
    "agility": 8,
    "required_level": 20
  },
  "equip_taiqingyuce": {
    "hp": 240,
    "intellect": 54,
    "agility": 14,
    "luck": 12,
    "required_level": 65
  },
  "equip_taixudiguan": {
    "hp": 400,
    "defense": 26,
    "intellect": 52,
    "luck": 14,
    "required_level": 75
  },
  "equip_tatianxue": {
    "hp": 280,
    "defense": 12,
    "force": 36,
    "agility": 30,
    "luck": 6,
    "required_level": 70
  },
  "equip_tayunxue": {
    "hp": 100,
    "intellect": 12,
    "agility": 18,
    "luck": 4,
    "required_level": 15
  },
  "equip_tiancanbaoyi": {
    "hp": 440,
    "defense": 24,
    "agility": 15,
    "luck": 8,
    "required_level": 60
  },
  "equip_tianjidaopao": {
    "hp": 380,
    "defense": 20,
    "intellect": 30,
    "luck": 14,
    "required_level": 45
  },
  "equip_tianjiguan": {
    "hp": 300,
    "defense": 18,
    "intellect": 32,
    "agility": 12,
    "required_level": 45
  },
  "equip_tianjikaijia": {
    "hp": 440,
    "defense": 24,
    "intellect": 34,
    "required_level": 55
  },
  "equip_tianjilu": {
    "hp": 260,
    "defense": 18,
    "intellect": 24,
    "agility": 20,
    "troop_capacity": 10,
    "required_level": 55
  },
  "equip_tianjiyupei": {
    "hp": 200,
    "intellect": 30,
    "agility": 10,
    "troop_capacity": 30,
    "required_level": 45
  },
  "equip_tianjizhang": {
    "hp": 260,
    "defense": 14,
    "intellect": 52,
    "agility": 14,
    "required_level": 60
  },
  "equip_tianquankaijia": {
    "hp": 420,
    "defense": 44,
    "required_level": 55
  },
  "equip_tianshukaijia": {
    "hp": 640,
    "defense": 32,
    "force": 12,
    "required_level": 55
  },
  "equip_tianxuankaijia": {
    "hp": 360,
    "defense": 20,
    "force": 40,
    "required_level": 55
  },
  "equip_tianyamingyuedao": {
    "hp": 200,
    "defense": 14,
    "force": 60,
    "agility": 8,
    "luck": 20,
    "required_level": 70
  },
  "equip_tianyinguqin": {
    "hp": 220,
    "intellect": 32,
    "agility": 10,
    "luck": 10,
    "required_level": 45
  },
  "equip_tiegugun": {
    "hp": 100,
    "defense": 14,
    "force": 18,
    "required_level": 20
  },
  "equip_tiehufu": {
    "hp": 200,
    "defense": 14,
    "required_level": 20,
    "luck": 10
  },
  "equip_tiejian": {
    "defense": 5,
    "force": 15,
    "set_key": "starter_cloth_set",
    "set_description": "一蓑烟雨",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 120,
        "force": 12,
        "troop_capacity": 12,
        "luck": 10
      }
    },
    "required_level": 1
  },
  "equip_tiexianjia": {
    "hp": 320,
    "defense": 16,
    "force": 12,
    "troop_capacity": 8,
    "required_level": 15
  },
  "equip_tieyekui": {
    "hp": 220,
    "defense": 16,
    "force": 12,
    "required_level": 10
  },
  "equip_tongchangdao": {
    "hp": 140,
    "defense": 6,
    "force": 20,
    "luck": 4,
    "required_level": 20
  },
  "equip_tongshanchui": {
    "hp": 180,
    "defense": 8,
    "force": 26,
    "required_level": 20
  },
  "equip_tulongdao": {
    "hp": 220,
    "force": 70,
    "luck": 14,
    "required_level": 60
  },
  "equip_wujinjia": {
    "hp": 340,
    "defense": 22,
    "force": 20,
    "luck": 6,
    "required_level": 30
  },
  "equip_wujixingxue": {
    "hp": 320,
    "defense": 16,
    "intellect": 38,
    "agility": 30,
    "required_level": 75
  },
  "equip_wuqiaojian": {
    "hp": 120,
    "force": 58,
    "agility": 12,
    "troop_capacity": 18,
    "required_level": 50
  },
  "equip_wuzhuima": {
    "hp": 300,
    "defense": 16,
    "force": 34,
    "agility": 14,
    "required_level": 45
  },
  "equip_xiaolifeidao": {
    "hp": 60,
    "force": 30,
    "agility": 28,
    "luck": 10,
    "required_level": 75
  },
  "equip_xiaoweichangjian": {
    "hp": 72,
    "force": 45,
    "troop_capacity": 25,
    "set_key": "xiaowei_set",
    "set_description": "校尉套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 220,
        "defense": 8,
        "force": 22,
        "troop_capacity": 30
      }
    },
    "required_level": 30
  },
  "equip_xiaoweichangxue": {
    "hp": 200,
    "defense": 14,
    "force": 5,
    "agility": 20,
    "troop_capacity": 10,
    "set_key": "xiaowei_set",
    "set_description": "校尉套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 220,
        "defense": 8,
        "force": 22,
        "troop_capacity": 30
      }
    },
    "required_level": 30
  },
  "equip_xiaoweikaijia": {
    "hp": 400,
    "defense": 25,
    "force": 10,
    "troop_capacity": 20,
    "set_key": "xiaowei_set",
    "set_description": "校尉套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 220,
        "defense": 8,
        "force": 22,
        "troop_capacity": 30
      }
    },
    "required_level": 30
  },
  "equip_xiaoweitoukui": {
    "hp": 300,
    "defense": 20,
    "force": 15,
    "troop_capacity": 15,
    "set_key": "xiaowei_set",
    "set_description": "校尉套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 220,
        "defense": 8,
        "force": 22,
        "troop_capacity": 30
      }
    },
    "required_level": 30
  },
  "equip_xingshashouchuan": {
    "hp": 180,
    "defense": 8,
    "intellect": 18,
    "luck": 18,
    "required_level": 30
  },
  "equip_xiuhuazhen": {
    "hp": 40,
    "force": 24,
    "agility": 15,
    "luck": 6,
    "required_level": 55
  },
  "equip_xuanbinggong": {
    "defense": 12,
    "force": 28,
    "agility": 22,
    "required_level": 35
  },
  "equip_xuanjiazhanbao": {
    "hp": 320,
    "defense": 22,
    "intellect": 20,
    "agility": 10,
    "required_level": 40
  },
  "equip_xuanjiazhanju": {
    "hp": 300,
    "defense": 22,
    "force": 18,
    "agility": 18,
    "troop_capacity": 28,
    "required_level": 60
  },
  "equip_xuanmingdeng": {
    "hp": 160,
    "intellect": 48,
    "luck": 12,
    "required_level": 55
  },
  "equip_xuantiechongjian": {
    "hp": 420,
    "defense": 32,
    "force": 92,
    "required_level": 80
  },
  "equip_xuantiejia": {
    "hp": 540,
    "defense": 22,
    "required_level": 20
  },
  "equip_xuantiekui": {
    "hp": 340,
    "defense": 32,
    "force": 10,
    "required_level": 40
  },
  "equip_xuanwuchui": {
    "hp": 380,
    "defense": 26,
    "force": 42,
    "set_key": "xuanwu_set",
    "set_description": "玄武套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 220,
        "defense": 40,
        "force": 12
      }
    },
    "required_level": 55
  },
  "equip_xuanwujia": {
    "hp": 540,
    "defense": 36,
    "intellect": 18,
    "set_key": "xuanwu_set",
    "set_description": "玄武套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 220,
        "defense": 40,
        "force": 12
      }
    },
    "required_level": 55
  },
  "equip_xuanwukui": {
    "hp": 480,
    "defense": 40,
    "intellect": 6,
    "set_key": "xuanwu_set",
    "set_description": "玄武套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 220,
        "defense": 40,
        "force": 12
      }
    },
    "required_level": 55
  },
  "equip_xuanwuxue": {
    "hp": 380,
    "defense": 30,
    "agility": 18,
    "set_key": "xuanwu_set",
    "set_description": "玄武套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 220,
        "defense": 40,
        "force": 12
      }
    },
    "required_level": 55
  },
  "equip_xuanwuyufu": {
    "hp": 480,
    "defense": 34,
    "intellect": 12,
    "required_level": 60
  },
  "equip_xuanyuanjian": {
    "hp": 300,
    "defense": 14,
    "force": 22,
    "intellect": 56,
    "agility": 14,
    "troop_capacity": 36,
    "required_level": 80
  },
  "equip_xuedao": {
    "hp": 300,
    "force": 56,
    "agility": 10,
    "required_level": 50
  },
  "equip_xueyuzhuo": {
    "hp": 340,
    "force": 22,
    "troop_capacity": 22,
    "required_level": 25
  },
  "equip_yanlingmao": {
    "hp": 160,
    "defense": 12,
    "agility": 12,
    "luck": 6,
    "required_level": 20
  },
  "equip_yanyuxue": {
    "hp": 140,
    "defense": 6,
    "force": 4,
    "agility": 16,
    "required_level": 10
  },
  "equip_yaoguangkaijia": {
    "hp": 440,
    "defense": 24,
    "force": 10,
    "agility": 18,
    "required_level": 55
  },
  "equip_yemantoukie": {
    "hp": 140,
    "defense": 8,
    "force": 20,
    "set_key": "yeman_set",
    "set_description": "野蛮套装",
    "set_bonus": {
      "pieces": 2,
      "bonus": {
        "hp": 250,
        "force": 20,
        "defense": 8
      }
    },
    "required_level": 15
  },
  "equip_yingxingxue": {
    "hp": 140,
    "defense": 6,
    "agility": 28,
    "luck": 8,
    "required_level": 25
  },
  "equip_yitianjian": {
    "hp": 300,
    "defense": 18,
    "force": 58,
    "intellect": 30,
    "agility": 14,
    "luck": 8,
    "troop_capacity": 12,
    "required_level": 65
  },
  "equip_youxiaxue": {
    "hp": 180,
    "defense": 4,
    "force": 6,
    "agility": 20,
    "luck": 4,
    "required_level": 20
  },
  "equip_yuanyangdao": {
    "hp": 260,
    "force": 36,
    "intellect": 24,
    "luck": 18,
    "required_level": 60
  },
  "equip_yuanyuewandao": {
    "hp": 140,
    "force": 50,
    "agility": 20,
    "luck": 14,
    "required_level": 80
  },
  "equip_yuhengkaijia": {
    "hp": 480,
    "defense": 28,
    "force": 16,
    "intellect": 16,
    "luck": 6,
    "required_level": 55
  },
  "equip_yupei": {
    "hp": 100,
    "troop_capacity": 28,
    "required_level": 15,
    "luck": 10
  },
  "equip_yuxiao": {
    "hp": 140,
    "intellect": 48,
    "agility": 12,
    "luck": 8,
    "required_level": 60
  },
  "equip_zhaohuangfeidian": {
    "hp": 220,
    "defense": 10,
    "force": 16,
    "agility": 26,
    "required_level": 50
  },
  "equip_zhaoyeyushizi": {
    "hp": 240,
    "defense": 12,
    "force": 12,
    "agility": 26,
    "troop_capacity": 12,
    "required_level": 40
  },
  "equip_zhenyuechui": {
    "hp": 340,
    "defense": 22,
    "force": 28,
    "required_level": 40
  },
  "equip_zhoutianxingpan": {
    "hp": 200,
    "defense": 26,
    "agility": 14,
    "luck": 24,
    "required_level": 75
  },
  "equip_zhuidian": {
    "hp": 220,
    "defense": 6,
    "agility": 24,
    "luck": 8,
    "required_level": 40
  },
  "equip_zhuifengxue": {
    "hp": 160,
    "defense": 6,
    "force": 8,
    "agility": 24,
    "required_level": 25
  },
  "equip_zhujian": {
    "hp": 100,
    "intellect": 24,
    "troop_capacity": 12,
    "required_level": 15
  },
  "equip_zhurixue": {
    "hp": 200,
    "defense": 16,
    "agility": 36,
    "required_level": 60
  },
  "equip_zhuxianjian": {
    "hp": 200,
    "defense": 8,
    "intellect": 90,
    "agility": 10,
    "luck": 14,
    "required_level": 80
  },
  "equip_zhuyuye": {
    "hp": 280,
    "defense": 18,
    "force": 14,
    "luck": 16,
    "required_level": 55
  },
  "equip_zijinhulu": {
    "hp": 240,
    "defense": 20,
    "intellect": 40,
    "required_level": 55
  },
  "equip_ziweiruanjian": {
    "hp": 80,
    "force": 36,
    "intellect": 16,
    "agility": 18,
    "required_level": 55
  }
}
"""
)

STAT_FIELDS = {
    "hp": "hp_bonus",
    "force": "force",
    "intellect": "intellect",
    "defense": "defense_stat",
    "agility": "agility",
    "luck": "luck",
    "troop_capacity": "troop_capacity_bonus",
}
SET_FIELDS = {**STAT_FIELDS, "attack": "attack_bonus"}
AFFIXES = ("agility", "force", "defense", "intellect")


def _affix_key(base_key, affix, limit):
    candidate = f"affix_{affix}__{base_key}"
    if len(candidate) <= limit:
        return candidate
    digest = hashlib.sha256(base_key.encode()).hexdigest()[:12]
    prefix = f"affix_{affix}__"
    return f"{prefix}{base_key[: limit - len(prefix) - 13]}_{digest}"


def _affix_payload(base, affix):
    payload = copy.deepcopy(base.effect_payload)
    gain = 0
    for stat in AFFIXES:
        if stat == affix:
            continue
        loss = int(payload.get(stat, 0)) // 5
        if stat in payload:
            payload[stat] -= loss
        gain += loss
    if gain:
        payload[affix] = int(payload.get(affix, 0)) + gain
    payload.update(equipment_base_key=base.key, equipment_base_name=base.name, equipment_affix=affix)
    return payload


def _normalize_set_bonus_defs(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        out = []
        for entry in raw:
            out.extend(_normalize_set_bonus_defs(entry))
        return out
    if isinstance(raw, dict):
        pieces = raw.get("pieces")
        bonus = raw.get("bonus") or raw.get("bonuses") or raw
        if isinstance(bonus, dict):
            return [(pieces, bonus)]
    return []


def _compute_guest_set_bonus(gears, base_set_info=None):
    base_set_info = base_set_info or {}
    by_set = {}
    for gear in gears:
        tpl = gear.template
        if not tpl:
            continue
        set_key = getattr(tpl, "set_key", "") or ""
        raw_set_bonus = getattr(tpl, "set_bonus", None)
        base_key = getattr(tpl, "base_key", "") or ""
        inherited = base_set_info.get(base_key)
        if inherited:
            set_key = set_key or inherited[0]
            raw_set_bonus = raw_set_bonus or inherited[1]
        if not set_key:
            continue
        defs = _normalize_set_bonus_defs(raw_set_bonus)
        if not defs:
            continue
        info = by_set.setdefault(set_key, {"count": 0, "defs": defs})
        info["count"] += 1
    totals = {}
    for info in by_set.values():
        count = info["count"]
        for pieces, bonus in info["defs"]:
            required = int(pieces or count or 0)
            if count < required:
                continue
            for stat, val in bonus.items():
                if stat in SET_FIELDS:
                    totals[stat] = totals.get(stat, 0) + int(val)
    return {k: v for k, v in totals.items() if v}


def apply_equipment_balance_calibration(apps, schema_editor):
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    Manor = apps.get_model("gameplay", "Manor")
    GearTemplate = apps.get_model("guests", "GearTemplate")
    GearItem = apps.get_model("guests", "GearItem")
    Guest = apps.get_model("guests", "Guest")

    # 遵循庄园 -> 装备 -> 门客死锁预防锁顺序。
    list(Manor.objects.using(database).select_for_update().order_by("pk").values_list("pk", flat=True))

    bases = {}
    payloads = {}
    for item in (
        ItemTemplate.objects.using(database).select_for_update().filter(key__in=EQUIPMENT_PAYLOADS).order_by("pk")
    ):
        payload = copy.deepcopy(EQUIPMENT_PAYLOADS[item.key])
        if item.effect_payload != payload:
            item.effect_payload = payload
            item.save(using=database, update_fields=["effect_payload"])
        bases[item.key] = item
        payloads[item.key] = payload

    limit = ItemTemplate._meta.get_field("key").max_length or 50
    variants = {_affix_key(key, affix, limit): (base, affix) for key, base in bases.items() for affix in AFFIXES}
    for key, (base, affix) in variants.items():
        payloads[key] = _affix_payload(base, affix)
    for item in ItemTemplate.objects.using(database).select_for_update().filter(key__in=variants).order_by("pk"):
        payload = payloads[item.key]
        if item.effect_payload != payload:
            item.effect_payload = payload
            item.save(using=database, update_fields=["effect_payload"])

    changes = {}
    for template in GearTemplate.objects.using(database).select_for_update().filter(key__in=payloads).order_by("pk"):
        payload = payloads[template.key]
        stats = {stat: int(payload[stat]) for stat in STAT_FIELDS if stat in payload}
        set_bonus = copy.deepcopy(payload.get("set_bonus")) if payload.get("set_bonus") else {}
        if template.extra_stats != stats or (template.set_bonus or {}) != set_bonus:
            changes[template.pk] = (template, stats, set_bonus)

    if not changes:
        return

    affected_gears = list(
        GearItem.objects.using(database)
        .select_for_update()
        .filter(template_id__in=changes, guest_id__isnull=False)
        .order_by("pk")
    )
    guest_ids = {gear.guest_id for gear in affected_gears}
    guests = {
        guest.pk: guest
        for guest in Guest.objects.using(database)
        .select_for_update()
        .filter(pk__in=guest_ids)
        .select_related("template")
        .order_by("pk")
    }
    updates = {pk: set() for pk in guests}

    for gear in affected_gears:
        guest = guests[gear.guest_id]
        template, new_stats, _new_set = changes[gear.template_id]
        old_stats = template.extra_stats or {}
        for stat, field in STAT_FIELDS.items():
            delta = new_stats.get(stat, 0) - int(old_stats.get(stat, 0))
            if delta:
                setattr(guest, field, max(0, int(getattr(guest, field)) + delta))
                updates[guest.pk].add(field)

    for template, new_stats, new_set in changes.values():
        template.extra_stats = new_stats
        template.set_bonus = new_set
        template.save(using=database, update_fields=["extra_stats", "set_bonus"])

    # 重新汇总受影响门客的套装加成增量。
    if guests:
        all_worn = list(
            GearItem.objects.using(database).filter(guest_id__in=guests).select_related("template").order_by("pk")
        )
        base_keys = {
            g.template.base_key
            for g in all_worn
            if g.template
            and getattr(g.template, "base_key", "")
            and g.template.base_key != g.template.key
            and (not g.template.set_key or not g.template.set_bonus)
        }
        base_set_info = (
            {
                key: (set_key, set_bonus)
                for key, set_key, set_bonus in GearTemplate.objects.using(database)
                .filter(key__in=base_keys)
                .values_list("key", "set_key", "set_bonus")
            }
            if base_keys
            else {}
        )
        gears_by_guest = {pk: [] for pk in guests}
        for g in all_worn:
            gears_by_guest[g.guest_id].append(g)

        for pk, guest in guests.items():
            old_set = guest.gear_set_bonus or {}
            new_set = _compute_guest_set_bonus(gears_by_guest[pk], base_set_info)
            if old_set != new_set:
                for stat, field in SET_FIELDS.items():
                    delta = int(new_set.get(stat, 0)) - int(old_set.get(stat, 0))
                    if delta:
                        setattr(guest, field, max(0, int(getattr(guest, field)) + delta))
                        updates[pk].add(field)
                guest.gear_set_bonus = new_set
                updates[pk].add("gear_set_bonus")

            maximum_hp = max(
                int(GUEST.MIN_HP_FLOOR),
                int(guest.template.base_hp)
                + int(guest.hp_bonus)
                + int(GUEST.DEFENSE_TO_HP_MULTIPLIER) * int(guest.defense_stat),
            ) + max(0, int(guest.level) - 1) * int(GUEST.LEVEL_HP_GAIN)
            if guest.current_hp > maximum_hp:
                guest.current_hp = maximum_hp
                updates[pk].add("current_hp")
            if updates[pk]:
                guest.save(using=database, update_fields=sorted(updates[pk]))


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0274_arena_power_formula_version"),
        ("guests", "0098_reduce_defense_hp_multiplier"),
    ]
    operations = [migrations.RunPython(apply_equipment_balance_calibration, migrations.RunPython.noop, atomic=True)]
