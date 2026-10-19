"""结构化工具错误：与前端 envelope.error 契约对齐。

code 为机器可读错误码，stage 区分校验/业务/审批/执行阶段，
retryable 标记是否允许模型重试——这是「如何做结构化错误治理」的载体。
"""
from uuid import uuid4


class ToolError(Exception):
    def __init__(self, code, message, details=None, *, stage='business', retryable=False):
        self.code = code
        self.message = message
        self.details = details
        self.stage = stage
        self.retryable = retryable
        super().__init__(message)

    def envelope(self, *, tool, invocation_id=None):
        return {
            'ok': False,
            'tool': tool,
            'invocation_id': invocation_id or uuid4().hex,
            'attempts': 1,
            'data': None,
            'error': {
                'code': self.code,
                'message': self.message,
                'stage': self.stage,
                'retryable': self.retryable,
                'details': self.details,
            },
        }
