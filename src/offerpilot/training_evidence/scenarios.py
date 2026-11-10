"""Versioned, explicit-option rules. Free-text answers are never scored."""
from typing import Any

RUBRIC_VERSION = "idempotency_retry.v1"
POOL_VERSION = "idempotency_retry_v1"
# Scenario, free response, explicit premises/question, choices, rule explanation.
_ITEMS = [
    ("retry_timeout", "投递已成功写入，但客户端等待响应超时。用户点击重试：如何判断是否已成功，并避免重复写入？请说明依据和恢复步骤。",
     "接口约定同一逻辑请求复用同一幂等键；服务端仍保存该键的回执，并支持查询或同键重放。客户端只知道等待响应超时。此时应怎样恢复？",
     [("query_same_key", "查询原键回执，或按契约以原键重放，核对处理结果。"), ("new_key_retry", "生成新键，再提交同一业务请求。"), ("assume_failed", "将超时直接认定为写入失败，重新执行。")],
     "客户端超时不等于服务端未提交。原请求身份及可查询回执是恢复证据；换键可能绕过去重，超时本身不足以判断失败。"),
    ("concurrent_retry", "同一份投递请求被两个客户端同时重试。如何保证只写入一次，并让两个请求都获得可核对的结果？请说明唯一约束和回执处理。",
     "两请求具有相同幂等键和相同规范化内容，并发到达同一服务，共享支持事务与唯一约束的数据库。业务写入和回执都在该数据库中，没有外部副作用。哪种方案可以建立可靠去重？",
     [("atomic_unique_receipt", "用幂等键唯一约束仲裁，并将业务变更与回执放在同一事务；冲突请求等待未完成事务或读取已提交回执。"), ("check_then_insert", "两个请求各自先查键不存在，再分别执行写入。"), ("client_mutex", "只在客户端禁用重复点击，不增加服务端仲裁。")],
     "先查后写存在竞态；客户端锁覆盖不了其他客户端。唯一仲裁及同事务边界才能把只执行一次与可核对回执关联起来。"),
    ("payload_conflict", "服务端已保存幂等键 K 对应请求 P1 的成功回执。客户端修改了投递内容，携带同一个 K 提交不同的规范化请求 P2。服务端应该怎样处理？请说明请求身份、冲突判断和后续操作。",
     "服务端保存了 K 对应 P1 的成功回执；同一个 K 现在携带规范化内容不同的 P2（P1≠P2）。哪种处理符合幂等键绑定契约？",
     [("reject_key_conflict", "返回同键异参冲突，不执行 P2；确认这是新业务操作后再建立新请求身份。"), ("replay_other_payload", "返回 P1 的成功回执，并把 P2 也视为成功。"), ("overwrite_receipt", "覆盖 K 的原回执，再执行 P2。")],
     "幂等键绑定同一命令内容，不是任意请求的通行证。不同内容复用键不是正常重试；原回执不能证明 P2 已执行。"),
    ("transaction_gap", "业务变更和幂等回执都保存在同一个支持 ACID 事务的数据库，但目前分两次提交。业务变更提交后进程崩溃，回执未写入；系统没有外部副作用。为了防止今后重试再次执行业务，应该怎样修改事务边界？请区分设计修复与既有异常数据核对。",
     "业务变更和回执在同一个支持 ACID 的数据库中，无外部副作用。当前分两次提交，业务提交后崩溃会缺少回执。为了避免今后再次出现该缺口，哪种设计符合前提？",
     [("atomic_business_receipt", "把幂等键绑定、业务变更和成功回执放入同一数据库事务，全部提交或全部回滚。"), ("receipt_after_commit", "仍分两次提交，只在业务提交后增加 try/catch。"), ("receipt_before_business", "先单独提交成功回执，再提交业务变更。")],
     "两次提交之间始终有崩溃窗口。先写成功回执可能留下虚假成功。设计修复不自动解决既有异常数据，也不能泛化到外部副作用。"),
    ("expired_receipt", "接口只承诺在 24 小时内按幂等键去重。48 小时后客户端重试，旧回执已经清理，原业务可能已成功，业务层没有另外的唯一约束。此时怎样处理才不会把未知结果误当成安全重试？请说明需要核对的事实和保证范围。",
     "去重只承诺 24 小时；48 小时后回执已清理，原业务可能已成功，业务层无另外的唯一约束。现在应如何恢复？",
     [("reconcile_business_outcome", "停止盲重试，先通过业务事实或人工核对确认原操作结果，再决定恢复步骤。"), ("same_key_guarantees", "继续使用原键，就仍保证不会重复执行。"), ("new_key_guarantees", "换新键提交，就能保证原业务不会重复。")],
     "去重保留期已过，同键不再提供题设中的保证；换键也不能消除已成功的可能。这里是不保证，不是必定重复。"),
    ("stale_revision", "一条带幂等键的保存命令已经成功，回执仍在。随后用户更正或撤回了记录。这个接口明确约定：原键重放只确认原命令已经处理，并返回当前可见状态，不重新执行，也不返回历史正文。旧客户端再次重试时应该如何处理？请说明为什么不能恢复旧答案。",
     "原保存命令成功且回执仍在，记录随后被更正或撤回。本接口约定同键重放只确认已处理并返回当前可见状态，不重做、不返回历史正文。哪个处理符合本题接口契约？",
     [("replay_current_state", "识别原命令已处理，返回当前可见状态，不重写或恢复旧内容。"), ("reexecute_old_write", "重新执行原保存命令，用旧答案覆盖当前记录。"), ("return_old_snapshot", "返回首次保存时的完整响应，包括现已撤回的正文。")],
     "成功回执证明原命令已处理，不授权覆盖之后的更正或撤回。这是本题明确的当前状态重放契约，不是所有幂等 API 的通用规范。"),
]
# Stable IDs define correctness; display position is not a scoring rule.
SUPPORTED_CHOICES = {
    "retry_timeout": "query_same_key",
    "concurrent_retry": "atomic_unique_receipt",
    "payload_conflict": "reject_key_conflict",
    "transaction_gap": "atomic_business_receipt",
    "expired_receipt": "reconcile_business_outcome",
    "stale_revision": "replay_current_state",
}
CHOICE_ORDER = {
    "retry_timeout": (1, 0, 2),
    "concurrent_retry": (2, 1, 0),
    "payload_conflict": (0, 2, 1),
    "transaction_gap": (1, 2, 0),
    "expired_receipt": (2, 0, 1),
    "stale_revision": (0, 1, 2),
}
SCENARIOS: tuple[dict[str, Any], ...] = (
    {"id": "original", "prompt": "回看这次已保存的练习回答，并如实标注作答时的帮助情况。", "decision_question": None},
    *({"id": sid, "prompt": prompt, "decision_question": {
        "rubric_version": RUBRIC_VERSION, "question_id": f"{sid}.v1", "prompt": question,
        "choices": [{"id": choices[index][0], "text": choices[index][1]} for index in CHOICE_ORDER[sid]]
        + [{"id": "uncertain", "text": "我还不确定"}],
    }} for sid, prompt, question, choices, _ in _ITEMS),
)
POOL_IDS = tuple(item[0] for item in _ITEMS)
RULES: dict[str, Any] = {sid: {"supported": SUPPORTED_CHOICES[sid], "reason": reason,
               "choices": dict(choices), "rule_id": f"{sid}.v1"}
         for sid, _, _, choices, reason in _ITEMS}

# Choice-specific explanations: these are rules about the selected option only.
_CHOICE_GUIDANCE = [
    [("该方案保留原请求身份，可用回执核对超时后的结果", "补充回执仍在处理中或暂时查不到时，怎样等待或继续用原键恢复"),
     ("新键会失去原请求的去重关联，原业务可能已经成功", "改用原键，说明怎样查询或重放并核对原回执"),
     ("客户端超时不能证明服务端未提交", "列出超时后仍可能已提交的情况，再说明用什么回执证据区分")],
    [("唯一仲裁和业务、回执同事务共同封闭并发重复写入窗口", "补充另一请求遇到未提交事务或已提交回执时如何等待、读取结果"),
     ("两个请求可能同时查到不存在，再各自写入", "画出两个请求交错执行的顺序，并把唯一约束仲裁与业务、回执放入同一事务"),
     ("一个客户端的按钮锁挡不住另一个客户端的请求", "将去重控制移到服务端，说明共享唯一约束与事务边界")],
    [("相同键只能重试已绑定的同一内容，P2不能借用P1回执", "补充哪些规范化字段参与内容绑定，以及怎样区分正常重试与新业务操作"),
     ("P1的成功回执不能证明不同内容P2已执行", "说明如何分别生成并比较P1和P2的内容指纹，解释服务端应返回什么冲突"),
     ("覆盖原键绑定会把不同操作混入同一重试身份", "保留原键与P1的绑定，并说明确认新业务操作后如何建立独立身份")],
    [("同一数据库事务使业务与成功回执共同提交或共同回滚", "列出提交前后进程崩溃的结果，并单独说明已有异常数据如何核对"),
     ("try/catch不能消除两次提交间进程崩溃的窗口", "标出业务提交与回执提交之间的窗口，并将二者合到同一事务"),
     ("先提交成功回执可能留下业务未完成却显示成功的状态", "分析回执提交后、业务提交前崩溃的结果，再调整原子事务边界")],
    [("题设去重保证已过期，需要业务事实核对而非假定可安全重试", "明确哪些业务事实可确认原操作结果，以及仍无法确认时如何交由人工核对"),
     ("原键的24小时去重保证不能覆盖48小时后的请求", "指出保证的时间边界，说明没有回执时还需哪些业务证据"),
     ("换键不能消除原业务可能已成功的事实", "先核对原业务结果，再说明什么时候才能建立新的业务操作")],
    [("该方案遵守题设当前状态重放契约，并保留后续更正或撤回", "分别说明记录已更正和已撤回时，原键重放应该返回什么"),
     ("重新执行旧命令可能覆盖之后的更正或撤回，违背题设重放契约", "区分确认原命令已处理与再次执行，并列出应保留的当前状态"),
     ("历史完整响应可能包含已撤回正文，不符合题设当前可见状态契约", "说明回执保留什么处理事实，以及哪些历史正文不能随重放返回")],
]
GUIDANCE = {sid: {choice_id: {"reason": reason, "next_step": next_step}
                  for (choice_id, _), (reason, next_step) in zip(choices, guidance, strict=True)}
            for (sid, _, _, choices, _), guidance in zip(_ITEMS, _CHOICE_GUIDANCE, strict=True)}
