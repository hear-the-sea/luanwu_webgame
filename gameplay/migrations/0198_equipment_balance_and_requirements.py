"""Immutable approved v2 snapshot; preserve existing worn equipment."""

import json

from django.db import migrations

EQUIPMENT_PAYLOADS = json.loads(
    r"""
{
  "equip_action_mask": {
    "hp": 300,
    "defense": 5,
    "force": 18,
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
  "equip_shiro_leash": {
    "hp": 300,
    "defense": 14,
    "force": 5,
    "luck": 10,
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
  "equip_kasukabe_satchel": {
    "hp": 400,
    "defense": 20,
    "agility": 10,
    "luck": 10,
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
  "equip_action_beam_glove": {
    "force": 40,
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
  "equip_nohara_pan": {
    "hp": 180,
    "defense": 12,
    "force": 36,
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
  "equip_caomao": {
    "hp": 250,
    "defense": 10,
    "force": 5,
    "set_key": "starter_cloth_set",
    "set_description": "一蓑烟雨",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 200,
        "force": 20,
        "troop_capacity": 20,
        "luck": 15
      }
    },
    "required_level": 1
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
        "hp": 200,
        "force": 20,
        "troop_capacity": 20,
        "luck": 15
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
        "hp": 200,
        "force": 20,
        "troop_capacity": 20,
        "luck": 15
      }
    },
    "required_level": 1
  },
  "equip_tiejian": {
    "defense": 5,
    "force": 15,
    "set_key": "starter_cloth_set",
    "set_description": "一蓑烟雨",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 200,
        "force": 20,
        "troop_capacity": 20,
        "luck": 15
      }
    },
    "required_level": 1
  },
  "equip_bumao": {
    "hp": 100,
    "defense": 5,
    "intellect": 8,
    "agility": 5,
    "required_level": 1
  },
  "equip_niupimao": {
    "hp": 100,
    "defense": 10,
    "force": 5,
    "agility": 5,
    "required_level": 10
  },
  "equip_tieyekui": {
    "hp": 120,
    "defense": 8,
    "force": 12,
    "required_level": 10
  },
  "equip_yulindin": {
    "hp": 250,
    "defense": 12,
    "force": 1,
    "agility": 4,
    "required_level": 10
  },
  "equip_baihongkui": {
    "hp": 160,
    "defense": 12,
    "force": 4,
    "troop_capacity": 10,
    "required_level": 10
  },
  "equip_zhubiankui": {
    "hp": 300,
    "defense": 15,
    "required_level": 10
  },
  "equip_yemantoukie": {
    "hp": 150,
    "defense": 10,
    "force": 16,
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
  "equip_huwenkui": {
    "hp": 220,
    "defense": 18,
    "force": 15,
    "required_level": 20
  },
  "equip_baoutoukui": {
    "hp": 200,
    "defense": 18,
    "agility": 12,
    "required_level": 20
  },
  "equip_yanlingmao": {
    "hp": 200,
    "defense": 15,
    "agility": 12,
    "required_level": 20
  },
  "equip_qingtengguan": {
    "hp": 200,
    "defense": 10,
    "intellect": 15,
    "luck": 5,
    "required_level": 15
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
        "hp": 300,
        "defense": 12,
        "force": 30,
        "troop_capacity": 50
      }
    },
    "required_level": 30
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
        "hp": 240,
        "defense": 8,
        "force": 24,
        "agility": 12
      }
    },
    "required_level": 35
  },
  "equip_xuantiekui": {
    "hp": 300,
    "defense": 26,
    "force": 15,
    "required_level": 40
  },
  "equip_qinglongkui": {
    "hp": 300,
    "defense": 20,
    "intellect": 22,
    "set_key": "sixiang_set",
    "set_description": "四象套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 300,
        "force": 25,
        "intellect": 25,
        "agility": 15
      }
    },
    "required_level": 35
  },
  "equip_hantiekui": {
    "hp": 300,
    "defense": 26,
    "force": 5,
    "required_level": 40
  },
  "equip_langyakui": {
    "hp": 300,
    "defense": 20,
    "force": 18,
    "agility": 15,
    "required_level": 40
  },
  "equip_feiyukui": {
    "hp": 300,
    "defense": 15,
    "agility": 15,
    "luck": 10,
    "required_level": 35
  },
  "equip_yunwenkui": {
    "hp": 300,
    "defense": 15,
    "intellect": 22,
    "luck": 10,
    "required_level": 30
  },
  "equip_qilinkui": {
    "hp": 360,
    "defense": 32,
    "force": 24,
    "intellect": 3,
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
  "equip_fenglingguan": {
    "hp": 360,
    "defense": 21,
    "intellect": 28,
    "luck": 11,
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
  "equip_bawangkui": {
    "hp": 360,
    "defense": 30,
    "force": 24,
    "troop_capacity": 15,
    "required_level": 60
  },
  "equip_xuanwukui": {
    "hp": 360,
    "defense": 32,
    "force": 10,
    "intellect": 10,
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
  "equip_pojunkui": {
    "hp": 360,
    "defense": 26,
    "force": 24,
    "agility": 15,
    "troop_capacity": 20,
    "required_level": 55
  },
  "equip_tianjiguan": {
    "hp": 360,
    "defense": 20,
    "intellect": 28,
    "luck": 12,
    "required_level": 45
  },
  "equip_longhuangguan": {
    "hp": 400,
    "defense": 26,
    "force": 12,
    "intellect": 30,
    "troop_capacity": 40,
    "required_level": 65
  },
  "equip_diwangkui": {
    "hp": 400,
    "defense": 29,
    "force": 24,
    "intellect": 29,
    "troop_capacity": 30,
    "required_level": 80
  },
  "equip_taixudiguan": {
    "hp": 400,
    "defense": 30,
    "intellect": 30,
    "luck": 14,
    "required_level": 75
  },
  "equip_bupao": {
    "hp": 80,
    "defense": 6,
    "intellect": 8,
    "required_level": 1
  },
  "equip_shengpijia": {
    "hp": 160,
    "defense": 12,
    "force": 4,
    "required_level": 5
  },
  "equip_housipao": {
    "hp": 80,
    "defense": 12,
    "intellect": 4,
    "agility": 6,
    "required_level": 10
  },
  "equip_shapijia": {
    "hp": 200,
    "defense": 14,
    "force": 4,
    "agility": 4,
    "required_level": 10
  },
  "equip_cubujia": {
    "hp": 150,
    "defense": 10,
    "force": 5,
    "agility": 5,
    "required_level": 10
  },
  "equip_suozijia": {
    "hp": 500,
    "defense": 22,
    "force": 5,
    "required_level": 20
  },
  "equip_xuantiejia": {
    "hp": 500,
    "defense": 22,
    "required_level": 20
  },
  "equip_shanwenjia": {
    "hp": 500,
    "defense": 20,
    "force": 10,
    "required_level": 15
  },
  "equip_qingzhujia": {
    "hp": 200,
    "defense": 20,
    "force": 5,
    "agility": 10,
    "required_level": 15
  },
  "equip_tiexianjia": {
    "hp": 500,
    "defense": 15,
    "force": 12,
    "required_level": 15
  },
  "equip_nichangyuyi": {
    "hp": 50,
    "defense": 15,
    "intellect": 15,
    "luck": 8,
    "set_key": "nichang_set",
    "set_description": "霓裳",
    "set_bonus": {
      "pieces": 3,
      "bonus": {
        "hp": 120,
        "intellect": 15,
        "agility": 8,
        "luck": 12
      }
    },
    "required_level": 15
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
        "hp": 300,
        "defense": 12,
        "force": 30,
        "troop_capacity": 50
      }
    },
    "required_level": 30
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
        "hp": 240,
        "defense": 8,
        "force": 24,
        "agility": 12
      }
    },
    "required_level": 35
  },
  "equip_qinglongjia": {
    "hp": 350,
    "defense": 24,
    "force": 15,
    "intellect": 18,
    "set_key": "sixiang_set",
    "set_description": "四象套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 300,
        "force": 25,
        "intellect": 25,
        "agility": 15
      }
    },
    "required_level": 35
  },
  "equip_wujinjia": {
    "hp": 400,
    "defense": 25,
    "force": 18,
    "required_level": 30
  },
  "equip_linwenzhanjia": {
    "hp": 400,
    "defense": 28,
    "force": 15,
    "required_level": 35
  },
  "equip_yunwenfayi": {
    "hp": 350,
    "defense": 10,
    "intellect": 25,
    "luck": 10,
    "required_level": 25
  },
  "equip_xuanjiazhanbao": {
    "hp": 400,
    "defense": 28,
    "intellect": 20,
    "required_level": 40
  },
  "equip_huxianpao": {
    "hp": 500,
    "defense": 25,
    "force": 3,
    "intellect": 28,
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
  "equip_qilinjia": {
    "hp": 500,
    "defense": 35,
    "force": 22,
    "intellect": 4,
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
  "equip_xuanwujia": {
    "hp": 500,
    "defense": 36,
    "force": 7,
    "intellect": 7,
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
  "equip_tiancanbaoyi": {
    "hp": 500,
    "defense": 30,
    "force": 6,
    "agility": 18,
    "required_level": 60
  },
  "equip_pojunzhanjia": {
    "hp": 500,
    "defense": 29,
    "force": 22,
    "troop_capacity": 20,
    "required_level": 55
  },
  "equip_tianjidaopao": {
    "hp": 376,
    "defense": 19,
    "intellect": 28,
    "luck": 15,
    "required_level": 45
  },
  "equip_longlinzhanjia": {
    "hp": 550,
    "defense": 38,
    "force": 26,
    "intellect": 3,
    "required_level": 75
  },
  "equip_jinsirujia": {
    "hp": 550,
    "defense": 32,
    "agility": 18,
    "luck": 16,
    "required_level": 70
  },
  "equip_hundunlongpao": {
    "hp": 540,
    "defense": 23,
    "intellect": 32,
    "luck": 15,
    "required_level": 65
  },
  "equip_ruanweijia": {
    "hp": 550,
    "defense": 38,
    "force": 8,
    "agility": 18,
    "required_level": 80
  },
  "equip_buxie": {
    "hp": 80,
    "defense": 4,
    "intellect": 4,
    "agility": 6,
    "required_level": 5
  },
  "equip_yangpixue": {
    "hp": 120,
    "defense": 2,
    "force": 4,
    "agility": 8,
    "required_level": 1
  },
  "equip_gangpianxue": {
    "hp": 200,
    "defense": 8,
    "force": 4,
    "agility": 10,
    "required_level": 10
  },
  "equip_yanyuxue": {
    "hp": 160,
    "defense": 8,
    "force": 4,
    "agility": 14,
    "required_level": 10
  },
  "equip_mabuxue": {
    "hp": 150,
    "defense": 8,
    "intellect": 5,
    "agility": 6,
    "required_level": 10
  },
  "equip_hupixue": {
    "hp": 200,
    "defense": 5,
    "force": 8,
    "agility": 15,
    "required_level": 10
  },
  "equip_tayunxue": {
    "hp": 100,
    "defense": 5,
    "force": 5,
    "agility": 22,
    "required_level": 15
  },
  "equip_jifengxue": {
    "hp": 200,
    "defense": 8,
    "agility": 22,
    "required_level": 20
  },
  "equip_youxiaxue": {
    "hp": 200,
    "defense": 5,
    "force": 8,
    "agility": 20,
    "required_level": 20
  },
  "equip_zhuyelu": {
    "hp": 100,
    "defense": 5,
    "agility": 20,
    "luck": 8,
    "required_level": 15
  },
  "equip_nichangwuxie": {
    "hp": 150,
    "defense": 5,
    "intellect": 10,
    "agility": 20,
    "set_key": "nichang_set",
    "set_description": "霓裳",
    "set_bonus": {
      "pieces": 3,
      "bonus": {
        "hp": 120,
        "intellect": 15,
        "agility": 8,
        "luck": 12
      }
    },
    "required_level": 15
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
        "hp": 300,
        "defense": 12,
        "force": 30,
        "troop_capacity": 50
      }
    },
    "required_level": 30
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
        "hp": 240,
        "defense": 8,
        "force": 24,
        "agility": 12
      }
    },
    "required_level": 35
  },
  "equip_qinglongxue": {
    "hp": 200,
    "defense": 10,
    "force": 12,
    "intellect": 12,
    "agility": 20,
    "set_key": "sixiang_set",
    "set_description": "四象套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 300,
        "force": 25,
        "intellect": 25,
        "agility": 15
      }
    },
    "required_level": 35
  },
  "equip_lingboxie": {
    "hp": 200,
    "defense": 10,
    "agility": 30,
    "required_level": 30
  },
  "equip_zhuifengxue": {
    "hp": 180,
    "defense": 8,
    "agility": 30,
    "required_level": 25
  },
  "equip_pojunxue": {
    "hp": 200,
    "defense": 14,
    "force": 12,
    "agility": 20,
    "required_level": 30
  },
  "equip_yingxingxue": {
    "hp": 200,
    "defense": 10,
    "agility": 30,
    "required_level": 25
  },
  "equip_jinlinxue": {
    "hp": 200,
    "defense": 14,
    "force": 11,
    "agility": 26,
    "required_level": 40
  },
  "equip_huxianxie": {
    "hp": 240,
    "defense": 10,
    "intellect": 18,
    "agility": 32,
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
  "equip_qilinxue": {
    "hp": 240,
    "defense": 20,
    "force": 14,
    "agility": 30,
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
  "equip_xuanwuxue": {
    "hp": 240,
    "defense": 20,
    "force": 7,
    "agility": 30,
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
  "equip_zhurixue": {
    "hp": 240,
    "defense": 20,
    "force": 10,
    "agility": 36,
    "required_level": 60
  },
  "equip_tianjilu": {
    "hp": 240,
    "defense": 15,
    "intellect": 18,
    "agility": 36,
    "required_level": 55
  },
  "equip_liekongxue": {
    "hp": 240,
    "defense": 16,
    "force": 14,
    "agility": 36,
    "required_level": 45
  },
  "equip_tatianxue": {
    "hp": 220,
    "defense": 12,
    "force": 20,
    "intellect": 7,
    "agility": 32,
    "required_level": 70
  },
  "equip_shenxingxue": {
    "hp": 180,
    "defense": 10,
    "force": 5,
    "agility": 42,
    "required_level": 80
  },
  "equip_wujixingxue": {
    "hp": 240,
    "defense": 18,
    "force": 7,
    "agility": 38,
    "required_level": 75
  },
  "equip_duanjian": {
    "hp": 80,
    "force": 8,
    "agility": 6,
    "required_level": 1
  },
  "equip_changjian": {
    "hp": 120,
    "defense": 1,
    "force": 14,
    "required_level": 5
  },
  "equip_qingmangjian": {
    "defense": 4,
    "force": 16,
    "agility": 5,
    "required_level": 15
  },
  "equip_duanmajian": {
    "force": 20,
    "agility": 8,
    "required_level": 10
  },
  "equip_duandao": {
    "hp": 40,
    "force": 12,
    "agility": 4,
    "required_level": 1
  },
  "equip_dakandao": {
    "hp": 80,
    "force": 16,
    "required_level": 5
  },
  "equip_tongchangdao": {
    "hp": 80,
    "defense": 4,
    "force": 22,
    "required_level": 20
  },
  "equip_jingtiedao": {
    "hp": 40,
    "force": 28,
    "required_level": 15
  },
  "equip_changqiang": {
    "hp": 160,
    "defense": 0,
    "force": 9,
    "troop_capacity": 15,
    "required_level": 1
  },
  "equip_baoweiqiang": {
    "defense": 4,
    "force": 8,
    "troop_capacity": 25,
    "required_level": 10
  },
  "equip_hutoumao": {
    "defense": 4,
    "force": 16,
    "troop_capacity": 15,
    "required_level": 10
  },
  "equip_pansheqiang": {
    "force": 16,
    "agility": 4,
    "troop_capacity": 20,
    "required_level": 10
  },
  "equip_changgong": {
    "force": 12,
    "agility": 4,
    "required_level": 1
  },
  "equip_fanqugong": {
    "force": 12,
    "agility": 6,
    "required_level": 5
  },
  "equip_tietaigong": {
    "defense": 4,
    "force": 12,
    "agility": 9,
    "required_level": 15
  },
  "equip_shenbigong": {
    "force": 16,
    "agility": 12,
    "required_level": 10
  },
  "equip_changbian": {
    "force": 12,
    "agility": 8,
    "required_level": 10
  },
  "equip_niupibian": {
    "force": 10,
    "agility": 6,
    "luck": 4,
    "required_level": 10
  },
  "equip_jicibian": {
    "force": 12,
    "agility": 6,
    "luck": 6,
    "required_level": 10
  },
  "equip_jiulonggangbian": {
    "force": 12,
    "agility": 8,
    "luck": 6,
    "required_level": 10
  },
  "equip_mingshejiebian": {
    "hp": 40,
    "force": 12,
    "agility": 6,
    "luck": 8,
    "required_level": 15
  },
  "equip_yemankandao": {
    "force": 40,
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
  "equip_tiegugun": {
    "defense": 5,
    "force": 30,
    "required_level": 20
  },
  "equip_tongshanchui": {
    "hp": 160,
    "defense": 5,
    "force": 32,
    "required_level": 20
  },
  "equip_pofengji": {
    "force": 25,
    "agility": 5,
    "troop_capacity": 25,
    "required_level": 20
  },
  "equip_shushengzhesan": {
    "force": 5,
    "intellect": 25,
    "luck": 10,
    "required_level": 20
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
        "hp": 300,
        "defense": 12,
        "force": 30,
        "troop_capacity": 50
      }
    },
    "required_level": 30
  },
  "equip_nichangjian": {
    "force": 10,
    "intellect": 35,
    "agility": 15,
    "set_key": "nichang_set",
    "set_description": "霓裳",
    "set_bonus": {
      "pieces": 3,
      "bonus": {
        "hp": 120,
        "intellect": 15,
        "agility": 8,
        "luck": 12
      }
    },
    "required_level": 25
  },
  "equip_jiaodoushizhichui": {
    "hp": 250,
    "defense": 8,
    "force": 45,
    "set_key": "jiaodoushi_set",
    "set_description": "角斗士套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 240,
        "defense": 8,
        "force": 24,
        "agility": 12
      }
    },
    "required_level": 35
  },
  "equip_junzijian": {
    "force": 25,
    "intellect": 35,
    "agility": 15,
    "required_level": 40
  },
  "equip_qinglongdao": {
    "hp": 300,
    "force": 30,
    "intellect": 25,
    "set_key": "sixiang_set",
    "set_description": "四象套装",
    "set_bonus": {
      "pieces": 4,
      "bonus": {
        "hp": 300,
        "force": 25,
        "intellect": 25,
        "agility": 15
      }
    },
    "required_level": 35
  },
  "equip_pozhenqiang": {
    "hp": 150,
    "force": 50,
    "troop_capacity": 40,
    "required_level": 30
  },
  "equip_chuanyungong": {
    "force": 40,
    "agility": 18,
    "required_level": 25
  },
  "equip_hanyuejian": {
    "defense": 10,
    "force": 35,
    "agility": 15,
    "required_level": 35
  },
  "equip_lieyandao": {
    "hp": 160,
    "force": 50,
    "required_level": 25
  },
  "equip_duanhunbian": {
    "force": 35,
    "agility": 20,
    "luck": 10,
    "required_level": 35
  },
  "equip_zhenyuechui": {
    "hp": 300,
    "defense": 12,
    "force": 35,
    "required_level": 40
  },
  "equip_xuanbinggong": {
    "force": 40,
    "agility": 22,
    "required_level": 35
  },
  "equip_xingluoshan": {
    "intellect": 40,
    "luck": 15,
    "required_level": 30
  },
  "equip_huxianjian": {
    "force": 41,
    "intellect": 27,
    "luck": 5,
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
  "equip_bawangqiang": {
    "hp": 237,
    "force": 46,
    "agility": 12,
    "troop_capacity": 50,
    "required_level": 45
  },
  "equip_qilindao": {
    "hp": 211,
    "force": 68,
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
  "equip_xuanwuchui": {
    "hp": 300,
    "defense": 12,
    "force": 52,
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
  "equip_fenglingshan": {
    "force": 15,
    "intellect": 48,
    "luck": 20,
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
  "equip_qixingjian": {
    "force": 47,
    "intellect": 19,
    "agility": 16,
    "required_level": 45
  },
  "equip_tulongdao": {
    "hp": 271,
    "force": 68,
    "required_level": 60
  },
  "equip_tianjizhang": {
    "hp": 187,
    "force": 15,
    "intellect": 50,
    "luck": 15,
    "required_level": 60
  },
  "equip_baguadao": {
    "hp": 200,
    "defense": 5,
    "force": 40,
    "intellect": 20,
    "required_level": 40
  },
  "equip_pojunji": {
    "hp": 214,
    "force": 50,
    "agility": 12,
    "troop_capacity": 45,
    "required_level": 45
  },
  "equip_shuanghuagong": {
    "force": 44,
    "agility": 22,
    "luck": 14,
    "required_level": 50
  },
  "equip_jinshejian": {
    "force": 45,
    "agility": 20,
    "luck": 15,
    "required_level": 50
  },
  "equip_duomingfeidao": {
    "force": 38,
    "agility": 26,
    "luck": 16,
    "required_level": 55
  },
  "equip_ziweiruanjian": {
    "force": 46,
    "agility": 22,
    "luck": 13,
    "required_level": 55
  },
  "equip_yuxiao": {
    "hp": 85,
    "force": 20,
    "intellect": 40,
    "luck": 16,
    "required_level": 60
  },
  "equip_jinlun": {
    "hp": 280,
    "defense": 8,
    "force": 68,
    "required_level": 60
  },
  "equip_xuedao": {
    "hp": 170,
    "force": 63,
    "agility": 17,
    "required_level": 50
  },
  "equip_yuanyangdao": {
    "force": 49,
    "agility": 20,
    "luck": 13,
    "required_level": 60
  },
  "equip_jinshezui": {
    "force": 43,
    "agility": 26,
    "luck": 12,
    "required_level": 55
  },
  "equip_wuqiaojian": {
    "force": 53,
    "agility": 16,
    "luck": 12,
    "required_level": 50
  },
  "equip_biyudao": {
    "force": 48,
    "agility": 20,
    "luck": 13,
    "required_level": 55
  },
  "equip_libiegou": {
    "force": 50,
    "agility": 17,
    "luck": 13,
    "required_level": 50
  },
  "equip_duoqinghuan": {
    "hp": 96,
    "force": 40,
    "intellect": 26,
    "luck": 12,
    "required_level": 45
  },
  "equip_xiuhuazhen": {
    "force": 42,
    "agility": 26,
    "luck": 11,
    "required_level": 55
  },
  "equip_yitianjian": {
    "hp": 180,
    "defense": 3,
    "force": 64,
    "intellect": 16,
    "agility": 14,
    "luck": 5,
    "required_level": 65
  },
  "equip_tuwujian": {
    "hp": 160,
    "defense": 3,
    "force": 70,
    "intellect": 12,
    "agility": 12,
    "luck": 3,
    "required_level": 65
  },
  "equip_fangtianhuaji": {
    "hp": 240,
    "defense": 10,
    "force": 55,
    "agility": 8,
    "troop_capacity": 70,
    "required_level": 75
  },
  "equip_xuanyuanjian": {
    "hp": 220,
    "defense": 6,
    "force": 62,
    "intellect": 24,
    "luck": 12,
    "required_level": 80
  },
  "equip_panlonggun": {
    "hp": 240,
    "defense": 10,
    "force": 72,
    "agility": 10,
    "luck": 6,
    "required_level": 65
  },
  "equip_zhuxianjian": {
    "hp": 160,
    "defense": 4,
    "force": 28,
    "intellect": 58,
    "agility": 6,
    "luck": 10,
    "required_level": 80
  },
  "equip_qiankunshengong": {
    "hp": 120,
    "defense": 5,
    "force": 58,
    "intellect": 7,
    "agility": 28,
    "luck": 8,
    "required_level": 75
  },
  "equip_xuantiechongjian": {
    "hp": 280,
    "defense": 12,
    "force": 78,
    "agility": 5,
    "required_level": 80
  },
  "equip_dagoubang": {
    "hp": 180,
    "defense": 7,
    "force": 56,
    "agility": 24,
    "luck": 15,
    "required_level": 70
  },
  "equip_xiaolifeidao": {
    "hp": 100,
    "force": 58,
    "intellect": 5,
    "agility": 30,
    "luck": 18,
    "required_level": 75
  },
  "equip_kongquelin": {
    "hp": 120,
    "force": 50,
    "intellect": 22,
    "agility": 26,
    "luck": 18,
    "required_level": 80
  },
  "equip_tianyamingyuedao": {
    "hp": 220,
    "defense": 8,
    "force": 74,
    "agility": 16,
    "required_level": 70
  },
  "equip_yuanyuewandao": {
    "hp": 160,
    "defense": 7,
    "force": 62,
    "agility": 22,
    "luck": 14,
    "required_level": 80
  },
  "equip_changshengjian": {
    "hp": 220,
    "defense": 10,
    "force": 48,
    "intellect": 34,
    "luck": 15,
    "required_level": 80
  },
  "equip_zhuyuye": {
    "hp": 320,
    "defense": 14,
    "force": 21,
    "luck": 22,
    "required_level": 55
  },
  "equip_tongling": {
    "agility": 10,
    "luck": 16,
    "required_level": 10
  },
  "equip_yupei": {
    "hp": 50,
    "troop_capacity": 60,
    "required_level": 15
  },
  "equip_hushenfu": {
    "hp": 100,
    "defense": 14,
    "force": 5,
    "luck": 5,
    "required_level": 20
  },
  "equip_taomupai": {
    "defense": 14,
    "intellect": 5,
    "luck": 10,
    "required_level": 20
  },
  "equip_fudai": {
    "hp": 100,
    "defense": 5,
    "luck": 16,
    "required_level": 10
  },
  "equip_tiehufu": {
    "hp": 200,
    "defense": 14,
    "required_level": 20
  },
  "equip_tanmunianzhu": {
    "defense": 5,
    "force": 5,
    "intellect": 15,
    "troop_capacity": 15,
    "luck": 10,
    "required_level": 20
  },
  "equip_yiqiyaopai": {
    "hp": 100,
    "defense": 5,
    "force": 20,
    "troop_capacity": 20,
    "required_level": 20
  },
  "equip_longwenyupei": {
    "hp": 100,
    "force": 12,
    "troop_capacity": 60,
    "luck": 15,
    "required_level": 30
  },
  "equip_fengyuzan": {
    "hp": 100,
    "intellect": 15,
    "luck": 20,
    "required_level": 25
  },
  "equip_huyaxianglian": {
    "hp": 250,
    "force": 20,
    "agility": 15,
    "required_level": 25
  },
  "equip_linghuzhui": {
    "hp": 150,
    "intellect": 25,
    "agility": 15,
    "required_level": 30
  },
  "equip_xingshashouchuan": {
    "hp": 150,
    "intellect": 16,
    "luck": 20,
    "required_level": 30
  },
  "equip_xueyuzhuo": {
    "hp": 200,
    "force": 20,
    "agility": 15,
    "required_level": 25
  },
  "equip_xuantiejie": {
    "hp": 300,
    "defense": 14,
    "force": 15,
    "required_level": 40
  },
  "equip_liuyunpei": {
    "hp": 150,
    "intellect": 9,
    "agility": 15,
    "luck": 15,
    "required_level": 30
  },
  "equip_qilinzui": {
    "hp": 235,
    "force": 24,
    "intellect": 6,
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
  "equip_fenghuangyu": {
    "hp": 87,
    "defense": 10,
    "intellect": 29,
    "luck": 19,
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
  "equip_longjingzhu": {
    "hp": 229,
    "force": 19,
    "agility": 16,
    "luck": 18,
    "required_level": 45
  },
  "equip_xuanwuyufu": {
    "hp": 286,
    "defense": 18,
    "force": 16,
    "intellect": 10,
    "required_level": 60
  },
  "equip_tianjiyupei": {
    "hp": 184,
    "force": 7,
    "intellect": 26,
    "luck": 22,
    "required_level": 45
  },
  "equip_pojunhufu": {
    "hp": 267,
    "force": 26,
    "intellect": 9,
    "agility": 16,
    "troop_capacity": 80,
    "required_level": 55
  },
  "equip_guiyuanzhu": {
    "hp": 297,
    "force": 4,
    "intellect": 23,
    "luck": 24,
    "required_level": 45
  },
  "equip_shenghuoling": {
    "hp": 261,
    "force": 14,
    "intellect": 17,
    "luck": 22,
    "troop_capacity": 80,
    "required_level": 50
  },
  "equip_heshibi": {
    "hp": 290,
    "defense": 7,
    "intellect": 32,
    "luck": 25,
    "required_level": 65
  },
  "equip_chuanguoyuxi": {
    "hp": 350,
    "defense": 5,
    "force": 16,
    "intellect": 16,
    "troop_capacity": 160,
    "luck": 15,
    "required_level": 80
  },
  "equip_qiankunzhu": {
    "hp": 271,
    "defense": 20,
    "force": 15,
    "intellect": 10,
    "agility": 15,
    "luck": 10,
    "required_level": 70
  },
  "equip_businiaoyu": {
    "hp": 315,
    "force": 28,
    "agility": 20,
    "required_level": 65
  },
  "equip_jianghuling": {
    "hp": 350,
    "force": 20,
    "intellect": 20,
    "agility": 20,
    "troop_capacity": 80,
    "required_level": 75
  },
  "equip_shangshanfaeling": {
    "hp": 273,
    "defense": 7,
    "force": 21,
    "intellect": 18,
    "luck": 22,
    "troop_capacity": 60,
    "required_level": 70
  },
  "equip_taotieding": {
    "hp": 450,
    "defense": 15,
    "force": 18,
    "intellect": 24,
    "luck": 10,
    "troop_stat_bonus": {
      "dao": {
        "attack_pct": 0.0025,
        "defense_pct": 0.0025,
        "hp_pct": 0.0025
      },
      "qiang": {
        "attack_pct": 0.0025,
        "defense_pct": 0.0025,
        "hp_pct": 0.0025
      },
      "jian": {
        "attack_pct": 0.0025,
        "defense_pct": 0.0025,
        "hp_pct": 0.0025
      },
      "quan": {
        "attack_pct": 0.0025,
        "defense_pct": 0.0025,
        "hp_pct": 0.0025
      },
      "gong": {
        "attack_pct": 0.0025,
        "defense_pct": 0.0025,
        "hp_pct": 0.0025
      },
      "scout": {
        "attack_pct": 0.0025,
        "defense_pct": 0.0025,
        "hp_pct": 0.0025
      }
    },
    "required_level": 55
  },
  "equip_taomujian": {
    "defense": 10,
    "intellect": 15,
    "luck": 5,
    "required_level": 15
  },
  "equip_tongjing": {
    "hp": 250,
    "defense": 14,
    "intellect": 5,
    "required_level": 15
  },
  "equip_luopan": {
    "intellect": 20,
    "agility": 5,
    "luck": 5,
    "required_level": 10
  },
  "equip_zhujian": {
    "defense": 5,
    "intellect": 24,
    "required_level": 15
  },
  "equip_moyuyan": {
    "hp": 200,
    "defense": 5,
    "intellect": 24,
    "required_level": 15
  },
  "equip_qingtongsuanchou": {
    "intellect": 20,
    "luck": 15,
    "required_level": 10
  },
  "equip_xiaoxingjiguanshu": {
    "hp": 400,
    "force": 12,
    "agility": 12,
    "troop_stat_bonus": {
      "dao": {
        "hp_pct": 0.005
      },
      "qiang": {
        "hp_pct": 0.005
      },
      "jian": {
        "hp_pct": 0.005
      },
      "quan": {
        "hp_pct": 0.005
      },
      "gong": {
        "hp_pct": 0.005
      },
      "scout": {
        "hp_pct": 0.005
      }
    },
    "required_level": 15
  },
  "equip_tongchanjiguan": {
    "hp": 200,
    "agility": 12,
    "force": 12,
    "intellect": 5,
    "troop_stat_bonus": {
      "dao": {
        "attack_pct": 0.005
      },
      "qiang": {
        "attack_pct": 0.005
      },
      "jian": {
        "attack_pct": 0.005
      },
      "quan": {
        "attack_pct": 0.005
      },
      "gong": {
        "attack_pct": 0.005
      },
      "scout": {
        "attack_pct": 0.005
      }
    },
    "required_level": 10
  },
  "equip_zhaoyaojing": {
    "hp": 300,
    "force": 22,
    "luck": 15,
    "required_level": 35
  },
  "equip_qiankunquan": {
    "defense": 10,
    "force": 22,
    "agility": 12,
    "required_level": 40
  },
  "equip_tianjiluopan": {
    "intellect": 25,
    "agility": 5,
    "luck": 20,
    "required_level": 40
  },
  "equip_lingwenyubi": {
    "hp": 200,
    "intellect": 25,
    "luck": 15,
    "required_level": 30
  },
  "equip_guanxingdeng": {
    "hp": 100,
    "intellect": 10,
    "luck": 22,
    "required_level": 35
  },
  "equip_jixiemao": {
    "hp": 400,
    "intellect": 10,
    "agility": 10,
    "force": 18,
    "troop_stat_bonus": {
      "gong": {
        "hp_pct": 0.01
      }
    },
    "required_level": 30
  },
  "equip_jiguanchuniao": {
    "hp": 400,
    "force": 15,
    "agility": 16,
    "troop_stat_bonus": {
      "gong": {
        "attack_pct": 0.01
      }
    },
    "required_level": 30
  },
  "equip_kuileimuren": {
    "hp": 150,
    "defense": 5,
    "force": 18,
    "intellect": 18,
    "troop_stat_bonus": {
      "qiang": {
        "hp_pct": 0.01
      }
    },
    "required_level": 40
  },
  "equip_qingji": {
    "agility": 16,
    "luck": 8,
    "troop_stat_bonus": {
      "jian": {
        "attack_pct": 0.01
      }
    },
    "required_level": 25
  },
  "equip_baguajing": {
    "hp": 400,
    "intellect": 30,
    "luck": 24,
    "required_level": 50
  },
  "equip_hunyuansan": {
    "hp": 400,
    "defense": 14,
    "force": 26,
    "luck": 5,
    "required_level": 60
  },
  "equip_zijinhulu": {
    "hp": 225,
    "defense": 18,
    "intellect": 30,
    "required_level": 55
  },
  "equip_tianyinguqin": {
    "hp": 300,
    "intellect": 30,
    "luck": 17,
    "required_level": 45
  },
  "equip_xuanmingdeng": {
    "hp": 256,
    "defense": 13,
    "intellect": 30,
    "luck": 4,
    "required_level": 55
  },
  "equip_jiguanxiong": {
    "hp": 450,
    "defense": 15,
    "force": 24,
    "troop_stat_bonus": {
      "quan": {
        "attack_pct": 0.01
      }
    },
    "required_level": 50
  },
  "equip_muniuliuma": {
    "hp": 300,
    "intellect": 24,
    "agility": 12,
    "luck": 10,
    "troop_stat_bonus": {
      "dao": {
        "hp_pct": 0.005
      },
      "qiang": {
        "hp_pct": 0.005
      },
      "jian": {
        "hp_pct": 0.005
      },
      "quan": {
        "hp_pct": 0.005
      },
      "gong": {
        "hp_pct": 0.005
      },
      "scout": {
        "hp_pct": 0.005
      }
    },
    "required_level": 45
  },
  "equip_kuileiren": {
    "hp": 179,
    "defense": 10,
    "intellect": 24,
    "agility": 12,
    "troop_stat_bonus": {
      "jian": {
        "attack_pct": 0.005,
        "defense_pct": 0.005,
        "hp_pct": 0.005
      }
    },
    "required_level": 45
  },
  "equip_xuanwujigui": {
    "hp": 450,
    "defense": 18,
    "luck": 10,
    "troop_stat_bonus": {
      "qiang": {
        "attack_pct": 0.005,
        "defense_pct": 0.005,
        "hp_pct": 0.005
      }
    },
    "required_level": 60
  },
  "equip_fengshenbang": {
    "hp": 350,
    "defense": 5,
    "intellect": 34,
    "luck": 20,
    "required_level": 70
  },
  "equip_shanheshejitu": {
    "hp": 350,
    "agility": 15,
    "intellect": 34,
    "luck": 10,
    "troop_capacity": 60,
    "required_level": 75
  },
  "equip_zhoutianxingpan": {
    "defense": 20,
    "agility": 10,
    "luck": 25,
    "required_level": 75
  },
  "equip_taiqingyuce": {
    "hp": 400,
    "intellect": 34,
    "luck": 16,
    "required_level": 65
  },
  "equip_feiyuan": {
    "hp": 450,
    "intellect": 26,
    "agility": 12,
    "luck": 8,
    "troop_stat_bonus": {
      "dao": {
        "attack_pct": 0.005
      },
      "qiang": {
        "attack_pct": 0.005
      },
      "jian": {
        "attack_pct": 0.005
      },
      "quan": {
        "attack_pct": 0.005
      },
      "gong": {
        "attack_pct": 0.005
      },
      "scout": {
        "attack_pct": 0.005
      }
    },
    "required_level": 65
  },
  "equip_chixiaojifeng": {
    "hp": 300,
    "force": 26,
    "agility": 12,
    "luck": 8,
    "troop_stat_bonus": {
      "gong": {
        "attack_pct": 0.015
      }
    },
    "required_level": 75
  },
  "equip_mojiajiguanren": {
    "hp": 450,
    "defense": 12,
    "force": 25,
    "intellect": 25,
    "agility": 12,
    "luck": 8,
    "troop_stat_bonus": {
      "dao": {
        "attack_pct": 0.005,
        "defense_pct": 0.005,
        "hp_pct": 0.005
      },
      "qiang": {
        "attack_pct": 0.005,
        "defense_pct": 0.005,
        "hp_pct": 0.005
      },
      "jian": {
        "attack_pct": 0.005,
        "defense_pct": 0.005,
        "hp_pct": 0.005
      },
      "quan": {
        "attack_pct": 0.005,
        "defense_pct": 0.005,
        "hp_pct": 0.005
      },
      "gong": {
        "attack_pct": 0.005,
        "defense_pct": 0.005,
        "hp_pct": 0.005
      },
      "scout": {
        "attack_pct": 0.005,
        "defense_pct": 0.005,
        "hp_pct": 0.005
      }
    },
    "required_level": 80
  },
  "equip_qingtongzhijiaodoushi": {
    "hp": 300,
    "defense": 8,
    "force": 12,
    "agility": 8,
    "troop_stat_bonus": {
      "quan": {
        "attack_pct": 0.005
      }
    },
    "required_level": 20
  },
  "equip_baiyinzhijiaodoushi": {
    "hp": 400,
    "defense": 10,
    "force": 18,
    "agility": 12,
    "troop_stat_bonus": {
      "quan": {
        "attack_pct": 0.01
      }
    },
    "required_level": 40
  },
  "equip_huangjinzhijiaodoushi": {
    "hp": 450,
    "defense": 15,
    "force": 24,
    "agility": 12,
    "troop_stat_bonus": {
      "quan": {
        "attack_pct": 0.01,
        "defense_pct": 0.005
      }
    },
    "required_level": 60
  },
  "equip_zaohongma": {
    "hp": 40,
    "defense": 4,
    "force": 4,
    "agility": 9,
    "required_level": 1
  },
  "equip_huangbiaoma": {
    "hp": 160,
    "defense": 8,
    "agility": 12,
    "required_level": 10
  },
  "equip_dawanma": {
    "hp": 160,
    "defense": 4,
    "agility": 16,
    "required_level": 10
  },
  "equip_qingcongma": {
    "hp": 150,
    "defense": 5,
    "force": 5,
    "agility": 12,
    "required_level": 10
  },
  "equip_xiaomaolv": {
    "hp": 80,
    "defense": 5,
    "agility": 10,
    "luck": 5,
    "required_level": 5
  },
  "equip_xiliangma": {
    "hp": 180,
    "defense": 5,
    "force": 8,
    "agility": 25,
    "required_level": 20
  },
  "equip_mengguma": {
    "hp": 180,
    "defense": 8,
    "force": 5,
    "agility": 20,
    "required_level": 15
  },
  "equip_taxuema": {
    "defense": 5,
    "agility": 26,
    "required_level": 10
  },
  "equip_qingzongma": {
    "hp": 180,
    "defense": 8,
    "agility": 25,
    "required_level": 20
  },
  "equip_chibiao": {
    "hp": 180,
    "defense": 5,
    "force": 8,
    "agility": 20,
    "required_level": 10
  },
  "equip_feiyunzui": {
    "hp": 220,
    "defense": 10,
    "force": 5,
    "agility": 30,
    "required_level": 25
  },
  "equip_zhaoyeyushizi": {
    "hp": 220,
    "defense": 10,
    "force": 10,
    "agility": 30,
    "required_level": 40
  },
  "equip_juanmaochitu": {
    "hp": 220,
    "defense": 10,
    "force": 5,
    "agility": 30,
    "required_level": 30
  },
  "equip_qianliju": {
    "hp": 220,
    "defense": 10,
    "agility": 30,
    "required_level": 25
  },
  "equip_zhuidian": {
    "hp": 220,
    "defense": 10,
    "agility": 30,
    "luck": 10,
    "required_level": 40
  },
  "equip_bailongju": {
    "hp": 220,
    "defense": 10,
    "agility": 25,
    "luck": 12,
    "required_level": 35
  },
  "equip_wuzhuima": {
    "hp": 260,
    "defense": 13,
    "force": 16,
    "agility": 35,
    "required_level": 45
  },
  "equip_jueying": {
    "hp": 260,
    "defense": 13,
    "agility": 35,
    "luck": 15,
    "required_level": 45
  },
  "equip_zhaohuangfeidian": {
    "hp": 260,
    "defense": 13,
    "force": 16,
    "agility": 35,
    "required_level": 50
  },
  "equip_dilu": {
    "hp": 260,
    "defense": 13,
    "agility": 35,
    "luck": 14,
    "required_level": 60
  },
  "equip_qingshuanglu": {
    "hp": 260,
    "defense": 13,
    "agility": 35,
    "luck": 15,
    "required_level": 55
  },
  "equip_xuanjiazhanju": {
    "hp": 260,
    "defense": 13,
    "force": 16,
    "agility": 35,
    "troop_capacity": 30,
    "required_level": 60
  },
  "equip_chituma": {
    "hp": 300,
    "defense": 14,
    "force": 20,
    "agility": 36,
    "troop_capacity": 40,
    "required_level": 80
  },
  "equip_hanxuebaoma": {
    "hp": 300,
    "defense": 14,
    "agility": 36,
    "required_level": 70
  },
  "equip_qilin": {
    "hp": 300,
    "defense": 14,
    "luck": 25,
    "agility": 20,
    "required_level": 65
  },
  "equip_qingluan": {
    "hp": 300,
    "defense": 14,
    "intellect": 20,
    "agility": 36,
    "required_level": 75
  },
  "equip_shendiao": {
    "hp": 300,
    "defense": 14,
    "agility": 36,
    "luck": 14,
    "required_level": 80
  }
}
"""
)

SOUL_RANGES = {
    "green": (800, 1600, 600, 1000, 30),
    "blue": (1500, 2500, 1000, 1600, 40),
    "purple": (2600, 3500, 1600, 2200, 55),
}
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
SLOTS = {
    name: name.removeprefix("equip_")
    for name in (
        "equip_helmet",
        "equip_armor",
        "equip_shoes",
        "equip_weapon",
        "equip_mount",
        "equip_ornament",
        "equip_device",
    )
}


def _soul_payload(payload, rarity):
    """Keep the old roll position; the written level makes reruns safe."""
    old_min, old_max, new_min, new_max, level = SOUL_RANGES[rarity]
    result = dict(payload or {})
    if result.get("required_level") != level:
        hp = int(result.get("hp", old_min))
        position = max(0, min(old_max - old_min, hp - old_min))
        result["hp"] = new_min + (position * (new_max - new_min) + (old_max - old_min) // 2) // (old_max - old_min)
    result["required_level"] = level
    return result


def _set_bonuses(gears):
    sets = {}
    for gear in gears:
        template = gear.template
        if not template.set_key:
            continue
        info = sets.setdefault(template.set_key, {"count": 0, "bonus": template.set_bonus})
        if info["bonus"] != template.set_bonus:
            raise ValueError(f"inconsistent equipment set: {template.set_key}")
        info["count"] += 1
    result = {}
    for info in sets.values():
        definitions = info["bonus"] or {}
        if isinstance(definitions, dict):
            definitions = [definitions]
        for definition in definitions:
            required = int(definition.get("pieces") or info["count"])
            if info["count"] < required:
                continue
            bonus = definition.get("bonus") or definition.get("bonuses") or definition
            for stat, value in bonus.items():
                if stat in SET_FIELDS:
                    result[stat] = result.get(stat, 0) + int(value)
    return result


def _add_stats(guest, stats, fields, sign, updates):
    for stat, field in fields.items():
        amount = int((stats or {}).get(stat, 0))
        if amount:
            setattr(guest, field, getattr(guest, field) + sign * amount)
            updates.add(field)


def _apply_gear(guest, template, sign, updates):
    _add_stats(guest, template.extra_stats, STAT_FIELDS, sign, updates)
    for field in ("attack_bonus", "defense_bonus"):
        amount = int(getattr(template, field))
        if amount:
            setattr(guest, field, getattr(guest, field) + sign * amount)
            updates.add(field)


def _sync_existing_gear(apps, database, items):
    """Historical-model template sync without aliases or eager creation."""
    GearTemplate = apps.get_model("guests", "GearTemplate")
    GearItem = apps.get_model("guests", "GearItem")
    Guest = apps.get_model("guests", "Guest")
    changes = {}
    for template in GearTemplate.objects.using(database).select_for_update().filter(key__in=items).order_by("pk"):
        item = items[template.key]
        payload = item.effect_payload or {}
        defaults = {
            "name": item.name,
            "slot": SLOTS[item.effect_type],
            "rarity": item.rarity,
            "required_level": payload["required_level"],
            "base_key": item.key,
            "base_name": item.name,
            "attack_bonus": 0,
            "defense_bonus": 0,
            "extra_stats": {stat: int(payload[stat]) for stat in STAT_FIELDS if stat in payload},
            "set_key": payload.get("set_key", ""),
            "set_description": payload.get("set_description", ""),
            "set_bonus": payload.get("set_bonus") or {},
        }
        if any(getattr(template, field) != value for field, value in defaults.items()):
            changes[template.pk] = (template, defaults)
    if not changes:
        return

    gears = list(
        GearItem.objects.using(database)
        .select_for_update()
        .filter(
            template_id__in=changes,
            guest_id__isnull=False,
        )
        .select_related("template")
        .order_by("pk")
    )
    guest_ids = {gear.guest_id for gear in gears}
    guests = {
        guest.pk: guest
        for guest in Guest.objects.using(database)
        .select_for_update()
        .filter(
            pk__in=guest_ids,
        )
        .select_related("template")
        .order_by("pk")
    }
    updates = {pk: set() for pk in guests}
    for pk, guest in guests.items():
        _add_stats(guest, guest.gear_set_bonus, SET_FIELDS, -1, updates[pk])
    for gear in gears:
        _apply_gear(guests[gear.guest_id], gear.template, -1, updates[gear.guest_id])

    for template, defaults in changes.values():
        for field, value in defaults.items():
            setattr(template, field, value)
        template.save(using=database, update_fields=list(defaults))
    for gear in gears:
        _apply_gear(
            guests[gear.guest_id],
            changes[gear.template_id][0],
            +1,
            updates[gear.guest_id],
        )
    for pk, guest in guests.items():
        current_bonus = _set_bonuses(GearItem.objects.using(database).filter(guest_id=pk).select_related("template"))
        _add_stats(guest, current_bonus, SET_FIELDS, +1, updates[pk])
        guest.gear_set_bonus = current_bonus
        updates[pk].add("gear_set_bonus")
        max_hp = max(
            200,
            int(guest.template.base_hp) + int(guest.hp_bonus) + int(guest.defense_stat) * 50,
        )
        if guest.current_hp > max_hp:
            guest.current_hp = max_hp
            updates[pk].add("current_hp")
        guest.save(using=database, update_fields=sorted(updates[pk]))


def apply_equipment_balance(apps, schema_editor):
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    Manor = apps.get_model("gameplay", "Manor")
    # Follow the normal Manor -> Guest equipment lock order.
    list(Manor.objects.using(database).select_for_update().order_by("pk").values_list("pk", flat=True))
    items = {}
    for item in (
        ItemTemplate.objects.using(database).select_for_update().filter(key__in=EQUIPMENT_PAYLOADS).order_by("pk")
    ):
        payload = EQUIPMENT_PAYLOADS[item.key]
        if item.effect_payload != payload:
            item.effect_payload = payload
            item.save(using=database, update_fields=["effect_payload"])
        items[item.key] = item
    for item in (
        ItemTemplate.objects.using(database)
        .select_for_update()
        .filter(
            key__startswith="soulorn_",
            effect_type="equip_ornament",
            rarity__in=SOUL_RANGES,
        )
        .order_by("pk")
    ):
        payload = _soul_payload(item.effect_payload, item.rarity)
        if payload != item.effect_payload:
            item.effect_payload = payload
            item.save(using=database, update_fields=["effect_payload"])
        items[item.key] = item
    _sync_existing_gear(apps, database, items)


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0197_arena_ring"),
        ("guests", "0089_equipment_requirements_and_identity"),
    ]
    operations = [migrations.RunPython(apply_equipment_balance, migrations.RunPython.noop)]
