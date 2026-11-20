"""0008：R3 智能饮食闭环核心表。

按设计文档 §13.2 建表，覆盖五个阶段：
- M3：diet_preference / recipe / recipe_ingredient / recipe_step / recipe_nutrition
- M4：meal_plan / meal_plan_version / plan_meal / known_ingredient /
       ingredient_ledger / ingredient_reservation / ingredient_check_session / item
- M5：shopping_list / shopping_item / shopping_item_source / price_observation
- M6：meal_observation / observation_record_link
- M7：review_report / user_feedback

全部 `CREATE TABLE IF NOT EXISTS`，与存量库共存；`down_revision = 0007`。
`price_observation` 只落结构与 Provider 接口，未配置价格源时不写伪造报价。
"""
from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_TABLES = [
    # ---------------- M3 ----------------
    """
    CREATE TABLE IF NOT EXISTS `diet_preference` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `user_id` bigint NOT NULL,
      `allergens` text NULL,
      `dislikes` text NULL,
      `cuisine_preferences` text NULL,
      `equipment` text NULL,
      `prep_time_limit` int NULL,
      `budget_mode` varchar(16) NOT NULL DEFAULT 'soft',
      `budget_value` decimal(10,2) NULL,
      `city` varchar(64) NULL,
      `ingredient_mode` varchar(24) NOT NULL DEFAULT 'prefer_available',
      `version` int NOT NULL DEFAULT 1,
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      UNIQUE KEY `uk_diet_preference_user` (`user_id`),
      CONSTRAINT `fk_diet_preference_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `recipe` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `name` varchar(120) NOT NULL,
      `owner_id` bigint NULL,
      `scope` varchar(16) NOT NULL DEFAULT 'system',
      `source_type` varchar(16) NOT NULL DEFAULT 'sample',
      `source_url` varchar(512) NULL,
      `license` varchar(120) NULL,
      `servings` int NOT NULL DEFAULT 1,
      `prep_minutes` int NULL,
      `cook_minutes` int NULL,
      `equipment` varchar(255) NULL,
      `category` varchar(32) NULL,
      `status` varchar(16) NOT NULL DEFAULT 'active',
      `version` int NOT NULL DEFAULT 1,
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_recipe_scope_status` (`scope`, `status`),
      KEY `idx_recipe_name` (`name`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `recipe_ingredient` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `recipe_id` bigint NOT NULL,
      `food_item_id` bigint NULL,
      `original_name` varchar(120) NOT NULL,
      `quantity` decimal(10,2) NULL,
      `unit` varchar(16) NULL,
      `gram_equivalent` decimal(10,2) NULL,
      `basis` varchar(16) NOT NULL DEFAULT 'raw',
      `optional` tinyint(1) NOT NULL DEFAULT 0,
      `allergen_status` varchar(16) NULL,
      `sort_order` int NOT NULL DEFAULT 0,
      PRIMARY KEY (`id`),
      KEY `idx_recipe_ingredient_recipe` (`recipe_id`),
      CONSTRAINT `fk_recipe_ingredient_recipe` FOREIGN KEY (`recipe_id`) REFERENCES `recipe` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `recipe_step` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `recipe_id` bigint NOT NULL,
      `step_index` int NOT NULL,
      `description` text NOT NULL,
      `duration_hint` varchar(64) NULL,
      `equipment` varchar(120) NULL,
      PRIMARY KEY (`id`),
      UNIQUE KEY `uk_recipe_step_index` (`recipe_id`, `step_index`),
      CONSTRAINT `fk_recipe_step_recipe` FOREIGN KEY (`recipe_id`) REFERENCES `recipe` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `recipe_nutrition` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `recipe_id` bigint NOT NULL,
      `recipe_version` int NOT NULL DEFAULT 1,
      `nutrients` json NULL,
      `coverage` decimal(5,4) NULL,
      `calculation_version` varchar(32) NOT NULL DEFAULT 'v1',
      `input_hash` varchar(64) NULL,
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      UNIQUE KEY `uk_recipe_nutrition_version` (`recipe_id`, `recipe_version`),
      CONSTRAINT `fk_recipe_nutrition_recipe` FOREIGN KEY (`recipe_id`) REFERENCES `recipe` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    # ---------------- M4 ----------------
    """
    CREATE TABLE IF NOT EXISTS `known_ingredient` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `user_id` bigint NOT NULL,
      `food_item_id` bigint NULL,
      `label` varchar(120) NOT NULL,
      `presence` varchar(16) NOT NULL DEFAULT 'unknown',
      `quantity_state` varchar(16) NOT NULL DEFAULT 'unknown',
      `quantity` decimal(10,2) NULL,
      `unit` varchar(16) NULL,
      `food_state` varchar(16) NULL,
      `storage_location` varchar(64) NULL,
      `last_confirmed_at` datetime NULL,
      `expiry_date` date NULL,
      `opened_at` datetime NULL,
      `source` varchar(24) NOT NULL DEFAULT 'manual',
      `version` int NOT NULL DEFAULT 1,
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_known_ingredient_user` (`user_id`),
      CONSTRAINT `fk_known_ingredient_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `ingredient_ledger` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `user_id` bigint NOT NULL,
      `ingredient_id` bigint NOT NULL,
      `event_type` varchar(24) NOT NULL,
      `delta` decimal(10,2) NULL,
      `before_qty` decimal(10,2) NULL,
      `after_qty` decimal(10,2) NULL,
      `unit` varchar(16) NULL,
      `cause_type` varchar(24) NULL,
      `cause_id` varchar(64) NULL,
      `idempotency_key` varchar(80) NOT NULL,
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      UNIQUE KEY `uk_ingredient_ledger_idem` (`idempotency_key`),
      KEY `idx_ingredient_ledger_ingredient` (`ingredient_id`),
      CONSTRAINT `fk_ingredient_ledger_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `meal_plan` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `user_id` bigint NOT NULL,
      `start_date` date NOT NULL,
      `end_date` date NOT NULL,
      `active_version_id` bigint NULL,
      `status` varchar(16) NOT NULL DEFAULT 'draft',
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_meal_plan_user_dates` (`user_id`, `start_date`, `end_date`),
      CONSTRAINT `fk_meal_plan_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `meal_plan_version` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `plan_id` bigint NOT NULL,
      `version_number` int NOT NULL,
      `parent_version_id` bigint NULL,
      `input_snapshot` json NULL,
      `constraints` json NULL,
      `checks` json NULL,
      `nutrition_coverage` decimal(5,4) NULL,
      `price_coverage` decimal(5,4) NULL,
      `reason` varchar(255) NULL,
      `status` varchar(16) NOT NULL DEFAULT 'draft',
      `adopted_at` datetime NULL,
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      UNIQUE KEY `uk_meal_plan_version` (`plan_id`, `version_number`),
      CONSTRAINT `fk_meal_plan_version_plan` FOREIGN KEY (`plan_id`) REFERENCES `meal_plan` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `plan_meal` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `plan_version_id` bigint NOT NULL,
      `meal_date` date NOT NULL,
      `meal_type` varchar(16) NOT NULL,
      `recipe_id` bigint NULL,
      `recipe_version` int NULL,
      `servings` int NOT NULL DEFAULT 1,
      `nutrition_snapshot` json NULL,
      `cost_range` json NULL,
      `execution_state` varchar(16) NOT NULL DEFAULT 'planned',
      `locked` tinyint(1) NOT NULL DEFAULT 0,
      PRIMARY KEY (`id`),
      KEY `idx_plan_meal_version_date` (`plan_version_id`, `meal_date`),
      CONSTRAINT `fk_plan_meal_version` FOREIGN KEY (`plan_version_id`) REFERENCES `meal_plan_version` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `ingredient_reservation` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `user_id` bigint NOT NULL,
      `ingredient_id` bigint NOT NULL,
      `plan_meal_id` bigint NOT NULL,
      `quantity` decimal(10,2) NOT NULL,
      `unit` varchar(16) NOT NULL,
      `status` varchar(16) NOT NULL DEFAULT 'active',
      `expires_at` datetime NULL,
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      UNIQUE KEY `uk_reservation_meal_ingredient` (`plan_meal_id`, `ingredient_id`),
      KEY `idx_reservation_ingredient` (`ingredient_id`),
      CONSTRAINT `fk_reservation_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `ingredient_check_session` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `user_id` bigint NOT NULL,
      `plan_version_ref` varchar(64) NULL,
      `status` varchar(24) NOT NULL DEFAULT 'open',
      `version` int NOT NULL DEFAULT 1,
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_check_session_user` (`user_id`),
      CONSTRAINT `fk_check_session_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `ingredient_check_item` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `session_id` bigint NOT NULL,
      `food_item_id` bigint NULL,
      `label` varchar(120) NOT NULL,
      `needed_quantity` decimal(10,2) NULL,
      `unit` varchar(16) NULL,
      `existing_assessment` varchar(24) NULL,
      `answer` varchar(24) NULL,
      `confirmed_quantity` decimal(10,2) NULL,
      `status` varchar(24) NOT NULL DEFAULT 'needs_check',
      `version` int NOT NULL DEFAULT 1,
      PRIMARY KEY (`id`),
      KEY `idx_check_item_session` (`session_id`),
      CONSTRAINT `fk_check_item_session` FOREIGN KEY (`session_id`) REFERENCES `ingredient_check_session` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    # ---------------- M5 ----------------
    """
    CREATE TABLE IF NOT EXISTS `shopping_list` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `user_id` bigint NOT NULL,
      `date_start` date NOT NULL,
      `date_end` date NOT NULL,
      `plan_version_refs` json NULL,
      `status` varchar(16) NOT NULL DEFAULT 'draft',
      `totals` json NULL,
      `coverage` decimal(5,4) NULL,
      `version` int NOT NULL DEFAULT 1,
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_shopping_list_user` (`user_id`),
      CONSTRAINT `fk_shopping_list_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `shopping_item` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `list_id` bigint NOT NULL,
      `food_item_id` bigint NULL,
      `label` varchar(120) NOT NULL,
      `spec` varchar(120) NULL,
      `need_quantity` decimal(10,2) NULL,
      `available_quantity` decimal(10,2) NULL,
      `shortage_quantity` decimal(10,2) NULL,
      `quantity_status` varchar(16) NOT NULL DEFAULT 'unknown',
      `buy_quantity` decimal(10,2) NULL,
      `package_quantity` decimal(10,2) NULL,
      `unit` varchar(16) NULL,
      `state` varchar(16) NOT NULL DEFAULT 'needs_check',
      `price_snapshot` json NULL,
      `ledger_id` bigint NULL,
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_shopping_item_list` (`list_id`),
      CONSTRAINT `fk_shopping_item_list` FOREIGN KEY (`list_id`) REFERENCES `shopping_list` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `shopping_item_source` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `item_id` bigint NOT NULL,
      `plan_meal_id` bigint NULL,
      `recipe_ingredient_id` bigint NULL,
      `required_quantity` decimal(10,2) NULL,
      `unit` varchar(16) NULL,
      PRIMARY KEY (`id`),
      KEY `idx_shopping_source_item` (`item_id`),
      CONSTRAINT `fk_shopping_source_item` FOREIGN KEY (`item_id`) REFERENCES `shopping_item` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `price_observation` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `food_item_id` bigint NULL,
      `label` varchar(120) NOT NULL,
      `spec` varchar(120) NULL,
      `region` varchar(64) NOT NULL,
      `channel` varchar(64) NULL,
      `unit_price_low` decimal(10,2) NULL,
      `unit_price_high` decimal(10,2) NULL,
      `currency` varchar(8) NOT NULL DEFAULT 'CNY',
      `price_type` varchar(16) NOT NULL DEFAULT 'regular',
      `observed_at` datetime NULL,
      `queried_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      `source_url` varchar(512) NULL,
      `evidence_text` text NULL,
      `conditions` varchar(255) NULL,
      PRIMARY KEY (`id`),
      KEY `idx_price_food_region` (`food_item_id`, `region`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    # ---------------- M6 ----------------
    """
    CREATE TABLE IF NOT EXISTS `meal_observation` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `user_id` bigint NOT NULL,
      `occurred_at` datetime NOT NULL,
      `description` text NOT NULL,
      `source` varchar(24) NOT NULL DEFAULT 'manual',
      `estimated_nutrition` json NULL,
      `completeness` varchar(16) NOT NULL DEFAULT 'partial',
      `confidence_kind` varchar(16) NULL,
      `status` varchar(24) NOT NULL DEFAULT 'draft',
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_meal_observation_user` (`user_id`, `occurred_at`),
      CONSTRAINT `fk_meal_observation_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `observation_record_link` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `observation_id` bigint NOT NULL,
      `diet_record_id` bigint NOT NULL,
      PRIMARY KEY (`id`),
      UNIQUE KEY `uk_observation_link` (`observation_id`, `diet_record_id`),
      KEY `idx_observation_link_record` (`diet_record_id`),
      CONSTRAINT `fk_observation_link_obs` FOREIGN KEY (`observation_id`) REFERENCES `meal_observation` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    # ---------------- M7 ----------------
    """
    CREATE TABLE IF NOT EXISTS `review_report` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `user_id` bigint NOT NULL,
      `period_start` date NOT NULL,
      `period_end` date NOT NULL,
      `input_hash` varchar(64) NULL,
      `metrics` json NULL,
      `data_gaps` json NULL,
      `suggestions` json NULL,
      `draft_plan_ref` varchar(64) NULL,
      `status` varchar(16) NOT NULL DEFAULT 'draft',
      `model_version` varchar(64) NULL,
      `prompt_version` varchar(64) NULL,
      `rule_version` varchar(64) NULL,
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_review_user_period` (`user_id`, `period_start`),
      CONSTRAINT `fk_review_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `user_feedback` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `user_id` bigint NOT NULL,
      `plan_meal_id` bigint NULL,
      `recipe_id` bigint NULL,
      `rating` varchar(8) NOT NULL,
      `hunger` varchar(16) NULL,
      `satiety` varchar(16) NULL,
      `difficulty` varchar(16) NULL,
      `reason` varchar(255) NULL,
      `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_user_feedback_user` (`user_id`),
      CONSTRAINT `fk_user_feedback_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
        ON DELETE CASCADE ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def upgrade() -> None:
    for statement in _TABLES:
        op.execute(statement)


def downgrade() -> None:
    for table in reversed(_table_names()):
        op.execute(f"DROP TABLE IF EXISTS `{table}`")


def _table_names() -> list[str]:
    import re

    names = []
    for statement in _TABLES:
        match = re.search(r"CREATE TABLE IF NOT EXISTS `([a-z_]+)`", statement)
        if match:
            names.append(match.group(1))
    return names
