"""LangChain LCEL 客户端封装：通用调用层，不绑定具体业务。

大纲生成、内容生成都用这套封装，不直接调 LangChain/OpenAI SDK。
"""

import json 
from typing import TypeVar 
from langchain_core.language_models.chat_models import BaseChatModel 
from langchain_core.prompts import ChatPromptTemplate 
from langchain_openai import ChatOpenAI 
from pydantic import BaseModel, ValidationError 
from app.core.config import Settings, get_settings 
from app.llm.errors import InvalidModelOutputError, LLMNotConfiguredError
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage # ← 新增

# TypeVar 让 complete() 的入参和返回值类型绑定到同一个 schema 子类 
# 调用方传 OutlineDraft，返回的就是 OutlineDraft，而不是泛泛的 BaseModel
"""
    重点讲一下TypeVar T
    T = TypeVar("T", bound=BaseModel) ：定义一个类型变量 T，必须是 BaseModel 的子类
"""        
T = TypeVar("T",bound=BaseModel)

# LCEL 管道前置件：标准 system + human 两段式 prompt 
# # - system：人设/约束/规则（"你是资深HR..."） 
# # - human：具体任务（"给张三生成5段简历大纲..."）
_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system","{system}"),
        ("human","{user}")
    ]
)
#小工具
def _strip_json_fences(text: str) -> str:
    """去掉 markdown 代码块包裹，只留 JSON 正文。

    LLM 不用 with_structured_output 时，可能把 JSON 包在 ```json ... ``` 里，
    这层壳得剥掉才能 json.loads。
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # 去掉第一行（```json 或 ```）
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]  # 去掉结尾的 ```
        text = "\n".join(lines).strip()
    return text

def create_chat_model(settings: Settings | None = None) -> ChatOpenAI:
    """创建 ChatOpenAI 实例：用 OpenAI 兼容协议对接 DeepSeek。

    DeepSeek 的 API 兼容 OpenAI 协议，所以用 langchain_openai 的 ChatOpenAI
    就能调用——只需把 base_url 指向 DeepSeek。
    """

    cfg = settings or get_settings()
    kwargs:dict = {
        "model":cfg.llm_model,
        "api_key":cfg.llm_api_key or "not-configured",
        "base_url": cfg.llm_base_url,
        "timeout":cfg.llm_timeout_seconds,
        "max_retries":2
    }
    # 思考模式默认关闭；关闭时不要传 thinking 参数，避免无谓地拉长延迟
    if cfg.llm_thinking_enabled:
        kwargs[ "extra_body" ] = { "thinking" : { "type" : "enabled" }}
    return ChatOpenAI(**kwargs)


class StructuredChatClient:
    """prompt | model.with_structured_output(schema) 的薄封装。

    封装了三件事：
    1. API Key 校验：没配置就抛 LLMNotConfiguredError，业务层能精确处理
    2. LCEL 管道拼接：prompt | structured_model，业务层只管传 prompt 文本和 schema
    3. 返回值归一化：不管 LLM 返回 dict 还是 BaseModel，统一转成 schema 实例
    """
    def __init__(self,*,model:BaseChatModel,api_key:str)->None:
        self._model = model
        self._api_key = api_key

    async def complete(
        self,
        schema:type[T],#这里用上了，调用时传进来是什么schema，T就是什么，返回也是同一个
        *,
        system:str,
        user:str,
        purpose:str,
    )->T:
        """按 schema 调 LLM，返回符合 schema 的对象。

        参数：
            schema: Pydantic 模型类，LLM 必须按这个结构返回
            system: system prompt 文本（人设/约束）
            user: user prompt 文本（具体任务）
            purpose: 业务用途描述，写进错误信息方便排查（如"生成大纲"）
        """
        # 1. API Key 校验
        if not self._api_key.strip():
            raise LLMNotConfiguredError(f"未配置 LLM API Key，无法 {purpose} ")
        # 2. LCEL 管道：prompt | structured_model 
        # #    with_structured_output 把 LLM 输出按 schema 解析
        #`with_structured_output` 有两种模式：1.json_mode,输出就是json模式，2.function_calling：没懂
        chain = _PROMPT | self._model.with_structured_output(schema,method="json_mode")

        # 3. 调用并捕获异常
        try:
            result = await chain.ainvoke({"system":system,"user":user})
        except Exception as error:
            raise InvalidModelOutputError("模型返回内容不符合约定结构") from error

        # 4. 返回值归一化：不同 LangChain 版本可能返回 dict 或 BaseModel
        if isinstance(result,schema):
            return result
        if isinstance(result,BaseModel):
            try:
                #model_validate作用：用数据，例如这里的result，按照schema逐字段校验，合格的转换成pydantic对象，不合格的抛异常
                return schema.model_validate(result.model_dump())
            except ValidationError as error:
                raise InvalidModelOutputError( "模型返回内容不符合约定结构" ) from error
        if isinstance(result,dict):
            try : 
                return schema.model_validate(result) 
            except ValidationError as error: 
                raise InvalidModelOutputError( "模型返回内容不符合约定结构" ) from error
        raise InvalidModelOutputError( "模型返回内容不符合约定结构" )

    async def complete_with_tools(
        self,
        schema:type[T],
        *,
        system:str,
        user:str,
        purpose:str,
        tools:list
    ) ->T:
        """带工具调用的 LLM 调用：LLM 可以自主决定要不要调工具。

        和 complete() 的区别：
        - complete()：with_structured_output，LLM 只能直接吐 JSON
        - complete_with_tools()：bind_tools，LLM 可以先调工具，再吐 JSON

        流程（工具调用循环）：
        1. bind_tools → LLM "看到" 工具箱
        2. 调 LLM → 看它返回有没有 tool_calls
        3. 有 tool_calls → 执行每个工具 → 把结果塞回消息列表 → 回到第 2 步
        4. 没有 tool_calls → 解析最终 JSON → 校验 schema → 返回
        """
        # 1. API Key 校验（和 complete() 一样）
        if not self._api_key.strip():
            raise LLMNotConfiguredError(f"未配置 LLM API Key，无法 {purpose} ")

        # 2. 给模型绑工具 + 建工具名到函数的映射表
        tool_map = {t.name:t for t in tools}
        model_with_tools = self._model.bind_tools(tools)
        # 3. 构建初始消息（system + human 两段，和 _PROMPT 一样）
        messages = [
            SystemMessage(content=system),
            HumanMessage(content=user)
        ]


        #4.工具调用循环 -- 最多跑5轮，防止LLM无限调工具卡死
        MAX_TOOL_ROUNDS = 5
        response = None
        for _round in range(MAX_TOOL_ROUNDS):
            response = await model_with_tools.ainvoke(messages)
            messages.append(response)
            # LLM 没要调工具 → 它觉得信息够了，准备输出最终结果
            if not response.tool_calls:
                break
             # LLM 要调工具 → 逐个执行，把结果变成 ToolMessage 喂回去   
            for tc in response.tool_calls:
                tool_func = tool_map[tc["name"]]
                result = await tool_func.ainvoke(tc["args"])
                messages.append(ToolMessage(
                    content = str(result),
                    tool_call_id = tc["id"]
                ))
        # 循环回去：带着工具结果再调一次 LLM

        # 5. 安全检查：如果跑完 5 轮 LLM 还在调工具，说明卡住了
        if response is None or response.tool_calls:
            raise InvalidModelOutputError(
                f"工具调用循环超过 {MAX_TOOL_ROUNDS} 轮，模型未输出最终结果"
            )

        # 6. 解析最终 JSON + Pydantic 校验（维持硬约束：必须 schema 校验） 
        try :
            parsed = json.loads(_strip_json_fences(response.content)) 
        except (json.JSONDecodeError, TypeError) as error: raise InvalidModelOutputError( "模型最终输出不是有效 JSON" ) from error 
        try : 
            return schema.model_validate(parsed) 
        except ValidationError as error: raise InvalidModelOutputError( "模型返回内容不符合约定结构" ) from error






