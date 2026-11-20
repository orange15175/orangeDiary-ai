"""0001 存量基线：只描述 M1 涉及的 user 域三张表。

为什么手写而不是 autogenerate：
- 生产/开发库里已经有 24 张表和真实数据，autogenerate 会试图把"库里多出来的表"
  当成漂移写成 DROP，风险不可接受；
- 建表语句要保留 `utf8mb4`、`ENGINE=InnoDB`、索引名这些 MySQL 细节，ORM 生成的
  DDL 与存量结构不会逐字一致，后续对比 `SHOW CREATE TABLE` 会出现假差异。

对已有库的正确用法是 `alembic stamp 0001`，让版本表直接记到基线，不执行建表。
空库才执行 `alembic upgrade head`；建表语句因此写成 `IF NOT EXISTS`，重复执行安全。

覆盖范围：M1 只建 `user` / `user_profile` / `user_goal`。社区、通知、文件、AI 会话
等表在 M2 的 revision 里补，不能因为"反正库里已经有"就跳过版本记录。
"""
from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `user` (
          `id` bigint NOT NULL AUTO_INCREMENT COMMENT '用户ID',
          `mobile` varchar(20) NULL DEFAULT NULL COMMENT '手机号',
          `email` varchar(255) NULL DEFAULT NULL COMMENT '登录邮箱',
          `wechat_openid` varchar(64) NULL DEFAULT NULL COMMENT '微信小程序用户唯一标识',
          `nickname` varchar(50) NULL DEFAULT NULL COMMENT '昵称',
          `avatar` varchar(255) NULL DEFAULT NULL COMMENT '头像URL',
          `password_hash` varchar(255) NULL DEFAULT NULL COMMENT '加密后的密码',
          `status` varchar(20) NOT NULL DEFAULT 'active' COMMENT '账号状态：active/disabled',
          `role` varchar(20) NOT NULL DEFAULT 'user' COMMENT '角色：user/admin',
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
          `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
          PRIMARY KEY (`id`) USING BTREE,
          UNIQUE KEY `uk_user_mobile` (`mobile`),
          UNIQUE KEY `uk_user_email` (`email`),
          UNIQUE KEY `uk_user_wechat_openid` (`wechat_openid`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户表'
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `user_profile` (
          `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
          `user_id` bigint NOT NULL COMMENT '用户ID',
          `gender` varchar(10) NULL DEFAULT NULL COMMENT '性别：male/female',
          `birthday` date NULL DEFAULT NULL COMMENT '出生日期',
          `height_cm` decimal(5,2) NULL DEFAULT NULL COMMENT '身高(cm)',
          `current_weight_kg` decimal(5,2) NULL DEFAULT NULL COMMENT '当前体重(kg)',
          `target_weight_kg` decimal(5,2) NULL DEFAULT NULL COMMENT '目标体重(kg)',
          `activity_level` varchar(20) NULL DEFAULT NULL COMMENT '活动水平：low/medium/high',
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
          `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
          PRIMARY KEY (`id`) USING BTREE,
          UNIQUE KEY `uk_user_profile_user_id` (`user_id`),
          CONSTRAINT `fk_user_profile_user_id` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户身体信息表'
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `user_goal` (
          `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
          `user_id` bigint NOT NULL COMMENT '用户ID',
          `goal_type` varchar(20) NOT NULL COMMENT '目标类型：lose/maintain/gain',
          `daily_intake_target` decimal(7,2) NULL DEFAULT NULL COMMENT '每日摄入目标(kcal)',
          `daily_burn_target` decimal(7,2) NULL DEFAULT NULL COMMENT '每日消耗目标(kcal)',
          `start_date` date NOT NULL COMMENT '目标开始日期',
          `end_date` date NULL DEFAULT NULL COMMENT '目标结束日期',
          `status` varchar(20) NOT NULL DEFAULT 'active' COMMENT '状态：active/finished/cancelled',
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
          `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
          PRIMARY KEY (`id`) USING BTREE,
          KEY `idx_user_goal_user_id` (`user_id`),
          CONSTRAINT `fk_user_goal_user_id` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户目标表'
        """
    )


def downgrade() -> None:
    # 基线不回滚：DROP 掉 user 会连带真实数据。要回退请用备份恢复，不要执行 downgrade。
    raise RuntimeError("0001 是存量基线，不支持 downgrade；回退请走备份恢复")
