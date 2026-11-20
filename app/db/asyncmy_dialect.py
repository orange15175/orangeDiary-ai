"""asyncmy 方言的 ping 参数修正。

SQLAlchemy 2.0.44 的 `MySQLDialect_pymysql.do_ping` 靠探测**环境里已安装的 pymysql**
签名来决定要不要给 `ping()` 传参数：

    try:
        Connection = __import__("pymysql.connections").connections.Connection
    except ImportError:
        return True                     # 没装 pymysql → 传 False
    # 装了则读取 pymysql.Connection.ping 的形参再判断

asyncmy 的方言（`MySQLDialect_asyncmy`）直接继承这套逻辑，但 asyncmy 适配器的
`AsyncAdapt_asyncmy_connection.ping(reconnect: bool)` **没有默认值**。于是只要环境里
存在 pymysql 1.1+（其 `ping` 已移除 `reconnect` 形参，探测结果为“不用传参”），
`pool_pre_ping` 每次从池里取连接都会抛：

    TypeError: AsyncAdapt_asyncmy_connection.ping() missing 1 required
               positional argument: 'reconnect'

把标志固定为 True 后，`do_ping` 走 `dbapi_connection.ping(False)`，与 asyncmy 适配器
签名一致。只作用于本进程的 asyncmy 方言，不改三方包源码；即便将来 pymysql 被卸载，
`ping(False)` 对 asyncmy 也依旧是正确的调用。
"""
from sqlalchemy.dialects.mysql.asyncmy import MySQLDialect_asyncmy


class AsyncmyDialect(MySQLDialect_asyncmy):
    # SQLAlchemy 只认“在类 __dict__ 里直接声明”的这个标志，继承来的不算；
    # 缺了它会关掉语句编译缓存并每次启动打 SAWarning（每轮请求都要重新编译 SQL）。
    supports_statement_cache = True

    @property
    def _send_false_to_ping(self) -> bool:  # type: ignore[override]
        # 父类是 memoized_property（探测 pymysql 签名），这里用普通 property 覆盖，
        # 保证无论环境里有没有 pymysql，都按 asyncmy 的要求传参。
        return True
