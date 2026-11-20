// 多步录入是「记录页 → 选择页 → 编辑页」三层。保存成功后如果只 replace 回记录页，
// 选择页会留在历史栈下面，用户按返回反而被送回刚离开的列表，看起来像退不出去。
// 这里记下这一趟的起点位置，保存成功时精确连退回去，历史栈和视觉返回都干净。
const KEY = "orangeDiaryFlowReturn";

// 进入子流程前调用：记住当前历史位置。
export function markFlowReturn() {
  try {
    const position = window.history.state?.position;
    if (typeof position === "number")
      sessionStorage.setItem(KEY, String(position));
  } catch {
    // 隐私模式下 storage 不可用时忽略，调用方会退回 replace。
  }
}

export function clearFlowReturn() {
  try {
    sessionStorage.removeItem(KEY);
  } catch {}
}

// 需要连退的层数。起点不明、已经被别的导航顶掉、或距离过远时返回 0，调用方退回 replace。
export function flowReturnSteps() {
  try {
    const marked = Number(sessionStorage.getItem(KEY));
    const position = window.history.state?.position;
    if (!sessionStorage.getItem(KEY) || !Number.isInteger(marked)) return 0;
    if (typeof position !== "number") return 0;
    const steps = position - marked;
    // 只在这条流程内部连退（最多 3 层），避免退到流程之外或退出应用。
    return steps > 0 && steps <= 3 ? steps : 0;
  } catch {
    return 0;
  }
}
