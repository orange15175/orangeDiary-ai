# 迁移矩阵（M0 基线）

> 本文件由 `scripts/migration_matrix.py` 生成，不要手改。
> 重新生成：`python scripts/migration_matrix.py`

- Java 端点总数：**70**
- 已迁到 Python：**70**（100.0%）
- SQL 中出现的表：**25**，entity 标注的表：**24**

## 端点

| 方法 | 路径 | Java 处理函数 | Python 状态 | 位置 |
|---|---|---|---|---|
| GET | `/api/admin/action-logs` | list | 已迁移（M3） | `app/api/admin_logs.py` |
| GET | `/api/admin/action-logs/{id}` | get | 已迁移（M3） | `app/api/admin_logs.py` |
| GET | `/api/admin/ai/traces` | list | 已迁移（M3） | `app/api/admin_ai.py` |
| GET | `/api/admin/ai/traces/{traceId}` | byTraceId | 已迁移（M3） | `app/api/admin_ai.py` |
| GET | `/api/admin/ai/rag-stats` | ragStats | 已迁移（M3） | `app/api/admin_ai.py` |
| PUT | `/api/admin/posts/{id}/hide` | hide | 已迁移（M3） | `app/api/posts.py` |
| PUT | `/api/admin/posts/{id}/restore` | restore | 已迁移（M3） | `app/api/posts.py` |
| PUT | `/api/admin/posts/{id}/comments/{commentId}/remove` | remove | 已迁移（M3） | `app/api/posts.py` |
| GET | `/api/admin/posts` | list | 已迁移（M3） | `app/api/posts.py` |
| GET | `/api/admin/posts/{id}` | get | 已迁移（M3） | `app/api/posts.py` |
| GET | `/api/admin/posts/{id}/comments` | comments | 已迁移（M3） | `app/api/posts.py` |
| POST | `/api/ai/chat` | chat | 已迁移（M4） | `app/api/ai.py` |
| GET | `/api/ai/conversation/messages` | history | 已迁移（M4） | `app/api/ai.py` |
| POST | `/api/ai/chat/stream` | chatStream | 已迁移（M4） | `app/api/ai.py` |
| POST | `/api/auth/register` | register | 已迁移（M1） | `app/api/auth.py` |
| POST | `/api/auth/login` | login | 已迁移（M1） | `app/api/auth.py` |
| POST | `/api/auth/wechat-login` | wechatLogin | 已迁移（M1） | `app/api/auth.py` |
| POST | `/api/auth/refresh` | refresh | 已迁移（M1） | `app/api/auth.py` |
| POST | `/api/auth/logout` | logout | 已迁移（M1） | `app/api/auth.py` |
| POST | `/api/avatars` | upload | 已迁移（M3） | `app/api/avatars.py` |
| GET | `/api/avatars/**` | get | 已迁移（M3） | `app/api/avatars.py` |
| POST | `/api/posts/{postId}/reports` | reportPost | 已迁移（M3） | `app/api/posts.py` |
| POST | `/api/posts/{postId}/comments/{commentId}/reports` | reportComment | 已迁移（M3） | `app/api/posts.py` |
| GET | `/api/admin/community-reports` | list | 已迁移（M3） | `app/api/community_reports.py` |
| PUT | `/api/admin/community-reports/{id}` | resolve | 已迁移（M3） | `app/api/community_reports.py` |
| POST | `/api/check-ins/today` | recordToday | 已迁移（M2） | `app/api/checkin.py` |
| GET | `/api/check-ins` | getFootprint | 已迁移（M2） | `app/api/checkin.py` |
| GET | `/api/daily-summaries` | getSummary | 已迁移（M2） | `app/api/summary.py` |
| POST | `/api/diet-records` | createRecord | 已迁移（M2） | `app/api/diet.py` |
| GET | `/api/diet-records` | listRecords | 已迁移（M2） | `app/api/diet.py` |
| DELETE | `/api/diet-records/{id}` | deleteRecord | 已迁移（M2） | `app/api/diet.py` |
| GET | `/api/reports/diet/today.pdf` | today | 已迁移（M3） | `app/api/reports.py` |
| GET | `/api/exercises` | listExercises | 已迁移（M2） | `app/api/exercise.py` |
| GET | `/api/exercises/categories` | listBrowseCategories | 已迁移（M2） | `app/api/exercise.py` |
| GET | `/api/exercises/browse` | browseExercises | 已迁移（M2） | `app/api/exercise.py` |
| POST | `/api/exercise-records` | createRecord | 已迁移（M2） | `app/api/exercise.py` |
| POST | `/api/exercise-records/preview` | previewRecord | 已迁移（M2） | `app/api/exercise.py` |
| GET | `/api/exercise-records` | listRecords | 已迁移（M2） | `app/api/exercise.py` |
| DELETE | `/api/exercise-records/{id}` | deleteRecord | 已迁移（M2） | `app/api/exercise.py` |
| GET | `/api/foods` | listFoods | 已迁移（M2） | `app/api/foods.py` |
| GET | `/api/foods/categories` | listBrowseCategories | 已迁移（M2） | `app/api/foods.py` |
| GET | `/api/foods/browse` | browseFoods | 已迁移（M2） | `app/api/foods.py` |
| GET | `/api/foods/match` | matchFood | 已迁移（M2） | `app/api/foods.py` |
| GET | `/health` | health | 已迁移（M0） | `app/main.py` |
| POST | `/api/files/images` | upload | 已迁移（M3） | `app/api/images.py` |
| GET | `/api/files/images/**` | get | 已迁移（M3） | `app/api/images.py` |
| GET | `/api/notifications` | list | 已迁移（M2） | `app/api/notifications.py` |
| GET | `/api/notifications/unread-count` | count | 已迁移（M2） | `app/api/notifications.py` |
| PUT | `/api/notifications/{id}/read` | read | 已迁移（M2） | `app/api/notifications.py` |
| POST | `/api/posts` | create | 已迁移（M3） | `app/api/posts.py` |
| GET | `/api/posts` | list | 已迁移（M3） | `app/api/posts.py` |
| GET | `/api/posts/mine` | mine | 已迁移（M3） | `app/api/posts.py` |
| GET | `/api/posts/{id}` | get | 已迁移（M3） | `app/api/posts.py` |
| PUT | `/api/posts/{id}/like` | like | 已迁移（M3） | `app/api/posts.py` |
| DELETE | `/api/posts/{id}/like` | unlike | 已迁移（M3） | `app/api/posts.py` |
| GET | `/api/posts/{id}/comments` | comments | 已迁移（M3） | `app/api/posts.py` |
| POST | `/api/posts/{id}/comments` | createComment | 已迁移（M3） | `app/api/posts.py` |
| GET | `/api/posts/{id}/comments/{commentId}` | comment | 已迁移（M3） | `app/api/posts.py` |
| DELETE | `/api/posts/{id}/comments/{commentId}` | deleteComment | 已迁移（M3） | `app/api/posts.py` |
| DELETE | `/api/posts/{id}` | delete | 已迁移（M3） | `app/api/posts.py` |
| GET | `/api/users/count` | activeUserCount | 已迁移（M2） | `app/api/users.py` |
| GET | `/api/users/me` | currentUser | 已迁移（M2） | `app/api/users.py` |
| GET | `/api/users/validate` | validateCurrentUser | 已迁移（M2） | `app/api/users.py` |
| PUT | `/api/users/profile` | updateProfile | 已迁移（M2） | `app/api/users.py` |
| POST | `/api/users/goals` | createGoal | 已迁移（M2） | `app/api/users.py` |
| POST | `/api/weight-records` | createRecord | 已迁移（M2） | `app/api/weight.py` |
| GET | `/api/weight-records` | listRecords | 已迁移（M2） | `app/api/weight.py` |
| GET | `/api/weight-records/trend` | getTrend | 已迁移（M2） | `app/api/weight.py` |
| GET | `/api/weight-records/progress` | getProgress | 已迁移（M2） | `app/api/weight.py` |
| DELETE | `/api/weight-records/{id}` | deleteRecord | 已迁移（M2） | `app/api/weight.py` |

## 表

| 表名 | 出现在 SQL | 有 entity | M1 是否已建模 |
|---|---|---|---|
| `admin_action_log` | 是 | 是 | 是 |
| `ai_chat_trace` | 是 | 是 | 是 |
| `ai_conversation` | 是 | 是 | 是 |
| `ai_conversation_message` | 是 | 是 | 是 |
| `avatar_upload` | 是 | 是 | 是 |
| `comment_notification` | 是 | — | 否 |
| `community_report` | 是 | 是 | 是 |
| `daily_summary` | 是 | 是 | 是 |
| `diet_record` | 是 | 是 | 是 |
| `exercise_item` | 是 | 是 | 是 |
| `exercise_record` | 是 | 是 | 是 |
| `food_alias` | 是 | 是 | 是 |
| `food_item` | 是 | 是 | 是 |
| `food_serving` | 是 | 是 | 是 |
| `idempotency_record` | 是 | 是 | 是 |
| `notification` | 是 | 是 | 是 |
| `post` | 是 | 是 | 是 |
| `post_comment` | 是 | 是 | 是 |
| `post_image` | 是 | 是 | 是 |
| `post_like` | 是 | 是 | 是 |
| `user` | 是 | 是 | 是 |
| `user_daily_checkin` | 是 | 是 | 是 |
| `user_goal` | 是 | 是 | 是 |
| `user_profile` | 是 | 是 | 是 |
| `weight_record` | 是 | 是 | 是 |

## 说明

- 「已迁移」只代表接口在 Python 侧存在且单测覆盖，**不代表**与 Java 的差分测试通过。
- M2 的完成定义是「Java 停机仍可完成旧业务流程」，届时这张表里不应再有「未开始」。
