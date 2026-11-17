#!/usr/bin/env python3
"""intent.py — 意图识别唯一入口

原先两套逻辑（控制台关键词打分 / 监听器关键词降级）各写一份，词表与阈值
容易漂移。这里合并为一处：
- classify_instruction：控制台派单用，关键词 evidence + bundle 打分；
- fallback_message：飞书监听器在 LLM 不可用时的降级路由。

监听器的 LLM 分类仍保留在 listener.py，但降级路径统一走本模块。
"""


def classify_instruction(instruction, role_missions, bundles, non_agent_patterns):
    """按关键词给指令打分，返回 {ok, data}；调用方提供角色词表与规则表。"""
    instruction = str(instruction or "").strip()
    if not instruction:
        raise ValueError("指令为空，请输入要下达的任务")
    if len(instruction) > 500:
        raise ValueError("指令太长，请浓缩到 500 字以内")

    for rx, op in non_agent_patterns:
        if rx.search(instruction):
            return {
                "ok": True,
                "data": {
                    "intent": op,
                    "needs_agent": False,
                    "suggested_roles": [],
                    "why": "系统操作，不需要 agent 执行，直接处理",
                    "evidence": [],
                    "confidence": 0.95,
                    "note": "建议直接点「新建」按钮 / 看板操作，不用派活。",
                },
            }

    bundle_hits = []
    for b in bundles:
        matched = [e for e in b["evidence"] if e in instruction]
        if matched:
            bundle_hits.append({"key": b["key"], "label": b["label"],
                                "evidence": matched, "roles": list(b["roles"])})

    scores, evidence_map = {}, {}
    for role, miss in role_missions.items():
        score = 0.0
        evidence = []
        seen = set()
        for v in miss.get("verbs", []):
            if v and v in instruction and v not in seen:
                seen.add(v)
                score += 3.0
                evidence.append(v)
        for n in miss.get("nouns", []):
            if n and n in instruction and n not in seen:
                seen.add(n)
                score += 2.0
                evidence.append(n)
        if score > 0:
            scores[role] = score * miss.get("weight", 1.0)
            evidence_map[role] = evidence

    for b in bundle_hits:
        for role in b["roles"]:
            scores[role] = scores.get(role, 0) + 4.0
            evidence_map.setdefault(role, []).append("[" + b["label"] + "]")

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    suggested = []
    for role, score in ranked[:3]:
        suggested.append({
            "role": role,
            "label": role_missions[role]["label"],
            "score": round(score, 1),
            "evidence": evidence_map.get(role, []),
            "bundle": next((b["label"] for b in bundle_hits if role in b["roles"]), ""),
        })

    needs_agent = len(suggested) > 0 and suggested[0]["score"] >= 2.0
    top_score = suggested[0]["score"] if suggested else 0
    confidence = min(0.95, 0.5 + top_score / 30.0)

    why_parts = []
    if bundle_hits:
        why_parts.append("匹配协作意图：" + "、".join(b["label"] for b in bundle_hits))
    if suggested:
        why_parts.append("关键证据词：" + "、".join(suggested[0]["evidence"][:5]))
    else:
        why_parts.append("没有命中明确 agent 域，建议手动选择")

    return {
        "ok": True,
        "data": {
            "intent": "agent_task" if needs_agent else "manual",
            "needs_agent": needs_agent,
            "suggested_roles": suggested,
            "why": "；".join(why_parts),
            "evidence": bundle_hits,
            "confidence": round(confidence, 2),
            "note": (
                "系统识别到这需要 agent 执行，勾勾选要派的角色后确认。"
                if needs_agent else
                "未命中典型 agent 职责域，你可以手动勾选要派的角色。"
            ),
        },
    }


def fallback_message(text):
    """LLM 不可用时的关键词降级，返回 {intent, ...}。"""
    t = str(text or "").lower()
    if any(k in t for k in ("咋样", "什么情况", "状态", "看看", "进度", "态势", "概览")):
        return {"intent": "brief"}
    if any(k in t for k in ("计划", "拆", "排期", "规划", "安排一下")):
        return {"intent": "plan", "goal": text}
    if any(k in t for k in ("加个任务", "新增任务", "新建任务", "todo", "待办")):
        return {"intent": "task", "title": text}
    if any(k in t for k in ("同步", "推进", "投事件", "投递")):
        return {"intent": "sync"}
    if any(k in t for k in ("help", "怎么用", "你能做", "功能")):
        return {"intent": "help"}
    return {"intent": "chat", "reply": "👌 已记录。试试问我「看看项目咋样」或「帮我写个诊断报告」"}
