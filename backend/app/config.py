from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / '.env', extra='ignore')
    data_dir: Path = BACKEND_DIR / 'data'
    model_provider: Literal['openai_compatible'] = 'openai_compatible'
    model_api_key: str = 'local'
    model_base_url: str = 'http://127.0.0.1:11434/v1'
    model_name: str = 'qwen3:8b'
    allow_cloud_model: bool = False
    max_steps: int = Field(default=8, ge=1, le=30)
    max_tokens: int = Field(default=20000, ge=512)
    model_output_tokens: int = Field(default=1500, ge=128)
    model_timeout_seconds: float = Field(default=180, gt=0)
    run_timeout_seconds: float = Field(default=120, gt=0)
    tool_timeout_seconds: float = Field(default=15, gt=0)
    approval_ttl_seconds: int = Field(default=86400, gt=0)
    # 运行时治理：每次 run 的工具调用硬上限（成本护栏，跨审批续跑累计）
    max_tool_calls_per_run: int = Field(default=20, ge=1, le=500)
    # 模型调用失败重试次数（指数退避：backoff * 2^attempt）
    model_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.5, gt=0)
    # 工具执行失败自动重试次数（指数退避：backoff * 2^attempt；0=禁用）。
    # 只重试瞬时错误（超时/显式 retryable），参数校验与确定性业务失败不重试；
    # 重试复用同一 tool call id → 幂等键不变 → 写工具查重兜底，不会重复建卡。
    tool_retries: int = Field(default=2, ge=0, le=5)
    # 上下文卫生：工具结果回喂模型前的治理（防上下文爆炸）
    context_max_chars: int = Field(default=2000, ge=100, le=20000,
                                   description='单条工具结果文本字符上限，超长截断并提示分页。')
    context_max_lines: int = Field(default=40, ge=5, le=200,
                                   description='单条工具结果文本行数上限，超行保留前 N 行。')
    tool_result_cache_ttl: int = Field(default=300, ge=0, le=86400,
                                       description='查询工具结果缓存秒数；0=禁用缓存。')
    nlu_threshold: float = Field(default=0.82, ge=0, le=1)
    nlu_margin: float = Field(default=0.18, ge=0, le=1)
    nlu_min_coverage: float = Field(default=0.90, ge=0, le=1)
    routing_timeout_seconds: float = Field(default=20, gt=0)
    parameter_retries: int = Field(default=1, ge=0, le=3)
    classification_threshold: float = Field(default=0.85, ge=0, le=1)
    classification_max_batches: int = Field(default=5, ge=1, le=50)
    sensitive_terms: list[str] = ['密码', '验证码', '密钥', '转账', '付款', '删除', '清空', '绕过', '免确认']
    # 允许访问后端的前端来源（CORS）。部署到非默认端口或域名时必须配置，
    # 环境变量用 JSON 数组：CORS_ORIGINS=["http://example.com:5173"]
    cors_origins: list[str] = ['http://127.0.0.1:5173', 'http://localhost:5173']

    # ----- Langfuse 可观测性（可选）-----
    # 三个变量都留空时追踪整体关闭，对主流程零影响、零网络请求。
    # 自托管时 host 必须写 127.0.0.1 而不是 localhost：
    # 本机 localhost 优先解析到 IPv6 ::1，而 ::1:3000 被 dllhost.exe 占着，会连不上。
    langfuse_public_key: str = ''
    langfuse_secret_key: str = ''
    langfuse_host: str = 'http://127.0.0.1:3000'

    @model_validator(mode='after')
    def privacy_boundary(self):
        if self.model_provider == 'openai_compatible':
            endpoint = urlparse(self.model_base_url)
            if endpoint.scheme not in {'http', 'https'} or not endpoint.hostname:
                raise ValueError('MODEL_BASE_URL 必须是 http(s) URL')
            if endpoint.hostname not in {'127.0.0.1', 'localhost', '::1'} and not self.allow_cloud_model:
                raise ValueError('云模型会收到对话和工具数据；需显式设置 ALLOW_CLOUD_MODEL=true')
        return self


settings = Settings()
