"""0003：食物库与饮食记录四张表。

`food_item` / `diet_record` 来自 calorie_tracker.sql，`food_alias` / `food_serving`
来自 food_natural_language_migration.sql。这里按依赖顺序重建（先 food_item，
再引用它的表），全部 `IF NOT EXISTS`，对存量库只做版本记账。

不复制源文件的示例数据（西红柿/黄瓜别名与份量）：那是演示数据，
用 migration 插入会让"新建一个干净的分析库"和"生产库"数据不一致。
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `food_item` (
          `id` bigint NOT NULL AUTO_INCREMENT COMMENT '食物ID',
          `name` varchar(100) NOT NULL COMMENT '食物名称',
          `category` varchar(50) NULL DEFAULT NULL COMMENT '食物分类',
          `unit` varchar(20) NOT NULL DEFAULT 'g' COMMENT '默认单位：g/ml/serving',
          `calorie_per_100g` decimal(7,2) NOT NULL COMMENT '每100g热量(kcal)',
          `protein_per_100g` decimal(7,2) NULL DEFAULT NULL COMMENT '每100g蛋白质(g)',
          `fat_per_100g` decimal(7,2) NULL DEFAULT NULL COMMENT '每100g脂肪(g)',
          `carb_per_100g` decimal(7,2) NULL DEFAULT NULL COMMENT '每100g碳水(g)',
          `brand` varchar(100) NULL DEFAULT NULL COMMENT '品牌',
          `source_type` varchar(20) NOT NULL DEFAULT 'system' COMMENT '来源类型：system/user',
          `created_by` bigint NULL DEFAULT NULL COMMENT '创建人用户ID，系统内置可为空',
          `status` varchar(20) NOT NULL DEFAULT 'active' COMMENT '状态：active/inactive',
          `cover_image_url` varchar(500) NULL DEFAULT NULL COMMENT '封面图片URL',
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
          `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
          PRIMARY KEY (`id`) USING BTREE,
          KEY `idx_food_item_name` (`name`),
          KEY `idx_food_item_category` (`category`),
          KEY `idx_food_item_created_by` (`created_by`),
          KEY `idx_food_item_source_status` (`source_type`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='食物基础库表'
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `food_alias` (
          `id` bigint NOT NULL AUTO_INCREMENT COMMENT '别名ID',
          `food_item_id` bigint NOT NULL COMMENT '食物ID',
          `alias_name` varchar(100) NOT NULL COMMENT '用户口语化别名',
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uk_food_alias_name` (`alias_name`),
          KEY `idx_food_alias_food_item_id` (`food_item_id`),
          CONSTRAINT `fk_food_alias_food_item_id` FOREIGN KEY (`food_item_id`) REFERENCES `food_item` (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='食物别名表'
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `food_serving` (
          `id` bigint NOT NULL AUTO_INCREMENT COMMENT '标准份量ID',
          `food_item_id` bigint NOT NULL COMMENT '食物ID',
          `serving_name` varchar(50) NOT NULL COMMENT '份量名称，如根、碗、片',
          `gram_weight` decimal(7,2) NOT NULL COMMENT '一个标准份量对应克数',
          `sort_order` int NOT NULL DEFAULT 0,
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uk_food_serving_name` (`food_item_id`, `serving_name`),
          KEY `idx_food_serving_food_item_id` (`food_item_id`),
          CONSTRAINT `fk_food_serving_food_item_id` FOREIGN KEY (`food_item_id`) REFERENCES `food_item` (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='食物标准份量表'
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS `diet_record` (
          `id` bigint NOT NULL AUTO_INCREMENT COMMENT '饮食记录ID',
          `user_id` bigint NOT NULL COMMENT '用户ID',
          `food_item_id` bigint NOT NULL COMMENT '食物ID',
          `meal_type` varchar(20) NOT NULL COMMENT '餐别：breakfast/lunch/dinner/snack',
          `food_name_snapshot` varchar(100) NOT NULL COMMENT '食物名称快照',
          `quantity_g` decimal(7,2) NOT NULL COMMENT '食用重量(g)',
          `calorie_snapshot` decimal(7,2) NOT NULL COMMENT '本次摄入热量(kcal)',
          `protein_snapshot` decimal(7,2) NULL DEFAULT NULL COMMENT '本次蛋白质摄入(g)',
          `fat_snapshot` decimal(7,2) NULL DEFAULT NULL COMMENT '本次脂肪摄入(g)',
          `carb_snapshot` decimal(7,2) NULL DEFAULT NULL COMMENT '本次碳水摄入(g)',
          `record_time` datetime NOT NULL COMMENT '实际进食时间',
          `record_date` date NOT NULL COMMENT '记录归属日期',
          `remark` varchar(255) NULL DEFAULT NULL COMMENT '备注',
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
          `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
          PRIMARY KEY (`id`) USING BTREE,
          KEY `idx_diet_record_user_id` (`user_id`),
          KEY `idx_diet_record_food_item_id` (`food_item_id`),
          KEY `idx_diet_record_record_date` (`record_date`),
          KEY `idx_diet_record_user_date` (`user_id`, `record_date`),
          CONSTRAINT `fk_diet_record_food_item_id` FOREIGN KEY (`food_item_id`) REFERENCES `food_item` (`id`),
          CONSTRAINT `fk_diet_record_user_id` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='饮食记录表'
        """
    )


def downgrade() -> None:
    # 只删本次新建的表；存量库里的数据不能因为降级被带走。
    op.execute("DROP TABLE IF EXISTS `diet_record`")
    op.execute("DROP TABLE IF EXISTS `food_serving`")
    op.execute("DROP TABLE IF EXISTS `food_alias`")
    op.execute("DROP TABLE IF EXISTS `food_item`")
