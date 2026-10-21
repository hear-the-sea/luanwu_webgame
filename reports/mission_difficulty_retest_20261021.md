# 天蓬第二层与姓氏传说难度重测

按最新工作区配置，用11组基准阵容、每组200个新种子（420000～420199），合计4,400场正式引擎战斗。

开发游戏数据库尚是80级旧参数；本次评级对应文件中的最新100级参数。只单独同步简体名称，不更新运行中的战斗参数。

| 任务 | 原分数 | 新分数 | 达标梯度 | 达标梯度胜率 |
| --- | ---: | ---: | ---: | ---: |
| 姓氏传说之“李” | 16 | 24 | 8 | 100.0% |
| 天蓬元帅的地窖（第二层） | 15 | 26 | 8 | 100.0% |

完整配置快照和每个梯度的胜率：[JSON证据](mission_difficulty_retest_20261021.json)。

复现：

    python scripts/analysis/rate_mission_difficulty.py --mission tianpeng_dijiao_2 --mission surname_legend_li --seeds 200 --seed-start 420000 --output reports/mission_difficulty_retest_20261021.json
