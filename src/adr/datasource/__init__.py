"""数据源层（mootdx 在线行情封装）。

导出 ``TdxClient``（行情客户端，含 4 道数据可用性断言）、``DataUnavailableError``、
``Cache``（原始行情本地缓存，保证幂等重跑不重复打网）。
"""

from src.adr.datasource.cache import Cache
from src.adr.datasource.tdx import DataUnavailableError, TdxClient

__all__ = ["TdxClient", "DataUnavailableError", "Cache"]
