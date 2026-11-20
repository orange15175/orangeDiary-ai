"""0004：每日汇总、体重、运动项目/记录、幂等记录。

这五张表是"记饮食"这条链路的必要依赖：`diet_record` 写完要重算 `daily_summary`，
而汇总要读 `exercise_record` 和 `weight_record`；写操作幂等要 `idempotency_record`。
缺任何一张，`POST /api/diet-records` 都跑不通，所以一起建。

DDL 取自 calorie_tracker.sql / idempotency_migration.sql，仅去掉 ROW_FORMAT
（不同 MySQL 版本默认值不同，写死会造成假差异）。
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `exercise_item` (
          `id` bigint NOT NULL AUTO_INCREMENT COMMENT '运动项目ID',
          `name` varchar(100) NOT NULL COMMENT '运动名称',
          `category` varchar(50) NULL DEFAULT NULL COMMENT '运动分类',
          `calorie_per_unit` decimal(7,2) NULL DEFAULT NULL COMMENT '每单位消耗热量',
          `unit_type` varchar(20) NULL DEFAULT NULL COMMENT '单位类型：minute/km/time',
          `met_value` decimal(5,2) NULL DEFAULT NULL COMMENT 'MET代谢当量值',
          `status` varchar(20) NOT NULL DEFAULT 'active' COMMENT '状态：active/inactive',
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
          `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
          PRIMARY KEY (`id`) USING BTREE,
          KEY `idx_exercise_item_name` (`name`),
          KEY `idx_exercise_item_category` (`category`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='运动基础库表'
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `exercise_record` (
          `id` bigint NOT NULL AUTO_INCREMENT COMMENT '运动记录ID',
          `user_id` bigint NOT NULL COMMENT '用户ID',
          `exercise_item_id` bigint NOT NULL COMMENT '运动项目ID',
          `exercise_name_snapshot` varchar(100) NOT NULL COMMENT '运动名称快照',
          `duration_min` int NULL DEFAULT NULL COMMENT '运动时长(分钟)',
          `distance_km` decimal(6,2) NULL DEFAULT NULL COMMENT '运动距离(km)',
          `calorie_burned` decimal(7,2) NOT NULL COMMENT '本次消耗热量(kcal)',
          `record_time` datetime NOT NULL COMMENT '实际运动时间',
          `record_date` date NOT NULL COMMENT '记录归属日期',
          `remark` varchar(255) NULL DEFAULT NULL COMMENT '备注',
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
          `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
          PRIMARY KEY (`id`) USING BTREE,
          KEY `idx_exercise_record_user_id` (`user_id`),
          KEY `idx_exercise_record_exercise_item_id` (`exercise_item_id`),
          KEY `idx_exercise_record_record_date` (`record_date`),
          KEY `idx_exercise_record_user_date` (`user_id`, `record_date`),
          CONSTRAINT `fk_exercise_record_exercise_item_id` FOREIGN KEY (`exercise_item_id`) REFERENCES `exercise_item` (`id`),
          CONSTRAINT `fk_exercise_record_user_id` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='运动记录表'
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `weight_record` (
          `id` bigint NOT NULL AUTO_INCREMENT COMMENT '体重记录ID',
          `user_id` bigint NOT NULL COMMENT '用户ID',
          `weight_kg` decimal(5,2) NOT NULL COMMENT '体重(kg)',
          `body_fat_rate` decimal(5,2) NULL DEFAULT NULL COMMENT '体脂率(%)',
          `bmi` decimal(5,2) NULL DEFAULT NULL COMMENT 'BMI',
          `record_date` date NOT NULL COMMENT '记录日期',
          `record_time` datetime NOT NULL COMMENT '记录时间',
          `remark` varchar(255) NULL DEFAULT NULL COMMENT '备注',
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
          `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
          PRIMARY KEY (`id`) USING BTREE,
          KEY `idx_weight_record_user_id` (`user_id`),
          KEY `idx_weight_record_record_date` (`record_date`),
          KEY `idx_weight_record_user_date` (`user_id`, `record_date`),
          CONSTRAINT `fk_weight_record_user_id` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='体重记录表'
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `daily_summary` (
          `id` bigint NOT NULL AUTO_INCREMENT COMMENT '每日汇总ID',
          `user_id` bigint NOT NULL COMMENT '用户ID',
          `summary_date` date NOT NULL COMMENT '汇总日期',
          `total_intake_calorie` decimal(8,2) NOT NULL DEFAULT 0.00 COMMENT '当天总摄入热量(kcal)',
          `total_burn_calorie` decimal(8,2) NOT NULL DEFAULT 0.00 COMMENT '当天总消耗热量(kcal)',
          `net_calorie` decimal(8,2) NOT NULL DEFAULT 0.00 COMMENT '净热量=摄入-消耗(kcal)',
          `diet_count` int NOT NULL DEFAULT 0 COMMENT '当天饮食记录数',
          `exercise_count` int NOT NULL DEFAULT 0 COMMENT '当天运动记录数',
          `weight_kg` decimal(5,2) NULL DEFAULT NULL COMMENT '当天体重快照(kg)',
          `goal_intake_target` decimal(7,2) NULL DEFAULT NULL COMMENT '当天摄入目标(kcal)',
          `goal_burn_target` decimal(7,2) NULL DEFAULT NULL COMMENT '当天消耗目标(kcal)',
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
          `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
          PRIMARY KEY (`id`) USING BTREE,
          UNIQUE KEY `uk_daily_summary_user_date` (`user_id`, `summary_date`),
          KEY `idx_daily_summary_summary_date` (`summary_date`),
          CONSTRAINT `fk_daily_summary_user_id` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='每日汇总表'
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `idempotency_record` (
          `id` bigint NOT NULL AUTO_INCREMENT,
          `user_id` bigint NOT NULL,
          `operation` varchar(32) NOT NULL,
          `request_id` varchar(64) NOT NULL,
          `request_payload_hash` char(64) NOT NULL,
          `status` varchar(20) NOT NULL,
          `response_json` text NULL,
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          `expires_at` datetime NOT NULL,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uk_idempotency_user_operation_request` (`user_id`, `operation`, `request_id`),
          KEY `idx_idempotency_expires_at` (`expires_at`),
          CONSTRAINT `fk_idempotency_record_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='关键写操作幂等结果'
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS `idempotency_record`")
    op.execute("DROP TABLE IF EXISTS `daily_summary`")
    op.execute("DROP TABLE IF EXISTS `weight_record`")
    op.execute("DROP TABLE IF EXISTS `exercise_record`")
    op.execute("DROP TABLE IF EXISTS `exercise_item`")
