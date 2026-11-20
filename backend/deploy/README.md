# 集成测试依赖

真实 MySQL 8 + Redis，只给 `tests/integration` 用。数据目录是 tmpfs，容器停掉即消失。

```bash
docker compose -f deploy/compose.test.yml up -d --wait
export APP_ENV=test
export TEST_DATABASE_URL='mysql+asyncmy://orange_test:orange_test@127.0.0.1:3307/orange_diary_test_local'
export TEST_REDIS_URL='redis://127.0.0.1:6380/0'
python -m pytest tests -m integration -q
docker compose -f deploy/compose.test.yml down -v
```

## 几条硬规则

- **端口固定 3307 / 6380**，不占用开发环境的 3306 / 6379，避免测试把业务库当测试库。
- **库名必须是 `orange_diary_test_` 前缀**，fixture 同时校验 `APP_ENV=test`；两者不一致就拒绝建库和清理。
- **不要用 `DATABASE_URL` 兜底**：`TEST_DATABASE_URL` 缺失时集成测试应直接 skip 并报“集成验收未完成”，不能静默连到业务库。
- 事务与并发用例要用独立 Session 并显式 commit，不要用包住整个用例的外层 rollback —— 那会把真实并发行为掩盖掉。
- 需要数据库的用例放在 `tests/integration/` 并标 `@pytest.mark.integration`；`live` 标记的用例调真实付费模型，不进普通 CI。

改端口时同步改 CI 的环境变量，两处只有这一份真源。
