# 其余门客头像统一处理（2026-10-17）

沿用用户确认的脚本处理方式，对 `data/images/guests` 中上一批未处理的全部图片进行等比缩放、位置对齐和背景补齐。

## 结果

- 本次处理 575 张：人物专属图片 474 张，根目录通用头像与关卡形象 101 张。
- 加上上一批 117 张，目录内全部 692 张图片均为 448×448。
- 原文件名、相对路径和人物造型保留；PNG/JPEG 文件格式保留，41 张不透明 RGBA 图片仍保留 RGBA 模式。
- 上一批 117 张的 SHA-256 与处理前一致，未重复缩放。
- 使用 `文037_男.png` 的人物轮廓高度 365 px、水平中心 x=227、脚底 y=415 为基准。
- 横向轮廓宽度上限约 392 px，宽姿态和道具等比收纳，保留四周边距。
- 5 张带装饰边框的小头像按完整卡面构图等比处理，装饰框保留。
- 21 张原图的深色轮廓接近画布边界，本次保留原图已有内容并补齐外围背景，不补画原图已缺失的部分。
- 本次没有修改门客配置或同步线上资源。

## 验证

- 在暂存目录完成全部 575 张的生成、读取、轮廓尺寸和位置检查后，才替换原图。
- 人物轮廓脚底与参考位置误差不超过 2 px；中心误差不超过 2 px；四周深色轮廓边距至少 19 px。
- 575 张原图备份、575 张输出、上一批 117 张分别通过 SHA-256 核对。
- `originals.zip` 的完整性校验通过。
- 逐页查看全部 20 页处理后预览，未见本次变换造成的轮廓截断或明显背景衔接异常。
- 再次扫描全部 692 张，均可正常读取且为 448×448。
- 门客配置中现有紫色、橙色头像引用无缺失。

## 备份和记录

- `originals.zip`：本次 575 张原图，压缩包内为原项目相对路径。
- `manifest.json`：逐图索引、前后尺寸、轮廓边界、等比缩放参数、位移和 SHA-256。
- `normalize.py`：实际处理脚本，已有完成记录时拒绝重复处理。
- 上一批原图仍保存在 `../guest_portrait_normalization_20261017/originals.zip`。

## 前后预览

预览标注逐图索引、人物名称和轮廓高度。

- 专属人物第 1 页：[处理前](named_before_01.jpg) / [处理后](named_after_01.jpg)
- 专属人物第 2 页：[处理前](named_before_02.jpg) / [处理后](named_after_02.jpg)
- 专属人物第 3 页：[处理前](named_before_03.jpg) / [处理后](named_after_03.jpg)
- 专属人物第 4 页：[处理前](named_before_04.jpg) / [处理后](named_after_04.jpg)
- 专属人物第 5 页：[处理前](named_before_05.jpg) / [处理后](named_after_05.jpg)
- 专属人物第 6 页：[处理前](named_before_06.jpg) / [处理后](named_after_06.jpg)
- 专属人物第 7 页：[处理前](named_before_07.jpg) / [处理后](named_after_07.jpg)
- 专属人物第 8 页：[处理前](named_before_08.jpg) / [处理后](named_after_08.jpg)
- 专属人物第 9 页：[处理前](named_before_09.jpg) / [处理后](named_after_09.jpg)
- 专属人物第 10 页：[处理前](named_before_10.jpg) / [处理后](named_after_10.jpg)
- 专属人物第 11 页：[处理前](named_before_11.jpg) / [处理后](named_after_11.jpg)
- 专属人物第 12 页：[处理前](named_before_12.jpg) / [处理后](named_after_12.jpg)
- 专属人物第 13 页：[处理前](named_before_13.jpg) / [处理后](named_after_13.jpg)
- 专属人物第 14 页：[处理前](named_before_14.jpg) / [处理后](named_after_14.jpg)
- 专属人物第 15 页：[处理前](named_before_15.jpg) / [处理后](named_after_15.jpg)
- 专属人物第 16 页：[处理前](named_before_16.jpg) / [处理后](named_after_16.jpg)
- 通用头像及关卡形象第 1 页：[处理前](root_before_01.jpg) / [处理后](root_after_01.jpg)
- 通用头像及关卡形象第 2 页：[处理前](root_before_02.jpg) / [处理后](root_after_02.jpg)
- 通用头像及关卡形象第 3 页：[处理前](root_before_03.jpg) / [处理后](root_after_03.jpg)
- 通用头像及关卡形象第 4 页：[处理前](root_before_04.jpg) / [处理后](root_after_04.jpg)
