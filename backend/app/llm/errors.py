"""LLM 调用相关的自定义异常。"""

class LLMNotConfiguredError(RuntimeError):
    """API Key 没配置时抛出。"""

class InvalidModelOutputError(RuntimeError):
    """模型返回内容不符合约定结构时抛出（通用）。"""

class InvalidOutlineOutputError(InvalidModelOutputError):
    """大纲生成返回内容不符合业务规则时抛出（专用于大纲）。"""



class InvalidContentOutputError(InvalidModelOutputError):
    """内容生成返回内容不符合业务规则时抛出（专用于单段内容）。"""

    