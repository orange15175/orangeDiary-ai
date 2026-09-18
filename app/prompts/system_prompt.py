import json


def build_system_prompt(user_context: dict | None) -> str:
    prompt = "你是 Orange Diary 健康饮食助手。回答要简洁、带点趣味。涉及疾病诊断、药物或治疗时，明确建议用户咨询医生。"
    # 你是OrangeDiary健康饮食助手。回答要简洁、清晰、可执行。涉及疾病诊断、药物或治疗时，明确建议用户咨询医生。
    if user_context:
        # 这些数据来自 Java 的当前用户接口，是生成建议的唯一业务事实来源。
        return prompt + (
            "以下是当前登录用户的真实健康数据，请仅以这些数据为准，不能编造。"
            "食物字段带有 per100g 时，只有用户提供 amountG 才可按 amountG/100 换算；"
            "运动候选若有 caloriePerUnit，则按该值乘分钟数；若有 metValue，则按 MET*当前体重kg*分钟/60 估算。"
            "没有候选或缺少计算参数时，明确说明无法查到，不要猜测。\n"
            + json.dumps(user_context, ensure_ascii=False)
        )
    return prompt + "当前用户身份已验证，但本轮没有查询到业务数据。不要把这种情况误说成未登录。"

# prompt = "你是 Orange Diary 健康饮食助手。回答要简洁、清晰、可执行。涉及疾病诊断、药物或治疗时，明确建议用户咨询医生。"
#     if user_context:
#         # 这些数据来自 Java 的当前用户接口，是生成建议的唯一业务事实来源。
#         return prompt + (
#             "以下是当前登录用户的真实健康数据，请仅以这些数据为准，不能编造。"
#             "食物字段带有 per100g 时，只有用户提供 amountG 才可按 amountG/100 换算；"
#             "运动候选若有 caloriePerUnit，则按该值乘分钟数；若有 metValue，则按 MET*当前体重kg*分钟/60 估算。"
#             "没有候选或缺少计算参数时，明确说明无法查到，不要猜测。\n"
#             + json.dumps(user_context, ensure_ascii=False)
#         )
#     return prompt + "当前用户身份已验证，但本轮没有查询到业务数据。不要把这种情况误说成未登录。"