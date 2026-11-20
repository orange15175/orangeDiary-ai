-- 集成测试账号需要在每次运行时创建 `orange_diary_test_<runId>_<worker>` 这类临时 schema。
-- MYSQL_USER 默认只有 bootstrap 库的权限，这里补到全库范围。
-- 安全前提：本容器数据在 tmpfs、端口不对外暴露业务数据、用完 down -v，
-- 并且 fixture 会校验 APP_ENV=test 与库名前缀，二者缺一拒绝建库与清理。
GRANT ALL PRIVILEGES ON *.* TO 'orange_test'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
