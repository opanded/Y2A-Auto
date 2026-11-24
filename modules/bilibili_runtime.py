#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import os
from typing import Optional

logger = logging.getLogger("bilibili_runtime")

_INITIALIZED = False
_LAST_ERROR: Optional[str] = None


def configure_bilibili_runtime() -> bool:
    """Configure the internal Bilibili SDK network runtime once per process."""
    global _INITIALIZED, _LAST_ERROR
    if _INITIALIZED:
        return True

    try:
        from .bili_sdk import request_settings

        impersonate = os.environ.get("BILIBILI_IMPERSONATE", "chrome131").strip()
        if impersonate:
            request_settings.set("impersonate", impersonate)

        # The SDK ships with a 30s default applied to every request, including
        # each multipart upload chunk. Slow uplinks routinely exceed that on
        # larger files, which surfaces as "curl: (28) Operation timed out".
        raw_timeout = os.environ.get("BILIBILI_UPLOAD_TIMEOUT", "300").strip()
        try:
            timeout = float(raw_timeout)
        except ValueError:
            timeout = 300.0
        if timeout > 0:
            request_settings.set("timeout", timeout)
            logger.info("bilibili-api 请求超时设置为 %.0f 秒", timeout)

        _INITIALIZED = True
        _LAST_ERROR = None
        return True
    except Exception as exc:
        _LAST_ERROR = str(exc)
        logger.warning("配置 bilibili-api 网络运行时失败: %s", exc)
        return False


def get_bilibili_runtime_error() -> Optional[str]:
    return _LAST_ERROR
