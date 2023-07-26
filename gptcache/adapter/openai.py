import base64
import json
import os
import time
from io import BytesIO
from typing import Iterator, Any, List

from gptcache import cache
from gptcache.adapter.adapter import adapt
from gptcache.adapter.base import BaseCacheLLM
from gptcache.manager.scalar_data.base import Answer, DataType
from gptcache.utils import import_openai, import_pillow
from gptcache.utils.error import wrap_error
from gptcache.utils.response import (
    get_stream_message_from_openai_answer,
    get_message_from_openai_answer,
    get_text_from_openai_answer,
    get_image_from_openai_b64,
    get_image_from_openai_url,
    get_audio_text_from_openai_answer,
)
from gptcache.utils.token import token_counter

import_openai()

# pylint: disable=C0413
# pylint: disable=E1102
import openai


class ChatCompletion(openai.ChatCompletion, BaseCacheLLM):
    """Openai ChatCompletion Wrapper

    ChatCompletion函数通过利用模型的语言生成能力来实现对话的连贯性。当接收到用户的输入时，
    函数将该输入提供给模型，并从模型中生成一个响应。这个响应可以是一个单独的文本字符串，也可
    以是一个包含多个文本字符串的列表，以提供更多的候选答案。

    Example:
        .. code-block:: python

            from gptcache import cache
            from gptcache.processor.pre import get_prompt
            # init gptcache
            cache.init()
            cache.set_openai_key()

            from gptcache.adapter import openai
            # run ChatCompletion model with gptcache
            response = openai.ChatCompletion.create(
                          model='gpt-3.5-turbo',
                          messages=[
                            {
                                'role': 'user',
                                'content': "what's github"
                            }],
                        )
            response_content = response['choices'][0]['message']['content']
    """


    @classmethod
    def _llm_handler(cls, *llm_args, **llm_kwargs):
        #llm_args里啥也没有  llm_kwargs是一个字典{'model':'gpt-3.5-turbo','messages':[{}]}
        #将用户输入转化为模型可以处理的格式，并将模型生成的响应转化为可读的文本
        """
        输入处理：该函数接收用户输入的对话内容，对其进行预处理和格式化。这可能包括对文本进行分词、去除停用词、转换为词向量等操作，以便模型能够理解和处理输入。
        上下文管理：对话系统通常需要维护上下文信息，以便在生成响应时考虑先前的对话历史。_llm_handler函数负责管理和维护这些上下文信息，并将其与当前的用户输入整合。
        生成响应：通过调用底层的语言模型，_llm_handler函数将处理后的用户输入提供给模型，并接收模型生成的响应。它可能还会对生成的响应进行一些后处理操作，如解码、去除特殊标记等，以便产生可读的文本。
        输出格式化：最后，_llm_handler函数将模型生成的响应转化为适当的输出格式，以便将其展示给用户。这可能包括对响应进行整理、分段、添加标点符号等操作，以生成清晰、连贯的回复。
        """

        try:
            return super().create(*llm_args, **llm_kwargs) if cls.llm is None else cls.llm(*llm_args, **llm_kwargs)
        except openai.OpenAIError as e:
            raise wrap_error(e) from e

    @staticmethod
    def _update_cache_callback(
        llm_data, update_cache_func, *args, **kwargs
    ):  # pylint: disable=unused-argument
        if not isinstance(llm_data, Iterator):  #这段代码使用了 isinstance() 函数来检查 llm_data 是否不是 Iterator 类型的实例。
            update_cache_func(
                Answer(get_message_from_openai_answer(llm_data), DataType.STR)
            )
            return llm_data
        else:

            def hook_openai_data(it):
                total_answer = ""
                for item in it:
                    total_answer += get_stream_message_from_openai_answer(item)
                    yield item
                update_cache_func(Answer(total_answer, DataType.STR))

            return hook_openai_data(llm_data)

    @classmethod
    def create(cls, *args, **kwargs):
        # print(type(kwargs))
        # for key, value in kwargs.items():
        #     print(key, value)

        chat_cache = kwargs.get("cache_obj", cache) #从kwargs寻找cache_obj的信息，没有则赋值cache
        #print(type(chat_cache)) #是位于gptcache\core.py的实例
        enable_token_counter = chat_cache.config.enable_token_counter
        '''enable_token_counter 是一个用于控制是否启用令牌计数器的标志或配置选项。
        
        令牌计数器（token counter）用于统计文本数据中各个令牌（token）的出现次数。在自然语言处理任务中，令牌可以是单词、字符、子词等文本的最小单位。通过统计令牌的出现次数，可以获得文本数据的一些基本统计信息，如词频、字符频率等。
        
        具体来说，当 enable_token_counter 设置为真（非零、非空、非 False 等）时，表示启用了令牌计数器功能。这意味着在处理文本数据时，会对输入的文本进行令牌化，并统计每个令牌的出现次数。
        
        启用令牌计数器可以用于多种用途，例如：
        
        词频统计：通过计数每个单词的出现次数，可以得到单词在文本数据中的词频信息，用于分析单词的重要性或频率分布。
        特征提取：根据令牌的出现次数，可以构建文本特征向量，用于训练机器学习模型或进行文本分类、聚类等任务。
        语言模型训练：通过统计令牌的出现频率，可以用于训练语言模型，预测下一个可能的令牌。
        需要注意的是，具体的使用方式和功能实现取决于代码的上下文和设计。enable_token_counter 可能是一个配置选项，用于控制是否进行令牌计数和相应的处理逻辑。'''
        def cache_data_convert(cache_data):
            if enable_token_counter:
                input_token = _num_tokens_from_messages(kwargs.get("messages"))
                output_token = token_counter(cache_data)
                saved_token = [input_token, output_token]
            else:
                saved_token = [0, 0]
            if kwargs.get("stream", False):
                return _construct_stream_resp_from_cache(cache_data, saved_token)
            return _construct_resp_from_cache(cache_data, saved_token)  #返回一个字典

        kwargs = cls.fill_base_args(**kwargs)   #将基本参数填充到缓存参数中
        return adapt(
            cls._llm_handler,
            cache_data_convert,
            cls._update_cache_callback,
            *args,
            **kwargs,
        )


class Completion(openai.Completion, BaseCacheLLM):
    """Openai Completion Wrapper

    Example:
        .. code-block:: python

            from gptcache import cache
            from gptcache.processor.pre import get_prompt
            # init gptcache
            cache.init()
            cache.set_openai_key()

            from gptcache.adapter import openai
            # run Completion model with gptcache
            response = openai.Completion.create(model="text-davinci-003",
                                                prompt="Hello world.")
            response_text = response["choices"][0]["text"]
    """

    @classmethod
    def _llm_handler(cls, *llm_args, **llm_kwargs):
        try:
            return super().create(*llm_args, **llm_kwargs) if not cls.llm else cls.llm(*llm_args, **llm_kwargs)
        except openai.OpenAIError as e:
            raise wrap_error(e) from e

    @staticmethod
    def _cache_data_convert(cache_data):
        return _construct_text_from_cache(cache_data)

    @staticmethod
    def _update_cache_callback(
        llm_data, update_cache_func, *args, **kwargs
    ):  # pylint: disable=unused-argument
        update_cache_func(Answer(get_text_from_openai_answer(llm_data), DataType.STR))
        return llm_data

    @classmethod
    def create(cls, *args, **kwargs):
        kwargs = cls.fill_base_args(**kwargs)
        return adapt(
            cls._llm_handler,
            cls._cache_data_convert,
            cls._update_cache_callback,
            *args,
            **kwargs,
        )


class Audio(openai.Audio):
    """Openai Audio Wrapper

    Example:
        .. code-block:: python

            from gptcache import cache
            from gptcache.processor.pre import get_file_bytes
            # init gptcache
            cache.init(pre_embedding_func=get_file_bytes)
            cache.set_openai_key()

            from gptcache.adapter import openai
            # run audio transcribe model with gptcache
            audio_file= open("/path/to/audio.mp3", "rb")
            transcript = openai.Audio.transcribe("whisper-1", audio_file)

            # run audio transcribe model with gptcache
            audio_file= open("/path/to/audio.mp3", "rb")
            transcript = openai.Audio.translate("whisper-1", audio_file)
    """

    @classmethod
    def transcribe(cls, model: str, file: Any, *args, **kwargs):
        def llm_handler(*llm_args, **llm_kwargs):
            try:
                return super(Audio, cls).transcribe(*llm_args, **llm_kwargs)
            except openai.OpenAIError as e:
                raise wrap_error(e) from e

        def cache_data_convert(cache_data):
            return _construct_audio_text_from_cache(cache_data)

        def update_cache_callback(
            llm_data, update_cache_func, *args, **kwargs
        ):  # pylint: disable=unused-argument
            update_cache_func(
                Answer(get_audio_text_from_openai_answer(llm_data), DataType.STR)
            )
            return llm_data

        return adapt(
            llm_handler,
            cache_data_convert,
            update_cache_callback,
            model=model,
            file=file,
            *args,
            **kwargs,
        )

    @classmethod
    def translate(cls, model: str, file: Any, *args, **kwargs):
        def llm_handler(*llm_args, **llm_kwargs):
            try:
                return super(Audio, cls).translate(*llm_args, **llm_kwargs)
            except openai.OpenAIError as e:
                raise wrap_error(e) from e

        def cache_data_convert(cache_data):
            return _construct_audio_text_from_cache(cache_data)

        def update_cache_callback(
            llm_data, update_cache_func, *args, **kwargs
        ):  # pylint: disable=unused-argument
            update_cache_func(
                Answer(get_audio_text_from_openai_answer(llm_data), DataType.STR)
            )
            return llm_data

        return adapt(
            llm_handler,
            cache_data_convert,
            update_cache_callback,
            model=model,
            file=file,
            *args,
            **kwargs,
        )


class Image(openai.Image):
    """Openai Image Wrapper

    Example:
        .. code-block:: python

            from gptcache import cache
            from gptcache.processor.pre import get_prompt
            # init gptcache
            cache.init(pre_embedding_func=get_prompt)
            cache.set_openai_key()

            from gptcache.adapter import openai
            # run image generation model with gptcache
            response = openai.Image.create(
              prompt="a white siamese cat",
              n=1,
              size="256x256"
            )
            response_url = response['data'][0]['url']
    """

    @classmethod
    def _llm_handler(cls, *llm_args, **llm_kwargs):
        try:
            return super().create(*llm_args, **llm_kwargs)
        except openai.OpenAIError as e:
            raise wrap_error(e) from e

    @classmethod
    def create(cls, *args, **kwargs):
        response_format = kwargs.pop("response_format", "url")
        size = kwargs.pop("size", "256x256")

        def cache_data_convert(cache_data):
            return _construct_image_create_resp_from_cache(
                image_data=cache_data, response_format=response_format, size=size
            )

        def update_cache_callback(
            llm_data, update_cache_func, *args, **kwargs
        ):  # pylint: disable=unused-argument
            if response_format == "b64_json":
                img_b64 = get_image_from_openai_b64(llm_data)
                if isinstance(img_b64, str):
                    img_b64 = img_b64.encode("ascii")
                update_cache_func(Answer(img_b64, DataType.IMAGE_BASE64))
            elif response_format == "url":
                update_cache_func(
                    Answer(get_image_from_openai_url(llm_data), DataType.IMAGE_URL)
                )
            return llm_data

        return adapt(
            cls._llm_handler,
            cache_data_convert,
            update_cache_callback,
            response_format=response_format,
            size=size,
            *args,
            **kwargs,
        )


class Moderation(openai.Moderation, BaseCacheLLM):
    """Openai Moderation Wrapper

    Example:
        .. code-block:: python

            from gptcache.adapter import openai
            from gptcache.adapter.api import init_similar_cache
            from gptcache.processor.pre import get_openai_moderation_input

            init_similar_cache(pre_func=get_openai_moderation_input)
            openai.Moderation.create(
                input="I want to kill them.",
            )
    """

    @classmethod
    def _llm_handler(cls, *llm_args, **llm_kwargs):
        try:
            return super().create(*llm_args, **llm_kwargs) if not cls.llm else cls.llm(*llm_args, **llm_kwargs)
        except openai.OpenAIError as e:
            raise wrap_error(e) from e

    @classmethod
    def _cache_data_convert(cls, cache_data):
        return json.loads(cache_data)

    @classmethod
    def _update_cache_callback(
        cls, llm_data, update_cache_func, *args, **kwargs
    ):  # pylint: disable=unused-argument
        update_cache_func(Answer(json.dumps(llm_data, indent=4), DataType.STR))
        return llm_data

    @classmethod
    def create(cls, *args, **kwargs):
        kwargs = cls.fill_base_args(**kwargs)
        res = adapt(
            cls._llm_handler,
            cls._cache_data_convert,
            cls._update_cache_callback,
            *args,
            **kwargs,
        )

        input_request_param = kwargs.get("input")
        expect_res_len = 1
        if isinstance(input_request_param, List):
            expect_res_len = len(input_request_param)
        if len(res.get("results")) != expect_res_len:
            kwargs["cache_skip"] = True
            res = adapt(
                cls._llm_handler,
                cls._cache_data_convert,
                cls._update_cache_callback,
                *args,
                **kwargs,
            )
        return res


def _construct_resp_from_cache(return_message, saved_token):
    return {
        "gptcache": True,
        "saved_token": saved_token,
        "choices": [
            {
                "message": {"role": "assistant", "content": return_message},
                "finish_reason": "stop",
                "index": 0,
            }
        ],
        "created": int(time.time()),
        "usage": {"completion_tokens": 0, "prompt_tokens": 0, "total_tokens": 0},
        "object": "chat.completion",
    }


def _construct_stream_resp_from_cache(return_message, saved_token):
    created = int(time.time())
    return [
        {
            "choices": [
                {"delta": {"role": "assistant"}, "finish_reason": None, "index": 0}
            ],
            "created": created,
            "object": "chat.completion.chunk",
        },
        {
            "choices": [
                {
                    "delta": {"content": return_message},
                    "finish_reason": None,
                    "index": 0,
                }
            ],
            "created": created,
            "object": "chat.completion.chunk",
        },
        {
            "gptcache": True,
            "choices": [{"delta": {}, "finish_reason": "stop", "index": 0}],
            "created": created,
            "object": "chat.completion.chunk",
            "saved_token": saved_token,
        },
    ]


def _construct_text_from_cache(return_text):
    return {
        "gptcache": True,
        "choices": [
            {
                "text": return_text,
                "finish_reason": "stop",
                "index": 0,
            }
        ],
        "created": int(time.time()),
        "usage": {"completion_tokens": 0, "prompt_tokens": 0, "total_tokens": 0},
        "object": "text_completion",
    }


def _construct_image_create_resp_from_cache(image_data, response_format, size):
    import_pillow()
    from PIL import Image as PILImage  # pylint: disable=C0415

    img_bytes = base64.b64decode((image_data))
    img_file = BytesIO(img_bytes)  # convert image to file-like object
    img = PILImage.open(img_file)
    new_size = tuple(int(a) for a in size.split("x"))
    if new_size != img.size:
        img = img.resize(new_size)
        buffered = BytesIO()
        img.save(buffered, format="JPEG")
    else:
        buffered = img_file

    if response_format == "url":
        target_url = os.path.abspath(str(int(time.time())) + ".jpeg")
        with open(target_url, "wb") as f:
            f.write(buffered.getvalue())
        image_data = target_url
    elif response_format == "b64_json":
        image_data = base64.b64encode(buffered.getvalue()).decode("ascii")
    else:
        raise AttributeError(
            f"Invalid response_format: {response_format} is not one of ['url', 'b64_json']"
        )

    return {
        "gptcache": True,
        "created": int(time.time()),
        "data": [{response_format: image_data}],
    }


def _construct_audio_text_from_cache(return_text):
    return {
        "gptcache": True,
        "text": return_text,
    }


def _num_tokens_from_messages(messages):
    """Returns the number of tokens used by a list of messages.
        返回消息列表使用的tokens数。
    """
    tokens_per_message = 3
    tokens_per_name = 1

    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        for key, value in message.items():
            num_tokens += token_counter(value)
            if key == "name":
                num_tokens += tokens_per_name
    num_tokens += 3  # every reply is primed with <|start|>assistant<|message|>
    return num_tokens
