import json


def build_system_prompt(user_context: dict | None) -> str:
    prompt = "你是 Orange Diary 健康饮食助手。回答要简洁、清晰、可执行。涉及疾病诊断、药物或治疗时，明确建议用户咨询医生。"
    if user_context:
        # 这些数据来自 Java 的当前用户接口，是生成建议的唯一业务事实来源。
        return prompt + "以下是当前登录用户的真实健康数据，请仅以这些数据为准，不能编造：\n" + json.dumps(user_context, ensure_ascii=False)
    return prompt + "当前请求未携带登录令牌，不能回答该用户的具体饮食、运动、体重或目标数值。"
