import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0163_arena_training_policy_snapshot"),
        ("guests", "0069_recruitmentextraattempt"),
    ]

    operations = [
        migrations.CreateModel(
            name="ArenaReserveTrainingAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("round_ordinal", models.PositiveSmallIntegerField(verbose_name="培养轮次")),
                ("action_ordinal_in_round", models.PositiveSmallIntegerField(verbose_name="轮内动作序号")),
                ("operation_id", models.CharField(max_length=64, verbose_name="动作操作 ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("assigned", "已分配"),
                            ("applied", "已执行"),
                            ("no_action", "无动作"),
                            ("released", "已释放"),
                        ],
                        default="assigned",
                        max_length=16,
                        verbose_name="分配状态",
                    ),
                ),
                ("reason", models.CharField(blank=True, default="", max_length=64, verbose_name="结果原因")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={"verbose_name": "竞技场后备训练分配", "verbose_name_plural": "竞技场后备训练分配"},
        ),
        migrations.CreateModel(
            name="BotMaintenanceCycle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cycle_id", models.CharField(max_length=64, unique=True, verbose_name="周期 ID")),
                ("cycle_ordinal", models.PositiveIntegerField(verbose_name="周期序号")),
                (
                    "trigger",
                    models.CharField(
                        choices=[("scheduled", "定时维护"), ("arena_acceleration", "竞技场加速")],
                        max_length=32,
                        verbose_name="触发来源",
                    ),
                ),
                ("max_actions", models.PositiveSmallIntegerField(verbose_name="动作槽位上限")),
                ("action_ordinal", models.PositiveSmallIntegerField(default=0, verbose_name="已分配动作槽位")),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "进行中"), ("completed", "已完成"), ("recovery_required", "需要恢复")],
                        default="open",
                        max_length=24,
                        verbose_name="周期状态",
                    ),
                ),
                ("covered_action_kinds", models.JSONField(blank=True, default=list, verbose_name="已覆盖动作类别")),
                ("used_business_keys", models.JSONField(blank=True, default=list, verbose_name="已使用动作 identity")),
                (
                    "healing_operation_id",
                    models.CharField(blank=True, default="", max_length=64, verbose_name="治疗前置操作 ID"),
                ),
                (
                    "salary_operation_id",
                    models.CharField(blank=True, default="", max_length=64, verbose_name="工资批次操作 ID"),
                ),
                ("started_at", models.DateTimeField(verbose_name="开始时间")),
                ("completed_at", models.DateTimeField(blank=True, null=True, verbose_name="完成时间")),
                ("last_reason", models.CharField(blank=True, default="", max_length=64, verbose_name="最近原因")),
                ("payload", models.JSONField(blank=True, default=dict, verbose_name="周期审计载荷")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={"verbose_name": "虚拟玩家维护周期", "verbose_name_plural": "虚拟玩家维护周期"},
        ),
        migrations.CreateModel(
            name="BotMaintenanceAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("operation_id", models.CharField(max_length=64, unique=True, verbose_name="操作 ID")),
                ("trigger", models.CharField(max_length=32, verbose_name="触发来源")),
                ("round_ordinal", models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="培养轮次")),
                (
                    "action_ordinal_in_round",
                    models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="轮内动作序号"),
                ),
                ("attempt_ordinal", models.PositiveSmallIntegerField(verbose_name="尝试序号")),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("busy", "忙碌"),
                            ("no_action", "无动作"),
                            ("applied", "已执行"),
                            ("commit_uncertain", "提交未知"),
                            ("programmer_error", "程序错误"),
                        ],
                        max_length=24,
                        verbose_name="尝试结果",
                    ),
                ),
                ("reason", models.CharField(blank=True, default="", max_length=64, verbose_name="尝试原因")),
                ("dispatched", models.BooleanField(default=False, verbose_name="已 dispatch")),
                (
                    "receipt_operation_id",
                    models.CharField(blank=True, default="", max_length=64, verbose_name="回执操作 ID"),
                ),
                ("shadow_cost", models.JSONField(blank=True, default=dict, verbose_name="标准化影子成本")),
                ("started_at", models.DateTimeField(verbose_name="开始时间")),
                ("completed_at", models.DateTimeField(auto_now_add=True, verbose_name="完成时间")),
            ],
            options={"verbose_name": "虚拟玩家维护尝试", "verbose_name_plural": "虚拟玩家维护尝试"},
        ),
        migrations.CreateModel(
            name="BotMaintenanceRecovery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "scope",
                    models.CharField(
                        choices=[
                            ("arena_member", "竞技场成员"),
                            ("arena_demand", "竞技场需求"),
                            ("profile", "虚拟玩家档案"),
                            ("guest", "门客"),
                        ],
                        max_length=24,
                        verbose_name="恢复范围",
                    ),
                ),
                ("entity_key", models.CharField(max_length=128, verbose_name="实体 key")),
                (
                    "status",
                    models.CharField(
                        choices=[("retry", "等待重试"), ("quarantined", "已隔离"), ("requeued", "已重新排队")],
                        default="retry",
                        max_length=16,
                        verbose_name="状态",
                    ),
                ),
                ("failure_code", models.CharField(max_length=64, verbose_name="失败代码")),
                ("failure_digest", models.CharField(max_length=64, verbose_name="失败摘要")),
                ("failure_streak", models.PositiveSmallIntegerField(default=1, verbose_name="连续失败次数")),
                ("first_failed_at", models.DateTimeField(verbose_name="首次失败时间")),
                ("last_failed_at", models.DateTimeField(verbose_name="最近失败时间")),
                (
                    "next_retry_at",
                    models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="下次重试时间"),
                ),
                ("quarantined_at", models.DateTimeField(blank=True, null=True, verbose_name="隔离时间")),
                ("requeued_at", models.DateTimeField(blank=True, null=True, verbose_name="重新排队时间")),
                (
                    "last_operation_id",
                    models.CharField(blank=True, default="", max_length=64, verbose_name="最近操作 ID"),
                ),
                ("payload", models.JSONField(blank=True, default=dict, verbose_name="恢复载荷")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "虚拟玩家维护恢复状态", "verbose_name_plural": "虚拟玩家维护恢复状态"},
        ),
        migrations.CreateModel(
            name="VirtualPlayerGrowthControlSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("control_date", models.DateField(verbose_name="控制日期")),
                ("region", models.CharField(max_length=32, verbose_name="地区")),
                ("prestige_band", models.CharField(max_length=32, verbose_name="声望段")),
                ("policy_version", models.PositiveSmallIntegerField(default=2, verbose_name="Bot 策略版本")),
                ("policy_checksum", models.CharField(max_length=64, verbose_name="Bot 策略校验和")),
                ("sample_count", models.PositiveIntegerField(default=0, verbose_name="样本数")),
                ("strength_p50", models.PositiveBigIntegerField(default=0, verbose_name="综合实力 P50")),
                ("strength_p75", models.PositiveBigIntegerField(default=0, verbose_name="综合实力 P75")),
                ("growth_24h_bps", models.IntegerField(verbose_name="24 小时成长基点")),
                ("growth_7d_bps", models.IntegerField(verbose_name="7 日成长基点")),
                ("component_statistics", models.JSONField(blank=True, default=dict, verbose_name="组件聚合统计")),
                ("effective_until", models.DateTimeField(verbose_name="有效截止时间")),
                ("is_fallback", models.BooleanField(default=False, verbose_name="是否 fallback")),
                ("snapshot_digest", models.CharField(max_length=64, verbose_name="快照摘要")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="写入时间")),
            ],
            options={"verbose_name": "虚拟玩家成长控制快照", "verbose_name_plural": "虚拟玩家成长控制快照"},
        ),
        migrations.AddField(
            model_name="arenavirtualreservemember",
            name="growth_action_ordinal_in_round",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="当前轮动作序号"),
        ),
        migrations.AddField(
            model_name="arenavirtualreservemember",
            name="growth_applied_action_count",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="已成功成长动作数"),
        ),
        migrations.AddField(
            model_name="arenavirtualreservemember",
            name="growth_execution_attempt_count",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="窗口内实际执行次数"),
        ),
        migrations.AddField(
            model_name="arenavirtualreservemember",
            name="growth_round_id",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="当前培养轮 ID"),
        ),
        migrations.AddField(
            model_name="arenavirtualreservemember",
            name="growth_round_training_guest_ids",
            field=models.JSONField(blank=True, default=list, verbose_name="当前轮已分配训练门客"),
        ),
        migrations.AddField(
            model_name="arenavirtualreservemember",
            name="growth_rounds_started",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="已开始培养轮次"),
        ),
        migrations.AddField(
            model_name="arenavirtualreservemember",
            name="growth_slot_attempt_ordinal",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="当前槽位尝试序号"),
        ),
        migrations.AddField(
            model_name="botmaintenanceexecution",
            name="action_ordinal_in_round",
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="轮内动作序号"),
        ),
        migrations.AddField(
            model_name="botmaintenanceexecution",
            name="cycle_id",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="培养周期 ID"),
        ),
        migrations.AddField(
            model_name="botmaintenanceexecution",
            name="round_ordinal",
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="培养轮次"),
        ),
        migrations.AddField(
            model_name="botmaintenanceexecution",
            name="shadow_cost",
            field=models.JSONField(blank=True, default=dict, verbose_name="标准化影子成本"),
        ),
        migrations.AddField(
            model_name="botmaintenanceexecution",
            name="slot_attempt_ordinal",
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="槽位尝试序号"),
        ),
        migrations.AddField(
            model_name="arenareservetrainingassignment",
            name="guest",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="arena_reserve_training_assignments",
                to="guests.guest",
                verbose_name="门客",
            ),
        ),
        migrations.AddField(
            model_name="arenareservetrainingassignment",
            name="member",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="training_assignments",
                to="gameplay.arenavirtualreservemember",
                verbose_name="后备成员",
            ),
        ),
        migrations.AddField(
            model_name="botmaintenanceattempt",
            name="profile",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="maintenance_attempts",
                to="gameplay.botprofile",
            ),
        ),
        migrations.AddField(
            model_name="botmaintenancecycle",
            name="profile",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="maintenance_cycles",
                to="gameplay.botprofile",
                verbose_name="虚拟玩家档案",
            ),
        ),
        migrations.AddField(
            model_name="botmaintenanceattempt",
            name="cycle",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attempts",
                to="gameplay.botmaintenancecycle",
            ),
        ),
        migrations.AddConstraint(
            model_name="botmaintenancerecovery",
            constraint=models.UniqueConstraint(fields=("scope", "entity_key"), name="bot_maint_recovery_scope_entity"),
        ),
        migrations.AddConstraint(
            model_name="botmaintenancerecovery",
            constraint=models.CheckConstraint(
                condition=models.Q(entity_key="", _negated=True), name="bot_maint_recovery_entity_nonempty"
            ),
        ),
        migrations.AddConstraint(
            model_name="botmaintenancerecovery",
            constraint=models.CheckConstraint(
                condition=models.Q(failure_digest="", _negated=True), name="bot_maint_recovery_digest_nonempty"
            ),
        ),
        migrations.AddConstraint(
            model_name="botmaintenancerecovery",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(status="quarantined", quarantined_at__isnull=False) | ~models.Q(status="quarantined")
                ),
                name="bot_maint_recovery_quarantine_marker",
            ),
        ),
        migrations.AddIndex(
            model_name="botmaintenancerecovery",
            index=models.Index(fields=["scope", "status", "next_retry_at"], name="bot_maint_recovery_due_idx"),
        ),
        migrations.AddConstraint(
            model_name="virtualplayergrowthcontrolsnapshot",
            constraint=models.UniqueConstraint(
                fields=("control_date", "region", "prestige_band"), name="bot_growth_control_date_region_band"
            ),
        ),
        migrations.AddConstraint(
            model_name="virtualplayergrowthcontrolsnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(policy_version=2), name="bot_growth_control_policy_v2"
            ),
        ),
        migrations.AddConstraint(
            model_name="virtualplayergrowthcontrolsnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(policy_checksum="", _negated=True), name="bot_growth_control_policy_checksum"
            ),
        ),
        migrations.AddConstraint(
            model_name="virtualplayergrowthcontrolsnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(snapshot_digest="", _negated=True), name="bot_growth_control_digest_nonempty"
            ),
        ),
        migrations.AddConstraint(
            model_name="virtualplayergrowthcontrolsnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(sample_count__gte=0), name="bot_growth_control_sample_nonnegative"
            ),
        ),
        migrations.AddIndex(
            model_name="virtualplayergrowthcontrolsnapshot",
            index=models.Index(
                fields=["region", "prestige_band", "effective_until"], name="bot_growth_control_lookup_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="virtualplayergrowthcontrolsnapshot",
            index=models.Index(fields=["control_date"], name="bot_growth_control_date_idx"),
        ),
        migrations.AddIndex(
            model_name="arenareservetrainingassignment",
            index=models.Index(fields=["member", "round_ordinal", "status"], name="arena_train_assign_st_idx"),
        ),
        migrations.AddConstraint(
            model_name="arenareservetrainingassignment",
            constraint=models.UniqueConstraint(
                fields=("member", "round_ordinal", "guest"), name="arena_training_assignment_member_round_guest"
            ),
        ),
        migrations.AddConstraint(
            model_name="arenareservetrainingassignment",
            constraint=models.UniqueConstraint(
                fields=("member", "round_ordinal", "action_ordinal_in_round"),
                name="arena_training_assignment_member_round_slot",
            ),
        ),
        migrations.AddConstraint(
            model_name="arenareservetrainingassignment",
            constraint=models.CheckConstraint(
                condition=models.Q(round_ordinal__gte=1, round_ordinal__lte=10),
                name="arena_training_assignment_round_1_10",
            ),
        ),
        migrations.AddConstraint(
            model_name="arenareservetrainingassignment",
            constraint=models.CheckConstraint(
                condition=models.Q(action_ordinal_in_round__gte=1, action_ordinal_in_round__lte=8),
                name="arena_training_assignment_slot_1_8",
            ),
        ),
        migrations.AddConstraint(
            model_name="arenareservetrainingassignment",
            constraint=models.CheckConstraint(
                condition=models.Q(operation_id="", _negated=True), name="arena_training_assignment_operation_nonempty"
            ),
        ),
        migrations.AddIndex(
            model_name="botmaintenancecycle",
            index=models.Index(fields=["profile", "status", "started_at"], name="bot_maint_cycle_profile_idx"),
        ),
        migrations.AddConstraint(
            model_name="botmaintenancecycle",
            constraint=models.UniqueConstraint(
                fields=("profile", "cycle_ordinal"), name="bot_maint_cycle_profile_ordinal"
            ),
        ),
        migrations.AddConstraint(
            model_name="botmaintenancecycle",
            constraint=models.CheckConstraint(
                condition=models.Q(max_actions__gte=1, max_actions__lte=16), name="bot_maint_cycle_actions_1_16"
            ),
        ),
        migrations.AddConstraint(
            model_name="botmaintenancecycle",
            constraint=models.CheckConstraint(
                condition=models.Q(action_ordinal__lte=models.F("max_actions")), name="bot_maint_cycle_ordinal_lte_max"
            ),
        ),
        migrations.AddConstraint(
            model_name="botmaintenancecycle",
            constraint=models.CheckConstraint(
                condition=models.Q(cycle_id__isnull=False) & ~models.Q(cycle_id=""), name="bot_maint_cycle_id_nonempty"
            ),
        ),
        migrations.AddIndex(
            model_name="botmaintenanceattempt",
            index=models.Index(fields=["profile", "completed_at"], name="bot_maint_attempt_profile_idx"),
        ),
        migrations.AddIndex(
            model_name="botmaintenanceattempt",
            index=models.Index(
                fields=["cycle", "round_ordinal", "action_ordinal_in_round"], name="bot_maint_attempt_cycle_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="botmaintenanceattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(attempt_ordinal__gte=1, attempt_ordinal__lte=5), name="bot_maint_attempt_1_5"
            ),
        ),
        migrations.AddConstraint(
            model_name="botmaintenanceattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(operation_id="", _negated=True), name="bot_maint_attempt_operation_nonempty"
            ),
        ),
        migrations.AddConstraint(
            model_name="botmaintenanceexecution",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("action_ordinal_in_round__isnull", True),
                        ("round_ordinal__isnull", True),
                        ("slot_attempt_ordinal__isnull", True),
                    ),
                    models.Q(
                        ("round_ordinal__gte", 1),
                        ("round_ordinal__lte", 10),
                        ("action_ordinal_in_round__gte", 1),
                        ("action_ordinal_in_round__lte", 16),
                        ("slot_attempt_ordinal__gte", 1),
                        ("slot_attempt_ordinal__lte", 5),
                    ),
                    _connector="OR",
                ),
                name="bot_maint_exec_slot_fields_together",
            ),
        ),
    ]
