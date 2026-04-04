from __future__ import annotations


def build_blocked_target_body(*, target_name: str, reason: str) -> str:
    return f"目标 {target_name} 当前无法交战（{reason}），您的部队已自动遣返。"
