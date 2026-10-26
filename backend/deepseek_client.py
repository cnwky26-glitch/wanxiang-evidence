"""DeepSeek JSON 调用，保存请求与响应，复用相同请求的已完成结果。"""
import json
import os
from urllib.request import Request, urlopen


BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-flash"


def complete_json(messages, response_path):
    body = {"model": MODEL, "messages": messages, "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"}, "temperature": 0, "max_tokens": 32768}
    request_path = response_path.with_name(response_path.stem + "_request.json")
    if request_path.exists():
        if json.loads(request_path.read_text(encoding="utf-8")) != body:
            raise ValueError(f"请求已变化，请使用新的输出目录：{request_path}")
    else:
        request_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if response_path.exists():
        result = json.loads(response_path.read_text(encoding="utf-8"))
        print(f"复用已完成响应：{response_path.name}", flush=True)
    else:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("请先配置 DEEPSEEK_API_KEY")
        request = Request(BASE_URL + "/chat/completions",
                          data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
        with urlopen(request, timeout=180) as response:
            result = json.load(response)
        response_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    choice = result["choices"][0]
    if choice["finish_reason"] != "stop":
        raise ValueError(f"模型未正常结束：{choice['finish_reason']}，响应见 {response_path}")
    content = choice["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"模型返回空内容：{response_path}")
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError(f"模型输出须为 JSON 对象：{response_path}")
    return payload
